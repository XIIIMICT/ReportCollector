"""
Серверный журнал правок: стек undo/redo в Valkey (rc:undo:{user}).

Каждое действие — запись о правке одной ячейки. Правка (PATCH) пушит
действие в стек пользователя и обновляет состояние ячейки (rc:cellstate:*).
Undo/redo переключает флаг undone у того же действия, не плодя новые.
Новое действие пользователя очищает его redo-ветку; новый WebSocket-клиент
(перезагрузка страницы) очищает весь стек — история не переживает сессию.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from src.collab.models import canonical_key

if TYPE_CHECKING:
    from src.collab.store import CollabStore


@dataclass
class AppliedCell:
    """Применённая правка одной ячейки (собирается до записи журнала)."""

    target_table: str
    row_id: UUID
    field: str
    old_value: object
    new_value: object
    revision: int = 0
    action_id: int = 0

    @property
    def canonical(self) -> str:
        return canonical_key(self.target_table, self.row_id, self.field)


async def record_cell_updates(
    store: CollabStore,
    *,
    user_id: UUID,
    user_name: str,
    project_key: str,
    section: str,
    report_month: date | None,
    applied: list[AppliedCell],
) -> None:
    """Пушит действия в стек undo/redo пользователя (на форме section) и бампает состояния ячеек."""
    if not applied:
        return
    # История undo/redo своя на каждую форму (секция + месяц), чтобы с одной
    # формы нельзя было отменить правки, сделанные на другой.
    scope = f"{section}/{report_month or ''}"
    await store.clear_redo(str(user_id), scope)
    actions: list[dict] = []
    for cell in applied:
        action: dict = {
            "id": 0,
            "project": project_key,
            "section": section,
            "reportMonth": report_month.isoformat() if report_month is not None else None,
            "targetTable": cell.target_table,
            "rowId": str(cell.row_id),
            "field": cell.field,
            "oldValue": cell.old_value,
            "newValue": cell.new_value,
            "revision": 0,
            "undone": False,
        }
        actions.append(action)
    for cell, action in zip(applied, actions, strict=True):
        action["id"] = await store.next_action_id()
        cell.action_id = action["id"]
        cell.revision = await store.bump_cell_state(
            cell.canonical,
            user_id=str(user_id),
            user_name=user_name,
            action_id=action["id"],
        )
        action["revision"] = cell.revision
    await store.push_actions(str(user_id), scope, actions)
