"""
Renovation plan READ surface: access resolution, meta, plan rows, facets, summary, versions, diff and the streaming export queries.

Moved verbatim from ``service.py`` (Phase 4). ``service.py`` re-exports these
names so existing importers keep working. Import/export orchestration and
cell mutations live in other modules; this one performs no writes.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import BigInteger, Column, Numeric, String, Text, and_, case, func, literal, or_
from sqlalchemy import cast as sa_cast
from sqlalchemy import false as sa_false
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.auth.service import (
    CurrentUserIdentity,
    get_user_by_identity,
)
from src.common.export import INLINE_EXPORT_MAX_ROWS
from src.raw_materials.service import CellValidationError
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import (
    RenovationFieldMeta,
    RenovationHeaderCell,
    catalog_labels,
    field_meta,
    field_metas,
    formula_engine,
    header_rows,
)
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RPlanState,
)
from src.renovation.renovation_id import (
    RENOVATION_ID_FORMULA_ENABLED,
    RenovationIdInput,
)
from src.renovation.schemas import (
    RenovationAssetRead,
    RenovationCatalogOptionRead,
    RenovationCatalogRead,
    RenovationColumnFilter,
    RenovationDiffCellRead,
    RenovationDiffRead,
    RenovationDiffRowRead,
    RenovationFacetsRead,
    RenovationFacetValueRead,
    RenovationFieldRead,
    RenovationHeaderCellRead,
    RenovationHeaderRowRead,
    RenovationMetaRead,
    RenovationPlanRead,
    RenovationPlanRowsRead,
    RenovationPlanVersionRead,
    RenovationSortColumn,
)
from src.renovation.service_common import (
    _ASSET_COLUMN_PROP,
    _ERR_DIFF_ARCHIVED_ONLY,
    _ERR_PLAN_NOT_FOUND,
    _ID_FOLD_LOWER,
    _ID_FOLD_UPPER,
    _PLAN_AUDIT_COLUMNS,
    _RENOVATION_ID_BAD_LABEL,
    _RENOVATION_ID_COLUMN,
    _RENOVATION_ID_OK_LABEL,
    _VIRTUAL_FIELDS,
    EXPORT_BATCH_SIZE,
    NoRenovationAccessError,
    RenovationPlanStateError,
    _baseline_boundary,
    _plan_audit_attr,
    _plan_row_read,
    _previous_archived_plan,
    _reference_labels,
    _row_renovation_id,
    _row_value_label,
    _rows_by_position,
    _safe_renovation_id,
    defect_number_text,
)

from .errors import PlanExportRequiresJobError, RenovationPlanNotFoundError
from .plan_queries import _plan_by_state, _plan_id_by_state, _renovation_project_id


async def resolve_renovation_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[RProjectRole], list[RAsset], RBlock | None]:
    """
    Return (user, roles, available assets, responsibility block).

    Admin, approver and block owners (Техническая дирекция/Дирекция КСиР/Дирекция МТО) always see
    all assets (a block owner is responsible for a whole block, not a subset of
    assets); only an asset worker is limited to the assets assigned in
    r_user_assets. The block comes from the user's r_user_roles link (empty for
    central roles). Raises NoRenovationAccessError if the user has no active
    role in the project.
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise NoRenovationAccessError

    links = (
        await session.exec(
            select(RUserRole)
            .join(Project, col(Project.id) == col(RUserRole.project_id))
            .where(
                col(RUserRole.user_id) == user.id,
                col(Project.key) == RENOVATION_PROJECT_KEY,
                col(RUserRole.is_active).is_(True),
            )
            .order_by(col(RUserRole.role))
        )
    ).all()
    if not links:
        raise NoRenovationAccessError
    roles = [link.role for link in links]
    block = links[0].block

    if any(role in (RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER) for role in roles):
        assets = (await session.exec(select(RAsset).order_by(RAsset.name))).all()
    else:
        assets = (
            await session.exec(
                select(RAsset)
                .join(RUserAsset, col(RUserAsset.asset_id) == col(RAsset.id))
                .where(RUserAsset.user_id == user.id)
                .order_by(RAsset.name)
            )
        ).all()
        if not assets:
            # An asset worker without a single grant is EQUIVALENT to blocked:
            # nothing is visible, so the project is denied rather than opened
            # empty.
            raise NoRenovationAccessError
    return user, roles, list(assets), block


async def read_meta(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    user_block: RBlock | None = None,
    user_roles: list[RProjectRole] | None = None,
) -> RenovationMetaRead:
    """Form dictionaries: assets, reference catalogs, field descriptions."""
    options = (
        await session.exec(
            select(RenovationReferenceValue).order_by(
                col(RenovationReferenceValue.catalog),
                col(RenovationReferenceValue.label),
            )
        )
    ).all()
    by_catalog: dict[str, list[RenovationReferenceValue]] = {}
    for option in options:
        by_catalog.setdefault(option.catalog.value, []).append(option)
    catalogs: list[RenovationCatalogRead] = []
    for catalog_id, values in by_catalog.items():
        catalogs.append(
            RenovationCatalogRead(
                id=catalog_id,
                label=catalog_labels().get(catalog_id, catalog_id),
                options=[RenovationCatalogOptionRead(valueId=str(value.id), label=value.label) for value in values],
            )
        )
    working_id = await _plan_id_by_state(session, RPlanState.WORKING)
    pending_id = await _plan_id_by_state(session, RPlanState.PENDING_APPROVAL)
    return RenovationMetaRead(
        assets=[_asset_read(asset) for asset in assets],
        catalogs=catalogs,
        fields=[_field_read(meta) for meta in field_metas()],
        headerCells=[_header_cell_read(cell) for _, cells in header_rows() for cell in cells],
        headerRows=[_header_row_read(level, cells) for level, cells in header_rows()],
        workingPlanId=working_id,
        pendingPlanId=pending_id,
        userBlock=user_block.value if user_block is not None else None,
        userRoles=sorted({role.value for role in user_roles}) if user_roles else [],
    )


def _asset_read(asset: RAsset) -> RenovationAssetRead:
    return RenovationAssetRead(
        id=asset.id,
        code=asset.code,
        repairCode=asset.code,
        name=asset.name,
    )


def _field_read(meta: RenovationFieldMeta) -> RenovationFieldRead:
    return RenovationFieldRead(
        dbName=meta.db_name,
        label=meta.label,
        cellType=meta.cell_type,
        catalog=meta.catalog,
        isCalculated=meta.is_calculated,
        isReadOnly=meta.is_read_only,
        decimals=meta.decimals,
        position=meta.position,
        ordinal=meta.ordinal,
        responsibilityBlock=meta.responsibility_block,
        rules=[dict(rule) for rule in meta.rules],
        allowNull=meta.allow_null,
    )


def _header_row_read(level: int, cells: tuple[RenovationHeaderCell, ...]) -> RenovationHeaderRowRead:
    return RenovationHeaderRowRead(
        level=level,
        cells=[_header_cell_read(cell) for cell in cells],
    )


def _header_cell_read(cell: RenovationHeaderCell) -> RenovationHeaderCellRead:
    return RenovationHeaderCellRead(
        id=cell.cell_id,
        text=cell.text,
        startPosition=cell.start_position,
        colSpan=cell.col_span,
        rowSpan=cell.row_span,
        level=cell.level,
        excelRange=cell.excel_range,
    )


async def read_plan(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
) -> RenovationPlanRead:
    """Return a plan version with rows (reference fields as labels, renovation ID)."""
    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
    else:
        plan = await _plan_by_state(session, state)
        if plan is None:
            error = RenovationPlanNotFoundError(f"Нет плана в состоянии «{state.value}».")
            raise error
    asset_ids = [asset.id for asset in assets]
    label_map = await _reference_labels(session)
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .outerjoin(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(col(RenovationPlanRow.plan_id) == plan.id)
            .where(
                col(RenovationPlanRow.asset_id).in_(asset_ids)
                | (col(RenovationPlanRow.asset_id).is_(None) & literal(bool(asset_ids)))
            )
            .order_by(col(RenovationPlanRow.position))
        )
    ).all()
    baseline_boundary = await _baseline_boundary(session) if plan.state == RPlanState.WORKING else None
    return RenovationPlanRead(
        id=plan.id,
        state=plan.state.value,
        baselineGeneration=plan.baseline_generation,
        updatedAt=plan.updated_at,
        updatedBy=plan.updated_by,
        rows=[_plan_row_read(row, asset, label_map) for row, asset in rows],
        baselineBoundary=baseline_boundary,
    )


