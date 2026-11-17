"""
Server-side transactional undo/redo stack (PostgreSQL, scoped per session/form).

The last active action is undone; redo replays undone actions in their
original order (the oldest undone one first — the reverse of the undo
sequence). An action is a batch of cell edits made in one PATCH.
Application only happens if, after the action, none of its cells was
changed by another user (collab_cell_states lastUserId/lastActionId) and none
is leased by another (lease). A cell the current user changed last
themselves is never a conflict — undoing one's own consecutive edits
works. The target row is applied through the project's own validator
(raw-materials, renovation or КППК).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlmodel import col, select

from src.auth.models import Project, UserRole
from src.collab.journal import AppliedCell
from src.collab.locking import lock_project, lock_rows
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE, canonical_key
from src.collab.store import lease_key
from src.common.section_policy import section_policy
from src.kppk.constants import KPPK_PROJECT_KEY
from src.kppk.dictionaries import apply_kppk_cell_value
from src.kppk.models import (
    KppkCost,
    KppkOrg,
    KppkOrgProduct,
    KppkProduct,
    KppkRaw,
    KppkRealization,
    KppkRealizationPlan,
)
from src.kppk.users import apply_kppk_user_value
from src.logistics.constants import LOGISTICS_PROJECT_KEY
from src.logistics.models import LogisticsRate, LogisticsUserAsset
from src.logistics.rates import apply_logistics_rate_value
from src.raw_materials.models import (
    AssetRawMaterial,
    MaterialMovement,
    MovementPeriod,
    RawMaterialPrice,
    RawMaterialUserAsset,
)
from src.raw_materials.service import apply_cell_value
from src.renovation.form_schema import field_meta
from src.renovation.models import RAsset, RProjectRole, RUserAsset, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.service import apply_renovation_cell_value

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from src.auth.models import User
    from src.collab.persistence import JournalStore

RENOVATION_TABLES = frozenset(
    {
        RENOVATION_PLAN_ROWS_TABLE,
        RAsset.__tablename__,
        RenovationReferenceValue.__tablename__,
    }
)

KPPK_TABLES = frozenset(
    {
        KppkProduct.__tablename__,
        KppkOrgProduct.__tablename__,
        KppkRaw.__tablename__,
        KppkOrg.__tablename__,
        KppkRealization.__tablename__,
        KppkRealizationPlan.__tablename__,
        KppkCost.__tablename__,
    }
)

LOGISTICS_TABLES = frozenset({LogisticsRate.__tablename__})


@dataclass
class UndoConflict:
    row_id: UUID
    field: str
    user_name: str
    action_id: int
    reason: Literal["conflict", "locked"]


@dataclass
class UndoOutcome:
    applied: bool
    reason: Literal["applied", "empty", "conflict", "locked"] = "empty"
    conflict: UndoConflict | None = None
    applied_cells: list[AppliedCell] = field(default_factory=list)
    revision: int = 0
    project_key: str = ""
    section: str = ""
    report_month: date | None = None
    applied_value: object = None


# Form scopes undo in parallel with running edits (shared lock); the «КППК»
# realization slices behave like the other forms.
_FORM_SCOPES = frozenset({"renovationPlan", "materialMovement", "rawMaterialPrices", "logisticsRates"})


def _default_project(scope: str) -> str:
    if scope.startswith("logisticsRates"):
        return LOGISTICS_PROJECT_KEY
    return "renovation" if scope.startswith("renovation") else "raw_materials"


def _is_form_scope(scope: str) -> bool:
    """Whether an undo scope belongs to a concurrently-edited form (shared lock)."""
    if scope.startswith("kppkRealization"):
        return True
    return scope.split("/", maxsplit=1)[0] in _FORM_SCOPES


def _action_cells(action: dict) -> list[dict]:
    """Cells of an action; falls back to the legacy single-cell layout."""
    cells = action.get("cells")
    if cells:
        return cells
    return [
        {
            "targetTable": action["targetTable"],
            "rowId": action["rowId"],
            "field": action["field"],
            "oldValue": action["oldValue"],
            "newValue": action["newValue"],
        }
    ]


async def undo_last_edit(
    session: AsyncSession,
    *,
    user: User,
    store: JournalStore,
    scope: str,
    session_id: str,
    skip_action_id: int | None = None,
    project_key: str | None = None,
) -> UndoOutcome:
    """
    Undoes the user's last action (a PATCH batch) on the session's form scope.

    scope is the form's section and month (section/reportMonth); each form
    has its own history. skip_action_id is the action the user explicitly
    skipped after a conflict (second undo press): it's excluded from the
    search and the next available action below is looked up.
    """
    project_key = project_key or _default_project(scope)
    await lock_project(session, project_key, exclusive=not _is_form_scope(scope))
    await lock_rows(session, [f"undo/{user.id}/{session_id}/{scope}"])
    action = await store.next_action(str(user.id), session_id, scope, undone=False, after_action_id=skip_action_id)
    if action is not None and action["project"] != project_key:
        return UndoOutcome(applied=False, reason="empty")
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(
        session, user=user, store=store, scope=scope, session_id=session_id, action=action, redo=False
    )


async def redo_last_undo(
    session: AsyncSession,
    *,
    user: User,
    store: JournalStore,
    scope: str,
    session_id: str,
    skip_action_id: int | None = None,
    project_key: str | None = None,
) -> UndoOutcome:
    """
    Return the user's oldest undone action on the session's form scope.

    Undone actions replay in their original order: the first action that
    was undone is redone first. skip_action_id is the action the user
    explicitly skipped after a conflict (second redo press): it's excluded
    from the search and the next available action above is looked up.
    """
    project_key = project_key or _default_project(scope)
    await lock_project(session, project_key, exclusive=not _is_form_scope(scope))
    await lock_rows(session, [f"undo/{user.id}/{session_id}/{scope}"])
    action = await store.next_action(str(user.id), session_id, scope, undone=True, after_action_id=skip_action_id)
    if action is not None and action["project"] != project_key:
        return UndoOutcome(applied=False, reason="empty")
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(
        session, user=user, store=store, scope=scope, session_id=session_id, action=action, redo=True
    )


async def _switch_entry(
    session: AsyncSession,
    *,
    user: User,
    store: JournalStore,
    scope: str,
    session_id: str,
    action: dict,
    redo: bool,
) -> UndoOutcome:
    cells = _action_cells(action)
    project_key = action["project"]
    action_id = int(action["id"])
    await lock_rows(session, [f"{cell['targetTable']}/{cell['rowId']}" for cell in cells])
    if not await _authorize_action(session, user, action, cells):
        return UndoOutcome(
            applied=False,
            reason="conflict",
            conflict=UndoConflict(
                row_id=UUID(cells[0]["rowId"]),
                field=cells[0]["field"],
                user_name="Нет права изменять эту версию или ячейку.",
                action_id=action_id,
                reason="conflict",
            ),
        )
    # The whole batch is atomic: if any cell conflicts or is leased by
    # someone else, nothing is applied.
    for cell in cells:
        key = canonical_key(cell["targetTable"], UUID(cell["rowId"]), cell["field"])
        conflict = await _check_conflict(store, cell, action_id, key, project_key, user, session_id)
        if conflict is not None:
            return UndoOutcome(applied=False, reason=conflict.reason, conflict=conflict)

    applied_cells: list[AppliedCell] = []
    for cell in cells:
        row_id = UUID(cell["rowId"])
        field = cell["field"]
        target_table = cell["targetTable"]
        key = canonical_key(target_table, row_id, field)
        value = cell["newValue"] if redo else cell["oldValue"]
        is_renovation = target_table in RENOVATION_TABLES or (
            target_table == "app_users" and str(action.get("section", "")).startswith("renovation")
        )
        try:
            if is_renovation:
                await apply_renovation_cell_value(
                    session,
                    target_table=target_table,
                    row_id=row_id,
                    field=field,
                    value=value,
                    user=user,
                )
            elif target_table == "app_users" and project_key == "kppk":
                await apply_kppk_user_value(session, row_id=row_id, field=field, value=value, editor=user)
            elif target_table in KPPK_TABLES:
                await apply_kppk_cell_value(
                    session,
                    target_table=target_table,
                    row_id=row_id,
                    field=field,
                    value=value,
                    user=user,
                )
            elif target_table in LOGISTICS_TABLES:
                await apply_logistics_rate_value(session, row_id=row_id, field=field, value=value, editor=user)
            else:
                await apply_cell_value(
                    session,
                    target_table=target_table,
                    row_id=row_id,
                    field=field,
                    value=value,
                    user=user,
                )
        except ValueError as error:
            # Business rules can reject the reverse apply even when the
            # conflict/lease checks passed (e.g. undoing a create whose record
            # is now referenced elsewhere). Surface it as a skippable conflict
            # instead of failing the request; drop any partial session state.
            await session.rollback()
            return UndoOutcome(
                applied=False,
                reason="conflict",
                conflict=UndoConflict(
                    row_id=row_id,
                    field=field,
                    user_name=str(error),
                    action_id=action_id,
                    reason="conflict",
                ),
            )
        revision = await store.bump_cell_state(
            key,
            user_id=str(user.id),
            user_name=user.name or "",
            action_id=action_id,
        )
        applied_cells.append(
            AppliedCell(
                target_table=target_table,
                row_id=row_id,
                field=field,
                old_value=cell["oldValue"],
                new_value=value,
                revision=revision,
                action_id=action_id,
            )
        )
    await store.mark_action_undone(str(user.id), session_id, scope, action_id, undone=not redo)
    await session.flush()
    return UndoOutcome(
        applied=True,
        reason="applied",
        applied_cells=applied_cells,
        revision=applied_cells[0].revision,
        project_key=project_key,
        section=action["section"],
        report_month=_parse_month(action["reportMonth"]),
        applied_value=applied_cells[0].new_value,
    )


def _parse_month(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def _check_conflict(
    store: JournalStore,
    cell: dict,
    action_id: int,
    key: str,
    project_key: str,
    user: User,
    session_id: str,
) -> UndoConflict | None:
    state = await store.get_cell_state(key)
    if state is not None:
        last_action_id = state.get("lastActionId")
        last_user_id = state.get("lastUserId")
        if last_action_id != str(action_id) and last_user_id != str(user.id):
            return UndoConflict(
                row_id=UUID(cell["rowId"]),
                field=cell["field"],
                user_name=state.get("lastUserName") or "",
                action_id=action_id,
                reason="conflict",
            )
    owner = await store.get_lease(lease_key(key, project=project_key))
    if owner is not None and (
        str(owner.get("userId")) != str(user.id) or owner.get("sessionId", session_id) != session_id
    ):
        return UndoConflict(
            row_id=UUID(cell["rowId"]),
            field=cell["field"],
            user_name=owner.get("name") or "",
            action_id=action_id,
            reason="locked",
        )
    return None


async def _dictionary_writable(*, user: User, link: object, section: str) -> bool:
    """
    Return whether a dictionary row of this section may be written by this user.

    Mirrors the routers (see `common/section_policy.py`): an admin-only
    dictionary (`undo == "admin"`) is edited — and therefore undone — by project
    admins only, so a member's stale dictionary action is never replayed. A
    dictionary that later becomes member-editable only changes its policy, not
    this call site.
    """
    del user
    if section_policy(section).undo != "admin":
        return True
    role = getattr(link, "role", None)
    return getattr(role, "value", None) == "admin"


async def _authorize_action(session: AsyncSession, user: User, action: dict, cells: list[dict]) -> bool:
    """Check that undo is a write: current role/asset/block/version rules apply."""
    if user.is_deleted:
        return False
    renovation = action["project"] == "renovation"
    role_model = RUserRole if renovation else UserRole
    link = (
        await session.exec(
            select(role_model)
            .join(Project, col(Project.id) == col(role_model.project_id))
            .where(
                col(role_model.user_id) == user.id,
                col(Project.key) == action["project"],
                col(role_model.is_active).is_(True),
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if link is None:
        return False
    admin = link.role.value == "admin"
    for cell in cells:
        table = cell["targetTable"]
        row_id = UUID(cell["rowId"])
        if table == "app_users" and row_id == user.id:
            return False
        if renovation and table == "r_plan_rows":
            row = await session.get(RenovationPlanRow, row_id, populate_existing=True)
            # «__created»/«__deleted» restore a row that no longer exists (or
            # undo removes the new one): the plan guard uses the snapshot's
            # plan, the live row only when it is there.
            snapshot = cell["oldValue"] if cell["field"] == "__deleted" else cell["newValue"]
            plan_id = row.plan_id if row is not None else None
            if plan_id is None and isinstance(snapshot, dict) and snapshot.get("planId"):
                plan_id = UUID(str(snapshot["planId"]))
            if plan_id is None:
                return False
            plan = await session.get(RenovationPlan, plan_id, populate_existing=True)
            if (
                plan is None
                or plan.state == RPlanState.ARCHIVED
                or (plan.state == RPlanState.PENDING_APPROVAL and not admin)
            ):
                return False
            if row is None and plan.state != RPlanState.WORKING:
                # Only a working plan is authored row by row.
                return False
            field = str(cell["field"])
            if row is None and field == "asset_id":
                return False
            meta = None if field.startswith("__") or row is None else field_meta(field)
            if row is not None and field == "asset_id":
                # The asset assignment is not a form column: any editor may redo it.
                meta = None
            if (
                not field.startswith("__")
                and field != "asset_id"
                and (meta is None or meta.is_calculated or meta.is_read_only)
            ):
                return False
            if admin:
                continue
            if not isinstance(link, RUserRole):
                return False
            if meta is None:
                # A row lifecycle change («__created»/«__deleted»/«asset_id»):
                # any editor of the plan who may write it now; the plan-state
                # guard above already refused an archived/pending plan.
                if link.role == RProjectRole.APPROVER:
                    return False
                continue
            if (
                link.role == RProjectRole.APPROVER
                or link.block is None
                or meta.responsibility_block != link.block.value
            ):
                return False
            if link.role == RProjectRole.ASSET_WORKER:
                if row is None:
                    # A restored row has no live asset to grant-check against.
                    continue
                grant = await session.get(RUserAsset, (user.id, link.project_id, row.asset_id))
                if grant is None:
                    return False
        elif not renovation and table in {"rm_material_movements", "rm_raw_material_prices"}:
            model = MaterialMovement if table == "rm_material_movements" else RawMaterialPrice
            row = await session.get(model, row_id, populate_existing=True)
            if row is None:
                return False
            if isinstance(row, MaterialMovement) and await session.get(MovementPeriod, row.report_month) is None:
                return False
            arm = await session.get(AssetRawMaterial, row.asset_raw_material_id)
            if arm is None or (not admin and await session.get(RawMaterialUserAsset, (user.id, arm.asset_id)) is None):
                return False
        elif action["project"] == KPPK_PROJECT_KEY and table in {
            "kppk_realization",
            "kppk_realization_plan",
            "kppk_cost",
        }:
            model = {
                "kppk_realization": KppkRealization,
                "kppk_realization_plan": KppkRealizationPlan,
                "kppk_cost": KppkCost,
            }[table]
            if not admin or await session.get(model, row_id, populate_existing=True) is None:
                return False
        elif action["project"] == LOGISTICS_PROJECT_KEY and table in LOGISTICS_TABLES:
            # A create/delete action is replayed in both directions, so the
            # row's current existence is a gate only for plain cell edits.
            is_existence = cell["field"] == "__created"
            row = await session.get(LogisticsRate, row_id, populate_existing=True)
            if not is_existence and row is None:
                return False
            if not admin:
                # An asset worker may undo what they did on their own assets;
                # a reviewer never writes, so nothing of theirs is replayed.
                if getattr(link.role, "value", None) != "asset_worker":
                    return False
                asset_id: UUID | None = row.org_id if row is not None else None
                if asset_id is None:
                    snapshot = cell["oldValue"] if isinstance(cell["oldValue"], dict) else cell["newValue"]
                    if not isinstance(snapshot, dict) or snapshot.get("assetId") is None:
                        return False
                    asset_id = UUID(str(snapshot["assetId"]))
                grant = (
                    await session.exec(
                        select(LogisticsUserAsset).where(
                            col(LogisticsUserAsset.user_id) == user.id,
                            col(LogisticsUserAsset.org_id) == asset_id,
                        )
                    )
                ).first()
                if grant is None:
                    return False
        elif not await _dictionary_writable(user=user, link=link, section=action.get("section") or ""):
            # A stale dictionary action of a member is not replayed while the
            # dictionary is admin-only (the rule lives in `section_policy`).
            return False
    return True
