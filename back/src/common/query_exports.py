"""Query-owned table exports: bounded inline responses and durable large jobs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import IO, TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, column, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from src.common import export as export_config
from src.common.durable_exports import DurableExportManager
from src.common.export_jobs import ExportJob, ExportJobRead, ExportSpec, final_path, part_path
from src.common.export_process import generate_in_process
from src.common.job_contracts import ExportQueueFullError
from src.common.xlsx_table import TableWorkbookWriter
from src.config import get_settings
from src.database import async_session_factory
from src.downloads import attachment_response

if TYPE_CHECKING:
    from src.common.export import ExportColumnLike

BATCH_ROWS = 500
type QuerySource = Literal["kppk_cost", "kppk_realization", "kppk_dictionary", "raw_materials", "renovation_dictionary"]


class QueryExportSpec(ExportSpec):
    source: QuerySource
    request: dict[str, object]
    asset_ids: list[UUID] = Field(default_factory=list)


@dataclass
class TableSource:
    """Ordered accessible IDs plus a loader that reads only the requested batch."""

    ids: SelectOfScalar[UUID]
    load: Callable[[list[UUID]], Awaitable[Sequence[BaseModel]]]
    title: str
    columns: Sequence[ExportColumnLike]
    header_pinned: bool
    merge_rows_by: tuple[str, ...] = ()
    lists: dict[str, list[str]] | None = None
    column_lists: dict[str, str] = field(default_factory=dict)
    calculated: Mapping[str, tuple[str, ...] | str] = field(default_factory=dict)
    totals: dict[str, float] | None = None
    values_only: bool = False

    def select_rows(self, row_ids: list[UUID] | None) -> None:
        if row_ids is not None:
            # One array bind avoids PostgreSQL's parameter ceiling for a large
            # client selection. The query still enforces the accessible scope.
            id_column = self.ids.selected_columns[0]
            selected = bindparam("export_row_ids", row_ids, type_=ARRAY(PG_UUID(as_uuid=True)))
            requested = (
                func.unnest(selected)
                .table_valued(column("id", PG_UUID(as_uuid=True)), with_ordinality="position")
                .render_derived(name="export_selection")
            )
            self.ids = (
                self.ids.join(requested, id_column == requested.c.id).order_by(None).order_by(requested.c.position)
            )

    async def count(self, session: AsyncSession) -> int:
        return int(await session.scalar(func.count().select().select_from(self.ids.order_by(None).subquery())) or 0)

    async def batches(self, session: AsyncSession) -> AsyncIterator[list[BaseModel]]:
        cursor = await session.stream_scalars(self.ids.execution_options(yield_per=BATCH_ROWS))
        try:
            async for partition in cursor.partitions(BATCH_ROWS):
                ids = list(partition)
                rows = await self.load(ids)
                by_id = {str(getattr(row, "id", None) or getattr(row, "valueId", "")): row for row in rows}
                yield [by_id[str(identifier)] for identifier in ids if str(identifier) in by_id]
        finally:
            await cursor.close()

    def writer(self, sink: IO[bytes], total: int) -> TableWorkbookWriter:
        return TableWorkbookWriter(
            sink,
            self.title,
            self.columns,
            header_pinned=self.header_pinned,
            row_total=total,
            merge_rows_by=self.merge_rows_by,
            lists=self.lists,
            column_lists=self.column_lists,
            calculated=self.calculated,
            totals_row=self.totals,
            values_only=self.values_only,
        )


async def resolve_source(session: AsyncSession, spec: QueryExportSpec) -> TableSource:
    values_only = bool(spec.request.get("valuesOnly", True))
    if spec.source == "raw_materials":
        from src.raw_materials.export_source import resolve_export_source as resolve_raw  # noqa: PLC0415

        source = await resolve_raw(session, spec)
    elif spec.source == "renovation_dictionary":
        from src.renovation.export_source import resolve_export_source as resolve_dictionary  # noqa: PLC0415

        source = await resolve_dictionary(session, spec)
    else:
        from src.kppk.export_source import resolve_export_source  # noqa: PLC0415

        source = await resolve_export_source(session, spec)
    source.values_only = values_only
    return source


async def _write_batch(writer: TableWorkbookWriter, rows: Sequence[BaseModel]) -> None:
    task = asyncio.create_task(asyncio.to_thread(writer.write_rows, rows))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def generate_query_file(job: ExportJob, *, report: Callable[[int, int], None] | None = None) -> None:
    spec = job.spec
    assert isinstance(spec, QueryExportSpec)  # noqa: S101
    async with async_session_factory() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        source = await resolve_source(session, spec)
        spec.total = await source.count(session)
        if report:
            report(0, spec.total)
        partial, final = part_path(job.file_id), final_path(job.file_id)
        with partial.open("wb") as sink:
            writer = source.writer(sink, spec.total)
            try:
                async for batch in source.batches(session):
                    await _write_batch(writer, batch)
                    job.progress += len(batch)
                    if report:
                        report(job.progress, spec.total)
            finally:
                await asyncio.to_thread(writer.close)
        partial.replace(final)
        job.path = final
        job.size = final.stat().st_size


query_export_jobs = DurableExportManager(
    QueryExportSpec, generate_in_process, kind="table_query", concurrency=get_settings().export_worker_concurrency
)


async def query_export_response(session: AsyncSession, spec: QueryExportSpec, user_id: str) -> Response:
    """Count before reading; large requests persist a query, never table rows."""
    source = await resolve_source(session, spec)
    spec.total = await source.count(session)
    inline_limit = export_config.INLINE_EXPORT_MAX_ROWS
    if spec.total > inline_limit:
        return await _enqueue(spec, user_id)
    # The inline path is bounded by the count; limit the actual read too in
    # case concurrent inserts change a READ COMMITTED snapshot.
    source.ids = source.ids.limit(inline_limit + 1)
    rows = [row async for batch in source.batches(session) for row in batch]
    if len(rows) > inline_limit:
        spec.total = len(rows)
        return await _enqueue(spec, user_id)

    def render() -> bytes:
        sink = BytesIO()
        writer = source.writer(sink, len(rows))
        try:
            writer.write_rows(rows)
        finally:
            writer.close()
        return sink.getvalue()

    return attachment_response(await asyncio.to_thread(render), filename=spec.file_name)


async def _enqueue(spec: QueryExportSpec, user_id: str) -> JSONResponse:
    try:
        job = await query_export_jobs.create(spec, user_id)
    except ExportQueueFullError as error:
        raise HTTPException(status_code=429, detail=str(error), headers={"Retry-After": "5"}) from error
    return JSONResponse(
        ExportJobRead(
            id=job.id,
            status=job.status,
            progress=job.progress,
            total=job.spec.total,
            fileName=job.spec.file_name,
            error=job.error,
            phase=job.phase,
            attempt=job.attempt,
            startedAt=job.started_at,
            heartbeatAt=job.heartbeat_at,
        ).model_dump(mode="json"),
        status_code=202,
    )


def new_query_spec(source: QuerySource, request: BaseModel, *, asset_ids: list[UUID] | None = None) -> QueryExportSpec:
    return QueryExportSpec(
        source=source,
        request=request.model_dump(mode="json"),
        file_name=str(request.model_dump()["fileName"]),
        total=0,
        cache_key=uuid4().hex,
        asset_ids=asset_ids or [],
    )
