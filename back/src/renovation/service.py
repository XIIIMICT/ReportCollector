"""
Renovation plan domain service: access, reading, cell edits, recalculation and lifecycle.

The «Ремонт» form is the legacy renovation plan: 216 fields (col_002..
col_217), formula-calculated columns and the composite renovation ID
(col_003, a text column). Reference fields are stored as
renovation_reference_values ids and transported over the API as display
labels (the same pattern as «deliveryBasisName» in the «Цены» form).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, Text, func, literal_column
from sqlalchemy import cast as sql_cast
from sqlalchemy import select as sa_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.emails import is_valid_email, normalize_email
from src.auth.models import Project, User
from src.auth.service import CurrentUserIdentity, get_user_by_identity, sync_project_memberships
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.store import get_collab_store
from src.raw_materials.service import CellValidationError, coerce_number, coerce_text
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
    RReferenceCatalog,
)
from src.renovation.renovation_id import RenovationIdInput, calculate_renovation_id
from src.renovation.schemas import (
    CellUpdate,
    RenovationAssetRead,
    RenovationAssetRowRead,
    RenovationCalculatedCellRead,
    RenovationCatalogOptionRead,
    RenovationCatalogOptionRowRead,
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
    RenovationPlanRowsRead,
    RenovationPlanVersionRead,
    RenovationUserRowRead,
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
_ERR_DELETE_APPROVER = RenovationPermissionError("Утверждающий не удаляет строки плана.")
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
                col(RenovationReferenceValue.sort_order),
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


@dataclass(slots=True)
class RenovationPlanExportData:
    """Plan metadata and ORM rows prepared for the streaming XLSX writer."""

    baseline_generation: int
    updated_at: datetime
    rows: list[tuple[int, str, str, dict[str, object]]]
    reference_labels: dict[UUID, str]


async def read_plan_export_data(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    row_ids: list[UUID] | None = None,
) -> RenovationPlanExportData:
    """Read plan rows directly for XLSX export without a 94k-row Pydantic copy."""
    if plan_id is not None:
        plan = await session.get(RenovationPlan, plan_id)
        if plan is None:
            raise _ERR_PLAN_NOT_FOUND
    else:
        plan = await _plan_by_state(session, state)
        if plan is None:
            message = f"Нет плана в состоянии «{state.value}»."
            raise RenovationPlanNotFoundError(message)

    sparse_values = func.jsonb_strip_nulls(
        func.to_jsonb(literal_column("renovation_plan_rows"))
        - sql_cast(
            [
                "id",
                "plan_id",
                "asset_id",
                "position",
                "baseline_generation",
                "revision",
                "created_at",
                "updated_at",
                "updated_by",
            ],
            ARRAY(Text),
        )
    )
    query = (
        sa_select(
            col(RenovationPlanRow.id),
            col(RenovationPlanRow.position),
            col(RAsset.name),
            col(RAsset.repair_code),
            sparse_values,
        )
        .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
        .where(
            col(RenovationPlanRow.plan_id) == plan.id,
            col(RenovationPlanRow.asset_id).in_([asset.id for asset in assets]),
        )
        .order_by(col(RenovationPlanRow.position))
    )
    if row_ids is not None:
        query = query.where(col(RenovationPlanRow.id).in_(row_ids))
    db_rows_with_ids = list((await session.exec(query)).all())  # ty: ignore[no-matching-overload]
    if row_ids is not None:
        by_id = {
            row_id: (position, asset_name, repair_code, values)
            for row_id, position, asset_name, repair_code, values in db_rows_with_ids
        }
        db_rows = [by_id[row_id] for row_id in row_ids if row_id in by_id]
    else:
        db_rows = [
            (position, asset_name, repair_code, values)
            for _, position, asset_name, repair_code, values in db_rows_with_ids
        ]
    label_map = await _reference_labels(session)
    for _, _, repair_code, values in db_rows:
        if not values.get("col_003"):
            values["col_003"] = _sparse_row_renovation_id(values, repair_code, label_map)
    return RenovationPlanExportData(
        baseline_generation=plan.baseline_generation,
        updated_at=plan.updated_at,
        rows=db_rows,
        reference_labels=label_map,
    )


def _sparse_row_renovation_id(
    values: dict[str, object],
    asset_repair_code: str,
    label_map: dict[UUID, str],
) -> str | None:
    """Compute a missing renovation ID from a sparse export row."""
    work_category_id = values.get("col_017")
    work_type_id = values.get("col_018")
    defect = values.get("col_023")
    result = calculate_renovation_id(
        RenovationIdInput(
            existing_id=None,
            start_date=str(values.get("col_024") or "") or None,
            asset_repair_code=asset_repair_code,
            direction_code=str(values.get("col_189") or "") or None,
            bpof_section=str(values.get("col_016") or "") or None,
            work_category=label_map.get(UUID(str(work_category_id))) if work_category_id else None,
            defect_number=f"{Decimal(str(defect)):.0f}" if defect is not None else None,
            technology_position=str(values.get("col_011") or "") or None,
            work_type_label=label_map.get(UUID(str(work_type_id))) if work_type_id else None,
        )
    )
    return result.value


async def read_plan_rows(
    session: AsyncSession,
    assets: list[RAsset],
    *,
    plan_id: UUID | None = None,
    state: RPlanState = RPlanState.WORKING,
    offset: int = 0,
    limit: int = 1000,
    asset_name: str | None = None,
) -> RenovationPlanRowsRead:
    """Return a window of plan rows plus the total row count (lazy loading)."""
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
        # The client asset filter is by display name; narrow the permitted
        # asset set so SQL offset/limit count only the visible rows.
        asset_ids = [asset.id for asset in assets if asset.name == asset_name]
    label_map = await _reference_labels(session)

    # The asset filter must be applied in SQL so offset/limit count only the
    # rows the caller may see — not the full plan.
    total = (
        await session.exec(
            select(func.count())
            .select_from(RenovationPlanRow)
            .where(
                col(RenovationPlanRow.plan_id) == plan.id,
                col(RenovationPlanRow.asset_id).in_(asset_ids),
            )
        )
    ).one()
    # The unfiltered total (for the «всего» counter) — the whole plan, ignoring
    # the asset filter.
    total_all: int | None = None
    if len(asset_ids) != len(assets):
        total_all = (
            await session.exec(
                select(func.count()).select_from(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id)
            )
        ).one()
    rows = (
        await session.exec(
            select(RenovationPlanRow, RAsset)
            .join(RAsset, col(RAsset.id) == col(RenovationPlanRow.asset_id))
            .where(
                col(RenovationPlanRow.plan_id) == plan.id,
                col(RenovationPlanRow.asset_id).in_(asset_ids),
            )
            .order_by(col(RenovationPlanRow.position))
            .offset(offset)
            .limit(limit)
        )
    ).all()
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
    roles: list[RProjectRole],
    row_id: UUID,
) -> None:
    """
    Delete a plan row.

    Delete is allowed for anyone who can reach the row (administrators, block
    owners, asset workers on their own assets) — but NOT the approver — and only
    while the row holds no data: just the mandatory asset plus the technical
    (created/updated) columns. A row with any filled editable field can only be
    cleared and then deleted.
    """
    plan = await _plan_by_state(session, RPlanState.WORKING)
    if plan is None:
        plan = await _plan_by_state(session, RPlanState.PENDING_APPROVAL)
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    if RProjectRole.APPROVER in roles:
        raise _ERR_DELETE_APPROVER
    row = await session.get(RenovationPlanRow, row_id)
    if row is None or row.plan_id != plan.id:
        raise _CELL_PLAN_ROW_NOT_FOUND
    if row.asset_id not in [asset.id for asset in assets]:
        raise _asset_access()
    boundary = await _baseline_boundary(session)
    if boundary is not None and row.position <= boundary:
        raise _ERR_DELETE_BASELINE_ROW
    # A row may be deleted only while it has no data beyond the mandatory asset.
    if any(getattr(row, field) not in (None, "") for field in editable_fields()):
        raise _ERR_DELETE_NOT_EMPTY
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
    Dictionary tables (assets, catalog options, project users) are applied
    with audit updates, the same way the plan rows are.
    """
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    if target_table == RAsset.__tablename__:
        # Both journal pseudo-fields share the same apply: __deleted carries
        # {old: snapshot, new: None}, __created carries {old: None, new:
        # snapshot} — value None means "end state deleted", a snapshot means
        # "end state restored".
        if field in ("__deleted", "__created"):
            await _apply_renovation_asset_deleted(session, row_id, value)
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
        asset.updated_by = updated_by
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
        option.updated_by = updated_by
        session.add(option)
        return
    if target_table == User.__tablename__:
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
    row.updated_by = updated_by
    asset = await session.get(RAsset, row.asset_id)
    if asset is not None:
        _recalculate_row(session, row)
        result = calculate_renovation_id(_renovation_id_input(row, asset, label_map))
        row.col_003 = result.value
    session.add(row)


