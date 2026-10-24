"""
Renovation plan domain service: access, reading, cell edits, recalculation and lifecycle.

The «Ремонт» form is the legacy renovation plan: 216 fields (col_002..
col_217), formula-calculated columns and the composite renovation ID
(col_003, a text column). Reference fields are stored as
r_reference_values ids and transported over the API as display
labels (the same pattern as «deliveryBasisName» in the «Цены» form).
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from sqlalchemy import Column, String, Text, and_, bindparam, delete, func, insert, literal, or_
from sqlalchemy import false as sa_false
from sqlalchemy import select as sa_select
from sqlalchemy import update as sa_update
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.auth.service import (
    CurrentUserIdentity,
    get_user_by_identity,
)
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.locking import lock_project, lock_rows, project_write
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.persistence import JournalStore
from src.raw_materials.service import CellValidationError, check_edit_leases, coerce_number, coerce_text
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import (
    RenovationFieldMeta,
    RenovationHeaderCell,
    catalog_labels,
    editable_fields,
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
from src.renovation.read_cache import reference_labels as cached_reference_labels
from src.renovation.renovation_id import (
    DIRECTION_CODE_FIELD,
    MISSING_COMPONENT,
    RENOVATION_ID_FORMULA_ENABLED,
    RenovationIdError,
    RenovationIdInput,
    RenovationIdResult,
    calculate_renovation_id,
    normalize_direction_code,
)
from src.renovation.schemas import (
    CellUpdate,
    RenovationAssetRead,
    RenovationCalculatedCellRead,
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
    RenovationIdRead,
    RenovationMetaRead,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanRowsRead,
    RenovationPlanVersionRead,
)

from .assets import (
    _DICT_ASSET_NOT_FOUND,
    _apply_renovation_asset_deleted,
    _plan_id_by_state,
)
from .catalogs import (
    _DICT_OPTION_NOT_FOUND,
    _apply_renovation_option_deleted,
)
from .errors import RenovationDictionaryError, RenovationPlanNotFoundError
from .plan_queries import _plan_by_state, _renovation_project_id
from .users import _apply_renovation_user_cell, restore_renovation_user


class NoRenovationAccessError(Exception):
    """The user has no access to the «Ремонт» project."""


class RenovationPermissionError(ValueError):
    """The user's role/block doesn't allow the requested operation."""


class RenovationPlanStateError(ValueError):
    """The plan lifecycle doesn't allow the requested transition."""


# Typical error messages (module constants + factories — project style).
# col_002 (asset) has no row column: the asset comes from the row's
# asset_id (a virtual field; read-only).
_VIRTUAL_FIELDS = frozenset({"col_002"})

# Rows per streamed export batch (see `_row_batches` in read_plan_export_data).
EXPORT_BATCH_SIZE = 5000
# The synthetic «Предприятие» column prop the grid uses for the asset name
# (there is no matching row column — it is joined from r_assets).
_ASSET_COLUMN_PROP = "__assetName"

# The renovation ID column gets a dedicated two-bucket header filter instead of
# a value list (see `_renovation_id_is_correct_condition`).
_RENOVATION_ID_COLUMN = "col_003"
_RENOVATION_ID_OK_LABEL = "Корректные"
_RENOVATION_ID_BAD_LABEL = "Некорректные"

_CELL_MISSING_ROW_OR_FIELD = ValueError("Ячейка не содержит идентификатор строки или поля.")
_CELL_PLAN_ROW_NOT_FOUND = ValueError("Строка плана не найдена.")
_ERR_NO_WORKING_PLAN = RenovationPlanNotFoundError("Нет рабочего плана.")
_ERR_NO_PENDING_PLAN = RenovationPlanNotFoundError("Нет плана на согласовании.")
_ERR_PLAN_NOT_FOUND = RenovationPlanNotFoundError("План не найден.")
_ERR_UNKNOWN_TABLE = CellValidationError("Неизвестная таблица для ячеек плана.")
_ERR_SUBMIT_PERMISSION = RenovationPermissionError(
    "Отправить план на согласование может администратор или владелец блока."
)
_ERR_APPROVE_PERMISSION = RenovationPermissionError("Утвердить план может утверждающий.")
_ERR_EDIT_PENDING = RenovationPermissionError("Версию на согласовании редактировать может только администратор.")
_ERR_ADD_PERMISSION = RenovationPermissionError(
    "Добавлять строки может администратор или работник Технической дирекции по активу."
)
_ERR_ADD_ASSET = RenovationPermissionError("Нельзя добавить строку по этому активу.")
_ERR_DELETE_BASELINE_ROW = RenovationPlanStateError("Нельзя удалить строку утверждённой базовой линии.")
_ERR_DELETE_APPROVER = RenovationPermissionError("Утверждающий не удаляет строки плана.")
_ERR_ROW_HAS_ASSET = RenovationPlanStateError("У строки уже указан актив.")
_ERR_DELETE_NOT_EMPTY = RenovationPlanStateError("Строку с заполненными данными удалить нельзя — очистите её сначала.")
_ERR_DIFF_ARCHIVED_ONLY = RenovationPlanStateError("Сравнение доступно только для архивных версий.")


def _unknown_field(field: str) -> CellValidationError:
    return CellValidationError(f"Неизвестное поле «{field}» формы «Ремонт».")


def _read_only_field(field: str) -> CellValidationError:
    return CellValidationError(f"Поле «{field}» формы «Ремонт» доступно только для чтения.")


def _approver_read_only(field: str) -> RenovationPermissionError:
    return RenovationPermissionError(f"Утверждающий не редактирует план (поле «{field}»).")


def _block_access(field: str, block: RBlock) -> RenovationPermissionError:
    return RenovationPermissionError(f"Нет доступа к полю «{field}» блока «{block.value}».")


def _asset_access() -> RenovationPermissionError:
    return RenovationPermissionError("Нет доступа к строке плана этого актива.")


def _reference_value(catalog: str, label: str) -> CellValidationError:
    return CellValidationError(f"Значение «{label}» справочника «{catalog}» не найдено.")


def _bad_date(value: object) -> CellValidationError:
    return CellValidationError(f"Не дата: {value!r}")


def _bad_boolean(value: object) -> CellValidationError:
    return CellValidationError(f"Не булево значение: {value!r}")


