"""Background execution for accepted renovation Excel uploads."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.durable_exports import LeaseLostError
from src.common.durable_imports import DurableImportManager, ImportJob
from src.common.import_process import generate_import_in_process
from src.common.job_repository import JobRepository
from src.config import get_settings
from src.database import async_session_factory
from src.observability import observe_phase
from src.renovation.import_apply import apply_plan_file, spool_plan_rows


@contextlib.asynccontextmanager
async def _timed_transaction(session: AsyncSession) -> AsyncGenerator[None]:
    """Own the worker transaction and time commit/rollback separately."""
    transaction = session.begin()
    await transaction.__aenter__()
    try:
        with observe_phase("import", "transaction_body"):
            yield
    except BaseException as error:
        with observe_phase("import", "rollback"):
            await transaction.__aexit__(type(error), error, error.__traceback__)
        raise
    else:
        with observe_phase("import", "commit"):
            await transaction.__aexit__(None, None, None)


async def generate_plan_import(
    job: ImportJob, source: Path, *, report: Callable[[int, int, str], None] | None = None
) -> dict[str, int]:
    """Apply one staged workbook in one short, lease-fenced worker transaction."""

    def progress(processed: int, total: int, phase: str) -> None:
        # `total` comes from the preflight row count the client accepted
        # (write + recompute); the counter stays monotonic while the worker
        # holds the project gate.
        job.progress = processed
        job.total = total
        job.phase = phase
        if report is not None:
            report(processed, total, phase)

    # Parse BEFORE opening a session, a write transaction or the project gate.
    # A temporary spool keeps RAM bounded and shortens the DB connection hold.
    job.phase = "parse"
    spool = None
    try:
        with source.open("rb") as handle, observe_phase("import", "parse"):
            spool = await asyncio.to_thread(spool_plan_rows, handle, source.suffix)
        async with async_session_factory() as session, _timed_transaction(session):
            result = await apply_plan_file(
                session,
                source=spool,
                user_id=UUID(job.user_id),
                plan_id=job.plan_id,
                streaming=True,
                expected_rows=job.expected_rows,
                progress=progress,
                source_suffix=source.suffix,
            )
            outcome = {"newRows": result.new_rows, "updatedRows": result.updated_rows}
            with observe_phase("import", "finalization"):
                if not await JobRepository().complete_in_transaction(
                    session,
                    job_id=UUID(job.id),
                    lease_token=job.lease_token,
                    total=result.new_rows + result.updated_rows,
                    result=outcome,
                ):
                    msg = "Import lease expired or was cancelled"
                    raise LeaseLostError(msg)
    finally:
        if spool is not None:
            spool.close()
    return outcome


import_jobs = DurableImportManager(
    kind="renovation_import",
    generate=generate_import_in_process,
    concurrency=get_settings().import_worker_concurrency,
)