@dataclass(slots=True)
class RenovationPlanExportMeta:
    """
    Plan metadata for the streaming XLSX writer — everything except the rows.

    `asset_ids` are the caller's permitted assets (already narrowed by the
    asset filter); `total` is the number of rows the row iterator will yield
    (the export-job progress denominator). The rows themselves are streamed in
    batches by `iter_plan_export_rows`, so a 300k-row plan never has to be
    materialized in one list.
    """

    plan_id: UUID
    baseline_generation: int
    state: RPlanState
    stamp: datetime
    reference_labels: dict[UUID, str]
    block: RBlock | None
    asset_ids: list[UUID]
    total: int
    # Additive numeric sums over the exported scope - the exported totals row
    # (the grid's pinned summary row). Keyed by the field's db name.
    totals: dict[str, float]


@dataclass(slots=True)
class RenovationPlanExportData:
    """
    Plan metadata and prepared rows for the streaming XLSX writer.

    Kept for the synchronous export endpoints and tests; the export-job worker
    uses `RenovationPlanExportMeta` + `iter_plan_export_rows` instead so memory
    stays bounded.
    """

    baseline_generation: int
    state: RPlanState
    stamp: datetime
    rows: list[tuple[int, str, str, dict[str, object]]]
    reference_labels: dict[UUID, str]
    # Responsibility block whose columns the export keeps (None — every column).
    block: RBlock | None = None
    # Additive numeric sums over the exported scope (the exported totals row).
    totals: dict[str, float] = dataclasses_field(default_factory=dict)


def _export_columns() -> tuple[Column, ...]:
    """Return the stored `col_*` value columns, read directly (see `_plan_export_query`)."""
    columns: list[Column] = []
    for meta in field_metas():
        attribute = getattr(RenovationPlanRow, meta.db_name, None)
        if attribute is not None:
            columns.append(attribute)
    return tuple(columns)


# The plan's value columns, in table order. Read as plain columns: the previous
# `jsonb_strip_nulls(to_jsonb(row) - keys)` build measured ~2 ms/row on the
# 300k-row plan (the export's bottleneck, ~10 minutes), while the direct read
# is ~0.1 ms/row — the JSONB object with 215 keys is expensive to build,
# transfer and parse.
EXPORT_COLUMNS: tuple[Column, ...] = _export_columns()
EXPORT_COLUMN_NAMES: tuple[str, ...] = tuple(column.name for column in EXPORT_COLUMNS)


def _plan_export_scope(
    plan_id: UUID,
    asset_ids: list[UUID],
    *,
    filters: list[RenovationColumnFilter] | None = None,
    label_map: dict[UUID, str] | None = None,
    include_unassigned: bool = False,
) -> ColumnElement[bool]:
    """
    Build the WHERE scope of an export read (mirrors `_plan_row_condition`).

    `filters` and `sorting` are the same header state the grid sends to
    `POST /plan/rows/query`, so the «видимая таблица» export matches what the
    user sees; `include_unassigned` keeps the not-yet-assigned draft rows the
    grid also shows when no asset filter is active.
    """
    condition = col(RenovationPlanRow.plan_id) == plan_id
    asset_condition = col(RenovationPlanRow.asset_id).in_(asset_ids)
    if include_unassigned:
        asset_condition = asset_condition | (col(RenovationPlanRow.asset_id).is_(None) & literal(bool(asset_ids)))
    condition = condition & asset_condition
    for column_filter in filters or []:
        condition = condition & _column_filter_condition(column_filter, label_map or {}, plan_id)
    return condition


def _plan_export_query(
    plan_id: UUID,
    asset_ids: list[UUID],
    row_ids: list[UUID] | None,
    *,
    filters: list[RenovationColumnFilter] | None = None,
    sorting: list[RenovationSortColumn] | None = None,
    label_map: dict[UUID, str] | None = None,
    include_unassigned: bool = False,
) -> Select:
    """Build the export row query (direct value columns, scope, optional exact set)."""
    query = (
        sa_select(
            col(RenovationPlanRow.id),
            col(RenovationPlanRow.position),
            col(RAsset.name),
            col(RAsset.code),
            *EXPORT_COLUMNS,
        )
        .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
        .where(
            _plan_export_scope(
                plan_id,
                asset_ids,
                filters=filters,
                label_map=label_map,
                include_unassigned=include_unassigned,
            )
        )
        .order_by(*_plan_order_by(sorting))
    )
    if row_ids is not None:
        query = query.where(col(RenovationPlanRow.id).in_(row_ids))
    return query


async def _resolve_export_plan(
    session: AsyncSession,
    plan_id: UUID | None,
    state: RPlanState,
) -> RenovationPlan:
    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
        return plan
    plan = await _plan_by_state(session, state)
    if plan is None:
        message = f"Нет плана в состоянии «{state.value}»."
        raise RenovationPlanNotFoundError(message)
    return plan


async def _plan_export_totals(
    session: AsyncSession,
    plan: RenovationPlan,
    asset_ids: list[UUID],
    *,
    filters: list[RenovationColumnFilter] | None = None,
    label_map: dict[UUID, str] | None = None,
    include_unassigned: bool = False,
    row_ids: list[UUID] | None = None,
) -> dict[str, float]:
    """
    Sum the additive numeric fields over the exported scope.

    The exported totals row carries these numbers (the grid's pinned summary row
    shows the same).
    """
    numeric = [
        meta.db_name
        for meta in field_metas()
        if meta.cell_type == "number" and meta.db_name not in _SUMMARY_EXCLUDED_FIELDS
    ]
    columns = [func.coalesce(func.sum(getattr(RenovationPlanRow, name)), 0) for name in numeric]
    condition = _plan_export_scope(
        plan.id,
        asset_ids,
        filters=filters,
        label_map=label_map or {},
        include_unassigned=include_unassigned,
    )
    if row_ids is not None:
        condition = condition & col(RenovationPlanRow.id).in_(row_ids)
    # Built inline so session.exec can infer the SQLModel overload (a separately
    # bound sa_select(*columns) loses it - same as read_plan_summary).
    sums = (await session.exec(select(*columns).select_from(RenovationPlanRow).where(condition))).one()
    return {name: float(value or 0) for name, value in zip(numeric, sums, strict=True)}


async def read_plan_export_meta(
    session: AsyncSession,
    asset_ids: list[UUID],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    row_ids: list[UUID] | None = None,
    block: RBlock | None = None,
    filters: list[RenovationColumnFilter] | None = None,
    sorting: list[RenovationSortColumn] | None = None,
    include_unassigned: bool = False,
) -> RenovationPlanExportMeta:
    """
    Resolve the export scope without reading rows.

    Plan, stamp, row count, reference labels and the responsibility block. The
    count honours the same header filters as the row read, so the job's progress
    total matches what the export actually writes.
    """
    plan = await _resolve_export_plan(session, plan_id, state)
    # Validate the client-provided sort columns even when the count query does
    # not otherwise touch the ordered row projection.
    _plan_order_by(sorting)
    label_map = await _reference_labels(session)
    count_query = (
        sa_select(func.count())
        .select_from(RenovationPlanRow)
        .where(
            _plan_export_scope(
                plan.id,
                asset_ids,
                filters=filters,
                label_map=label_map,
                include_unassigned=include_unassigned,
            )
        )
    )
    if row_ids is not None:
        count_query = count_query.where(col(RenovationPlanRow.id).in_(row_ids))
    # ty cannot match this aggregate to the sqlmodel `exec` overloads: the
    # `select()` it returns carries no row type once the query is an aggregate,
    # so the call is typed by hand here rather than silenced for a real error.
    total = int((await session.exec(count_query)).one()[0])  # ty: ignore[no-matching-overload]
    totals = await _plan_export_totals(
        session,
        plan,
        asset_ids,
        filters=filters,
        label_map=label_map,
        include_unassigned=include_unassigned,
        row_ids=row_ids,
    )
    return RenovationPlanExportMeta(
        plan_id=plan.id,
        baseline_generation=plan.baseline_generation,
        state=plan.state,
        stamp=await _plan_export_stamp(session, plan, asset_ids),
        reference_labels=label_map,
        block=block,
        asset_ids=list(asset_ids),
        total=total,
        totals=totals,
    )


