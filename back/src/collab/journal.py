"""
Server-side edit journal in PostgreSQL, committed atomically with business rows.

Each action is a record of one PATCH request (a batch of cell edits —
`cells: [...]`). A PATCH pushes a single action onto the user's stack
(scoped per browser session and form: section/reportMonth) and bumps the
cell state (collab_cell_states) of every edited cell. Undo/redo toggles the
undone flag on the same action, without creating new ones. A new edit by
the user clears their redo branch; the stack survives form switches within
the session, is forgotten on page reload (a new session id starts empty)
and is cleared for the fixed table by a final month fix.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException

from src.collab.models import canonical_key
from src.collab.store import lease_key

if TYPE_CHECKING:
    from src.collab.persistence import JournalStore


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
    store: JournalStore,
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
    # Dictionary routes use this same guard after applying their tentative SQL
    # changes; an exception rolls the entire request transaction back.
    for owner in await store.get_leases([lease_key(cell.canonical, project=project_key) for cell in applied]):
        if owner is not None and (
            str(owner.get("userId")) != str(user_id) or owner.get("sessionId", session_id) != session_id
        ):
            raise HTTPException(status_code=409, detail="Ячейка редактируется в другой сессии.")
    # Repeated edits of one cell in a batch undo to its first old value.
    merged: dict[str, AppliedCell] = {}
    for cell in applied:
        if cell.canonical in merged:
            first = merged[cell.canonical]
            cell.old_value = first.old_value
        merged[cell.canonical] = cell
    applied[:] = merged.values()
    # Undo/redo history is per browser session and form (section + month):
    # edits made on one form can't be undone from another, and a page reload
    # (a new session id) starts with an empty history.
    scope = f"{section}/{report_month or ''}"
    await store.clear_redo(str(user_id), session_id, scope)
    action_id = await store.next_action_id()
    revisions = await store.bump_cell_states(
        [cell.canonical for cell in applied], user_id=str(user_id), user_name=user_name, action_id=action_id
    )
    cells: list[dict] = []
    for cell in applied:
        cell.action_id = action_id
        cell.revision = revisions[cell.canonical]
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
