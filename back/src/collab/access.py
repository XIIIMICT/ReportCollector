"""Server-side room and cell authorization; no database session survives a check."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User
from src.collab.models import CellState
from src.common.section_policy import admin_room_section
from src.kppk.constants import (
    KPPK_PROJECT_KEY,
    KPPK_REALIZATION_FACT_FIELDS,
    KPPK_REALIZATION_PLAN_FIELDS,
)
from src.kppk.models import KppkOrg, KppkProduct, KppkRaw, KppkRealization, KppkRealizationPlan
from src.raw_materials.models import (
    AssetRawMaterial,
    DeliveryBasis,
    MaterialMovement,
    MovementPeriod,
    RawMaterial,
    RawMaterialAsset,
    RawMaterialPrice,
    ReportSnapshot,
)
from src.raw_materials.service import MOVEMENT_CELL_FIELDS, PRICE_CELL_FIELDS
from src.renovation.form_schema import field_meta
from src.renovation.models import RAsset
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RPlanState,
    RReferenceCatalog,
)

if TYPE_CHECKING:
    from src.auth.service import CurrentUserIdentity


class RoomAccessDeniedError(ValueError):
    """The room or cell is outside the current user's permissions."""


RAW_SECTIONS = {
    "materialMovement": (MaterialMovement, frozenset(MOVEMENT_CELL_FIELDS)),
    "rawMaterialPrices": (RawMaterialPrice, frozenset((*PRICE_CELL_FIELDS, "deliveryBasisName"))),
    "rawMaterials": (RawMaterial, frozenset({"name", "assetNames"})),
    "assets": (RawMaterialAsset, frozenset({"name"})),
    "deliveryBases": (DeliveryBasis, frozenset({"name"})),
    "users": (User, frozenset({"email", "fullname", "isAdmin", "isActive", "assetNames"})),
}
RENOVATION_SECTIONS = {
    "renovationPlan": (RenovationPlanRow, frozenset()),
    "renovationAssets": (RAsset, frozenset({"name", "repairCode"})),
    "renovationUsers": (User, frozenset({"email", "fullname", "role", "block", "isActive", "assetNames"})),
    **{
        f"renovationCatalog:{catalog.value}": (RenovationReferenceValue, frozenset({"label"}))
        for catalog in RReferenceCatalog
    },
}
# The «КППК» dictionaries: admin-edited copies, every member may read them.
KPPK_SECTIONS = {
    "kppkPositions": (KppkProduct, frozenset({"name", "productNum", "orgNames"})),
    "kppkRaws": (KppkRaw, frozenset({"name", "rawNum"})),
    "kppkOrgs": (KppkOrg, frozenset({"code", "name"})),
    # «Реализация» (Таблицы.xlsx): one section per slice storage — the plan
    # slices (Бизнес-план / Оперплан) share the plan copy, the fact slice is
    # its own room. The room cell keys carry the table name, the fields are
    # the camelCase DB attributes of the slice columns.
    "kppkRealizationPlan": (KppkRealizationPlan, frozenset(KPPK_REALIZATION_PLAN_FIELDS)),
    "kppkRealizationFact": (KppkRealization, frozenset(KPPK_REALIZATION_FACT_FIELDS)),
}


def _sections_for_project(project: str) -> dict[str, Any]:
    """Return the section table map of a project (КППК has its own copies)."""
    if project == KPPK_PROJECT_KEY:
        return KPPK_SECTIONS
    return RAW_SECTIONS if project == "raw_materials" else RENOVATION_SECTIONS