async def _apply_renovation_asset_deleted(
    session: AsyncSession,
    row_id: UUID,
    value: object,
) -> None:
    """Undo/redo of a deleted renovation asset (snapshot restore / re-delete)."""
    if value is None:
        # Redo: delete the restored asset again (with its user grants).
        asset = await session.get(RAsset, row_id)
        if asset is None:
            return
        links = (await session.exec(select(RUserAsset).where(col(RUserAsset.asset_id) == row_id))).all()
        for link in links:
            await session.delete(link)
        await session.delete(asset)
        return
    # Undo: restore the row and its per-user grants from the snapshot.
    if not isinstance(value, dict):
        message = "Некорректные данные для восстановления актива."
        raise CellValidationError(message)
    asset = RAsset(
        id=row_id,
        code=str(value.get("code") or ""),
        name=str(value.get("name") or ""),
        repair_code=str(value.get("repairCode") or ""),
    )
    session.add(asset)
    for item in value.get("userLinks") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            user_id = UUID(str(item[0]))
            project_id = UUID(str(item[1]))
        except ValueError:
            continue
        session.add(RUserAsset(user_id=user_id, project_id=project_id, asset_id=row_id))


async def _apply_renovation_option_deleted(
    session: AsyncSession,
    row_id: UUID,
    value: object,
) -> None:
    """Undo/redo of a deleted renovation catalog option (restore / re-delete)."""
    if value is None:
        # Redo: delete the restored option again.
        option = await session.get(RenovationReferenceValue, row_id)
        if option is None:
            return
        await session.delete(option)
        return
    # Undo: restore the option from the snapshot.
    if not isinstance(value, dict):
        message = "Некорректные данные для восстановления значения каталога."
        raise CellValidationError(message)
    try:
        catalog = RReferenceCatalog(str(value.get("catalog") or ""))
    except ValueError as error:
        message = "Некорректный каталог в данных для восстановления."
        raise CellValidationError(message) from error
    option = RenovationReferenceValue(
        id=row_id,
        catalog=catalog,
        value_key=str(value.get("valueKey") or ""),
        label=str(value.get("label") or ""),
        sort_order=int(value.get("sortOrder") or 0),
        payload=value.get("payload") if value.get("payload") is not None else None,
        is_active=True,
    )
    session.add(option)