async def iter_plan_export_rows(
    session: AsyncSession,
    *,
    plan_id: UUID,
    asset_ids: list[UUID],
    row_ids: list[UUID] | None = None,
    filters: list[RenovationColumnFilter] | None = None,
    sorting: list[RenovationSortColumn] | None = None,
    include_unassigned: bool = False,
    batch_size: int = EXPORT_BATCH_SIZE,
) -> AsyncIterator[list[tuple[int, str, str, dict[str, object]]]]:
    """
    Stream prepared export rows in batches.

    Each row is `(position, asset name, repair code, values)`. The values are
    the stored `col_*` columns (NULLs dropped — the sparse shape the writers
    expect). Formula fields are recomputed ONLY for rows that miss a calculated
    value (recalc happens on edit; adopted/imported rows may never have run it),
    so a normal plan skips the engine entirely — it used to cost ~2 minutes for
    300k rows. A server-side cursor keeps memory bounded: one batch at a time.
    """
    label_map = await _reference_labels(session)
    engine = formula_engine()
    calculated_fields = tuple(meta.db_name for meta in field_metas() if meta.is_calculated)

    def _prepare(values: dict[str, object], repair_code: str) -> dict[str, object]:
        if any(values.get(field) is None for field in calculated_fields):
            engine.materialize(values)
        if RENOVATION_ID_FORMULA_ENABLED and not values.get("work_or_repair_code"):
            values["work_or_repair_code"] = _sparse_row_renovation_id(values, repair_code, label_map)
        return values

    def _prepare_entry(entry: object) -> tuple[int, str, str, dict[str, object]]:
        row = cast("Sequence[object]", entry)
        position = cast("int", row[1])
        asset_name = cast("str", row[2])
        repair_code = cast("str", row[3])
        values: dict[str, object] = {
            name: value for name, value in zip(EXPORT_COLUMN_NAMES, row[4:], strict=True) if value is not None
        }
        return (position, asset_name, repair_code, _prepare(values, repair_code))

    def _prepare_partition(
        partition: Sequence[object],
    ) -> list[tuple[int, str, str, dict[str, object]]]:
        return [_prepare_entry(entry) for entry in partition]

    if row_ids is not None:
        # Exact-set exports can be large too. Query only one requested ID batch,
        # retaining the caller's order without materializing the whole plan.
        for start in range(0, len(row_ids), batch_size):
            ids = row_ids[start : start + batch_size]
            batch_query = _plan_export_query(
                plan_id,
                asset_ids,
                ids,
                filters=filters,
                sorting=sorting,
                include_unassigned=include_unassigned,
            )
            entries = (await session.exec(batch_query)).all()  # ty: ignore[no-matching-overload]
            prepared = await asyncio.to_thread(_prepare_partition, entries)
            by_id = {entry[0]: value for entry, value in zip(entries, prepared, strict=True)}
            yield [by_id[row_id] for row_id in ids if row_id in by_id]
        return
    query = _plan_export_query(
        plan_id,
        asset_ids,
        None,
        filters=filters,
        sorting=sorting,
        include_unassigned=include_unassigned,
    )
    result = await session.stream(query)
    try:
        async for partition in result.partitions(batch_size):
            # Formula materialization is CPU-bound: run it off the event loop
            # so the API stays responsive while a plan export is generating.
            yield await asyncio.to_thread(_prepare_partition, partition)
    finally:
        await result.close()


async def read_plan_export_data(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    row_ids: list[UUID] | None = None,
    asset_name: str | None = None,
    block: RBlock | None = None,
    filters: list[RenovationColumnFilter] | None = None,
    sorting: list[RenovationSortColumn] | None = None,
) -> RenovationPlanExportData:
    """
    Read plan rows directly for XLSX export without a 94k-row Pydantic copy.

    `asset_name` narrows the rows to one asset; `block` narrows the exported
    columns to one responsibility block; `filters` are the grid's header filters
    (the server-side "visible" scope). Synchronous path (the old streaming
    endpoints/tests); export jobs stream through `read_plan_export_meta` +
    `iter_plan_export_rows`.
    """
    asset_ids = [asset.id for asset in assets]
    if asset_name is not None:
        asset_ids = [asset.id for asset in assets if asset.name == asset_name]
    include_unassigned = asset_name is None
    meta = await read_plan_export_meta(
        session,
        asset_ids,
        plan_id=plan_id,
        state=state,
        row_ids=row_ids,
        block=block,
        filters=filters,
        sorting=sorting,
        include_unassigned=include_unassigned,
    )
    rows: list[tuple[int, str, str, dict[str, object]]] = []
    if meta.total > INLINE_EXPORT_MAX_ROWS:
        raise PlanExportRequiresJobError
    async for batch in iter_plan_export_rows(
        session,
        plan_id=meta.plan_id,
        asset_ids=asset_ids,
        row_ids=row_ids,
        filters=filters,
        sorting=sorting,
        include_unassigned=include_unassigned,
    ):
        # READ COMMITTED can see inserts after the count. Never keep reading
        # an unexpectedly growing plan into the HTTP process.
        if len(rows) + len(batch) > INLINE_EXPORT_MAX_ROWS:
            raise PlanExportRequiresJobError
        rows.extend(batch)
    return RenovationPlanExportData(
        baseline_generation=meta.baseline_generation,
        state=meta.state,
        stamp=meta.stamp,
        rows=rows,
        reference_labels=meta.reference_labels,
        block=meta.block,
        totals=meta.totals,
    )


async def _plan_export_stamp(
    session: AsyncSession,
    plan: RenovationPlan,
    asset_ids: list[UUID],
) -> datetime:
    """
    Timestamp for the exported plan file name.

    A working plan carries no frozen archive timestamp, so its name should read
    the moment of the table's last edit: the max row.updated_at within the
    caller's asset scope (falling back to the plan stamp when a row was never
    touched). Pending/archived plans were frozen when submitted/approved, so
    their own updated_at is that moment.
    """
    if plan.state != RPlanState.WORKING:
        return plan.updated_at
    latest = (
        await session.exec(  # ty: ignore[no-matching-overload]
            sa_select(func.max(col(RenovationPlanRow.updated_at))).where(
                col(RenovationPlanRow.plan_id) == plan.id,
                col(RenovationPlanRow.asset_id).in_(asset_ids),
            )
        )
    ).one_or_none()
    return latest[0] if latest else plan.updated_at


def _sparse_row_renovation_id(
    values: dict[str, object],
    asset_repair_code: str,
    label_map: dict[UUID, str],
) -> str | None:
    """Compute a missing renovation ID from a sparse export row."""
    work_category_id = values.get("work_category_id")
    work_type_id = values.get("work_type_id")
    defect = values.get("defect_list_or_bill_of_quantities_number")
    project_code = values.get("plan_adjustment_type")
    result = _safe_renovation_id(
        RenovationIdInput(
            existing_id=None,
            start_date=str(values.get("work_start_date") or "") or None,
            asset_repair_code=asset_repair_code,
            direction_code=str(values.get("lotting_activity_direction_code") or "") or None,
            bpof_section=str(values.get("bpof_section") or "") or None,
            work_category=label_map.get(UUID(str(work_category_id))) if work_category_id else None,
            project_code=str(project_code) if project_code else None,
            defect_number=defect_number_text(defect),
            technology_position=str(values.get("process_position") or "") or None,
            work_type_label=label_map.get(UUID(str(work_type_id))) if work_type_id else None,
        )
    )
    return result.value


def _renovation_id_is_correct_condition(plan_id: UUID) -> ColumnElement[bool]:
    """
    Rows of a plan whose renovation ID (`work_or_repair_code`) is correct.

    With the generator off (`RENOVATION_ID_FORMULA_ENABLED`), a code is judged
    by uniqueness alone: a correct code is non-empty and appears exactly once in
    the plan. «Некорректные» therefore means an empty code or a duplicate — the
    two things that still block submit. The «Идентификационный код» header
    filter offers these two groups above the searchable concrete code list.
    """
    raw = col(RenovationPlanRow.work_or_repair_code)
    non_empty = raw.is_not(None) & (raw != "")
    duplicates = (
        select(raw)
        .where(col(RenovationPlanRow.plan_id) == plan_id, non_empty)
        .group_by(raw)
        .having(func.count() > 1)
        .subquery()
    )
    return non_empty & raw.not_in(sa_select(duplicates))


# How many offending row numbers the submit refusal lists. The counts stay
# exact, but the «строки: …» list is capped — with hundreds of broken rows the
# dialog text would be unreadable.
SUBMIT_ID_ISSUE_ROW_LIMIT = 10