class RoomAccess:
    """Fresh access checks batched per socket, with narrow row projections."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        identity: CurrentUserIdentity,
        resolver: Callable[[AsyncSession, CurrentUserIdentity], Awaitable[tuple]],
        project: str,
        section: str,
        report_month: str | None,
    ) -> None:
        self.session_factory = session_factory
        self.identity = identity
        self.resolver = resolver
        self.project = project
        self.section = section
        self.report_month = report_month
        sections = _sections_for_project(project)
        if section not in sections:
            raise RoomAccessDeniedError
        self.model, self.fields = sections[section]
        if section == "materialMovement":
            try:
                month = date.fromisoformat(report_month or "")
            except ValueError as error:
                raise RoomAccessDeniedError from error
            if month.day != 1 or month.isoformat() != report_month:
                raise RoomAccessDeniedError
        elif report_month is not None:
            raise RoomAccessDeniedError

    async def resolve(self, session: AsyncSession) -> tuple:
        result = await self.resolver(session, self.identity)
        _, roles, _, _ = result
        if admin_room_section(self.section) and not any(role.value == "admin" for role in roles):
            raise RoomAccessDeniedError
        return result

    async def cells(self, session: AsyncSession, access: tuple, keys: list[str], *, write: bool) -> set[str]:
        _, roles, assets, block = access
        role_names = {role.value for role in roles}
        admin = "admin" in role_names
        central = bool(role_names & {"admin", "approver", "block_owner"})
        parsed: dict[str, tuple[UUID, str]] = {}
        for key in keys:
            try:
                table, row, field = key.split("/")
                row_id = UUID(row)
            except ValueError, AttributeError:
                continue
            if table != self.model.__tablename__:
                continue
            if write:
                if self.model is RenovationPlanRow:
                    meta = field_meta(field)
                    if meta is None or meta.is_read_only or meta.is_calculated:
                        continue
                    if not admin and (
                        "approver" in role_names or block is None or meta.responsibility_block != block.value
                    ):
                        continue
                elif field not in self.fields:
                    continue
            parsed[key] = row_id, field
        if not parsed:
            return set()
        ids = {row for row, _ in parsed.values()}
        model = self.model
        query = select(col(model.id)).where(col(model.id).in_(ids))
        if model in (MaterialMovement, RawMaterialPrice):
            arm_column = (
                MaterialMovement.asset_raw_material_id
                if model is MaterialMovement
                else RawMaterialPrice.asset_raw_material_id
            )
            query = query.join(AssetRawMaterial, col(arm_column) == col(AssetRawMaterial.id))
            if not central:
                query = query.where(col(AssetRawMaterial.asset_id).in_([asset.id for asset in assets]))
            if model is MaterialMovement:
                query = query.where(col(MaterialMovement.report_month) == date.fromisoformat(self.report_month or ""))
                if write:
                    period = await session.get(MovementPeriod, date.fromisoformat(self.report_month or ""))
                    if period is None:
                        return set()
                    final = (
                        await session.exec(
                            select(ReportSnapshot.id).where(
                                col(ReportSnapshot.report_month) == date.fromisoformat(self.report_month or ""),
                                col(ReportSnapshot.is_final).is_(True),
                            )
                        )
                    ).first()
                    if final is not None:
                        return set()
        elif model is RenovationPlanRow:
            query = query.join(RenovationPlan, col(RenovationPlan.id) == col(RenovationPlanRow.plan_id))
            query = query.where(col(RenovationPlan.state) != RPlanState.ARCHIVED)
            if not central:
                query = query.where(col(RenovationPlanRow.asset_id).in_([asset.id for asset in assets]))
            if write and not admin:
                query = query.where(col(RenovationPlan.state) == RPlanState.WORKING)
        elif model is RenovationReferenceValue:
            query = query.where(col(RenovationReferenceValue.catalog) == self.section.split(":", 1)[1])
        allowed_ids = set((await session.exec(query)).all())
        return {key for key, (row_id, _) in parsed.items() if row_id in allowed_ids}

    async def filter_messages(self, messages: list[dict]) -> list[dict]:
        """Recheck rights before every batch, including silent/long-lived sockets."""
        keys: list[str] = []
        for message in messages:
            if message.get("type") == "cell:changed":
                keys.append(f"{self.model.__tablename__}/{message.get('rowId')}/{message.get('field')}")
            elif message.get("type") in {"cell:locked", "cell:unlocked"}:
                keys.append(message.get("key", ""))
            elif message.get("type") in {"hello", "presence:update"}:
                for member in message.get("members", {}).values():
                    keys.extend((member.get("selection") or {}).get("cellKeys", []))
        async with self.session_factory() as session:
            access = await self.resolve(session)
            allowed = await self.cells(session, access, keys, write=False)
            revisions = (
                {
                    state.key: state.state["revision"]
                    for state in (await session.exec(select(CellState).where(col(CellState.key).in_(keys)))).all()
                }
                if keys
                else {}
            )
            _, roles, assets, _ = access
            central = any(role.value in {"admin", "approver", "block_owner"} for role in roles)
            names = {asset.name for asset in assets}
        result = []
        for message in messages:
            kind = message.get("type")
            if kind == "cell:changed":
                key = f"{self.model.__tablename__}/{message.get('rowId')}/{message.get('field')}"
                if key not in allowed:
                    continue
                if int(message.get("revision", 0)) < revisions.get(key, 0):
                    continue
            elif kind in {"cell:locked", "cell:unlocked"}:
                if message.get("key") not in allowed:
                    continue
            elif kind in {"hello", "presence:update"}:
                members = {}
                for member_id, source in message.get("members", {}).items():
                    if (
                        not central
                        and source.get("role") == "asset_worker"
                        and not names.intersection(source.get("assets", []))
                    ):
                        continue
                    member = {**source}
                    selection = member.pop("selection", None)
                    if selection and selection.get("cellKeys") and all(key in allowed for key in selection["cellKeys"]):
                        member["selection"] = selection
                    members[member_id] = member
                result.append({**message, "members": members})
                continue
            result.append(message)
        return result
