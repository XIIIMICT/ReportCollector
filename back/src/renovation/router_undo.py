"""
Undo / redo endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

from fastapi import APIRouter
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.events import publish_cell_changed
from src.collab.persistence import JournalStore
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.renovation.constants import RENOVATION_PROJECT_KEY

router = APIRouter()

from src.renovation.router_common import (  # noqa: F401
    PLAN_EXPORT_RENDER_VERSION,
    PLAN_ROWS_JSON,
    RENOVATION_SECTION,
    AccessDependency,
    AdminDependency,
    RenovationAccess,
    SessionDependency,
    _asset_scope_lookup,
    _dictionary_fingerprint,
    _import_cache_scope,
    _publish_rows_changed,
    _renovation_project_id_or_error,
    _rewind_and_hash_upload,
    _stage_upload,
    _token,
    _upload_too_large,
    _version_read,
    require_renovation_access,
    require_renovation_admin,
)


@router.get("/undo-state", response_model=UndoStateRead)
async def undo_state_cells(
    session: SessionDependency,
    access: AccessDependency,
    session_id: str,
    section: str = RENOVATION_SECTION,
    report_month: str | None = None,
) -> UndoStateRead:
    """Undo/redo availability from the user's session action stack on the plan."""
    scope = f"{section}/{report_month or ''}"
    can_undo, can_redo = await JournalStore(session).undo_state(str(access.user.id), session_id, scope)
    return UndoStateRead(canUndo=can_undo > 0, canRedo=can_redo > 0)


@router.post("/undo", response_model=UndoResponse)
async def undo_cells(
    session: SessionDependency,
    access: AccessDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Undoes the current user's last cell edit on the renovation plan."""
    section = request.section if request is not None else None
    report_month = request.reportMonth if request is not None else None
    scope = f"{section or ''}/{report_month or ''}"
    outcome = await undo_last_edit(
        session,
        user=access.user,
        store=JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(session, outcome, access)


@router.post("/redo", response_model=UndoResponse)
async def redo_cells(
    session: SessionDependency,
    access: AccessDependency,
    session_id: str,
    request: UndoRequest | None = None,
) -> UndoResponse:
    """Return the current user's last undone action on the renovation plan."""
    section = request.section if request is not None else None
    report_month = request.reportMonth if request is not None else None
    scope = f"{section or ''}/{report_month or ''}"
    outcome = await redo_last_undo(
        session,
        user=access.user,
        store=JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(session, outcome, access)


async def _undo_response(session: AsyncSession, outcome: UndoOutcome, access: RenovationAccess) -> UndoResponse:
    if not outcome.applied:
        conflict = outcome.conflict
        return UndoResponse(
            applied=False,
            reason=outcome.reason,
            conflicts=(
                [
                    UndoConflictRead(
                        rowId=conflict.row_id,
                        field=conflict.field,
                        user=conflict.user_name,
                        actionId=conflict.action_id,
                        reason=conflict.reason,
                    )
                ]
                if conflict is not None
                else []
            ),
        )
    cells = outcome.applied_cells
    if outcome.section == RENOVATION_SECTION:
        # Undo recalculates dependent fields as well as the edited field.
        await _publish_rows_changed(session, access)
    if not cells:
        return UndoResponse(applied=True, reason="applied", section=outcome.section, reportMonth=outcome.report_month)
    await publish_cell_changed(
        JournalStore(session),
        project_key=outcome.project_key,
        section=outcome.section,
        report_month=outcome.report_month.isoformat() if outcome.report_month is not None else None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=cells,
    )
    return UndoResponse(
        applied=True,
        reason="applied",
        section=outcome.section,
        reportMonth=outcome.report_month,
        rowId=cells[0].row_id,
        field=cells[0].field,
        value=cells[0].new_value,
        revision=cells[0].revision,
    )
