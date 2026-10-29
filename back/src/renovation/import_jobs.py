"""Background execution for accepted renovation XLSB uploads."""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from src.common.durable_exports import LeaseLostError
from src.common.durable_imports import DurableImportManager, ImportJob
from src.common.import_process import generate_import_in_process
from src.common.job_repository import JobRepository
from src.config import get_settings
from src.database import async_session_factory

from .import_apply import apply_plan_file


async def generate_plan_import(
    job: ImportJob, source: Path, *, report: Callable[[int], None] | None = None
) -> dict[str, int]:
    """Apply one staged workbook in a worker-owned transaction."""
    async with async_session_factory() as session, session.begin():

        def progress(processed: int) -> None:
            # The parser is intentionally streamed, so an exact total is not
            # available without a second full pass. The count is monotonic and
            # is enough to show liveness while the worker holds the project gate.
            job.progress = processed
            if report is not None:
                report(processed)

        with source.open("rb") as handle:
            result = await apply_plan_file(
                session,
                source=handle,
                user_id=UUID(job.user_id),
                plan_id=job.plan_id,
                streaming=True,
                progress=progress,
            )
        outcome = {"newRows": result.new_rows, "updatedRows": result.updated_rows}
        if not await JobRepository().complete_in_transaction(
            session,
            job_id=UUID(job.id),
            lease_token=job.lease_token,
            total=result.new_rows + result.updated_rows,
            result=outcome,
        ):
            msg = "Import lease expired or was cancelled"
            raise LeaseLostError(msg)
    return outcome


import_jobs = DurableImportManager(
    kind="renovation_import",
    generate=generate_import_in_process,
    concurrency=get_settings().import_worker_concurrency,
)
