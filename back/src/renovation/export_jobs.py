"""Renovation query specifications and the isolated, streaming XLSX generator."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from src.common.durable_exports import DurableExportManager
from src.common.export_jobs import (
    JOB_TTL_SECONDS,  # noqa: F401  # re-exported for tests/back-compat
    MAX_ACTIVE_JOBS,  # noqa: F401
    MAX_ACTIVE_JOBS_PER_USER,  # noqa: F401
    ExportJob,
    ExportJobManager,  # noqa: F401 - compatibility import for legacy queue tests
    ExportQueueFullError,  # noqa: F401
    ExportSpec,
    export_dir,  # noqa: F401
    final_path,
    part_path,
)
from src.common.export_process import generate_in_process
from src.config import get_settings
from src.database import async_session_factory

from .export import RenovationWorkbookWriter, build_plan_sheet_layout
from .models import RBlock  # noqa: TC001 - Pydantic resolves these types at runtime
from .plan import RPlanState  # noqa: TC001
from .schemas import RenovationColumnFilter, RenovationSortColumn  # noqa: TC001
from .service import iter_plan_export_rows, read_plan_export_meta


class ExportJobSpec(ExportSpec):
    """Persisted query scope; it contains no materialized table rows."""

    plan_id: UUID
    state: RPlanState
    asset_ids: list[UUID]
    row_ids: list[UUID] | None
    block: RBlock | None
    # Header filters and whether unassigned draft rows belong to the scope.
    filters: list[RenovationColumnFilter] | None = None
    sorting: list[RenovationSortColumn] | None = None
    include_unassigned: bool = False
    values_only: bool = True


async def generate_plan_file(job: ExportJob, *, report: Callable[[int, int], None] | None = None) -> None:
    """Read a consistent snapshot in batches and write one fenced artifact."""
    spec = job.spec
    assert isinstance(spec, ExportJobSpec)  # noqa: S101
    async with async_session_factory() as session:
        # Metadata and the cursor must describe one immutable database snapshot.
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        meta = await read_plan_export_meta(
            session,
            spec.asset_ids,
            plan_id=spec.plan_id,
            state=spec.state,
            row_ids=spec.row_ids,
            block=spec.block,
            filters=spec.filters,
            sorting=spec.sorting,
            include_unassigned=spec.include_unassigned,
        )
        spec.total = meta.total
        if report is not None:
            report(0, meta.total)
        partial = part_path(job.file_id)
        final = final_path(job.file_id)
        with partial.open("wb") as sink:
            writer = RenovationWorkbookWriter(
                sink,
                build_plan_sheet_layout(spec.block, meta.totals, values_only=spec.values_only),
                meta.reference_labels,
                totals=meta.totals,
                row_total=meta.total,
            )
            try:
                async for batch in iter_plan_export_rows(
                    session,
                    plan_id=meta.plan_id,
                    asset_ids=spec.asset_ids,
                    row_ids=spec.row_ids,
                    filters=spec.filters,
                    sorting=spec.sorting,
                    include_unassigned=spec.include_unassigned,
                ):
                    # Keep the child process's lease watchdog responsive too.
                    task = asyncio.create_task(asyncio.to_thread(writer.write_rows, batch))
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        # A cancelled await does not stop the Python thread.
                        await task
                        raise
                    job.progress += len(batch)
                    if report is not None:
                        report(job.progress, meta.total)
            finally:
                await asyncio.to_thread(writer.close)
        partial.replace(final)
        job.path = final
        job.size = final.stat().st_size


export_jobs = DurableExportManager(
    ExportJobSpec, generate_in_process, kind="renovation_plan", concurrency=get_settings().export_worker_concurrency
)
