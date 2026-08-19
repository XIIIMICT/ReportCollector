"""
Renovation plan domain service: access, reading, cell edits, recalculation and lifecycle.

The «Ремонт» form is the legacy renovation plan: 216 fields (col_002..
col_217), formula-calculated columns and the composite renovation ID
(col_003, a text column). Reference fields are stored as
renovation_reference_values ids and transported over the API as display
labels (the same pattern as «deliveryBasisName» in the «Цены» form).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import Project, User
from src.auth.service import CurrentUserIdentity, get_user_by_identity
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.store import get_collab_store
from src.raw_materials.service import CellValidationError, coerce_number, coerce_text
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
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.reference_data import REFERENCE_OPTIONS
from src.renovation.renovation_id import RenovationIdInput, calculate_renovation_id
from src.renovation.schemas import (
    CellUpdate,
    RenovationAssetRead,
    RenovationCalculatedCellRead,
    RenovationCatalogOptionRead,
    RenovationCatalogRead,
    RenovationDeletedRowRead,
    RenovationDiffCellRead,
    RenovationDiffRead,
    RenovationDiffRowRead,
    RenovationFieldRead,
    RenovationHeaderCellRead,
    RenovationHeaderRowRead,
    RenovationIdRead,
    RenovationMetaRead,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanVersionRead,
)


class NoRenovationAccessError(Exception):
    """The user has no access to the «Ремонт» project."""


class RenovationPermissionError(ValueError):
    """The user's role/block doesn't allow the requested operation."""


class RenovationPlanNotFoundError(ValueError):
    """The requested plan version doesn't exist."""


class RenovationPlanStateError(ValueError):
    """The plan lifecycle doesn't allow the requested transition."""


# Typical error messages (module constants + factories — project style).
# col_002 (asset) has no row column: the asset comes from the row's
# asset_id (a virtual field; read-only).
_VIRTUAL_FIELDS = frozenset({"col_002"})

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
_ERR_DELETE_BASELINE_ROW = RenovationPlanStateError("Нельзя удалить строку утверждённой базовой линии.")
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