async def _apply_renovation_user_cell(
    session: AsyncSession,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """Apply a renovation project-user dictionary cell (undo/redo)."""
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    user_row = await session.get(User, row_id)
    if user_row is None or user_row.is_deleted:
        raise _DICT_USER_NOT_FOUND
    project_id = await _renovation_project_id_required(session)
    link = (
        await session.exec(select(RUserRole).where(RUserRole.user_id == row_id, RUserRole.project_id == project_id))
    ).one_or_none()
    if field == "email":
        new_email = value if isinstance(value, str) and value.strip() else None
        if new_email is not None:
            user_row.email = new_email
    elif field == "fullname":
        user_row.name = value if isinstance(value, str) else ""
    elif field == "role":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        try:
            new_role = RProjectRole(value) if isinstance(value, str) else RProjectRole.ASSET_WORKER
        except ValueError as error:
            message = f"Неизвестная роль «{value}»."
            raise CellValidationError(message) from error
        link.role = new_role
        if new_role in (RProjectRole.ADMIN, RProjectRole.APPROVER):
            link.block = None
            await replace_renovation_user_assets(session, project_id, user_row.id, [])
    elif field == "block":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        try:
            link.block = RBlock(value) if isinstance(value, str) and value else None
        except ValueError as error:
            message = f"Неизвестный блок «{value}»."
            raise CellValidationError(message) from error
    elif field == "isActive":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        link.is_active = bool(value)
    elif field == "assetNames":
        await replace_renovation_user_assets(
            session, project_id, user_row.id, value if isinstance(value, list) else None
        )
    else:
        message = f"Неизвестное поле «{field}» справочника «Пользователи»."
        raise CellValidationError(message)
    user_row.updated_at = now
    session.add(user_row)
    if link is not None:
        link.updated_at = now
        link.updated_by = updated_by
        session.add(link)


async def _renovation_project_id_required(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        raise _ERR_NO_RENOVATION_PROJECT
    return project_id


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


# ─── Editable reference dictionaries ─────────────────────────────────────


class RenovationDictionaryError(ValueError):
    """Invalid or conflicting dictionary operation (e.g. a used option)."""


# Dictionary error messages (module constants — project style).
_DICT_ASSET_NOT_FOUND = ValueError("Актив не найден.")
_DICT_ASSET_USED = RenovationDictionaryError("Нельзя удалить: актив используется в плане ремонта.")
_DICT_OPTION_NOT_FOUND = ValueError("Значение каталога не найдено.")
_DICT_OPTION_USED = RenovationDictionaryError("Нельзя удалить: значение используется в плане ремонта.")
_DICT_USER_EXISTS = RenovationDictionaryError("Пользователь с таким email уже существует.")
_DICT_USER_NOT_FOUND = ValueError("Пользователь не найден.")
_DICT_ASSETS_NOT_FOUND = RenovationDictionaryError("Один или несколько активов не найдены.")
_DICT_ASSET_NAME_EXISTS = RenovationDictionaryError("Актив с таким названием уже существует.")
_DICT_ASSET_CODE_EXISTS = RenovationDictionaryError("Актив с таким ремонтным кодом уже существует.")
_DICT_OPTION_LABEL_EMPTY = RenovationDictionaryError("Название значения не может быть пустым.")
_DICT_USER_INVALID_EMAIL = RenovationDictionaryError("Некорректный email.")
_DICT_BLOCK_REQUIRED = RenovationDictionaryError("Для этой роли укажите блок ответственности")
_UNSET = object()
"""Sentinel: an optional parameter that was not provided by the caller."""
_ERR_NO_RENOVATION_PROJECT = RenovationPlanNotFoundError("Проект «Ремонт» не найден.")


async def read_renovation_asset_rows(
    session: AsyncSession,
    *,
    asset_id: UUID | None = None,
) -> list[RenovationAssetRowRead]:
    """«Активы» dictionary rows (all assets, ordered by name)."""
    query = select(RAsset).order_by(RAsset.name)
    if asset_id is not None:
        query = query.where(col(RAsset.id) == asset_id)
    rows = (await session.exec(query)).all()
    return [
        RenovationAssetRowRead(
            id=asset.id,
            repairCode=asset.repair_code,
            name=asset.name,
            createdAt=asset.created_at,
            updatedAt=asset.updated_at,
            updatedBy=asset.updated_by,
        )
        for asset in rows
    ]


async def _ensure_asset_unique(
    session: AsyncSession,
    *,
    name: str,
    repair_code: str,
    exclude_id: UUID | None = None,
) -> None:
    """Reject asset creation/renaming that duplicates name or repair code."""
    where = [] if exclude_id is None else [RAsset.id != exclude_id]
    if await session.scalar(select(RAsset.id).where(RAsset.name == name, *where)) is not None:
        raise _DICT_ASSET_NAME_EXISTS
    if await session.scalar(select(RAsset.id).where(RAsset.repair_code == repair_code, *where)) is not None:
        raise _DICT_ASSET_CODE_EXISTS


async def create_renovation_asset(
    session: AsyncSession,
    *,
    name: str,
    repair_code: str,
    user: User,
) -> tuple[RAsset, RenovationAssetRowRead]:
    """Create a renovation asset (code is auto-generated; never shown)."""
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    name = name.strip()
    repair_code = repair_code.strip()
    await _ensure_asset_unique(session, name=name, repair_code=repair_code)
    asset = RAsset(
        code=uuid4().hex,
        repair_code=repair_code,
        name=name,
        created_at=now,
        updated_at=now,
        updated_by=updated_by,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    row = (await read_renovation_asset_rows(session, asset_id=asset.id))[0]
    return asset, row


async def update_renovation_asset(
    session: AsyncSession,
    *,
    asset_id: UUID,
    name: str | None,
    repair_code: str | None,
    user: User,
) -> tuple[RAsset, list[AppliedCell]]:
    """Rename a renovation asset / change its repair code."""
    asset = await session.get(RAsset, asset_id)
    if asset is None:
        raise _DICT_ASSET_NOT_FOUND
    old_name = asset.name
    old_repair_code = asset.repair_code
    if name is not None:
        name = name.strip()
    if repair_code is not None:
        repair_code = repair_code.strip()
    if name is not None and name != old_name:
        await _ensure_asset_unique(session, name=name, repair_code=asset.repair_code, exclude_id=asset_id)
    if repair_code is not None and repair_code != old_repair_code:
        await _ensure_asset_unique(session, name=asset.name, repair_code=repair_code, exclude_id=asset_id)
    if name is not None:
        asset.name = name
    if repair_code is not None:
        asset.repair_code = repair_code
    asset.updated_at = datetime.now(UTC)
    asset.updated_by = user.name or user.email or ""
    session.add(asset)
    await session.commit()
    applied: list[AppliedCell] = []
    if asset.name != old_name:
        applied.append(
            AppliedCell(
                target_table=RAsset.__tablename__,
                row_id=asset.id,
                field="name",
                old_value=old_name,
                new_value=asset.name,
            )
        )
    if asset.repair_code != old_repair_code:
        applied.append(
            AppliedCell(
                target_table=RAsset.__tablename__,
                row_id=asset.id,
                field="repairCode",
                old_value=old_repair_code,
                new_value=asset.repair_code,
            )
        )
    return asset, applied


async def delete_renovation_asset(
    session: AsyncSession,
    *,
    asset_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a renovation asset; blocked while used by plan rows.

    The deletion is journaled as an undoable «__deleted» cell: the undo
    restores the row (and its user grants) from the snapshot, the redo
    deletes it again.
    """
    asset = await session.get(RAsset, asset_id)
    if asset is None:
        raise _DICT_ASSET_NOT_FOUND
    used = (await session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.asset_id) == asset_id))).first()
    if used is not None:
        raise _DICT_ASSET_USED
    user_links = (await session.exec(select(RUserAsset).where(col(RUserAsset.asset_id) == asset_id))).all()
    links = [(str(link.user_id), str(link.project_id)) for link in user_links]
    for link in user_links:
        await session.delete(link)
    snapshot = {
        "code": asset.code,
        "name": asset.name,
        "repairCode": asset.repair_code,
        "userLinks": links,
    }
    await session.delete(asset)
    await session.commit()
    return [
        AppliedCell(
            target_table=RAsset.__tablename__,
            row_id=asset_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def read_renovation_catalog_rows(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID | None = None,
) -> list[RenovationCatalogOptionRowRead]:
    """Options of a renovation reference catalog (ordered by sort_order, label)."""
    query = (
        select(RenovationReferenceValue)
        .where(col(RenovationReferenceValue.catalog) == catalog)
        .order_by(col(RenovationReferenceValue.sort_order), col(RenovationReferenceValue.label))
    )
    if option_id is not None:
        query = query.where(col(RenovationReferenceValue.id) == option_id)
    rows = (await session.exec(query)).all()
    return [
        RenovationCatalogOptionRowRead(
            valueId=str(option.id),
            label=option.label,
            createdAt=option.created_at,
            updatedAt=option.updated_at,
            updatedBy=option.updated_by,
        )
        for option in rows
    ]


async def _ensure_option_label_free(
    session: AsyncSession,
    *,
    catalog: str,
    label: str,
    exclude_id: UUID | None,
) -> None:
    """Reject a duplicate label within one catalog (case-insensitive)."""
    query = select(RenovationReferenceValue).where(
        col(RenovationReferenceValue.catalog) == RReferenceCatalog(catalog),
        func.lower(col(RenovationReferenceValue.label)) == label.lower(),
    )
    if exclude_id is not None:
        query = query.where(col(RenovationReferenceValue.id) != exclude_id)
    duplicate = (await session.exec(query)).first()
    if duplicate is not None:
        message = f"Запись «{label}» уже есть в этом справочнике."
        raise RenovationDictionaryError(message)


async def create_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    label: str,
    user: User,
) -> tuple[RenovationReferenceValue, RenovationCatalogOptionRowRead]:
    """Append an option to a renovation reference catalog."""
    label = label.strip()
    if not label:
        raise _DICT_OPTION_LABEL_EMPTY
    await _ensure_option_label_free(session, catalog=catalog, label=label, exclude_id=None)
    max_order = (
        await session.exec(
            select(func.max(col(RenovationReferenceValue.sort_order))).where(
                col(RenovationReferenceValue.catalog) == catalog
            )
        )
    ).one()
    now = datetime.now(UTC)
    option = RenovationReferenceValue(
        catalog=RReferenceCatalog(catalog),
        value_key=f"{catalog}_{uuid4().hex[:8]}",
        label=label,
        sort_order=int(max_order) + 1 if max_order is not None else 1,
        created_at=now,
        updated_at=now,
        updated_by=user.name or user.email or "",
    )
    session.add(option)
    await session.commit()
    await session.refresh(option)
    row = (await read_renovation_catalog_rows(session, catalog=catalog, option_id=option.id))[0]
    return option, row


async def update_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID,
    label: str | None,
    user: User,
) -> tuple[RenovationReferenceValue, list[AppliedCell]]:
    """Rename an option of a renovation reference catalog."""
    option = await session.get(RenovationReferenceValue, option_id)
    if option is None or option.catalog.value != catalog:
        raise _DICT_OPTION_NOT_FOUND
    old_label = option.label
    if label is not None:
        label = label.strip()
        if not label:
            raise _DICT_OPTION_LABEL_EMPTY
        await _ensure_option_label_free(session, catalog=catalog, label=label, exclude_id=option_id)
        option.label = label
    option.updated_at = datetime.now(UTC)
    option.updated_by = user.name or user.email or ""
    session.add(option)
    await session.commit()
    applied: list[AppliedCell] = []
    if option.label != old_label:
        applied.append(
            AppliedCell(
                target_table=RenovationReferenceValue.__tablename__,
                row_id=option.id,
                field="label",
                old_value=old_label,
                new_value=option.label,
            )
        )
    return option, applied


async def delete_renovation_catalog_option(
    session: AsyncSession,
    *,
    catalog: str,
    option_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a catalog option; blocked while used by plan rows.

    The deletion is journaled as an undoable «__deleted» cell (same pattern
    as the asset deletion: undo restores the option from the snapshot).
    """
    option = await session.get(RenovationReferenceValue, option_id)
    if option is None or option.catalog.value != catalog:
        raise _DICT_OPTION_NOT_FOUND
    reference_fields = [getattr(RenovationPlanRow, meta.db_name) for meta in field_metas() if meta.catalog == catalog]
    for field in reference_fields:
        # Fetch one scalar instead of materializing a 224-column plan row. A
        # per-reference-column index makes this independent of plan size.
        used = (await session.exec(select(1).where(field == option_id).limit(1))).first()
        if used is not None:
            raise _DICT_OPTION_USED
    snapshot = {
        "catalog": option.catalog.value,
        "valueKey": option.value_key,
        "label": option.label,
        "sortOrder": option.sort_order,
        "payload": option.payload,
    }
    await session.delete(option)
    await session.commit()
    return [
        AppliedCell(
            target_table=RenovationReferenceValue.__tablename__,
            row_id=option_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def read_renovation_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
) -> list[RenovationUserRowRead]:
    """
    «Пользователи» dictionary rows (the «Ремонт» project, without deleted).

    Shows all users with an r_user_roles link (including inactive ones —
    the project admin activates them). isActive and isAdmin come from the
    per-project link, not from the user's global fields.
    """
    project_id = (await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))).first()
    if project_id is None:
        return []
    query = (
        select(User, RUserRole)
        .join(RUserRole, col(RUserRole.user_id) == col(User.id))
        .where(col(RUserRole.project_id) == project_id, col(User.is_deleted) == False)  # noqa: E712
        .order_by(col(RUserRole.is_active).desc(), func.coalesce(User.name, ""))
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    rows = (await session.exec(query)).all()
    users = [user for user, _ in rows]
    links_by_user = {user.id: link for user, link in rows}
    asset_links = (
        await session.exec(
            select(RUserAsset, RAsset)
            .join(RAsset, col(RAsset.id) == col(RUserAsset.asset_id))
            .where(col(RUserAsset.user_id).in_([user.id for user in users]))
            .order_by(RAsset.name)
        )
    ).all()
    names_by_user: dict[UUID, list[str]] = {}
    for link, asset in asset_links:
        names_by_user.setdefault(link.user_id, []).append(asset.name)
    rows: list[RenovationUserRowRead] = []
    for user, link in ((user, links_by_user[user.id]) for user in users):
        # Admin/approver/block_owner implicitly see all assets — no list shown.
        central = link.role in (RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER)
        rows.append(
            RenovationUserRowRead(
                id=user.id,
                email=user.email or "",
                fullname=user.name or "",
                role=link.role,
                block=link.block,
                assetNames=[] if central else names_by_user.get(user.id, []),
                isActive=link.is_active,
                lastAuthenticatedAt=user.last_authenticated_at,
                createdAt=user.created_at,
                updatedAt=link.updated_at,
                updatedBy=link.updated_by,
            )
        )
    return rows


async def create_renovation_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    role: RProjectRole,
    block: RBlock | None,
    asset_names: list[str],
) -> User:
    """
    Add a user to the «Ремонт» project with role and assets.

    The existing app_users record is re-used when present; the renovation
    link (r_user_roles) is created with the chosen role, and the user is
    provisioned into all projects through sync_project_memberships (the
    same cross-project visibility as the «Сырьё» users dictionary).
    Admin/approver roles implicitly see all assets — no links are stored.
    """
    if role in (RProjectRole.BLOCK_OWNER, RProjectRole.ASSET_WORKER) and block is None:
        raise _DICT_BLOCK_REQUIRED
    central = role in (RProjectRole.ADMIN, RProjectRole.APPROVER)
    email = normalize_email(email)
    if not is_valid_email(email):
        raise _DICT_USER_INVALID_EMAIL
    existing = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
    if existing is not None:
        link = (
            await session.exec(
                select(RUserRole).where(RUserRole.user_id == existing.id, RUserRole.project_id == project_id)
            )
        ).one_or_none()
        if link is not None:
            raise _DICT_USER_EXISTS
        user = existing
    else:
        now = datetime.now(UTC)
        user = User(
            email=email,
            name=fullname.strip(),
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
    now = datetime.now(UTC)
    updated_by = user.name or user.email or ""
    session.add(
        RUserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            block=None if central else block,
            is_active=True,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
    )
    await replace_renovation_user_assets(session, project_id, user.id, [] if central else asset_names)
    await sync_project_memberships(session, user.id)
    await session.commit()
    return user


async def update_renovation_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    fullname: str | None = None,
    role: RProjectRole | None = None,
    block: RBlock | object | None = _UNSET,
    asset_names: list[str] | None = None,
    is_active: bool | None = None,
    updated_by: str = "",
) -> tuple[User, list[AppliedCell]]:
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_USER_NOT_FOUND
    link = (
        await session.exec(select(RUserRole).where(RUserRole.user_id == user_id, RUserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_USER_NOT_FOUND
    old_fullname = user.name
    old_role = link.role
    old_block = link.block
    old_is_active = link.is_active
    old_asset_names = await _renovation_user_asset_names(session, project_id, user.id)
    new_fullname = fullname.strip() if fullname is not None else old_fullname
    new_role = role if role is not None else old_role
    block_provided = block is not _UNSET
    new_block = block if isinstance(block, RBlock) else (old_block if not block_provided else None)
    if (
        new_role in (RProjectRole.BLOCK_OWNER, RProjectRole.ASSET_WORKER)
        and new_block is None
        and (role is not None or block_provided)
    ):
        raise _DICT_BLOCK_REQUIRED
    new_is_active = is_active if is_active is not None else old_is_active
    if fullname is not None:
        user.name = new_fullname
    if is_active is not None:
        link.is_active = is_active
    role_changed = new_role != old_role
    if role is not None:
        link.role = new_role
    if block_provided:
        link.block = new_block
    new_central = new_role in (RProjectRole.ADMIN, RProjectRole.APPROVER)
    if role_changed and new_central:
        link.block = None
    if asset_names is not None or role_changed:
        await replace_renovation_user_assets(
            session,
            project_id,
            user.id,
            asset_names if asset_names is not None else ([] if new_central else None),
        )
    new_asset_names = asset_names if asset_names is not None else ([] if role_changed else old_asset_names)
    now = datetime.now(UTC)
    link.updated_at = now
    link.updated_by = updated_by
    user.updated_at = now
    session.add(user)
    session.add(link)
    await session.commit()
    applied: list[AppliedCell] = []
    if new_fullname != old_fullname:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="fullname",
                old_value=old_fullname,
                new_value=new_fullname,
            )
        )
    if role_changed:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="role",
                old_value=old_role.value,
                new_value=new_role.value,
            )
        )
    if block_provided and new_block != old_block:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="block",
                old_value=old_block.value if old_block is not None else None,
                new_value=new_block.value if new_block is not None else None,
            )
        )
    if new_is_active != old_is_active:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="isActive",
                old_value=old_is_active,
                new_value=new_is_active,
            )
        )
    if new_asset_names != old_asset_names:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="assetNames",
                old_value=old_asset_names,
                new_value=new_asset_names,
            )
        )
    return user, applied


