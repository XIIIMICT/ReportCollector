"""
Server-side edit journal: the undo/redo stack in Valkey (rc:undo:{user}:{session}:{scope}).

Each action is a record of one PATCH request (a batch of cell edits —
`cells: [...]`). A PATCH pushes a single action onto the user's stack
(scoped per browser session and form: section/reportMonth) and bumps the
cell state (rc:cellstate:*) of every edited cell. Undo/redo toggles the
undone flag on the same action, without creating new ones. A new edit by
the user clears their redo branch; the stack survives form switches within
the session, is forgotten on page reload (a new session id starts empty)
and is cleared for the fixed table by a final month fix.
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
    """Applied single-cell edit (collected before journaling)."""

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
    session_id: str,
    applied: list[AppliedCell],
) -> None:
    """Pushes a single batch action (all cells of one PATCH) onto the session undo/redo stack and bumps cell states."""
    if not applied:
        return
    # Undo/redo history is per browser session and form (section + month):
    # edits made on one form can't be undone from another, and a page reload
    # (a new session id) starts with an empty history.
    scope = f"{section}/{report_month or ''}"
    await store.clear_redo(str(user_id), session_id, scope)
    action_id = await store.next_action_id()
    cells: list[dict] = []
    for cell in applied:
        cell.action_id = action_id
        cell.revision = await store.bump_cell_state(
            cell.canonical,
            user_id=str(user_id),
            user_name=user_name,
            action_id=action_id,
        )
        cells.append(
            {
                "targetTable": cell.target_table,
                "rowId": str(cell.row_id),
                "field": cell.field,
                "oldValue": cell.old_value,
                "newValue": cell.new_value,
                "revision": cell.revision,
            }
        )
    action: dict = {
        "id": action_id,
        "project": project_key,
        "section": section,
        "reportMonth": report_month.isoformat() if report_month is not None else None,
        "cells": cells,
        "undone": False,
    }
    await store.push_actions(str(user_id), session_id, scope, [action])