async def resolve_renovation_access(
    session: AsyncSession,
    identity: CurrentUserIdentity,
) -> tuple[User, list[RProjectRole], list[RAsset], RBlock | None]:
    """
    Return (user, roles, available assets, responsibility block).

    Admin and approver see all assets; block_owner/asset_worker only the
    assets assigned in r_user_assets. The block comes from the user's
    r_user_roles link (empty for central roles). Raises
    NoRenovationAccessError if the user has no active role in the project.
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

    if RProjectRole.ADMIN in roles or RProjectRole.APPROVER in roles:
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


async def read_meta(session: AsyncSession, assets: list[RAsset]) -> RenovationMetaRead:
    """Form dictionaries: assets, reference catalogs, field descriptions."""
    catalogs: list[RenovationCatalogRead] = []
    by_catalog: dict[str, list[tuple[str, str]]] = {}
    for catalog, value_id, label, _ in REFERENCE_OPTIONS:
        by_catalog.setdefault(catalog, []).append((value_id, label))
    for catalog_id, options in by_catalog.items():
        catalogs.append(
            RenovationCatalogRead(
                id=catalog_id,
                label=catalog_labels().get(catalog_id, catalog_id),
                options=[RenovationCatalogOptionRead(valueId=value_id, label=label) for value_id, label in options],
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
            .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(col(RenovationPlanRow.plan_id) == plan.id)
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
        rows=[_plan_row_read(row, asset, label_map) for row, asset in rows if asset.id in asset_ids],
        baselineBoundary=baseline_boundary,
    )


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


async def add_plan_row(
    session: AsyncSession,
    *,
    user: User,
    assets: list[RAsset],
    asset_id: UUID,
) -> RenovationPlanRowRead:
    """Append a new row to the working plan (any project user; the asset is required)."""
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    asset = next((candidate for candidate in assets if candidate.id == asset_id), None)
    if asset is None:
        raise _asset_access()
    max_position = (
        await session.exec(
            select(func.max(col(RenovationPlanRow.position))).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).one()
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    row = RenovationPlanRow(
        plan_id=plan.id,
        asset_id=asset.id,
        position=int(max_position) + 1 if max_position is not None else 1,
        baseline_generation=plan.baseline_generation,
        revision=0,
        created_at=now,
        updated_at=now,
        updated_by=updated_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    label_map = await _reference_labels(session)
    return _plan_row_read(row, asset, label_map)


async def delete_plan_row(
    session: AsyncSession,
    *,
    assets: list[RAsset],
    row_id: UUID,
) -> None:
    """Delete a working-plan row (rows of the approved baseline are protected)."""
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    row = await session.get(RenovationPlanRow, row_id)
    if row is None or row.plan_id != plan.id:
        raise _CELL_PLAN_ROW_NOT_FOUND
    if row.asset_id not in [asset.id for asset in assets]:
        raise _asset_access()
    boundary = await _baseline_boundary(session)
    if boundary is not None and row.position <= boundary:
        raise _ERR_DELETE_BASELINE_ROW
    await session.delete(row)
    await session.commit()


async def read_plan_diff(session: AsyncSession, *, plan_id: UUID) -> RenovationDiffRead:
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
    target = await _rows_by_position(session, plan.id)
    before = await _rows_by_position(session, previous.id)
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
        RenovationDeletedRowRead(position=position, assetName=asset.name)
        for position, (_, asset) in before.items()
        if position not in target
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
) -> dict[int, tuple[RenovationPlanRow, RAsset]]:
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(col(RenovationPlanRow.plan_id) == plan_id)
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


def _plan_row_read(row: RenovationPlanRow, asset: RAsset, label_map: dict[UUID, str]) -> RenovationPlanRowRead:
    values: dict[str, str | float | bool | date | None] = {}
    for meta in field_metas():
        if meta.db_name in _VIRTUAL_FIELDS:
            values[meta.db_name] = asset.name
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            values[meta.db_name] = None
        elif meta.cell_type == "reference":
            values[meta.db_name] = label_map.get(value)
        elif meta.cell_type == "number":
            values[meta.db_name] = float(value) if isinstance(value, Decimal) else value
        elif meta.cell_type == "date":
            values[meta.db_name] = value
        elif meta.cell_type == "boolean":
            values[meta.db_name] = bool(value)
        else:
            values[meta.db_name] = value
    renovation_id, missing = _row_renovation_id(row, asset, label_map)
    return RenovationPlanRowRead(
        id=row.id,
        assetId=asset.id,
        assetCode=asset.code,
        assetName=asset.name,
        assetRepairCode=asset.repair_code,
        position=row.position,
        revision=row.revision,
        updatedAt=row.updated_at,
        updatedBy=row.updated_by,
        values=values,
        renovationId=renovation_id,
        missingComponents=list(missing),
    )


def _row_renovation_id(
    row: RenovationPlanRow,
    asset: RAsset,
    label_map: dict[UUID, str],
) -> tuple[str | None, tuple[str, ...]]:
    """Return the stored renovation ID; if empty — recompute it for UI highlighting."""
    if row.col_003:
        return row.col_003, ()
    result = calculate_renovation_id(_renovation_id_input(row, asset, label_map))
    return result.value, result.missing_components


def _renovation_id_input(row: RenovationPlanRow, asset: RAsset, label_map: dict[UUID, str]) -> RenovationIdInput:
    """Row inputs for the renovation ID (legacy: project_code/suffix always None)."""
    return RenovationIdInput(
        existing_id=row.col_003,
        start_date=row.col_024.isoformat() if isinstance(row.col_024, date) else None,
        asset_repair_code=asset.repair_code,
        direction_code=row.col_189,
        bpof_section=row.col_016,
        work_category=label_map.get(row.col_017) if row.col_017 is not None else None,
        defect_number=f"{row.col_023:.0f}" if row.col_023 is not None else None,
        technology_position=row.col_011,
        work_type_label=label_map.get(row.col_018) if row.col_018 is not None else None,
    )


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
    Apply working-plan cell edits, recalculate formula fields and the renovation ID, and write the edit journal (same transaction).

    The returned lists contain the recalculated cells and IDs of every
    touched row (published to the room; not journaled — they are derived).
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    asset_ids = [asset.id for asset in assets]
    assets_by_id = {asset.id: asset for asset in assets}
    is_admin = RProjectRole.ADMIN in roles
    is_approver = RProjectRole.APPROVER in roles
    label_map = await _reference_labels(session)
    rows: dict[UUID, RenovationPlanRow] = {}

    applied: list[AppliedCell] = []
    touched: list[RenovationPlanRow] = []
    for update in updates:
        if update.rowId is None or not update.field:
            raise _CELL_MISSING_ROW_OR_FIELD
        row = rows.get(update.rowId)
        if row is None:
            row = await session.get(RenovationPlanRow, update.rowId)
            if row is None or row.plan_id != plan.id:
                raise _CELL_PLAN_ROW_NOT_FOUND
            rows[update.rowId] = row
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
                raise _block_access(field, block if block is not None else RBlock.TASHPAEV)
        old_value = _row_value_label(row, meta, label_map)
        new_value, db_value = await _coerce_cell(session, meta, update.value)
        if db_value == getattr(row, field):
            continue
        setattr(row, field, db_value)
        row.updated_at = datetime.now(UTC)
        row.updated_by = user.name or user.email or ""
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
        if row not in touched:
            touched.append(row)

    calculated: list[RenovationCalculatedCellRead] = []
    renovation_ids: list[RenovationIdRead] = []
    for row in touched:
        asset = assets_by_id.get(row.asset_id)
        if asset is None:
            continue
        recalculated = _recalculate_row(session, row)
        for field, value in recalculated.items():
            calculated.append(
                RenovationCalculatedCellRead(
                    rowId=row.id,
                    field=field,
                    value=float(value)
                    if isinstance(value, Decimal)
                    else cast("str | float | bool | date | None", value),
                )
            )
        result = calculate_renovation_id(_renovation_id_input(row, asset, label_map))
        if row.col_003 != result.value:
            row.col_003 = result.value
            session.add(row)
        renovation_ids.append(
            RenovationIdRead(rowId=row.id, value=result.value, missingComponents=list(result.missing_components))
        )

    await record_cell_updates(
        get_collab_store(),
        user_id=user.id,
        user_name=user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section="renovationPlan",
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await session.commit()
    return applied, calculated, renovation_ids


def _recalculate_row(session: AsyncSession, row: RenovationPlanRow) -> dict[str, object]:
    """Materialize formula fields into the row (in place)."""
    values: dict[str, object] = {}
    for meta in field_metas():
        if meta.is_calculated or meta.db_name in _VIRTUAL_FIELDS:
            continue
        value = getattr(row, meta.db_name)
        if value is None:
            continue
        values[meta.db_name] = value
    formula_engine().materialize(values)
    calculated: dict[str, object] = {}
    for meta in field_metas():
        if not meta.is_calculated:
            continue
        new_value = values.get(meta.db_name)
        if getattr(row, meta.db_name) != new_value:
            setattr(row, meta.db_name, new_value)
            session.add(row)
        calculated[meta.db_name] = new_value
    return calculated


def _row_value_label(
    row: RenovationPlanRow,
    meta: RenovationFieldMeta,
    label_map: dict[UUID, str],
) -> str | float | bool | date | None:
    """Display value of a field (reference → label) for the journal/response."""
    value = getattr(row, meta.db_name)
    if value is None:
        return None
    if meta.cell_type == "reference":
        return label_map.get(value)
    if meta.cell_type == "number":
        return float(value) if isinstance(value, Decimal) else value
    if meta.cell_type == "date":
        return value
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
        return parsed, parsed
    if meta.cell_type == "date":
        parsed = _coerce_date(value)
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
    return text, text


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
    rows = (await session.exec(select(RenovationReferenceValue))).all()
    return {value.id: value.label for value in rows}


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
    """
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
    row.updated_at = datetime.now(UTC)
    row.updated_by = user.name or user.email or ""
    asset = await session.get(RAsset, row.asset_id)
    if asset is not None:
        _recalculate_row(session, row)
        result = calculate_renovation_id(_renovation_id_input(row, asset, label_map))
        row.col_003 = result.value
    session.add(row)