async def _renovation_user_asset_names(session: AsyncSession, project_id: UUID, user_id: UUID) -> list[str]:
    rows = (
        await session.exec(
            select(RAsset.name)
            .join(RUserAsset, col(RUserAsset.asset_id) == col(RAsset.id))
            .where(RUserAsset.user_id == user_id, RUserAsset.project_id == project_id)
            .order_by(RAsset.name)
        )
    ).all()
    return list(rows)


async def replace_renovation_user_assets(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    asset_names: list[str] | None,
) -> None:
    """Replace the user's renovation asset assignment (None — only remove links)."""
    existing = (
        await session.exec(
            select(RUserAsset).where(col(RUserAsset.user_id) == user_id, col(RUserAsset.project_id) == project_id)
        )
    ).all()
    for link in existing:
        await session.delete(link)
    await session.flush()
    if asset_names:
        assets = (await session.exec(select(RAsset).where(col(RAsset.name).in_(asset_names)))).all()
        names_found = {asset.name for asset in assets}
        missing = [name for name in asset_names if name not in names_found]
        if missing:
            message = f"Активы не найдены: {', '.join(missing)}"
            raise RenovationDictionaryError(message)
        now = datetime.now(UTC)
        for asset in assets:
            session.add(RUserAsset(user_id=user_id, project_id=project_id, asset_id=asset.id, created_at=now))
    await session.flush()
