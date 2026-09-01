"""
Server-side per-user undo/redo stack (Valkey, scoped per form).

The last active action is undone; redo replays undone actions in their
original order (the oldest undone one first — the reverse of the undo
sequence). An action is a batch of cell edits made in one PATCH.
Application only happens if, after the action, none of its cells was
changed by another user (rc:cellstate lastUserId/lastActionId) and none
is leased by another (lease). A cell the current user changed last
themselves is never a conflict — undoing one's own consecutive edits
works.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from src.collab.journal import AppliedCell
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE, canonical_key
from src.collab.store import lease_key
from src.raw_materials.service import apply_cell_value
from src.renovation.models import RAsset
from src.renovation.plan import RenovationReferenceValue
from src.renovation.service import apply_renovation_cell_value

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from src.auth.models import User
    from src.collab.store import CollabStore

RENOVATION_TABLES = frozenset(
    {
        RENOVATION_PLAN_ROWS_TABLE,
        RAsset.__tablename__,
        RenovationReferenceValue.__tablename__,
    }
)


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
    store: CollabStore,
    scope: str,
    session_id: str,
    skip_action_id: int | None = None,
) -> UndoOutcome:
    """
    Undoes the user's last action (a PATCH batch) on the session's form scope.

    scope is the form's section and month (section/reportMonth); each form
    has its own history. skip_action_id is the action the user explicitly
    skipped after a conflict (second undo press): it's excluded from the
    search and the next available action below is looked up.
    """
    action = await store.next_action(str(user.id), session_id, scope, undone=False, after_action_id=skip_action_id)
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(
        session, user=user, store=store, scope=scope, session_id=session_id, action=action, redo=False
    )


async def redo_last_undo(
    session: AsyncSession,
    *,
    user: User,
    store: CollabStore,
    scope: str,
    session_id: str,
    skip_action_id: int | None = None,
) -> UndoOutcome:
    """
    Return the user's oldest undone action on the session's form scope.

    Undone actions replay in their original order: the first action that
    was undone is redone first. skip_action_id is the action the user
    explicitly skipped after a conflict (second redo press): it's excluded
    from the search and the next available action above is looked up.
    """
    action = await store.next_action(str(user.id), session_id, scope, undone=True, after_action_id=skip_action_id)
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(
        session, user=user, store=store, scope=scope, session_id=session_id, action=action, redo=True
    )


async def _switch_entry(
    session: AsyncSession,
    *,
    user: User,
    store: CollabStore,
    scope: str,
    session_id: str,
    action: dict,
    redo: bool,
) -> UndoOutcome:
    cells = _action_cells(action)
    project_key = action["project"]
    action_id = int(action["id"])
    # The whole batch is atomic: if any cell conflicts or is leased by
    # someone else, nothing is applied.
    for cell in cells:
        key = canonical_key(cell["targetTable"], UUID(cell["rowId"]), cell["field"])
        conflict = await _check_conflict(store, cell, action_id, key, project_key, user)
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
    await session.commit()
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
    store: CollabStore,
    cell: dict,
    action_id: int,
    key: str,
    project_key: str,
    user: User,
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
    if owner is not None and str(owner.get("userId")) != str(user.id):
        return UndoConflict(
            row_id=UUID(cell["rowId"]),
            field=cell["field"],
            user_name=owner.get("name") or "",
            action_id=action_id,
            reason="locked",
        )
    return None
