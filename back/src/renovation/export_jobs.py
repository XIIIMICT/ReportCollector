"""
Фоновые задания выгрузки пообъектного плана в XLSX.

Полная выгрузка (≈306k строк × 216 столбцов) занимает минуты и сотни
мегабайт, поэтому она идёт через общий менеджер заданий
(``src.common.export_jobs``): очередь, единственный worker, TTL готовых
файлов и хвостовая отдача растущего файла. Этот модуль держит только
реновационную специфику — спецификацию скоупа выгрузки и сам генератор
файла (чтение строк батчами через серверный курсор, запись — стриминговым
writer'ом в отдельном потоке, чтобы API отвечал во время сборки).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from src.common.export_jobs import (
    JOB_TTL_SECONDS,  # noqa: F401  # re-exported for tests/back-compat
    MAX_ACTIVE_JOBS,  # noqa: F401
    MAX_ACTIVE_JOBS_PER_USER,  # noqa: F401
    ExportJob,
    ExportJobManager,
    ExportQueueFullError,  # noqa: F401
    export_dir,  # noqa: F401
    final_path,
    part_path,
)
from src.database import async_session_factory

from .export import RenovationWorkbookWriter, build_plan_sheet_layout
from .service import iter_plan_export_rows, read_plan_export_meta

if TYPE_CHECKING:
    from .models import RBlock
    from .plan import RPlanState
    from .schemas import RenovationColumnFilter


@dataclass(slots=True)
class ExportJobSpec:
    """Всё, что нужно worker'у, чтобы собрать файл (сериализуемо в память)."""

    plan_id: UUID
    state: RPlanState
    asset_ids: list[UUID]
    row_ids: list[UUID] | None
    block: RBlock | None
    file_name: str
    total: int
    cache_key: str
    # Активные заголовочные фильтры сетки («видимая таблица») и входят ли в
    # скоуп ещё не распределённые черновые строки.
    filters: list[RenovationColumnFilter] | None = None
    include_unassigned: bool = False


async def _generate_plan_file(job: ExportJob) -> None:
    """Собрать XLSX: чтение батчами, запись — в отдельном потоке."""
    spec = job.spec
    assert isinstance(spec, ExportJobSpec)  # noqa: S101
    async with async_session_factory() as session:
        meta = await read_plan_export_meta(
            session,
            spec.asset_ids,
            plan_id=spec.plan_id,
            state=spec.state,
            row_ids=spec.row_ids,
            block=spec.block,
            filters=spec.filters,
            include_unassigned=spec.include_unassigned,
        )
        partial = part_path(job.id)
        final = final_path(job.id)
        with partial.open("wb") as sink:
            writer = RenovationWorkbookWriter(
                sink,
                build_plan_sheet_layout(spec.block, meta.totals),
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
                    include_unassigned=spec.include_unassigned,
                ):
                    # Подготовка строк и сборка XML — CPU-bound: уводим из
                    # event loop, чтобы API отвечал во время выгрузки.
                    await asyncio.to_thread(writer.write_rows, batch)
                    job.progress += len(batch)
            finally:
                await asyncio.to_thread(writer.close)
        partial.replace(final)
        job.path = final
        job.size = final.stat().st_size


export_jobs = ExportJobManager(_generate_plan_file, kind="renovation_plan")
