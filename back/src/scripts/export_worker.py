"""One fenced export attempt in an isolated interpreter, supervised by the API worker."""

import asyncio
import json
import sys

from sqlalchemy.exc import DBAPIError, OperationalError

from src.common.export_jobs import ExportJob
from src.common.job_models import DataJob
from src.common.job_repository import JobRepository
from src.common.query_exports import QueryExportSpec, generate_query_file
from src.database import async_engine
from src.observability import REQUEST_ID
from src.renovation.export_jobs import ExportJobSpec, generate_plan_file


async def _watch_lease(repository: JobRepository, record: DataJob) -> None:
    while await repository.owns(record):  # noqa: ASYNC110 - durable lease lives in another process
        await asyncio.sleep(5)
    message = "Export lease expired or was cancelled"
    raise RuntimeError(message)


def _progress(progress: int, total: int) -> None:
    print(json.dumps({"event": "progress", "progress": progress, "total": total}), flush=True)


async def run(job_id: str, artifact: str) -> None:
    from uuid import UUID  # noqa: PLC0415

    repository = JobRepository()
    async with repository.session_factory() as session:
        record = await session.get(DataJob, UUID(job_id))
    if record is None or record.kind not in {"renovation_plan", "table_query"} or record.artifact != artifact:
        message = "Unknown export attempt"
        raise ValueError(message)
    if not await repository.owns(record):
        message = "Export attempt is no longer owned"
        raise RuntimeError(message)
    REQUEST_ID.set(record.request_id)
    spec = (ExportJobSpec if record.kind == "renovation_plan" else QueryExportSpec).model_validate(record.payload)
    job = ExportJob(id=job_id, user_id=record.user_id, spec=spec, artifact_key=artifact)
    generator = generate_plan_file if record.kind == "renovation_plan" else generate_query_file
    work = asyncio.create_task(generator(job, report=_progress))
    watch = asyncio.create_task(_watch_lease(repository, record))
    try:
        done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        await work
    finally:
        work.cancel()
        watch.cancel()
        await asyncio.gather(work, watch, return_exceptions=True)


async def main() -> int:
    try:
        await run(sys.argv[1], sys.argv[2])
    except Exception as error:  # noqa: BLE001 - parent receives a stable classification
        retryable = isinstance(error, (ConnectionError, OperationalError)) or (
            isinstance(error, DBAPIError) and error.connection_invalidated
        )
        # The parent owns the rotating application log; child processes must
        # not race its rename/rotation or hold a second file handler.
        print(json.dumps({"event": "failed", "retryable": retryable, "error": type(error).__name__}), flush=True)
        return 1
    finally:
        await async_engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