def _submit_id_validation_error(
    incorrect: int,
    duplicates: int,
    incorrect_rows: list[int],
    duplicate_rows: list[int],
) -> RenovationPlanStateError:
    """Return a submit refusal naming the counts and the first offending rows."""

    def _rows(rows: list[int]) -> str:
        listed = ", ".join(str(position) for position in rows[:SUBMIT_ID_ISSUE_ROW_LIMIT])
        more = len(rows) - SUBMIT_ID_ISSUE_ROW_LIMIT
        return listed + (f" … (и ещё {more})" if more > 0 else "")

    problems: list[str] = []
    if incorrect:
        problems.append(f"некорректных идентификационных кодов — {incorrect} (строки: {_rows(incorrect_rows)})")
    if duplicates:
        problems.append(f"дублирующихся идентификационных кодов — {duplicates} (строки: {_rows(duplicate_rows)})")
    return RenovationPlanStateError(
        "План нельзя отправить на согласование: "
        + "; ".join(problems)
        + ". Исправьте идентификационные коды работ и повторите отправку."
    )


async def _plan_id_issues(
    session: AsyncSession,
    plan_id: UUID,
) -> tuple[int, int, list[int], list[int]]:
    """
    Collect the rows blocking submit: incorrect (empty) and duplicated work IDs.

    A row is «incorrect» when its stored `work_or_repair_code` is empty; a duplicate is a
    non-empty ID shared by more than one row — with the generator off
    (`RENOVATION_ID_FORMULA_ENABLED`) both are the user's own text and there is
    nothing else to check. Returns the exact counts plus the first
    `SUBMIT_ID_ISSUE_ROW_LIMIT` row positions («№») of each kind — `submit_plan`
    refuses to freeze the version while either count is non-zero.
    """
    raw = col(RenovationPlanRow.work_or_repair_code)
    position = col(RenovationPlanRow.position)
    plan_condition = col(RenovationPlanRow.plan_id) == plan_id
    empty_condition = raw.is_(None) | (raw == "")
    incorrect_rows = list(
        (
            await session.exec(
                select(position)
                .where(plan_condition, empty_condition)
                .order_by(position)
                .limit(SUBMIT_ID_ISSUE_ROW_LIMIT)
            )
        ).all()
    )
    incorrect = (
        await session.exec(select(func.count()).select_from(RenovationPlanRow).where(plan_condition, empty_condition))
    ).one()
    duplicate_ids = (
        select(raw).where(plan_condition, raw.is_not(None), raw != "").group_by(raw).having(func.count() > 1).subquery()
    )
    duplicates = (await session.exec(select(func.count()).select_from(duplicate_ids))).one()
    duplicate_rows = list(
        (
            await session.exec(
                select(position)
                .where(plan_condition, raw.in_(sa_select(duplicate_ids)))
                .order_by(position)
                .limit(SUBMIT_ID_ISSUE_ROW_LIMIT)
            )
        ).all()
    )
    return int(incorrect), int(duplicates), incorrect_rows, duplicate_rows


def _column_filter_condition(
    column_filter: RenovationColumnFilter,
    label_map: dict[UUID, str],
    plan_id: UUID | None = None,
    *,
    time_zone: str | None = None,
) -> ColumnElement[bool]:
    """
    Build the SQL condition for one header filter (a column facet selection).

    Display values map back to raw columns: reference labels → their ids; the
    synthetic asset column → rows whose asset name is among the kept names;
    booleans → Да/Нет; numbers → Decimals. `includeNulls` adds the empty bucket
    (NULL or empty string). An empty condition already covers "all kept" — the
    client omits a filter whose every value is kept.

    `plan_id` scopes the «Некорректные» bucket: a code is incorrect when it
    repeats INSIDE the plan, so the duplicate lookup must not see other plans.
    """
    column = column_filter.column
    if (column_filter.from_ is not None or column_filter.to is not None) and _plan_audit_attr(column) is None:
        # A MIN–MAX window is the shape of an INSTANT column only; the audit
        # columns are routed to their own branch below. Any other column is
        # filtered by VALUES, so a window here is a stale client — refuse it
        # instead of returning an empty match nobody can explain.
        message = f"Столбец «{column}» фильтруется по значениям, а не по диапазону."
        raise CellValidationError(message)
    if column == _RENOVATION_ID_COLUMN and column_filter.valueMode != "exact":
        # Preserve saved group filters; new concrete selections use exact mode.
        if plan_id is None:
            message = "Фильтр идентификационных кодов требует план."
            raise ValueError(message)
        correct = _renovation_id_is_correct_condition(plan_id)
        conditions: list[ColumnElement[bool]] = []
        codes: list[str] = []
        for value in column_filter.values:
            if value == _RENOVATION_ID_OK_LABEL:
                conditions.append(correct)
            elif value == _RENOVATION_ID_BAD_LABEL:
                conditions.append(~correct)
            else:
                codes.append(value)
        if codes:
            conditions.append(col(RenovationPlanRow.work_or_repair_code).in_(codes))
        if column_filter.includeNulls:
            raw = col(RenovationPlanRow.work_or_repair_code)
            conditions.append(raw.is_(None) | (raw == ""))
        return or_(*conditions) if conditions else sa_false()
    value_conditions: list[ColumnElement[bool]] = []
    if column == _ASSET_COLUMN_PROP:
        if column_filter.values:
            value_conditions.append(
                col(RenovationPlanRow.asset_id).in_(
                    sa_select(col(RAsset.id)).where(col(RAsset.name).in_(column_filter.values))
                )
            )
    elif (audit_attr := _plan_audit_attr(column)) is not None:
        # The audit trio's own rules: a MIN–MAX window for an instant column (a
        # value list would compare timestamps as text), the display name for the
        # editor. The generic branch below must not run for them.
        return _audit_filter_conditions(column, audit_attr, column_filter, time_zone=time_zone)
    else:
        meta = field_meta(column)
        if meta is None or meta.db_name in _VIRTUAL_FIELDS:
            message = f"Неизвестный столбец «{column}»."
            raise CellValidationError(message)
        raw = getattr(RenovationPlanRow, column)
        if meta.cell_type == "reference":
            kept = set(column_filter.values)
            ids = [value_id for value_id, label in label_map.items() if label in kept]
            if ids:
                value_conditions.append(raw.in_(ids))
        elif meta.cell_type == "boolean":
            if column_filter.values:
                value_conditions.append(raw.in_([value == "Да" for value in column_filter.values]))
        elif meta.cell_type == "number":
            numbers = [_facet_number(value) for value in column_filter.values]
            if numbers:
                value_conditions.append(raw.in_(numbers))
        elif meta.cell_type == "date":
            # The facet renders dates as ISO strings; asyncpg cannot compare a
            # date column to a varchar, so parse the selection back to dates.
            dates = [parsed for value in column_filter.values if (parsed := _facet_date(value)) is not None]
            if dates:
                value_conditions.append(raw.in_(dates))
        elif column_filter.values:
            # Text columns are stored as strings the facet echoes back verbatim,
            # so an exact IN match is enough.
            value_conditions.append(raw.in_(column_filter.values))
        if column_filter.includeNulls:
            # The «Пустые» bucket always covers NULL. An empty string lands in
            # the same bucket only for a textual column, where the facet
            # collapses both. Comparing a uuid/date/boolean/numeric column to
            # '' makes Postgres fail the cast and 500 the whole window, so the
            # empty-string branch is text-only.
            empty_bucket: ColumnElement[bool] = raw.is_(None)
            if isinstance(raw.property.columns[0].type, String):
                empty_bucket = empty_bucket | (raw == "")
            value_conditions.append(empty_bucket)
    if value_conditions:
        return or_(*value_conditions)
    # A filter that keeps nothing must match no row, never the whole table.
    return sa_false()


def _facet_field_kind(meta: RenovationFieldMeta | None) -> str:
    """
    Derive the search a column's facet accepts from the FIELD's own type.

    The filter must follow the field, never the other way round: the defect
    number is text since migration 0030, so «706179» is searched as a substring
    and a numeric window is refused — before, the client kept sending a range
    for a column that no longer held numbers.
    """
    if meta is None:
        return "text"
    if meta.cell_type == "number" or meta.is_calculated:
        return "range"
    if meta.cell_type in {"date", "datetime"}:
        return "datetime"
    return "text"


def _check_facet_search(kind: str, search_kind: str | None) -> str | None:
    """
    Refuse a search shape the column's type does not offer (no silent rewrite).

    No `search_kind` means the popover is loading its FIRST, unfiltered window —
    that request is valid for every column type. Treating it as a text search
    made the initial load of every number/date column a 422, so their option
    lists showed «Не удалось загрузить значения».
    """
    if search_kind is None:
        return None
    if search_kind == kind:
        return search_kind
    if kind == "text" and search_kind == "range":
        message = "Столбец фильтруется по тексту, а не по числовому диапазону."
    elif kind == "range" and search_kind == "text":
        message = "Столбец фильтруется по числовому диапазону, а не по тексту."
    else:
        message = "Столбец фильтруется по диапазону дат, а не по выбранному типу поиска."
    raise CellValidationError(message)


