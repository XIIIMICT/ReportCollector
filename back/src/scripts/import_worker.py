"""Execute one fenced XLSB import without sharing the ASGI interpreter or log file."""

import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from src.common.durable_imports import DurableImportManager, input_path
from src.common.job_models import DataJob
from src.common.job_repository import JobRepository
from src.database import async_engine
from src.observability import REQUEST_ID
from src.renovation.import_jobs import generate_plan_import


async def run(job_id: str, lease_token: str) -> dict:
    repository = JobRepository()
    async with repository.session_factory() as session:
        record = await session.get(DataJob, UUID(job_id))
    if (
        record is None
        or record.kind != "renovation_import"
        or str(record.lease_token) != lease_token
        or not await repository.owns(record)
    ):
        msg = "Import attempt is no longer owned"
        raise RuntimeError(msg)
    REQUEST_ID.set(record.request_id)
    job = DurableImportManager.job_from_record(record)

    def report(progress: int, total: int, phase: str) -> None:
        print(
            json.dumps({"event": "progress", "progress": progress, "total": total, "phase": phase}),
            flush=True,
        )

    async def watch() -> None:
        while await repository.owns(record):  # noqa: ASYNC110 - watchdog for orphan processes
            await asyncio.sleep(5)
        current = await repository.get(job.id, job.user_id, record.kind)
        if current.status != "done":
            msg = "Import lease expired or was cancelled"
            raise RuntimeError(msg)

    work = asyncio.create_task(generate_plan_import(job, input_path(job.input_key), report=report))
    watchdog = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((work, watchdog), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        return await work
    finally:
        work.cancel()
        watchdog.cancel()
        await asyncio.gather(work, watchdog, return_exceptions=True)


async def main() -> int:
    try:
        result = await run(sys.argv[1], sys.argv[2])
        print(json.dumps({"event": "done", "result": result}), flush=True)
    except Exception as error:  # noqa: BLE001 - stable, bounded protocol to the supervisor
        retryable = isinstance(error, (ConnectionError, OperationalError)) or (
            isinstance(error, DBAPIError) and error.connection_invalidated
        )
        detail = str(error)[:2000] if isinstance(error, ValueError) else None
        if isinstance(error, IntegrityError):
            detail = "Загрузка нарушила бы ограничения уникальности — ни одна строка не изменена."
        print(
            json.dumps({"event": "failed", "retryable": retryable, "detail": detail, "error": type(error).__name__}),
            flush=True,
        )
        return 1
    finally:
        await async_engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
