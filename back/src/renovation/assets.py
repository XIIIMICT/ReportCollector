"""Renovation assets, working-plan aggregates and deletion replay."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

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
) -> list[RenovationAssetRowRead]:
    """«Активы» dictionary rows (all assets, ordered by name)."""
    query = select(RAsset).order_by(RAsset.name)
    if asset_id is not None:
        query = query.where(col(RAsset.id) == asset_id)
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
                .group_by(col(RenovationPlanRow.asset_id))
            )
        ).all()
        totals = {asset_id: float(total or 0) for asset_id, total, _count in sums if asset_id is not None}
        counts = {asset_id: int(count) for asset_id, _total, count in sums if asset_id is not None}
    return [
        RenovationAssetRowRead(
            id=asset.id,
            repairCode=asset.repair_code,
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
    if await session.scalar(select(RAsset.id).where(RAsset.repair_code == repair_code, *where)) is not None:
        raise _DICT_ASSET_CODE_EXISTS


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
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
        "code": asset.code,
        "name": asset.name,
        "repairCode": asset.repair_code,
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