def _parse_instant(
    value: str | None,
    column: str,
    edge: str,
    time_zone: str | None = None,
) -> datetime | None:
    """
    Parse one bound of an instant window; a bad one is a 422, not «no bound».

    A bound WITHOUT an offset is LOCAL time — it is what the picker shows and what
    the cells print — so it is read in the CLIENT's zone (`time_zone`, the
    browser's IANA name) and falls back to UTC when the client sent none. Reading
    it as UTC shifted every window by the zone offset, which made «с 15.10.2026
    00:00» miss the rows the user was looking at.
    """
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        message = f"Некорректное значение {edge} диапазона столбца «{column}»."
        raise CellValidationError(message) from error
    if parsed.tzinfo is not None:
        return parsed
    zone = _client_zone(time_zone)
    return parsed.replace(tzinfo=zone)


def _client_zone(name: str | None) -> tzinfo:
    """Resolve the client's zone by its IANA name (UTC when absent or unknown)."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return UTC


def _audit_filter_conditions(
    column: str,
    attribute: InstrumentedAttribute[object],
    column_filter: RenovationColumnFilter,
    *,
    time_zone: str | None = None,
) -> ColumnElement[bool]:
    """
    Build the SQL condition of one audit-column filter (see `_PLAN_AUDIT_COLUMNS`).

    An INSTANT column carries a MIN–MAX window and the server matches the ROWS
    between the two ends (both inclusive) — it has no value list to compare with,
    because every row carries its own timestamp. `updatedBy` keeps the ordinary
    value-list filter: the editor's display name, resolved back to the user id.

    The window's two ends are AND-ed (both must hold); «Пустые» is OR-ed with
    that condition, exactly like a value list keeps «any of the kept names».
    """
    column_expr = col(attribute)
    if column in _PLAN_AUDIT_COLUMNS:
        # The bounds arrive as TEXT and are parsed here: a bad one is a 422 with a
        # message, never a 500 over the whole window nor a silently dropped bound
        # (which showed the entire plan).
        start = _parse_instant(column_filter.from_, column, "начала", time_zone)
        end = _parse_instant(column_filter.to, column, "конца", time_zone)
        bounds: list[ColumnElement[bool]] = []
        if start is not None:
            bounds.append(column_expr >= start)
        if end is not None:
            bounds.append(column_expr <= end)
        # Both ends must hold: AND over the bounds, never OR.
        keep = and_(*bounds) if bounds else sa_false()
    elif column_filter.values:
        keep = column_expr.in_(sa_select(col(User.id)).where(col(User.name).in_(column_filter.values)))
    else:
        # A filter that keeps nothing must match no row, never the whole table.
        keep = sa_false()
    if column_filter.includeNulls:
        return or_(keep, column_expr.is_(None))
    return keep


def _facet_minute(value: str) -> datetime | None:
    """
    Parse a facet's instant value back to a UTC minute (None when not ISO).

    The facet renders `createdAt`/`updatedAt` as ISO text WITHOUT a zone
    (`2026-02-03T10:45`), so it is read as UTC — the same clock the column is
    stored in — and truncated to the minute it names.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(second=0, microsecond=0)


def _facet_number(value: str) -> Decimal | None:
    """
    Parse a facet display number back to a Decimal (None when not numeric).

    Accepts both the RU form ("1 234,5") and a plain/exponential one
    ("1.00001E+6", "1000005") — a number column's facets may be rendered by the
    client, so nothing guarantees the display spelling here.
    """
    text = value.replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation, ValueError:
        return None


def _number_or_zero(value: str) -> Decimal:
    """Return the numeric sort key of a facet value (non-numeric text sorts as zero)."""
    parsed = _facet_number(value)
    return parsed if parsed is not None else Decimal(0)


