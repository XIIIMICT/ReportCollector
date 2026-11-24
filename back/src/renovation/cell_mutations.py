"""
Renovation plan WRITE surface: row create/delete/asset assignment, cell edits, undo/redo cell application and reference coercion.

Moved verbatim from ``service.py`` (Phase 4). ``@project_write`` decorators,
row locks, lease checks, journal writes and revision bumps stay exactly where
they were; ``service.py`` re-exports these names for its historical importers.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.locking import lock_rows, project_write
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.persistence import JournalStore
from src.common.job_models import ACTIVE_STATES, DataJob
from src.raw_materials.service import CellValidationError, check_edit_leases, coerce_number, coerce_text
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import (
    RenovationFieldMeta,
    editable_fields,
    field_meta,
)
from src.renovation.models import RAsset, RBlock, RProjectRole
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RPlanState,
)
from src.renovation.recompute import _recalculate_row
from src.renovation.renovation_id import (
    DEFECT_NUMBER_FIELD,
    DIRECTION_CODE_FIELD,
    RENOVATION_ID_FORMULA_ENABLED,
    RenovationIdResult,
    normalize_direction_code,
)
from src.renovation.schemas import (
    CellUpdate,
    RenovationCalculatedCellRead,
    RenovationIdRead,
    RenovationPlanRowRead,
)
from src.renovation.service_common import (
    _CELL_MISSING_ROW_OR_FIELD,
    _CELL_PLAN_ROW_NOT_FOUND,
    _ERR_ADD_ASSET,
    _ERR_ADD_PERMISSION,
    _ERR_DELETE_APPROVER,
    _ERR_DELETE_BASELINE_ROW,
    _ERR_DELETE_NOT_EMPTY,
    _ERR_EDIT_PENDING,
    _ERR_NO_WORKING_PLAN,
    _ERR_ROW_HAS_ASSET,
    _ERR_UNKNOWN_TABLE,
    _RENOVATION_ID_INPUT_FIELDS,
    RenovationPlanStateError,
    _approver_read_only,
    _asset_access,
    _bad_boolean,
    _baseline_boundary,
    _block_access,
    _check_date_rules,
    _check_edit_rules,
    _coerce_date,
    _plan_row_read,
    _read_only_field,
    _reference_labels,
    _renovation_id_input,
    _resolve_reference_id,
    _row_datetime,
    _row_value_label,
    _safe_renovation_id,
    _unknown_field,
    defect_number_text,
    prefetch_reference_ids,
    resolve_reference_id_from_map,
)

from .assets import (
    _DICT_ASSET_NOT_FOUND,
    _apply_renovation_asset_deleted,
)
from .catalogs import (
    _DICT_OPTION_NOT_FOUND,
    _apply_renovation_option_deleted,
)
from .plan_queries import _plan_by_state
from .users import _apply_renovation_user_cell, restore_renovation_user


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
    # Gate A: an active workbook load owns the plan. The UI blocks the form;
    # this check keeps a stale/foreign client from writing behind it.
    active_import = (
        await session.exec(
            select(DataJob.id)
            .where(DataJob.kind == "renovation_import", col(DataJob.status).in_(ACTIVE_STATES))
            .limit(1)
        )
    ).first()
    if active_import is not None:
        message = "База обновляется загрузкой файла. Подождите завершения загрузки."
        raise RenovationPlanStateError(message)
    asset_ids = [asset.id for asset in assets]
    assets_by_id = {asset.id: asset for asset in assets}
    is_admin = RProjectRole.ADMIN in roles
    is_approver = RProjectRole.APPROVER in roles
    # A version under review is frozen for everyone except the admins.
    if plan.state == RPlanState.PENDING_APPROVAL and not is_admin:
        raise _ERR_EDIT_PENDING
    label_map = await _reference_labels(session)
    # Phase 6.2: one bounded snapshot instead of one catalog SELECT per
    # reference-valued cell in this batch.
    reference_ids: dict[str, dict[str, UUID]] | None = None
    for update in updates:
        meta = field_meta(update.field)
        if meta is not None and meta.cell_type == "reference":
            reference_ids = await prefetch_reference_ids(session)
            break
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
        new_value, db_value = await _coerce_cell(session, meta, update.value, reference_ids)
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
        # work_or_repair_code is another editable cell and the user's text is kept verbatim.
        edited_fields = touched_fields.get(row.id) or set()
        if RENOVATION_ID_FORMULA_ENABLED:
            keep_existing = not (edited_fields & _RENOVATION_ID_INPUT_FIELDS)
            result = _safe_renovation_id(_renovation_id_input(row, asset, label_map, keep_existing=keep_existing))
        else:
            result = RenovationIdResult(value=row.work_or_repair_code or "", missing_components=())
        id_changed = row.work_or_repair_code != result.value
        if id_changed:
            row.work_or_repair_code = result.value
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


async def _coerce_cell(
    session: AsyncSession,
    meta: RenovationFieldMeta,
    value: object,
    reference_ids: dict[str, dict[str, UUID]] | None = None,
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
        if reference_ids is not None:
            value_id = resolve_reference_id_from_map(reference_ids, meta.catalog, label)
        else:
            value_id = await _resolve_reference_id(session, meta.catalog, label)
        return label, value_id
    text = coerce_text(value)
    if meta.db_name == DIRECTION_CODE_FIELD:
        text = normalize_direction_code(text)
    if meta.db_name == DEFECT_NUMBER_FIELD:
        # Text now: the seven-digit number keeps its leading zero (`706179` →
        # `0706179`), the same normalisation the importer applies.
        text = defect_number_text(text)
    return text, text


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
            asset.code = value if isinstance(value, str) else ""
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
            row.work_or_repair_code = result.value
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