def _rule_violation(label: str, detail: str) -> CellValidationError:
    return CellValidationError(f"Значение в ячейке «{label}» нарушает ограничение: {detail}.")


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
        repairCode=asset.repair_code,
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

    `filters` are the same header filters the grid sends to `GET /plan/rows`, so
    the «видимая таблица» export matches what the user sees; `include_unassigned`
    keeps the not-yet-assigned draft rows the grid also shows when no asset
    filter is active.
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
    label_map: dict[UUID, str] | None = None,
    include_unassigned: bool = False,
) -> Select:
    """Build the export row query (direct value columns, scope, optional exact set)."""
    query = (
        sa_select(
            col(RenovationPlanRow.id),
            col(RenovationPlanRow.position),
            col(RAsset.name),
            col(RAsset.repair_code),
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
        .order_by(col(RenovationPlanRow.position))
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
    include_unassigned: bool = False,
) -> RenovationPlanExportMeta:
    """
    Resolve the export scope without reading rows.

    Plan, stamp, row count, reference labels and the responsibility block. The
    count honours the same header filters as the row read, so the job's progress
    total matches what the export actually writes.
    """
    plan = await _resolve_export_plan(session, plan_id, state)
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
    query = _plan_export_query(
        plan_id,
        asset_ids,
        row_ids,
        filters=filters,
        include_unassigned=include_unassigned,
    )
    label_map = await _reference_labels(session)
    engine = formula_engine()
    calculated_fields = tuple(meta.db_name for meta in field_metas() if meta.is_calculated)

    def _prepare(values: dict[str, object], repair_code: str) -> dict[str, object]:
        if any(values.get(field) is None for field in calculated_fields):
            engine.materialize(values)
        if RENOVATION_ID_FORMULA_ENABLED and not values.get("col_003"):
            values["col_003"] = _sparse_row_renovation_id(values, repair_code, label_map)
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
        # An exact-set export is small: read it fully and keep the rowIds order.
        db_rows_with_ids = list((await session.exec(query)).all())  # ty: ignore[no-matching-overload]
        by_id = {entry[0]: _prepare_entry(entry) for entry in db_rows_with_ids}
        ordered = [by_id[row_id] for row_id in row_ids if row_id in by_id]
        for start in range(0, len(ordered), batch_size):
            yield ordered[start : start + batch_size]
        return
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
        include_unassigned=include_unassigned,
    )
    rows: list[tuple[int, str, str, dict[str, object]]] = []
    async for batch in iter_plan_export_rows(
        session,
        plan_id=meta.plan_id,
        asset_ids=asset_ids,
        row_ids=row_ids,
        filters=filters,
        include_unassigned=include_unassigned,
    ):
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
    work_category_id = values.get("col_017")
    work_type_id = values.get("col_018")
    defect = values.get("col_023")
    project_code = values.get("col_136")
    result = _safe_renovation_id(
        RenovationIdInput(
            existing_id=None,
            start_date=str(values.get("col_024") or "") or None,
            asset_repair_code=asset_repair_code,
            direction_code=str(values.get("col_189") or "") or None,
            bpof_section=str(values.get("col_016") or "") or None,
            work_category=label_map.get(UUID(str(work_category_id))) if work_category_id else None,
            project_code=str(project_code) if project_code else None,
            defect_number=_defect_number_text(defect),
            technology_position=str(values.get("col_011") or "") or None,
            work_type_label=label_map.get(UUID(str(work_type_id))) if work_type_id else None,
        )
    )
    return result.value


