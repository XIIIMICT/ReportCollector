"""
Серверный стек undo/redo на пользователя (Valkey, не переживает сессию).

Последнее активное действие отменяется, последнее отменённое возвращается.
Применение происходит только если после действия/отмены ячейку не менял
другой пользователь (rc:cellstate lastActionId) и ячейку не арендует другой
(lease).
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from src.collab.journal import AppliedCell
from src.collab.models import canonical_key
from src.collab.store import lease_key
from src.raw_materials.service import apply_cell_value

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from src.auth.models import User
    from src.collab.store import CollabStore


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
    applied_cell: AppliedCell | None = None
    revision: int = 0
    project_key: str = ""
    section: str = ""
    report_month: date | None = None
    applied_value: object = None


async def undo_last_edit(
    session: AsyncSession,
    *,
    user: User,
    store: CollabStore,
    skip_action_id: int | None = None,
) -> UndoOutcome:
    """
    Отменяет последнее действие пользователя (одна ячейка).

    skip_action_id — действие, которое пользователь явно пропустил после
    конфликта (второе нажатие undo): оно исключается из поиска, ищется
    следующее доступное действие ниже.
    """
    action = await store.next_action(str(user.id), undone=False, after_action_id=skip_action_id)
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(session, user=user, store=store, action=action, redo=False)


async def redo_last_undo(
    session: AsyncSession,
    *,
    user: User,
    store: CollabStore,
    skip_action_id: int | None = None,
) -> UndoOutcome:
    """Возвращает последнее отменённое действие пользователя."""
    action = await store.next_action(str(user.id), undone=True, after_action_id=skip_action_id)
    if action is None:
        return UndoOutcome(applied=False, reason="empty")
    return await _switch_entry(session, user=user, store=store, action=action, redo=True)


async def _switch_entry(
    session: AsyncSession,
    *,
    user: User,
    store: CollabStore,
    action: dict,
    redo: bool,
) -> UndoOutcome:
    row_id = UUID(action["rowId"])
    field = action["field"]
    target_table = action["targetTable"]
    project_key = action["project"]
    action_id = int(action["id"])
    key = canonical_key(target_table, row_id, field)
    conflict = await _check_conflict(store, action, key, project_key, user)
    if conflict is not None:
        return UndoOutcome(applied=False, reason=conflict.reason, conflict=conflict)

    value = action["newValue"] if redo else action["oldValue"]
    await apply_cell_value(
        session,
        target_table=target_table,
        row_id=row_id,
        field=field,
        value=value,
        user=user,
    )
    await store.mark_action_undone(str(user.id), action_id, undone=not redo)
    revision = await store.bump_cell_state(
        key,
        user_id=str(user.id),
        user_name=user.name or "",
        action_id=action_id,
    )
    await session.commit()
    return UndoOutcome(
        applied=True,
        reason="applied",
        applied_cell=AppliedCell(
            target_table=target_table,
            row_id=row_id,
            field=field,
            old_value=action["oldValue"],
            new_value=action["newValue"],
            revision=revision,
            action_id=action_id,
        ),
        revision=revision,
        project_key=project_key,
        section=action["section"],
        report_month=_parse_month(action["reportMonth"]),
        applied_value=value,
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
    action: dict,
    key: str,
    project_key: str,
    user: User,
) -> UndoConflict | None:
    state = await store.get_cell_state(key)
    if state is not None and state.get("lastActionId") != str(action["id"]):
        return UndoConflict(
            row_id=UUID(action["rowId"]),
            field=action["field"],
            user_name=state.get("lastUserName") or "",
            action_id=int(action["id"]),
            reason="conflict",
        )
    owner = await store.get_lease(lease_key(key, project=project_key))
    if owner is not None and str(owner.get("userId")) != str(user.id):
        return UndoConflict(
            row_id=UUID(action["rowId"]),
            field=action["field"],
            user_name=owner.get("name") or "",
            action_id=int(action["id"]),
            reason="locked",
        )
    return None