def _facet_date(value: str) -> date | None:
    """Parse a facet display date back to a date (None when not ISO)."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _facet_matches_search(
    value: str,
    *,
    kind: str,
    query: str | None,
    minimum: float | None,
    maximum: float | None,
    time_zone: str | None = None,
) -> bool:
    """Apply a facet search before pagination, using the displayed bucket value."""
    if kind == "text":
        return (query or "").casefold() in value.casefold()
    if kind == "range":
        comparable = _facet_number(value)
    elif kind == "datetime":
        try:
            parsed_datetime = datetime.fromisoformat(value)
            if parsed_datetime.tzinfo is None:
                parsed_datetime = parsed_datetime.replace(tzinfo=UTC if "T" in value else _client_zone(time_zone))
            comparable = parsed_datetime.timestamp() * 1000
        except ValueError:
            parsed_date = _facet_date(value)
            comparable = (
                datetime.combine(parsed_date, datetime.min.time(), tzinfo=_client_zone(time_zone)).timestamp() * 1000
                if parsed_date is not None
                else None
            )
    else:
        return False
    if comparable is None:
        return False
    if minimum is not None and comparable < minimum:
        return False
    return not (maximum is not None and comparable > maximum)


def _plan_row_condition(
    plan: RenovationPlan,
    assets: list[RAsset],
    *,
    asset_name: str | None,
    filters: list[RenovationColumnFilter] | None,
    label_map: dict[UUID, str],
    position_from: int | None,
    position_to: int | None,
    time_zone: str | None = None,
) -> ColumnElement[bool]:
    """Build the SQL scope of a plan read: visible assets plus header/position filters."""
    asset_ids = [asset.id for asset in assets]
    if asset_name is not None:
        asset_ids = [asset.id for asset in assets if asset.name == asset_name]
    condition = col(RenovationPlanRow.plan_id) == plan.id
    if asset_name is not None:
        condition = condition & col(RenovationPlanRow.asset_id).in_(asset_ids)
    else:
        condition = condition & (
            col(RenovationPlanRow.asset_id).in_(asset_ids)
            | (col(RenovationPlanRow.asset_id).is_(None) & literal(bool(asset_ids)))
        )
    for column_filter in filters or []:
        condition = condition & _column_filter_condition(column_filter, label_map, plan.id, time_zone=time_zone)
    if position_from is not None:
        condition = condition & (col(RenovationPlanRow.position) >= position_from)
    if position_to is not None:
        condition = condition & (col(RenovationPlanRow.position) <= position_to)
    return condition


def _plan_sort_expression(
    column: str,
) -> ColumnElement[object]:
    """
    Return the display value used to order one plan column.

    Reference columns are stored as UUIDs but displayed as catalog labels. A
    correlated scalar lookup keeps ordering identical to what the grid shows
    without loading the plan into Python. Text values use a lower-case key for
    case-insensitive ordering; the raw value remains a deterministic tie-break.
    """
    if column == _ASSET_COLUMN_PROP:
        return cast("ColumnElement[object]", RAsset.name)
    if (audit_attr := _plan_audit_attr(column)) is not None:
        if column == "updatedBy":
            return cast(
                "ColumnElement[object]",
                sa_select(col(User.name))
                .where(col(User.id) == col(audit_attr))
                .correlate(RenovationPlanRow)
                .scalar_subquery(),
            )
        return cast("ColumnElement[object]", col(audit_attr))
    meta = field_meta(column)
    if meta is None or meta.db_name in _VIRTUAL_FIELDS:
        message = f"Неизвестный столбец «{column}»."
        raise CellValidationError(message)
    raw = cast("ColumnElement[object]", getattr(RenovationPlanRow, meta.db_name))
    if meta.cell_type == "reference":
        return cast(
            "ColumnElement[object]",
            sa_select(col(RenovationReferenceValue.label))
            .where(col(RenovationReferenceValue.id) == raw)
            .correlate(RenovationPlanRow)
            .scalar_subquery(),
        )
    return raw


def _plan_order_by(
    sorting: list[RenovationSortColumn] | None,
) -> list[ColumnElement[object]]:
    """Build stable SQL ordering for the remote plan window."""
    order: list[ColumnElement[object]] = []
    for item in sorting or []:
        expression = _plan_sort_expression(item.column)
        meta = field_meta(item.column)
        is_text = item.column in {_ASSET_COLUMN_PROP, "updatedBy"} or (
            meta is not None and meta.cell_type in {"text", "reference"}
        )
        key = func.lower(expression) if is_text else expression
        ordered = key.asc().nulls_last() if item.order == "asc" else key.desc().nulls_last()
        order.append(cast("ColumnElement[object]", ordered))
        # Case-insensitive keys can tie; raw text gives a stable, repeatable
        # order before the final plan position tie-break below.
        if is_text:
            raw = _plan_sort_expression(item.column)
            order.append(
                cast(
                    "ColumnElement[object]",
                    raw.asc().nulls_last() if item.order == "asc" else raw.desc().nulls_last(),
                )
            )
    order.append(cast("ColumnElement[object]", col(RenovationPlanRow.position).asc()))
    order.append(cast("ColumnElement[object]", col(RenovationPlanRow.id).asc()))
    return order


async def _window_anchor_position(
    session: AsyncSession,
    *,
    row_condition: ColumnElement[bool],
    offset: int,
    positions_are_dense: bool,
) -> int | None:
    """
    Return the `position` of the row just before a window's first row.

    The window is addressed by its index in the FILTERED scope, so the anchor is
    "the row at index offset-1". Only a scope verified to contain every integer
    position from 1 through its row count can use offset as its position.

    `None` means the offset is past the last matching row.
    """
    if positions_are_dense:
        return offset
    anchor = (
        await session.exec(
            select(col(RenovationPlanRow.position))
            .where(row_condition)
            .order_by(col(RenovationPlanRow.position))
            .offset(offset - 1)
            .limit(1)
        )
    ).first()
    return int(anchor) if anchor is not None else None


async def _count_plan_scope(
    session: AsyncSession,
    plan_id: UUID,
    row_condition: ColumnElement[bool],
    anchor_position: int | None = None,
) -> tuple[int, int, bool, int | None]:
    """Count only this plan and verify dense positions in the same aggregate."""
    position = col(RenovationPlanRow.position)
    anchor_count = (
        func.count().filter(row_condition & (position < anchor_position))
        if anchor_position is not None
        else literal(None)
    )
    total, total_all, minimum, maximum, anchor_offset = (
        await session.exec(  # ty: ignore[no-matching-overload]
            sa_select(
                func.count().filter(row_condition),
                func.count(),
                func.min(position),
                func.max(position),
                anchor_count,
            )
            .select_from(RenovationPlanRow)
            .where(col(RenovationPlanRow.plan_id) == plan_id)
        )
    ).one()
    # (plan_id, position) is unique. These bounds prove there are no gaps.
    dense = total == total_all and minimum == 1 and maximum == total_all
    return int(total), int(total_all), dense, None if anchor_offset is None else int(anchor_offset)


def _empty_plan_window(
    plan: RenovationPlan,
    *,
    baseline_boundary: int | None,
    total: int,
    total_all: int,
    anchor_offset: int | None,
) -> RenovationPlanRowsRead:
    """Return the plan header with no rows — an offset past the last matching row."""
    return RenovationPlanRowsRead(
        id=plan.id,
        state=plan.state.value,
        baselineGeneration=plan.baseline_generation,
        updatedAt=plan.updated_at,
        updatedBy=plan.updated_by,
        rows=[],
        total=total,
        baselineBoundary=baseline_boundary,
        totalAll=total_all,
        anchorOffset=anchor_offset,
    )


async def read_plan_rows(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    offset: int = 0,
    limit: int = 1000,
    asset_name: str | None = None,
    filters: list[RenovationColumnFilter] | None = None,
    sorting: list[RenovationSortColumn] | None = None,
    position_from: int | None = None,
    position_to: int | None = None,
    anchor_position: int | None = None,
    time_zone: str | None = None,
) -> RenovationPlanRowsRead:
    """
    Return a window of plan rows plus the total row count (lazy loading).

    `anchor_position` additionally resolves a stored row position onto the
    current filtered scope: the response's `anchorOffset` is the number of
    matching rows BEFORE that position, so the client can keep the same row
    centred after a reload or a filter change (0 when every row is after it,
    `total` when the anchor is past the last row).
    """
    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
    else:
        plan = await _plan_by_state(session, state)
        if plan is None:
            error = RenovationPlanNotFoundError(f"Нет плана в состоянии «{state.value}».")
            raise error
    label_map = await _reference_labels(session)

    # Show the caller's asset rows plus the not-yet-assigned empty drafts; the
    # header and row-number filters combine with AND, so offset/limit count only
    # the matching rows.
    row_condition = _plan_row_condition(
        plan,
        assets,
        time_zone=time_zone,
        asset_name=asset_name,
        filters=filters,
        label_map=label_map,
        position_from=position_from,
        position_to=position_to,
    )
    # The filtered total (the rows matching every filter) and the whole-plan
    # total (the «всего» counter, which never shrinks when a filter hides rows)
    # come from a single round-trip via conditional aggregation. The plan total
    # is ALWAYS returned — not only when the asset filter is active — so the
    # footer's «всего» stays the plan's row count under any filter.
    #
    # Deliberately NOT cached: a token that would keep it correct across every
    # write path (bulk import, undo replay, direct SQL) costs more than the
    # count itself at 306k rows — see `read_cache`.
    total, total_all, dense, anchor_offset = await _count_plan_scope(session, plan.id, row_condition, anchor_position)
    # Keyset pagination, not OFFSET. `OFFSET n` walks n index entries — 2 seconds
    # at n=150k on the 306k-row plan — while a seek is one index hop whatever n
    # is. The client addresses rows by their FILTERED index, so the first row of
    # a window is resolved in a scope where the seek is cheap:
    #   * proven dense scope — the grid index IS the «№» (`position`), so the window
    #     starts at `position > anchor_position_of_offset`;
    #   * otherwise resolve the anchor using an index-only OFFSET query.
    window_condition = row_condition
    if offset > 0 and not sorting:
        anchor = await _window_anchor_position(
            session, row_condition=row_condition, offset=offset, positions_are_dense=dense
        )
        if anchor is None:
            baseline = await _baseline_boundary(session) if plan.state == RPlanState.WORKING else None
            return _empty_plan_window(
                plan,
                baseline_boundary=baseline,
                total=total,
                total_all=total_all,
                anchor_offset=anchor_offset,
            )
        window_condition = row_condition & (col(RenovationPlanRow.position) > anchor)
    rows_query = (
        select(RenovationPlanRow, RAsset)
        .outerjoin(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
        .where(window_condition)
        .order_by(*_plan_order_by(sorting))
    )
    if sorting:
        # A sorted scope is ordered by arbitrary expressions, so the stored
        # position cannot serve as a seek key. The grid's offset is bounded by
        # its virtualized window; retain the fast keyset path for the default
        # plan order above.
        rows_query = rows_query.offset(offset)
    rows = (await session.exec(rows_query.limit(limit))).all()
    baseline_boundary = await _baseline_boundary(session) if plan.state == RPlanState.WORKING else None
    return RenovationPlanRowsRead(
        id=plan.id,
        state=plan.state.value,
        baselineGeneration=plan.baseline_generation,
        updatedAt=plan.updated_at,
        updatedBy=plan.updated_by,
        rows=[_plan_row_read(row, asset, label_map) for row, asset in rows],
        total=total,
        baselineBoundary=baseline_boundary,
        totalAll=total_all,
        anchorOffset=anchor_offset,
    )


# Numeric fields that are NOT summed into the pinned summary row: an aggregate
# is meaningless for them (a defect-statement number, a duration, physical
# volumes, and the whole «Статус готовности МТР к проведению ремонта» block).
_SUMMARY_EXCLUDED_FIELDS = frozenset(
    {
        "defect_list_or_bill_of_quantities_number",  # «Дефектная ведомость/ВОР №»
        "work_duration_days",  # «Продолжительность, дни»
        "adjustment_work_volume_unit",  # «Физические объемы работ» — «Ед. изм.»
        "adjustment_work_volume_quantity",  # «Физические объемы работ» — «Количество»
        "em_contracting_cs_pct",  # «Статус готовности МТР…» — «Централизованная поставка»
        "equipment_materials_contracting_direct_supply_readiness_percent",  # «Статус готовности МТР…» — «Прямая поставка»
        "em_delivery_cs_pct",  # «Статус готовности МТР…» — «Централизованная поставка»
        "equipment_materials_delivery_direct_supply_readiness_percent",  # «Статус готовности МТР…» — «Прямая поставка»
        "contractor_equipment_materials_supply_readiness_percent",  # «Статус готовности МТР…» — «Поставка оборудования и МТР Подрядчика»
    }
)


async def read_plan_summary(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    asset_name: str | None = None,
    filters: list[RenovationColumnFilter] | None = None,
    position_from: int | None = None,
    position_to: int | None = None,
    time_zone: str | None = None,
) -> dict[str, float]:
    """
    Sum every additive numeric field over the filtered plan (the pinned summary row).

    Same scope as `read_plan_rows` (visible assets plus the header and
    row-number filters), so the totals always match what the grid shows. Only
    numeric fields are returned; an empty scope yields zeros. Fields whose sum
    is meaningless (`_SUMMARY_EXCLUDED_FIELDS`) are omitted — their summary
    cells stay empty.
    """
    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
    else:
        plan = await _plan_by_state(session, state)
        if plan is None:
            error = RenovationPlanNotFoundError(f"Нет плана в состоянии «{state.value}».")
            raise error
    label_map = await _reference_labels(session)
    row_condition = _plan_row_condition(
        plan,
        assets,
        asset_name=asset_name,
        filters=filters,
        label_map=label_map,
        position_from=position_from,
        position_to=position_to,
        time_zone=time_zone,
    )
    numeric = [
        meta.db_name
        for meta in field_metas()
        if meta.cell_type == "number" and meta.db_name not in _SUMMARY_EXCLUDED_FIELDS
    ]
    columns = [func.coalesce(func.sum(getattr(RenovationPlanRow, name)), 0) for name in numeric]
    sums = (await session.exec(select(*columns).select_from(RenovationPlanRow).where(row_condition))).one()
    return {name: float(value or 0) for name, value in zip(numeric, sums, strict=True)}


async def read_plan_facets(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    column: str,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    asset_name: str | None = None,
    offset: int = 0,
    limit: int = 200,
    search_kind: str | None = None,
    search_query: str | None = None,
    search_min: float | None = None,
    search_max: float | None = None,
    search_timezone: str | None = None,
    filters: list[RenovationColumnFilter] | None = None,
) -> RenovationFacetsRead:
    """
    Distinct display values of one plan column (the header-filter options).

    Mirrors `read_plan_rows` scope: the caller's assets, optionally narrowed to
    one asset. Values are the same strings the grid shows (references resolved
    to labels, dates ISO, numbers rendered by the client). NULL and empty string
    collapse into one «Пустые» bucket, reported via `hasNulls` and excluded from
    the sorted window; `total` counts the distinct non-empty values across the
    whole filtered plan after the optional search, so the window (offset/limit)
    can be lazy-loaded without losing matches.

    The SEARCH shape follows the field's own type (`_facet_field_kind`): text
    columns take a substring query, numeric ones the MIN–MAX window, dates the
    date window. A request that names another shape is refused rather than
    quietly rewritten, so a stale client cannot ask for a number range on a
    column that stopped holding numbers.
    """
    if column in _PLAN_AUDIT_COLUMNS:
        # An INSTANT column has no usable option list: every row carries its own
        # timestamp, so the popover asks for a MIN–MAX window instead and the
        # rows are matched server-side (see `_audit_filter_conditions`).
        message = f"Столбец «{column}» фильтруется по диапазону, а не по значениям."
        raise CellValidationError(message)
    is_asset_column = column == _ASSET_COLUMN_PROP
    is_audit_column = _plan_audit_attr(column) is not None
    meta: RenovationFieldMeta | None = None
    if not is_asset_column and not is_audit_column:
        resolved = field_meta(column)
        if resolved is None or resolved.db_name in _VIRTUAL_FIELDS:
            message = f"Неизвестный столбец «{column}»."
            raise CellValidationError(message)
        meta = resolved
    search_kind = _check_facet_search(_facet_field_kind(meta), search_kind)

    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
    else:
        plan = await _plan_by_state(session, state)
        if plan is None:
            error = RenovationPlanNotFoundError(f"Нет плана в состоянии «{state.value}».")
            raise error

    label_map = await _reference_labels(session)
    # Only preceding filters constrain the option list. The current and later
    # filters must not hide choices when the user edits an earlier step.
    preceding = []
    for item in filters or []:
        if item.column == column:
            break
        preceding.append(item)
    row_condition = _plan_row_condition(
        plan,
        assets,
        asset_name=asset_name,
        filters=preceding,
        label_map=label_map,
        position_from=None,
        position_to=None,
        time_zone=search_timezone,
    )

    # Group in SQL by the raw column; references/asset need the joined tables or
    # the label map. Booleans and numbers are stringified in Python afterwards.
    if column == _RENOVATION_ID_COLUMN:
        # Keep high-cardinality code grouping/search/paging inside PostgreSQL.
        # Presets are independent of the page and search; their rows overlap
        # code values, so they must not inflate the distinct/row counts.
        raw_code = col(RenovationPlanRow.work_or_repair_code)
        non_empty = raw_code.is_not(None) & (raw_code != "")
        correct = _renovation_id_is_correct_condition(plan.id)
        ok_count, bad_count, distinct_count, null_count = (
            await session.exec(  # ty: ignore[no-matching-overload]
                sa_select(
                    func.count().filter(correct),
                    func.count().filter(~correct),
                    func.count(raw_code.distinct()).filter(non_empty),
                    func.count().filter(~non_empty),
                )
                .select_from(RenovationPlanRow)
                .where(row_condition)
            )
        ).one()
        presets = [
            RenovationFacetValueRead(value=label, count=int(count))
            for label, count in (
                (_RENOVATION_ID_OK_LABEL, ok_count),
                (_RENOVATION_ID_BAD_LABEL, bad_count),
            )
        ]
        # PostgreSQL 14 has neither `casefold()` nor the `pg_unicode_fast`
        # collation, so the fold is a portable, locale-independent expression;
        # C ordering is deterministic across pages. Escape LIKE metacharacters
        # in typed codes.
        folded = func.translate(raw_code, _ID_FOLD_UPPER, _ID_FOLD_LOWER)
        folded = func.replace(func.replace(folded, "ẞ", "ss"), "ß", "ss")
        matches = row_condition & non_empty
        if search_kind == "text":
            matches &= folded.contains((search_query or "").casefold(), autoescape=True)
        elif search_kind is not None:
            matches &= sa_false()
        grouped = select(raw_code.label("code"), func.count().label("rows")).where(matches).group_by(raw_code)
        if search_kind is None:
            total, matching_rows = int(distinct_count), int(ok_count) + int(bad_count)
        else:
            counts = grouped.subquery()
            total, matching_rows = (
                await session.exec(select(func.count(), func.coalesce(func.sum(counts.c.rows), 0)))
            ).one()
            matching_rows = int(matching_rows) + int(null_count)
        entries = (
            await session.exec(grouped.order_by(folded.collate("C"), raw_code.collate("C")).offset(offset).limit(limit))
        ).all()
        return RenovationFacetsRead(
            column=column,
            values=[RenovationFacetValueRead(value=code, count=int(count)) for code, count in entries],
            presets=presets,
            total=int(total),
            totalWithoutSearch=int(distinct_count),
            rowCount=int(matching_rows),
            hasNulls=bool(null_count),
            nullCount=int(null_count),
        )
    # Phase 6.1: every generic facet is grouped, searched, sorted, counted and
    # paginated in PostgreSQL. The display bucket is computed once as a SQL
    # expression, so a high-cardinality column never materializes its distinct
    # values in Python. The code column above keeps its specialized path.
    display_expr, needs_reference_join = _facet_display_sql(column, meta, is_asset_column)
    base = sa_select(display_expr.label("display"), func.count().label("rows")).select_from(RenovationPlanRow)
    if is_asset_column:
        base = base.outerjoin(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
    elif needs_reference_join:
        base = base.outerjoin(
            RenovationReferenceValue, col(RenovationReferenceValue.id) == col(getattr(RenovationPlanRow, column))
        )
    base = base.where(row_condition).group_by(display_expr).subquery()

    folded = _facet_casefold_sql(base.c.display)
    search_condition = _facet_search_condition(
        folded, base.c.display, search_kind, search_query, search_min, search_max, search_timezone
    )

    def keep(display_column: Any) -> Any:  # noqa: ANN401
        condition = display_column.is_not(None)
        if search_condition is not None:
            # Rebuild the search against the outer alias when needed.
            if display_column is base.c.display:
                return and_(condition, search_condition)
            folded_outer = _facet_casefold_sql(display_column)
            outer_search = _facet_search_condition(
                folded_outer, display_column, search_kind, search_query, search_min, search_max, search_timezone
            )
            return and_(condition, outer_search) if outer_search is not None else condition
        return condition

    # ONE grouped scan. Window functions compute the three totals over ALL
    # buckets (including the empty one) before the outer WHERE picks the page.
    totals = sa_select(
        base.c.display.label("display"),
        base.c.rows.label("rows"),
        func.count().filter(keep(base.c.display)).over().label("total"),
        func.coalesce(func.sum(base.c.rows).filter(keep(base.c.display)).over(), 0).label("matching"),
        func.coalesce(func.sum(base.c.rows).filter(base.c.display.is_(None)).over(), 0).label("null_count"),
    ).subquery("facet_totals")
    if meta is not None and meta.cell_type == "number":
        sort_keys = (
            _facet_numeric_sql(_facet_casefold_sql(totals.c.display)),
            _facet_casefold_sql(totals.c.display).collate("C"),
        )
    else:
        sort_keys = (
            _facet_casefold_sql(totals.c.display).collate("C"),
            totals.c.display.collate("C"),
        )
    page_query = (
        sa_select(totals.c.display, totals.c.rows, totals.c.total, totals.c.matching, totals.c.null_count)
        .where(keep(totals.c.display))
        .order_by(*sort_keys)
        .offset(offset)
        .limit(limit)
    )
    window = (await session.exec(page_query)).all()
    if window:
        total, matching_rows, null_count = window[0][2], window[0][3], window[0][4]
    else:
        # An empty page still needs exact counts: one extra grouped scan only in
        # this rare case, never on a normal request.
        total, matching_rows, null_count = (
            await session.exec(  # ty: ignore[no-matching-overload]
                sa_select(
                    func.count().filter(keep(base.c.display)),
                    func.coalesce(func.sum(base.c.rows).filter(keep(base.c.display)), 0),
                    func.coalesce(func.sum(base.c.rows).filter(base.c.display.is_(None)), 0),
                )
            )
        ).one()
    return RenovationFacetsRead(
        column=column,
        values=[RenovationFacetValueRead(value=value, count=int(count)) for value, count, *_ in window],
        total=int(total),
        rowCount=int(matching_rows) + null_count,
        hasNulls=bool(null_count),
        nullCount=null_count,
    )


_FACET_NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?$"


def _facet_text_sql(value: Any) -> Any:  # noqa: ANN401
    """Empty strings belong to the same «Пустые» bucket as NULL."""
    text = sa_cast(value, Text)
    return case((text == "", None), else_=text)


def _facet_casefold_sql(value: ColumnElement[str]) -> ColumnElement[str]:
    """Casefold equivalent: `lower` covers Cyrillic/Latin, ß/ẞ expand to ss."""
    lowered = func.lower(value)
    return func.replace(func.replace(lowered, "ẞ", "ss"), "ß", "ss")


def _facet_numeric_sql(value: ColumnElement[str]) -> ColumnElement[Decimal | None]:
    """Parse a facet display string as a number, NULL for non-numeric text."""
    text = func.replace(func.replace(func.replace(value, "\u00a0", ""), "\u202f", ""), " ", "")
    text = func.replace(text, ",", ".")
    return case((text.op("~")(_FACET_NUMERIC_RE), sa_cast(text, Numeric)), else_=None)


def _facet_milliseconds_sql(value: ColumnElement[str], time_zone: str | None) -> ColumnElement[float | None]:
    """Epoch milliseconds of a facet display date/minute (UTC for minute values)."""
    minute = sa_cast(
        func.extract("epoch", func.to_timestamp(value, 'YYYY-MM-DD"T"HH24:MI').op("AT TIME ZONE")("UTC")), Numeric
    )
    day = sa_cast(
        func.extract("epoch", func.to_timestamp(value, "YYYY-MM-DD").op("AT TIME ZONE")(time_zone or "UTC")),
        Numeric,
    )
    return case((value.contains("T"), minute * 1000), else_=day * 1000)


def _facet_search_condition(
    folded: ColumnElement[str],
    display: ColumnElement[str],
    kind: str | None,
    query: str | None,
    minimum: float | None,
    maximum: float | None,
    time_zone: str | None,
) -> ColumnElement[bool] | None:
    """Apply the same search semantics as `_facet_matches_search` in SQL."""
    if kind is None:
        return None
    if kind == "text":
        return folded.contains((query or "").casefold(), autoescape=True)
    comparable = _facet_numeric_sql(folded) if kind == "range" else _facet_milliseconds_sql(display, time_zone)
    conditions = []
    if minimum is not None:
        conditions.append(comparable >= minimum)
    if maximum is not None:
        conditions.append(comparable <= maximum)
    return and_(comparable.is_not(None), *conditions) if conditions else comparable.is_not(None)


def _facet_display_sql(column: str, meta: RenovationFieldMeta | None, is_asset_column: bool) -> tuple[Any, bool]:
    """Build the display-bucket expression and whether a reference join is needed."""
    if is_asset_column:
        raw = col(RAsset.name)
    elif column == "updatedBy":
        raw = sa_inspect(RenovationPlanRow).attrs._updater_name  # noqa: SLF001
    else:
        raw = col(getattr(RenovationPlanRow, column))
    if meta is None:
        return _facet_text_sql(raw), False
    if column in _PLAN_AUDIT_COLUMNS:
        return func.to_char(raw.op("AT TIME ZONE")("UTC"), 'YYYY-MM-DD"T"HH24:MI'), False
    if meta.cell_type == "reference":
        return col(RenovationReferenceValue.label), True
    if meta.cell_type == "boolean":
        return case((raw.is_(None), None), (raw.is_(True), "Да"), else_="Нет"), False
    if meta.cell_type == "number":
        whole = sa_cast(sa_cast(raw, BigInteger), Text)
        fraction = func.rtrim(func.rtrim(sa_cast(raw, Text), "0"), ".")
        return case((raw.is_(None), None), (raw == func.trunc(raw), whole), else_=fraction), False
    if meta.cell_type == "date":
        return sa_cast(raw, Text), False
    return _facet_text_sql(raw), False


async def read_plan_versions(session: AsyncSession) -> list[RenovationPlanVersionRead]:
    """All renovation plan versions (newest first) with row counts."""
    project_id = await _renovation_project_id(session)
    if project_id is None:
        return []
    plans = (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.project_id) == project_id)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).all()
    plan_ids = [plan.id for plan in plans]
    counts = {
        count: row_count
        for count, row_count in (
            await session.exec(
                select(col(RenovationPlanRow.plan_id), func.count())
                .where(col(RenovationPlanRow.plan_id).in_(plan_ids))
                .group_by(col(RenovationPlanRow.plan_id))
            )
        ).all()
    }
    return [
        RenovationPlanVersionRead(
            id=plan.id,
            state=plan.state.value,
            baselineGeneration=plan.baseline_generation,
            rowCount=counts.get(plan.id, 0),
            createdAt=plan.created_at,
            createdBy=plan.created_by,
            updatedAt=plan.updated_at,
            updatedBy=plan.updated_by,
        )
        for plan in plans
    ]


async def read_plan_diff(session: AsyncSession, *, plan_id: UUID, assets: list[RAsset]) -> RenovationDiffRead:
    """Cell diff of an archived version vs the previous archived version."""
    plan = await session.get(RenovationPlan, plan_id)
    if plan is None:
        raise _ERR_PLAN_NOT_FOUND
    if plan.state != RPlanState.ARCHIVED:
        raise _ERR_DIFF_ARCHIVED_ONLY
    previous = await _previous_archived_plan(session, plan.created_at)
    if previous is None:
        return RenovationDiffRead(planId=plan.id, comparedToPlanId=None)
    label_map = await _reference_labels(session)
    target = await _rows_by_position(session, plan.id, assets)
    before = await _rows_by_position(session, previous.id, assets)
    diff_rows: list[RenovationDiffRowRead] = []
    for position, (row, asset) in target.items():
        prev = before.get(position)
        cells: list[RenovationDiffCellRead] = []
        if prev is None:
            for meta in field_metas():
                if meta.db_name in _VIRTUAL_FIELDS:
                    continue
                if meta.db_name == "work_or_repair_code":
                    value = _row_renovation_id(row, asset, label_map)[0]
                else:
                    value = _row_value_label(row, meta, label_map)
                if value is not None:
                    cells.append(RenovationDiffCellRead(field=meta.db_name, newValue=value))
        else:
            for meta in field_metas():
                if meta.db_name in _VIRTUAL_FIELDS:
                    continue
                new_value = _row_value_label(row, meta, label_map)
                old_value = _row_value_label(prev[0], meta, label_map)
                if new_value != old_value:
                    cells.append(RenovationDiffCellRead(field=meta.db_name, oldValue=old_value, newValue=new_value))
        diff_rows.append(
            RenovationDiffRowRead(
                rowId=row.id,
                position=position,
                assetName=asset.name,
                isNew=prev is None,
                cells=cells,
            )
        )
    deleted = [
        _plan_row_read(row, asset, label_map) for position, (row, asset) in before.items() if position not in target
    ]
    deleted.sort(key=lambda item: item.position)
    return RenovationDiffRead(
        planId=plan.id,
        comparedToPlanId=previous.id,
        rows=diff_rows,
        deletedRows=deleted,
    )