def _renovation_id_is_correct_condition(plan_id: UUID) -> ColumnElement[bool]:
    """
    Rows of a plan whose renovation ID (`col_003`) is correct.

    With the generator off (`RENOVATION_ID_FORMULA_ENABLED`), a code is judged
    by uniqueness alone: a correct code is non-empty and appears exactly once in
    the plan. «Некорректные» therefore means an empty code or a duplicate — the
    two things that still block submit. The «Идентификационный код» header
    filter offers exactly these two buckets (correct / incorrect) instead of a
    value list, because a value list of mostly-unique codes would be useless.
    """
    raw = col(RenovationPlanRow.col_003)
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

    A row is «incorrect» when its stored `col_003` is empty; a duplicate is a
    non-empty ID shared by more than one row — with the generator off
    (`RENOVATION_ID_FORMULA_ENABLED`) both are the user's own text and there is
    nothing else to check. Returns the exact counts plus the first
    `SUBMIT_ID_ISSUE_ROW_LIMIT` row positions («№») of each kind — `submit_plan`
    refuses to freeze the version while either count is non-zero.
    """
    raw = col(RenovationPlanRow.col_003)
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
    if column == _RENOVATION_ID_COLUMN:
        # The two-bucket ID filter (correct / incorrect), not a value list.
        if plan_id is None:
            message = "Фильтр идентификационных кодов требует план."
            raise ValueError(message)
        correct = _renovation_id_is_correct_condition(plan_id)
        conditions: list[ColumnElement[bool]] = []
        for value in column_filter.values:
            if value == _RENOVATION_ID_OK_LABEL:
                conditions.append(correct)
            elif value == _RENOVATION_ID_BAD_LABEL:
                conditions.append(~correct)
        return or_(*conditions) if conditions else sa_false()
    value_conditions: list[ColumnElement[bool]] = []
    if column == _ASSET_COLUMN_PROP:
        if column_filter.values:
            value_conditions.append(
                col(RenovationPlanRow.asset_id).in_(
                    sa_select(col(RAsset.id)).where(col(RAsset.name).in_(column_filter.values))
                )
            )
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


def _facet_number(value: str) -> Decimal | None:
    """Parse a facet display number back to a Decimal (None when not numeric)."""
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation, ValueError:
        return None


def _facet_date(value: str) -> date | None:
    """Parse a facet display date back to a date (None when not ISO)."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _plan_row_condition(
    plan: RenovationPlan,
    assets: list[RAsset],
    *,
    asset_name: str | None,
    filters: list[RenovationColumnFilter] | None,
    label_map: dict[UUID, str],
    position_from: int | None,
    position_to: int | None,
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
        condition = condition & _column_filter_condition(column_filter, label_map, plan.id)
    if position_from is not None:
        condition = condition & (col(RenovationPlanRow.position) >= position_from)
    if position_to is not None:
        condition = condition & (col(RenovationPlanRow.position) <= position_to)
    return condition


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
    "the row at index offset-1". Two cases:

    - the scope keeps every row of the plan and the «№» values are dense
      (`positions_are_dense`) — then the row at index offset-1 is the one whose
      `position == offset` (positions start at 1), so the anchor is `offset`
      with no query at all;
    - anything else — resolve it with ONE seek over the scope. With filters that
      scope is much smaller than the plan, so the OFFSET stays shallow; without
      dense «№» (deleted positions leave gaps) the seek is still one statement
      in the same read order.

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
) -> tuple[int, int]:
    """Return `(rows matching the filters, rows in the whole plan)` in one round trip."""
    total, total_all = (
        await session.exec(
            select(
                func.count().filter(row_condition),
                func.count().filter(col(RenovationPlanRow.plan_id) == plan_id),
            ).select_from(RenovationPlanRow)
        )
    ).one()
    return int(total), int(total_all)


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
    position_from: int | None = None,
    position_to: int | None = None,
    anchor_position: int | None = None,
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
    total, total_all = await _count_plan_scope(session, plan.id, row_condition)
    # Keyset pagination, not OFFSET. `OFFSET n` walks n index entries — 2 seconds
    # at n=150k on the 306k-row plan — while a seek is one index hop whatever n
    # is. The client addresses rows by their FILTERED index, so the first row of
    # a window is resolved in a scope where the seek is cheap:
    #   * no filters — the grid index IS the «№» (`position`), so the window
    #     starts at `position > anchor_position_of_offset`;
    #   * with filters the row set is (much) smaller, so the OFFSET that finds
    #     its first index stays shallow.
    window_condition = row_condition
    if offset > 0:
        # Dense «№» is safe to assume only when the scope is every row of the
        # plan: any filter (asset, position range, column) or a narrower asset
        # set can skip positions, and then index != position.
        dense = (
            asset_name is None and not filters and position_from is None and position_to is None and total == total_all
        )
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
                anchor_offset=None if anchor_position is None else total,
            )
        window_condition = row_condition & (col(RenovationPlanRow.position) > anchor)
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .outerjoin(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(window_condition)
            .order_by(col(RenovationPlanRow.position))
            .limit(limit)
        )
    ).all()
    baseline_boundary = await _baseline_boundary(session) if plan.state == RPlanState.WORKING else None
    anchor_offset: int | None = None
    if anchor_position is not None:
        anchor_offset = int(
            (
                await session.exec(  # ty: ignore[no-matching-overload]
                    sa_select(func.count())
                    .select_from(RenovationPlanRow)
                    .where(
                        row_condition,
                        col(RenovationPlanRow.position) < anchor_position,
                    )
                )
            ).one()[0]
        )
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
        "col_023",  # «Дефектная ведомость/ВОР №»
        "col_026",  # «Продолжительность, дни»
        "col_083",  # «Физические объемы работ» — «Ед. изм.»
        "col_084",  # «Физические объемы работ» — «Количество»
        "col_208",  # «Статус готовности МТР…» — «Централизованная поставка»
        "col_209",  # «Статус готовности МТР…» — «Прямая поставка»
        "col_210",  # «Статус готовности МТР…» — «Централизованная поставка»
        "col_211",  # «Статус готовности МТР…» — «Прямая поставка»
        "col_214",  # «Статус готовности МТР…» — «Поставка оборудования и МТР Подрядчика»
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
) -> RenovationFacetsRead:
    """
    Distinct display values of one plan column (the header-filter options).

    Mirrors `read_plan_rows` scope: the caller's assets, optionally narrowed to
    one asset. Values are the same strings the grid shows (references resolved
    to labels, dates ISO, numbers rendered by the client). NULL and empty string
    collapse into one «Пустые» bucket, reported via `hasNulls` and excluded from
    the sorted window; `total` counts the distinct non-empty values across the
    whole filtered plan, so the window (offset/limit) can be lazy-loaded.
    """
    is_asset_column = column == _ASSET_COLUMN_PROP
    meta: RenovationFieldMeta | None = None
    if not is_asset_column:
        resolved = field_meta(column)
        if resolved is None or resolved.db_name in _VIRTUAL_FIELDS:
            message = f"Неизвестный столбец «{column}»."
            raise CellValidationError(message)
        meta = resolved

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
    if asset_name is not None:
        asset_ids = [asset.id for asset in assets if asset.name == asset_name]

    label_map = await _reference_labels(session)
    row_condition = col(RenovationPlanRow.plan_id) == plan.id
    if asset_name is not None:
        row_condition = row_condition & col(RenovationPlanRow.asset_id).in_(asset_ids)
    else:
        row_condition = row_condition & (
            col(RenovationPlanRow.asset_id).in_(asset_ids)
            | (col(RenovationPlanRow.asset_id).is_(None) & literal(bool(asset_ids)))
        )

    # Group in SQL by the raw column; references/asset need the joined tables or
    # the label map. Booleans and numbers are stringified in Python afterwards.
    if column == _RENOVATION_ID_COLUMN:
        # The ID filter is two fixed buckets (correct / incorrect), not a value
        # list: every ID is unique, so a value list would be unusable.
        correct = _renovation_id_is_correct_condition(plan.id)
        ok_count, bad_count = (
            await session.exec(  # ty: ignore[no-matching-overload]
                sa_select(func.count().filter(correct), func.count().filter(~correct))
                .select_from(RenovationPlanRow)
                .where(row_condition)
            )
        ).one()
        return RenovationFacetsRead(
            column=column,
            values=[
                RenovationFacetValueRead(value=_RENOVATION_ID_OK_LABEL, count=int(ok_count)),
                RenovationFacetValueRead(value=_RENOVATION_ID_BAD_LABEL, count=int(bad_count)),
            ],
            total=2,
            rowCount=int(ok_count + bad_count),
            hasNulls=False,
            nullCount=0,
        )
    if is_asset_column:
        group_column = col(RAsset.name)
        base = (
            sa_select(group_column, func.count())
            .select_from(RenovationPlanRow)
            .outerjoin(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
        )
    else:
        group_column = getattr(RenovationPlanRow, column)
        base = sa_select(group_column, func.count()).select_from(RenovationPlanRow)
    grouped = (await session.exec(base.where(row_condition).group_by(group_column))).all()  # ty: ignore[no-matching-overload]

    counts: dict[str, int] = {}
    has_nulls = False
    null_count = 0
    for raw_value, count in grouped:
        display = _facet_display(raw_value, meta, label_map)
        if display is None:
            has_nulls = True
            null_count += int(count)
        else:
            counts[display] = counts.get(display, 0) + int(count)

    ordered = sorted(counts.items(), key=lambda item: (item[0].casefold(), item[0]))
    window = ordered[offset : offset + limit]
    return RenovationFacetsRead(
        column=column,
        values=[RenovationFacetValueRead(value=value, count=count) for value, count in window],
        total=len(ordered),
        rowCount=sum(counts.values()) + null_count,
        hasNulls=has_nulls,
        nullCount=null_count,
    )


def _facet_display(
    raw_value: object,
    meta: RenovationFieldMeta | None,
    label_map: dict[UUID, str],
) -> str | None:
    """Display string of a grouped facet value; None for the «Пустые» bucket."""
    if raw_value is None:
        return None
    if meta is None:
        # The synthetic asset column: the grouped value is already the name.
        text = str(raw_value)
        return text or None
    if meta.cell_type == "reference":
        label = label_map.get(cast("UUID", raw_value))
        return label or None
    if meta.cell_type == "boolean":
        return "Да" if raw_value else "Нет"
    if meta.cell_type == "number":
        parsed = float(raw_value) if isinstance(raw_value, Decimal) else raw_value
        return f"{parsed:g}"
    if meta.cell_type == "date":
        rendered = raw_value.isoformat() if isinstance(raw_value, date) else str(raw_value)
        return rendered or None
    text = str(raw_value)
    return text or None


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


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def add_plan_row(
    session: AsyncSession,
    *,
    user: User,
    roles: list[RProjectRole],
    block: RBlock | None,
    assets: list[RAsset],
    asset_id: UUID | None = None,
) -> tuple[RenovationPlanRowRead, AppliedCell]:
    """
    Append a new (empty) row to the current plan.

    The target is the working plan when it exists, otherwise the pending
    («на согласовании») one. Adding is an edit: a pending version can only be
    appended to by an admin; for the working plan only administrators and asset
    workers of the Техническая дирекция (tech) block — on their own assets — may
    add. Other block workers, block owners and approvers cannot. The row is
    inserted empty; its asset comes from `asset_id` (validated against the
    caller's assets) when given, else it is pre-filled for an asset worker with
    exactly one asset. Everyone else adds an asset-less draft (removed on submit
    if never assigned a row/asset).
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    is_admin = RProjectRole.ADMIN in roles
    if plan.state == RPlanState.PENDING_APPROVAL and not is_admin:
        raise _ERR_EDIT_PENDING
    may_add = is_admin or (RProjectRole.ASSET_WORKER in roles and block == RBlock.TECH)
    if not may_add:
        raise _ERR_ADD_PERMISSION
    asset: RAsset | None = None
    if asset_id is not None:
        asset = next((candidate for candidate in assets if candidate.id == asset_id), None)
        if asset is None:
            raise _ERR_ADD_ASSET
    elif RProjectRole.ASSET_WORKER in roles and len(assets) == 1:
        asset = assets[0]
    max_position = (
        await session.exec(
            select(func.max(col(RenovationPlanRow.position))).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).one()
    now = datetime.now(UTC)
    row = RenovationPlanRow(
        plan_id=plan.id,
        asset_id=asset.id if asset is not None else None,
        position=int(max_position) + 1 if max_position is not None else 1,
        baseline_generation=plan.baseline_generation,
        revision=0,
        created_at=now,
        updated_at=now,
        updater_user_id=user.id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    label_map = await _reference_labels(session)
    return _plan_row_read(row, asset, label_map), _plan_row_created_cell(row)


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def assign_plan_row_asset(
    session: AsyncSession,
    *,
    user: User,
    roles: list[RProjectRole],
    block: RBlock | None,
    assets: list[RAsset],
    row_id: UUID,
    asset_id: UUID,
) -> tuple[RenovationPlanRowRead, AppliedCell]:
    """
    Set the asset of an asset-less draft row.

    Only a row that currently has no asset can be pointed at an asset (a row is
    created once and its asset was fixed at creation for the legacy cases). The
    caller must be able to add/edit the row: an admin, or a representative of
    block Технической дирекции; the target asset must be within the caller's asset scope. A
    pending plan is only editable by an admin.
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    is_admin = RProjectRole.ADMIN in roles
    if plan.state == RPlanState.PENDING_APPROVAL and not is_admin:
        raise _ERR_EDIT_PENDING
    if RProjectRole.APPROVER in roles:
        raise _ERR_ADD_PERMISSION
    if not (is_admin or block == RBlock.TECH):
        raise _ERR_ADD_PERMISSION
    asset = next((candidate for candidate in assets if candidate.id == asset_id), None)
    if asset is None:
        raise _asset_access()
    row = await session.get(RenovationPlanRow, row_id)
    if row is None or row.plan_id != plan.id:
        raise _CELL_PLAN_ROW_NOT_FOUND
    if row.asset_id is not None:
        raise _ERR_ROW_HAS_ASSET
    row.asset_id = asset.id
    row.updated_at = datetime.now(UTC)
    row.updater_user_id = user.id
    session.add(row)
    await session.flush()
    await session.refresh(row)
    label_map = await _reference_labels(session)
    read = _plan_row_read(row, asset, label_map)
    return read, AppliedCell(
        target_table=RenovationPlanRow.__tablename__,
        row_id=row.id,
        field="asset_id",
        old_value=None,
        new_value=str(asset.id),
    )


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def delete_plan_row(
    session: AsyncSession,
    *,
    assets: list[RAsset],
    roles: list[RProjectRole],
    row_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a plan row.

    Delete is allowed for anyone who can reach the row (administrators, block
    owners, asset workers on their own assets) — but NOT the approver — and only
    while the row holds no data: just its asset plus the technical
    (created/updated) columns. A row with any filled editable field can only be
    cleared and then deleted. A version under review is only deletable by an
    admin; an asset-less draft is also only deletable by an admin.
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    if RProjectRole.APPROVER in roles:
        raise _ERR_DELETE_APPROVER
    is_admin = RProjectRole.ADMIN in roles
    if plan.state == RPlanState.PENDING_APPROVAL and not is_admin:
        raise _ERR_EDIT_PENDING
    row = await session.get(RenovationPlanRow, row_id)
    if row is None or row.plan_id != plan.id:
        raise _CELL_PLAN_ROW_NOT_FOUND
    if row.asset_id is None:
        if not is_admin:
            raise _asset_access()
    elif row.asset_id not in [asset.id for asset in assets]:
        raise _asset_access()
    boundary = await _baseline_boundary(session)
    if boundary is not None and row.position <= boundary:
        raise _ERR_DELETE_BASELINE_ROW
    # A row may be deleted only while it has no data beyond the mandatory asset.
    if any(getattr(row, field) not in (None, "") for field in editable_fields()):
        raise _ERR_DELETE_NOT_EMPTY
    snapshot = _plan_row_snapshot(row)
    await session.delete(row)
    await session.flush()
    return [
        AppliedCell(
            target_table=RenovationPlanRow.__tablename__,
            row_id=row_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


def _plan_row_snapshot(row: RenovationPlanRow) -> dict[str, object]:
    """Journal snapshot of a plan row: everything needed to recreate it exactly."""
    return {
        "planId": str(row.plan_id),
        "assetId": str(row.asset_id) if row.asset_id is not None else None,
        "position": row.position,
        "baselineGeneration": row.baseline_generation,
        "revision": row.revision,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "updatedBy": row.updated_by,
    }


def _plan_row_created_cell(row: RenovationPlanRow) -> AppliedCell:
    """Journal cell of a newly added plan row (undo removes it again)."""
    return AppliedCell(
        target_table=RenovationPlanRow.__tablename__,
        row_id=row.id,
        field="__created",
        old_value=None,
        new_value=_plan_row_snapshot(row),
    )


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
                if meta.db_name == "col_003":
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


async def _rows_by_position(
    session: AsyncSession,
    plan_id: UUID,
    assets: list[RAsset],
) -> dict[int, tuple[RenovationPlanRow, RAsset]]:
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(col(RenovationPlanRow.plan_id) == plan_id)
            .where(col(RenovationPlanRow.asset_id).in_([asset.id for asset in assets]))
            .order_by(col(RenovationPlanRow.position))
        )
    ).all()
    return {row.position: (row, asset) for row, asset in rows}


async def _latest_archived_plan(session: AsyncSession) -> RenovationPlan | None:
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.state) == RPlanState.ARCHIVED)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _previous_archived_plan(session: AsyncSession, before: datetime) -> RenovationPlan | None:
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.state) == RPlanState.ARCHIVED, col(RenovationPlan.created_at) < before)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _baseline_boundary(session: AsyncSession) -> int | None:
    """Return the max row position of the latest archived plan (the working baseline)."""
    plan = await _latest_archived_plan(session)
    if plan is None:
        return None
    max_position = (
        await session.exec(
            select(func.max(col(RenovationPlanRow.position))).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).one()
    return int(max_position) if max_position is not None else None


def _plan_row_read(
    row: RenovationPlanRow,
    asset: RAsset | None,
    label_map: dict[UUID, str],
) -> RenovationPlanRowRead:
    values: dict[str, str | float | bool | date | None] = {}
    for meta in field_metas():
        if meta.db_name in _VIRTUAL_FIELDS:
            # The synthetic «Предприятие» column is blank until an asset is chosen.
            values[meta.db_name] = asset.name if asset is not None else ""
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            # Omit empty cells. The plan is ~90% empty, so the null placeholders
            # dominate the payload: a 1000-row window measures ~4.2 MB with every
            # key present and ~0.9 MB without. The grid renders a missing key
            # exactly like an explicit null, so the response stays equivalent.
            continue
        if meta.cell_type == "reference":
            value = label_map.get(value)
        elif meta.cell_type == "number":
            value = float(value) if isinstance(value, Decimal) else value
        elif meta.cell_type == "boolean":
            value = bool(value)
        if value is None:
            # A reference id with no label resolves to nothing — still sparse.
            continue
        values[meta.db_name] = value
    renovation_id, missing = _row_renovation_id(row, asset, label_map)
    return RenovationPlanRowRead(
        id=row.id,
        assetId=asset.id if asset is not None else None,
        assetCode=asset.code if asset is not None else None,
        assetName=asset.name if asset is not None else None,
        assetRepairCode=asset.repair_code if asset is not None else None,
        position=row.position,
        revision=row.revision,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        updatedBy=row.updated_by,
        values=values,
        renovationId=renovation_id,
        missingComponents=list(missing),
        invalidFields=_row_rule_violations(row, label_map),
    )


def _row_rule_violations(
    row: RenovationPlanRow,
    label_map: dict[UUID, str],
) -> list[str]:
    """
    Fields whose stored value breaks a form rule (for the grid's red highlight).

    Rules from the source workbook (minimum/percentage/allowed_values/integer)
    are enforced on input by `_coerce_cell`, but a CALCULATED field is never
    edited directly: e.g. «Продолжительность, дни» = col_025 − col_024 can go
    negative when the dates are entered in the wrong order. Reporting the
    offending fields lets the grid tint those cells red, like the incomplete
    renovation ID.
    """
    invalid: list[str] = []
    for meta in field_metas():
        if meta.db_name in _VIRTUAL_FIELDS or not meta.rules:
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            continue
        if meta.cell_type == "reference":
            value = label_map.get(value)
            if value is None:
                continue
        if _value_breaks_rules(meta, value):
            invalid.append(meta.db_name)
    return invalid


def _value_breaks_rules(meta: RenovationFieldMeta, value: object) -> bool:
    try:
        _check_edit_rules(meta, value)
    except CellValidationError:
        return True
    return False


def _safe_renovation_id(input_: RenovationIdInput) -> RenovationIdResult:
    """
    Compute the renovation ID, tolerating invalid components.

    The source spreadsheets carry values that violate the legacy rules (a
    defect number that is not seven digits, a malformed direction code, …). A
    bad row must not break the whole plan read: the ID falls back to the
    `<NULL>` placeholder and is flagged via `missing_components`.
    """
    try:
        return calculate_renovation_id(input_)
    except RenovationIdError:
        return RenovationIdResult(value=MISSING_COMPONENT, missing_components=("invalid_components",))


def _row_renovation_id(
    row: RenovationPlanRow,
    asset: RAsset | None,
    label_map: dict[UUID, str],
) -> tuple[str | None, tuple[str, ...]]:
    """
    Return the stored renovation ID, previewing the generator when it is empty.

    With the generator OFF the preview would show a code the database does not
    hold — the cell must read as what will be saved, so an empty `col_003` stays
    empty. The generator stays for the day it is switched back on.
    """
    if row.col_003:
        return row.col_003, ()
    if asset is None or not RENOVATION_ID_FORMULA_ENABLED:
        return None, ()
    result = _safe_renovation_id(_renovation_id_input(row, asset, label_map))
    return result.value, result.missing_components


# The cells `calculate_renovation_id` reads. Editing any of them must rebuild
# col_003 from the row's inputs instead of keeping the stored value — but only
# while the generator is ON (`RENOVATION_ID_FORMULA_ENABLED`).
_RENOVATION_ID_INPUT_FIELDS = frozenset(
    {"col_011", "col_016", "col_017", "col_018", "col_023", "col_024", "col_136", "col_189"}
)


def _defect_number_text(value: object) -> str | None:
    """
    Return the 7-digit defect-statement number as text.

    The source stores it as a number, so a dropped leading zero is restored
    (`706179` → `0706179`); the legacy id expects exactly seven digits.
    """
    if value is None:
        return None
    try:
        text = f"{Decimal(str(value)):.0f}"
    except InvalidOperation, ValueError:
        return str(value)
    if text.isdigit() and len(text) < 7:
        return text.zfill(7)
    return text


def _renovation_id_input(
    row: RenovationPlanRow,
    asset: RAsset,
    label_map: dict[UUID, str],
    *,
    keep_existing: bool = True,
) -> RenovationIdInput:
    """
    Row inputs for the renovation ID (legacy: project_code/suffix always None).

    `keep_existing` preserves an already-assigned id (the import/backfill and
    unrelated edits); pass False when the edit changed one of the id inputs so
    the id is rebuilt from them.
    """
    return RenovationIdInput(
        existing_id=row.col_003 if keep_existing else None,
        start_date=row.col_024.isoformat() if isinstance(row.col_024, date) else None,
        asset_repair_code=asset.repair_code,
        direction_code=row.col_189,
        bpof_section=row.col_016,
        work_category=label_map.get(row.col_017) if row.col_017 is not None else None,
        project_code=row.col_136,
        defect_number=_defect_number_text(row.col_023),
        technology_position=row.col_011,
        work_type_label=label_map.get(row.col_018) if row.col_018 is not None else None,
    )


@project_write(RENOVATION_PROJECT_KEY)
async def update_plan_cells(
    session: AsyncSession,
    *,
    updates: list[CellUpdate],
    user: User,
    roles: list[RProjectRole],
    assets: list[RAsset],
    block: RBlock | None,
    session_id: str,
) -> tuple[list[AppliedCell], list[RenovationCalculatedCellRead], list[RenovationIdRead]]:
    """
    Apply plan cell edits, recalculate formula fields and the renovation ID, and write the edit journal (same transaction).

    The plan edited is the current working plan when one exists, otherwise the
    pending («на согласовании») one — the block owners/admins keep refining it
    until it is approved. The returned lists contain the recalculated cells and
    IDs of every touched row (published to the room; not journaled — derived).
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    asset_ids = [asset.id for asset in assets]
    assets_by_id = {asset.id: asset for asset in assets}
    is_admin = RProjectRole.ADMIN in roles
    is_approver = RProjectRole.APPROVER in roles
    # A version under review is frozen for everyone except the admins.
    if plan.state == RPlanState.PENDING_APPROVAL and not is_admin:
        raise _ERR_EDIT_PENDING
    label_map = await _reference_labels(session)
    await lock_rows(session, [f"undo/{user.id}/{session_id}/renovationPlan/"])
    await lock_rows(session, [f"{RENOVATION_PLAN_ROWS_TABLE}/{update.rowId}" for update in updates])
    await check_edit_leases(
        updates, user=user, session_id=session_id, table=RENOVATION_PLAN_ROWS_TABLE, project=RENOVATION_PROJECT_KEY
    )
    rows = {
        row.id: row
        for row in (
            await session.exec(
                select(RenovationPlanRow)
                .where(col(RenovationPlanRow.id).in_([update.rowId for update in updates]))
                .execution_options(populate_existing=True)
            )
        ).all()
    }

    applied: list[AppliedCell] = []
    touched: dict[UUID, RenovationPlanRow] = {}
    touched_fields: dict[UUID, set[str]] = {}
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row = rows.get(update.rowId)
        if row is None or row.plan_id != plan.id:
            raise _CELL_PLAN_ROW_NOT_FOUND
        if not is_admin and not is_approver and row.asset_id not in asset_ids:
            raise _asset_access()
        field = update.field
        meta = field_meta(field)
        if meta is None:
            raise _unknown_field(field)
        if meta.is_calculated or meta.is_read_only:
            raise _read_only_field(field)
        if not is_admin:
            if is_approver:
                raise _approver_read_only(field)
            if block is None or meta.responsibility_block != block.value:
                raise _block_access(field, block if block is not None else RBlock.TECH)
        old_value = _row_value_label(row, meta, label_map)
        new_value, db_value = await _coerce_cell(session, meta, update.value)
        if db_value == getattr(row, field):
            continue
        setattr(row, field, db_value)
        row.updated_at = datetime.now(UTC)
        row.updater_user_id = user.id
        session.add(row)
        applied.append(
            AppliedCell(
                target_table=RENOVATION_PLAN_ROWS_TABLE,
                row_id=row.id,
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
        )
        touched[row.id] = row
        touched_fields.setdefault(row.id, set()).add(field)

    calculated: list[RenovationCalculatedCellRead] = []
    renovation_ids: list[RenovationIdRead] = []
    for row in touched.values():
        row.revision += 1
        asset = assets_by_id.get(row.asset_id)
        if asset is None:
            continue
        # Recompute only the dependency closure of the edited fields (AUD-28).
        recalculated = _recalculate_row(session, row, touched_fields.get(row.id))
        for field, raw_value in recalculated.items():
            if isinstance(raw_value, Decimal):
                json_value: str | float | bool | None = float(raw_value)
            elif isinstance(raw_value, date):
                json_value = raw_value.isoformat()
            else:
                json_value = cast("str | float | bool | None", raw_value)
            calculated.append(
                RenovationCalculatedCellRead(
                    rowId=row.id,
                    field=field,
                    value=json_value,
                )
            )
        # Rebuild the id only when the edit touched one of its inputs (e.g.
        # clearing the defect number and filling the correction); otherwise the
        # stored id stands. With the generator OFF the value is never rebuilt:
        # col_003 is another editable cell and the user's text is kept verbatim.
        edited_fields = touched_fields.get(row.id) or set()
        if RENOVATION_ID_FORMULA_ENABLED:
            keep_existing = not (edited_fields & _RENOVATION_ID_INPUT_FIELDS)
            result = _safe_renovation_id(_renovation_id_input(row, asset, label_map, keep_existing=keep_existing))
        else:
            result = RenovationIdResult(value=row.col_003 or "", missing_components=())
        id_changed = row.col_003 != result.value
        if id_changed:
            row.col_003 = result.value
            session.add(row)
        renovation_ids.append(
            RenovationIdRead(
                rowId=row.id,
                value=result.value,
                missingComponents=list(result.missing_components),
                changed=id_changed,
            )
        )

    await record_cell_updates(
        JournalStore(session),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section="renovationPlan",
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await session.flush()
    return applied, calculated, renovation_ids


_CALCULATED_METAS: tuple[RenovationFieldMeta, ...] = tuple(meta for meta in field_metas() if meta.is_calculated)
# col_003 (the renovation ID) is derived by `_renovation_id_input`, not by the
# formula engine; `_materialize_row` leaves it to the caller.
_FORMULA_METAS: tuple[RenovationFieldMeta, ...] = tuple(meta for meta in _CALCULATED_METAS if meta.db_name != "col_003")
_CALCULATED_COLUMNS: tuple[str, ...] = tuple(meta.db_name for meta in _FORMULA_METAS)
# Every formula input (neither calculated nor virtual) — the full-recompute sweep.
_INPUT_FIELD_NAMES: tuple[str, ...] = tuple(
    meta.db_name for meta in field_metas() if not meta.is_calculated and meta.db_name not in _VIRTUAL_FIELDS
)


def _materialize_row(row: RenovationPlanRow, only: set[str] | None = None) -> tuple[dict[str, object], bool]:
    """
    Compute the formula fields of one row without touching the row object.

    `only` restricts the recompute to the dependency closure of those changed
    fields (AUD-28); `None` recomputes every formula field (the backfill). The
    values are keyed by field name, so a calculated dependency inside the
    closure is produced in the same pass and one outside is read from the row.
    Returns every calculated field (recomputed or stored) and whether any
    recomputed one differs from the stored value.
    """
    engine = formula_engine()
    if only is None:
        target_names: set[str] | None = None
        input_names = _INPUT_FIELD_NAMES
    else:
        target_names = engine.affected_fields(only)
        input_names = tuple(engine.inputs_of(target_names))
    values: dict[str, object] = {}
    for name in input_names:
        value = getattr(row, name)
        if value is not None:
            values[name] = value
    if target_names is None:
        engine.materialize(values)
    elif target_names:
        engine.materialize_fields(values, target_names)
    calculated: dict[str, object] = {}
    changed = False
    for meta in _FORMULA_METAS:
        if target_names is None or meta.db_name in target_names:
            new_value = values.get(meta.db_name)
            changed = changed or getattr(row, meta.db_name) != new_value
        else:
            # Not part of the closure: unchanged, so the stored value stands.
            new_value = getattr(row, meta.db_name)
        calculated[meta.db_name] = new_value
    return calculated, changed


def _recalculate_row(
    session: AsyncSession, row: RenovationPlanRow, changed_fields: set[str] | None = None
) -> dict[str, object]:
    """Materialize formula fields into the row (in place) and register the change."""
    calculated, changed = _materialize_row(row, changed_fields)
    if changed:
        for name, value in calculated.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
        session.add(row)
    return calculated


@dataclass(frozen=True, slots=True)
class RenovationRecomputeResult:
    """Outcome of `recompute_plan_rows` (scanned/changed row counts)."""

    plan_id: UUID
    scanned: int
    changed: int


async def recompute_plan_rows(
    session: AsyncSession,
    *,
    plan_id: UUID | None = None,
    batch_size: int = 1000,
) -> RenovationRecomputeResult:
    """
    Materialize the calculated fields and the renovation ID of every plan row.

    The importer skips calculated fields (the app is expected to recompute them)
    and `read_plan_rows` never materializes, so imported/adopted rows carry no
    derived values until they are edited. This recomputes and stores them; run it
    once after importing a plan, and whenever the formula definitions change.

    Rows stream by primary key (bounded memory) and are written with one batched
    `UPDATE` per batch (a per-row ORM flush costs ~2 ms/row on a 300k-row plan).
    The row `revision`/`updated_at` are left alone: this is a data fix, not an
    edit, and bumping the revision would invalidate in-flight undo/redo.
    """
    if plan_id is None:
        plan = await _plan_by_state(session, RPlanState.WORKING)
        if plan is None:
            raise _ERR_NO_WORKING_PLAN
        plan_id = plan.id
    label_map = await _reference_labels(session)
    assets_by_id = {asset.id: asset for asset in (await session.exec(select(RAsset))).all()}
    statement = (
        sa_update(RenovationPlanRow)
        .where(col(RenovationPlanRow.id) == bindparam("row_id"))
        .values({name: bindparam(name) for name in (*_CALCULATED_COLUMNS, "col_003")})
        .execution_options(synchronize_session=False)
    )
    connection = await session.connection()
    scanned = 0
    changed = 0
    last_id: UUID | None = None
    while True:
        query = select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan_id)
        if last_id is not None:
            query = query.where(col(RenovationPlanRow.id) > last_id)
        rows = list((await session.exec(query.order_by(col(RenovationPlanRow.id)).limit(batch_size))).all())
        if not rows:
            break
        parameters: list[dict[str, object]] = []
        for row in rows:
            calculated, row_changed = _materialize_row(row)
            asset = assets_by_id.get(row.asset_id)
            new_id = row.col_003
            if asset is not None and RENOVATION_ID_FORMULA_ENABLED:
                new_id = _safe_renovation_id(_renovation_id_input(row, asset, label_map)).value
            changed += row_changed or new_id != row.col_003
            scanned += 1
            parameters.append({"row_id": row.id, **calculated, "col_003": new_id})
        await connection.execute(statement, parameters)
        last_id = rows[-1].id
        for row in rows:
            session.expunge(row)
    return RenovationRecomputeResult(plan_id=plan_id, scanned=scanned, changed=changed)


def _row_value_label(
    row: RenovationPlanRow,
    meta: RenovationFieldMeta,
    label_map: dict[UUID, str],
) -> str | float | bool | None:
    """Display value of a field (reference → label, date → ISO string) for the journal/response."""
    value = getattr(row, meta.db_name)
    if value is None:
        return None
    if meta.cell_type == "reference":
        return label_map.get(value)
    if meta.cell_type == "number":
        return float(value) if isinstance(value, Decimal) else value
    if meta.cell_type == "date":
        # Dates must stay JSON-safe: the journal, undo stack and the collab
        # cell_changed payload are all plain-JSON (json.dumps has no date
        # default), and the frontend grid cells are ISO strings anyway.
        return value.isoformat()
    if meta.cell_type == "boolean":
        return bool(value)
    return value


async def _coerce_cell(
    session: AsyncSession,
    meta: RenovationFieldMeta,
    value: object,
) -> tuple[object, object]:
    """Coerce an API cell value to (journal value, DB value) by the field type."""
    if meta.cell_type == "number":
        parsed = coerce_number(value)
        _check_edit_rules(meta, parsed)
        return parsed, parsed
    if meta.cell_type == "date":
        parsed = _coerce_date(value)
        _check_date_rules(meta, parsed)
        return parsed.isoformat() if parsed is not None else None, parsed
    if meta.cell_type == "boolean":
        if value is None:
            return None, None
        if not isinstance(value, bool):
            raise _bad_boolean(value)
        return value, value
    if meta.cell_type == "reference":
        label = coerce_text(value)
        value_id = await _resolve_reference_id(session, meta.catalog, label)
        return label, value_id
    text = coerce_text(value)
    if meta.db_name == DIRECTION_CODE_FIELD:
        text = normalize_direction_code(text)
    return text, text


def _is_field_rule(rule: dict[str, object], kind: str) -> bool:
    return rule.get("kind") == kind


def _percentage_bounds(rule: dict[str, object]) -> tuple[Decimal, Decimal]:
    minimum = Decimal(str(rule.get("minimum", 0)))
    maximum = Decimal(str(rule.get("maximum", 100)))
    return minimum, maximum


def _check_edit_rules(meta: RenovationFieldMeta, value: object) -> None:
    """
    Validate a value against the form's field-local rules before it is stored.

    Only rules checkable from the cell value itself are enforced here (values
    are non-null: `None` is allowed by `allowNull` and clears the cell):
    `minimum`, `integer`, `percentage` and `allowed_values`. The cross-field
    rules (`field_compare` — col_025 ≥ col_024; `calendar_year` — col_024) are
    validated in the grid, which has both the field metadata and the row.
    """
    if value is None:
        return
    for rule in meta.rules:
        kind = rule.get("kind")
        if kind == "minimum":
            minimum = rule.get("value")
            if minimum is not None and Decimal(str(value)) < Decimal(str(minimum)):
                raise _rule_violation(meta.label, "значение не может быть отрицательным")
        elif kind == "integer":
            if Decimal(str(value)) != Decimal(str(value)).to_integral_value():
                raise _rule_violation(meta.label, "значение должно быть целым числом")
        elif kind == "percentage":
            minimum, maximum = _percentage_bounds(rule)
            if not (minimum <= Decimal(str(value)) <= maximum):
                raise _rule_violation(meta.label, f"значение должно быть от {minimum} до {maximum}%")
        elif kind == "allowed_values":
            allowed = rule.get("values")
            if isinstance(allowed, list) and str(value) not in {str(item) for item in allowed}:
                raise _rule_violation(meta.label, "значение не входит в список допустимых")


def _check_date_rules(meta: RenovationFieldMeta, value: date | None) -> None:
    """Date rules needing the row are checked in the grid; nothing here yet."""
    del meta, value


def _coerce_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as error:
            raise _bad_date(value) from error
    raise _bad_date(value)


async def _resolve_reference_id(session: AsyncSession, catalog: str | None, label: str | None) -> UUID | None:
    """Resolve the reference value id by display label (''/None → no value)."""
    if label is None or catalog is None:
        return None
    value = (
        await session.exec(
            select(RenovationReferenceValue).where(
                col(RenovationReferenceValue.catalog) == catalog,
                col(RenovationReferenceValue.label) == label,
            )
        )
    ).first()
    if value is None:
        raise _reference_value(catalog, label)
    return value.id


async def _reference_labels(session: AsyncSession) -> dict[UUID, str]:
    """Labels of the reference catalogue (cached until a catalogue row changes)."""
    return cast("dict[UUID, str]", await cached_reference_labels(session))


async def apply_renovation_cell_value(
    session: AsyncSession,
    *,
    target_table: str,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """
    Apply a normalized cell value (undo/redo) with recalculation.

    The journal holds display labels for reference fields; here they are
    resolved back to value ids. Calculated fields and the renovation ID are
    recomputed after the edit (never journaled — they are derived).
    Dictionary tables (assets, catalog options, project users) are applied
    with audit updates, the same way the plan rows are.
    """
    now = datetime.now(UTC)
    if target_table == RenovationPlanRow.__tablename__ and field in ("__created", "__deleted", "asset_id"):
        await _apply_plan_row_change(session, row_id, field, value, user)
        return
    if target_table == RAsset.__tablename__:
        # Both journal pseudo-fields share the same apply: __deleted carries
        # {old: snapshot, new: None}, __created carries {old: None, new:
        # snapshot} — value None means "end state deleted", a snapshot means
        # "end state restored".
        if field in ("__deleted", "__created"):
            await _apply_renovation_asset_deleted(session, row_id, value, user.id)
            return
        asset = await session.get(RAsset, row_id)
        if asset is None:
            raise _DICT_ASSET_NOT_FOUND
        if field == "name":
            asset.name = value if isinstance(value, str) else ""
        elif field == "repairCode":
            asset.repair_code = value if isinstance(value, str) else ""
        else:
            message = f"Неизвестное поле «{field}» справочника «Активы»."
            raise CellValidationError(message)
        asset.updated_at = now
        asset.updater_user_id = user.id
        session.add(asset)
        return
    if target_table == RenovationReferenceValue.__tablename__:
        if field in ("__deleted", "__created"):
            await _apply_renovation_option_deleted(session, row_id, value)
            return
        option = await session.get(RenovationReferenceValue, row_id)
        if option is None:
            raise _DICT_OPTION_NOT_FOUND
        if field != "label":
            message = f"Неизвестное поле «{field}» справочника каталога."
            raise CellValidationError(message)
        option.label = value if isinstance(value, str) else ""
        option.updated_at = now
        option.updater_user_id = user.id
        session.add(option)
        return
    if target_table == User.__tablename__:
        if field == "__deleted":
            if value is None:
                # Redo: the user is gone from this project already.
                return
            if isinstance(value, dict):
                await restore_renovation_user(session, row_id=row_id, snapshot=value, updater_user_id=user.id)
            return
        await _apply_renovation_user_cell(session, row_id, field, value, user)
        return
    if target_table != RENOVATION_PLAN_ROWS_TABLE:
        raise _ERR_UNKNOWN_TABLE
    row = await session.get(RenovationPlanRow, row_id)
    if row is None:
        raise _CELL_PLAN_ROW_NOT_FOUND
    meta = field_meta(field)
    if meta is None:
        raise _unknown_field(field)
    if meta.is_calculated or meta.is_read_only:
        raise _read_only_field(field)
    label_map = await _reference_labels(session)
    _, db_value = await _coerce_cell(session, meta, value)
    setattr(row, field, db_value)
    row.updated_at = now
    row.updater_user_id = user.id
    asset = await session.get(RAsset, row.asset_id)
    if asset is not None:
        _recalculate_row(session, row)
        if RENOVATION_ID_FORMULA_ENABLED:
            result = _safe_renovation_id(_renovation_id_input(row, asset, label_map))
            row.col_003 = result.value
    session.add(row)


async def _apply_plan_row_change(
    session: AsyncSession,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """
    Undo/redo of a plan row lifecycle change: add, delete, asset assignment.

    «__created» carries {old: None, new: snapshot} — undo (None) deletes the
    added row, redo (snapshot) recreates it. «__deleted» is the mirror image.
    «asset_id» is the asset assignment of a draft row (the only other way a
    row's asset changes). The plan-state guard has already refused an archived
    or pending plan for a non-admin (`_authorize_action`), so this only has to
    recreate/remove the row.
    """
    if field in ("__created", "__deleted"):
        if value is None:
            row = await session.get(RenovationPlanRow, row_id)
            if row is None:
                return
            await session.delete(row)
            await session.flush()
            return
        if not isinstance(value, dict):
            message = "Некорректные данные для восстановления строки плана."
            raise CellValidationError(message)
        if await session.get(RenovationPlanRow, row_id) is not None:
            return
        plan_id = UUID(str(value.get("planId")))
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None or plan.state == RPlanState.ARCHIVED:
            message = "Строку нельзя вернуть: план недоступен или заархивирован."
            raise CellValidationError(message)
        asset_id = value.get("assetId")
        row = RenovationPlanRow(
            id=row_id,
            plan_id=plan_id,
            asset_id=UUID(str(asset_id)) if asset_id else None,
            position=int(value.get("position") or 1),
            baseline_generation=int(value.get("baselineGeneration") or 0),
            revision=int(value.get("revision") or 0),
            created_at=_row_datetime(value.get("createdAt")),
            updated_at=_row_datetime(value.get("updatedAt")),
            updater_user_id=user.id,
        )
        session.add(row)
        await session.flush()
        return
    if field == "asset_id":
        row = await session.get(RenovationPlanRow, row_id)
        if row is None:
            raise _CELL_PLAN_ROW_NOT_FOUND
        row.asset_id = UUID(str(value)) if value else None
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.flush()
        return
    message = f"Неизвестное изменение строки плана «{field}»."
    raise CellValidationError(message)


def _row_datetime(value: object) -> datetime:
    """Parse a journaled ISO timestamp; the current time when it is unusable."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


async def submit_plan(session: AsyncSession, *, user: User, roles: list[RProjectRole]) -> RenovationPlan:
    """Send the working plan for approval (admin or a block owner)."""
    await lock_project(session, RENOVATION_PROJECT_KEY, exclusive=True)
    if RProjectRole.ADMIN not in roles and RProjectRole.BLOCK_OWNER not in roles:
        raise _ERR_SUBMIT_PERMISSION
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    now = datetime.now(UTC)
    # Empty drafts (rows added «in the moment») that still hold no data — even if
    # only their asset is picked — are dropped before the version is frozen.
    draft_cols = editable_fields()
    empty_columns = []
    for field in draft_cols:
        column = getattr(RenovationPlanRow, field)
        empty_columns.append((column.is_(None) | (column == "")) if isinstance(column.type, Text) else column.is_(None))
    await session.exec(delete(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id, and_(*empty_columns)))
    # The plan may be sent for approval only when every work ID is complete and
    # unique; otherwise the version would freeze with unusable identifiers.
    incorrect, duplicates, incorrect_rows, duplicate_rows = await _plan_id_issues(session, plan.id)
    if incorrect or duplicates:
        raise _submit_id_validation_error(incorrect, duplicates, incorrect_rows, duplicate_rows)
    plan.state = RPlanState.PENDING_APPROVAL
    plan.updated_at = now
    plan.updater_user_id = user.id
    session.add(plan)
    await session.flush()
    await session.refresh(plan)
    return plan


async def approve_plan(
    session: AsyncSession,
    *,
    user: User,
    roles: list[RProjectRole],
) -> tuple[RenovationPlan, RenovationPlan]:
    """
    Approve the pending plan: archive it and create a new working copy.

    The new working plan is a full copy of the approved rows with
    baseline_generation+1 (the approved version is frozen, the next cycle
    starts from the approved state).
    """
    if RProjectRole.ADMIN not in roles and RProjectRole.APPROVER not in roles:
        raise _ERR_APPROVE_PERMISSION
    await lock_project(session, RENOVATION_PROJECT_KEY, exclusive=True)
    plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_PENDING_PLAN
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    plan.state = RPlanState.ARCHIVED
    plan.updated_at = now
    plan.updater_user_id = user.id
    session.add(plan)

    working = RenovationPlan(
        project_id=plan.project_id,
        state=RPlanState.WORKING,
        baseline_generation=plan.baseline_generation + 1,
        created_at=now,
        created_by=updated_by,
        updated_at=now,
        updater_user_id=user.id,
    )
    session.add(working)
    await session.flush()
    # Copy inside PostgreSQL: no 94k-row ORM graph or millions of Python assignments.
    table = RenovationPlanRow.metadata.tables["r_plan_rows"]
    overrides = {
        "id": func.gen_random_uuid(),
        "plan_id": literal(working.id),
        "baseline_generation": literal(working.baseline_generation),
        "revision": literal(0),
        "created_at": literal(now),
        "updated_at": literal(now),
        "updater_user_id": literal(user.id),
    }
    columns = list(table.columns)
    await session.exec(
        insert(table).from_select(
            [column.name for column in columns],
            sa_select(*(overrides.get(column.name, column) for column in columns)).where(table.c.plan_id == plan.id),
            include_defaults=False,
        )
    )
    await JournalStore(session).clear_undo_state_for_scope("renovationPlan/")
    await session.flush()
    await session.refresh(plan)
    await session.refresh(working)
    return plan, working


# ─── Editable reference dictionaries ─────────────────────────────────────


# Dictionary error messages (module constants — project style).
_DICT_ASSETS_NOT_FOUND = RenovationDictionaryError("Один или несколько активов не найдены.")