async def submit_plan(session: AsyncSession, *, user: User, roles: list[RProjectRole]) -> RenovationPlan:
    """Send the working plan for approval (admin or a block owner)."""
    if RProjectRole.ADMIN not in roles and RProjectRole.BLOCK_OWNER not in roles:
        raise _ERR_SUBMIT_PERMISSION
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    now = datetime.now(UTC)
    plan.state = RPlanState.PENDING_APPROVAL
    plan.updated_at = now
    plan.updated_by = user.name or user.email or ""
    session.add(plan)
    await session.commit()
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
    plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_PENDING_PLAN
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    plan.state = RPlanState.ARCHIVED
    plan.updated_at = now
    plan.updated_by = updated_by
    session.add(plan)

    working = RenovationPlan(
        project_id=plan.project_id,
        state=RPlanState.WORKING,
        baseline_generation=plan.baseline_generation + 1,
        created_at=now,
        created_by=updated_by,
        updated_at=now,
        updated_by=updated_by,
    )
    session.add(working)
    await session.flush()
    rows = (
        await session.exec(
            select(RenovationPlanRow)
            .where(col(RenovationPlanRow.plan_id) == plan.id)
            .order_by(col(RenovationPlanRow.position))
        )
    ).all()
    for source in rows:
        copy = RenovationPlanRow(
            plan_id=working.id,
            asset_id=source.asset_id,
            position=source.position,
            baseline_generation=working.baseline_generation,
            revision=0,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
        for meta in field_metas():
            if meta.db_name in _VIRTUAL_FIELDS:
                continue
            setattr(copy, meta.db_name, getattr(source, meta.db_name))
        session.add(copy)
    await session.commit()
    await session.refresh(plan)
    await session.refresh(working)
    return plan, working


async def _plan_by_state(session: AsyncSession, state: RPlanState) -> RenovationPlan | None:
    project_id = await _renovation_project_id(session)
    if project_id is None:
        return None
    return (
        await session.exec(
            select(RenovationPlan)
            .where(col(RenovationPlan.project_id) == project_id, col(RenovationPlan.state) == state)
            .order_by(col(RenovationPlan.created_at).desc())
        )
    ).first()


async def _plan_id_by_state(session: AsyncSession, state: RPlanState) -> UUID | None:
    plan = await _plan_by_state(session, state)
    return plan.id if plan is not None else None


async def _renovation_project_id(session: AsyncSession) -> UUID | None:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one_or_none()
    return project_id
