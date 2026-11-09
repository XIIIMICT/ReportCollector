"""Renovation assets, working-plan aggregates and deletion replay."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.raw_materials.errors import CellValidationError
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RAsset, RUserAsset
from src.renovation.plan import RenovationPlanRow, RPlanState
from src.renovation.schemas import RenovationAssetRowRead

from .errors import RenovationDictionaryError
from .plan_queries import _plan_id_by_state

if TYPE_CHECKING:
    from src.auth.models import User

_DICT_ASSET_NOT_FOUND = ValueError("Актив не найден.")
_DICT_ASSET_USED = RenovationDictionaryError("Нельзя удалить: актив используется в плане ремонта.")
_DICT_ASSET_NAME_EXISTS = RenovationDictionaryError("Актив с таким названием уже существует.")
_DICT_ASSET_CODE_EXISTS = RenovationDictionaryError("Актив с таким ремонтным кодом уже существует.")


async def read_renovation_asset_rows(
    session: AsyncSession,
    *,
    asset_id: UUID | None = None,
    row_ids: list[UUID] | None = None,
) -> list[RenovationAssetRowRead]:
    """«Активы» dictionary rows (all assets, ordered by name)."""
    query = select(RAsset).order_by(RAsset.name)
    if asset_id is not None:
        query = query.where(col(RAsset.id) == asset_id)
    if row_ids is not None:
        query = query.where(col(RAsset.id).in_(row_ids))
    rows = (await session.exec(query)).all()
    # Computed columns of the working plan's rows per asset: the total
    # «Стоимость итого» (col_031) and the number of repair works (rows).
    working_plan_id = await _plan_id_by_state(session, RPlanState.WORKING)
    totals: dict[UUID, float] = {}
    counts: dict[UUID, int] = {}
    if working_plan_id is not None:
        sums = (
            await session.exec(
                select(
                    col(RenovationPlanRow.asset_id),
                    func.coalesce(func.sum(col(RenovationPlanRow.col_031)), 0),
                    func.count(),
                )
                .where(col(RenovationPlanRow.plan_id) == working_plan_id)
                .where(col(RenovationPlanRow.asset_id).in_([row.id for row in rows]))
                .group_by(col(RenovationPlanRow.asset_id))
            )
        ).all()
        totals = {asset_id: float(total or 0) for asset_id, total, _count in sums if asset_id is not None}
        counts = {asset_id: int(count) for asset_id, _total, count in sums if asset_id is not None}
    return [
        RenovationAssetRowRead(
            id=asset.id,
            repairCode=asset.code,
            name=asset.name,
            createdAt=asset.created_at,
            updatedAt=asset.updated_at,
            updatedBy=asset.updated_by,
            totalCost=totals.get(asset.id, 0.0),
            repairCount=counts.get(asset.id, 0),
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
    if await session.scalar(select(RAsset.id).where(RAsset.code == repair_code, *where)) is not None:
        raise _DICT_ASSET_CODE_EXISTS


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def create_renovation_asset(
    session: AsyncSession,
    *,
    name: str,
    repair_code: str,
    user: User,
) -> tuple[RAsset, RenovationAssetRowRead]:
    """Create a renovation asset (the code is the repair code of the work ID)."""
    now = datetime.now(UTC)
    name = name.strip()
    repair_code = repair_code.strip()
    await _ensure_asset_unique(session, name=name, repair_code=repair_code)
    asset = RAsset(
        code=repair_code,
        name=name,
        created_at=now,
        updated_at=now,
        updater_user_id=user.id,
    )
    session.add(asset)
    await session.flush()
    await session.refresh(asset)
    row = (await read_renovation_asset_rows(session, asset_id=asset.id))[0]
    return asset, row


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
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
    old_repair_code = asset.code
    if name is not None:
        name = name.strip()
    if repair_code is not None:
        repair_code = repair_code.strip()
    if name is not None and name != old_name:
        await _ensure_asset_unique(session, name=name, repair_code=asset.code, exclude_id=asset_id)
    if repair_code is not None and repair_code != old_repair_code:
        await _ensure_asset_unique(session, name=asset.name, repair_code=repair_code, exclude_id=asset_id)
    if name is not None:
        asset.name = name
    if repair_code is not None:
        asset.code = repair_code
    asset.updated_at = datetime.now(UTC)
    asset.updater_user_id = user.id
    session.add(asset)
    await session.flush()
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
    if asset.code != old_repair_code:
        applied.append(
            AppliedCell(
                target_table=RAsset.__tablename__,
                row_id=asset.id,
                field="repairCode",
                old_value=old_repair_code,
                new_value=asset.code,
            )
        )
    return asset, applied


async def replace_renovation_asset(
    session: AsyncSession,
    *,
    asset_id: UUID,
    target_id: UUID | None,
    user: User,
) -> list[AppliedCell]:
    """
    Merge one renovation asset into another, then delete the replaced one.

    Plan rows of the replaced asset move to the target. When the target already
    has a row with the same work code (its case-insensitive duplicate), that
    row keeps its place and the moved one is dropped — the plan never gets two
    rows with one code. Per-user grants merge the same way. Deleting a replaced
    asset that plan rows reference is refused with the usual dictionary error.
    """
    source = await session.get(RAsset, asset_id)
    if source is None:
        raise _DICT_ASSET_NOT_FOUND
    if target_id is None:
        return await delete_renovation_asset(session, asset_id=asset_id)
    if target_id == asset_id:
        msg = "Нельзя заменить значение на него же."
        raise ValueError(msg)
    target = await session.get(RAsset, target_id)
    if target is None:
        raise _DICT_ASSET_NOT_FOUND
    now = datetime.now(UTC)
    target_codes = {
        (code or "").casefold()
        for code in (
            await session.exec(select(RenovationPlanRow.col_003).where(col(RenovationPlanRow.asset_id) == target_id))
        ).all()
        if code
    }
    for row in (await session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.asset_id) == asset_id))).all():
        code = (row.col_003 or "").casefold()
        if code and code in target_codes:
            # The same work already exists on the target: keep one row.
            await session.delete(row)
            await session.flush()
            continue
        row.asset_id = target_id
        row.updated_at = now
        row.updater_user_id = user.id
        if code:
            target_codes.add(code)
        await session.flush()
    target_grants = {
        (grant.user_id, grant.project_id)
        for grant in (await session.exec(select(RUserAsset).where(col(RUserAsset.asset_id) == target_id))).all()
    }
    for grant in (await session.exec(select(RUserAsset).where(col(RUserAsset.asset_id) == asset_id))).all():
        if (grant.user_id, grant.project_id) in target_grants:
            await session.delete(grant)
        else:
            grant.asset_id = target_id
        await session.flush()
    source_name = source.name
    target.updated_at = now
    target.updater_user_id = user.id
    session.add(target)
    await session.delete(source)
    await session.flush()
    return [
        AppliedCell(
            target_table=RAsset.__tablename__,
            row_id=asset_id,
            field="__replaced",
            old_value={"name": source_name, "targetId": str(target_id)},
            new_value=None,
        )
    ]


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
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
        "name": asset.name,
        "repairCode": asset.code,
        "userLinks": links,
    }
    await session.delete(asset)
    await session.flush()
    return [
        AppliedCell(
            target_table=RAsset.__tablename__,
            row_id=asset_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def _apply_renovation_asset_deleted(
    session: AsyncSession,
    row_id: UUID,
    value: object,
    updater_user_id: UUID,
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
    # `repairCode` is the journal's historical field name for the asset code.
    asset = RAsset(
        id=row_id,
        code=str(value.get("repairCode") or ""),
        name=str(value.get("name") or ""),
        updater_user_id=updater_user_id,
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
