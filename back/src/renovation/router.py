"""«Ремонт» project API: reading/editing the renovation plan, plan lifecycle, undo/redo."""

from io import BytesIO
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from src.auth.models import User
    from src.renovation.models import RBlock, RProjectRole

from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.collab.events import publish_cell_changed, publish_rows_changed
from src.collab.journal import AppliedCell
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.store import get_collab_store
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.database import get_session
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.export import render_renovation_workbook
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.schemas import (
    RenovationApproveRead,
    RenovationDiffRead,
    RenovationMetaRead,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanVersionRead,
    RenovationRowAddRequest,
    RenovationUpdateRead,
    RenovationUpdateRequest,
)
from src.renovation.service import (
    CellValidationError,
    NoRenovationAccessError,
    RenovationPermissionError,
    RenovationPlanNotFoundError,
    RenovationPlanStateError,
    add_plan_row,
    approve_plan,
    delete_plan_row,
    read_meta,
    read_plan,
    read_plan_diff,
    read_plan_versions,
    resolve_renovation_access,
    submit_plan,
    update_plan_cells,
)

router = APIRouter(prefix="/renovation", tags=["renovation"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
IdentityDependency = Annotated[CurrentUserIdentity, Depends(read_signed_identity)]

RENOVATION_SECTION = "renovationPlan"


class RenovationAccess:
    """Resolved access of the current user to the «Ремонт» project."""

    def __init__(
        self,
        *,
        user: User,
        roles: list[RProjectRole],
        assets: list,
        block: RBlock | None,
    ) -> None:
        self.user = user
        self.roles = roles
        self.assets = assets
        self.block = block


async def require_renovation_access(
    session: SessionDependency,
    identity: IdentityDependency,
) -> RenovationAccess:
    """Resolve the user's renovation access; 403 without access to the project."""
    try:
        user, roles, assets, block = await resolve_renovation_access(session, identity)
    except NoRenovationAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к проекту «Ремонт».",
        ) from error
    return RenovationAccess(user=user, roles=roles, assets=assets, block=block)


AccessDependency = Annotated[RenovationAccess, Depends(require_renovation_access)]


@router.get("/meta", response_model=RenovationMetaRead)
async def get_renovation_meta(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationMetaRead:
    """Form dictionaries: assets, reference catalogs, field descriptions."""
    return await read_meta(session, access.assets)


@router.get("/plan", response_model=RenovationPlanRead)
async def get_renovation_plan(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
) -> RenovationPlanRead:
    """Return a renovation plan version; default — the working plan."""
    try:
        return await read_plan(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/plans", response_model=list[RenovationPlanVersionRead])
async def get_renovation_plans(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[RenovationPlanVersionRead]:
    """All renovation plan versions (newest first) with row counts."""
    return await read_plan_versions(session)


@router.patch("/plan", response_model=RenovationUpdateRead)
async def patch_renovation_plan(
    request: RenovationUpdateRequest,
    session: SessionDependency,
    access: AccessDependency,
    session_id: str,
) -> RenovationUpdateRead:
    """Batch of working-plan cell edits (with recalculation and renovation ID)."""
    try:
        applied, calculated, renovation_ids = await update_plan_cells(
            session,
            updates=request.updates,
            user=access.user,
            roles=access.roles,
            assets=access.assets,
            block=access.block,
            session_id=session_id,
        )
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    cells = list(applied)
    cells.extend(
        AppliedCell(
            target_table=RENOVATION_PLAN_ROWS_TABLE,
            row_id=cell.rowId,
            field=cell.field,
            old_value=None,
            new_value=cell.value,
            revision=0,
            action_id=0,
        )
        for cell in calculated
    )
    await publish_cell_changed(
        get_collab_store(),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=cells,
    )
    return RenovationUpdateRead(applied=len(applied), calculated=calculated, renovationIds=renovation_ids)


@router.post("/plan/rows", response_model=RenovationPlanRowRead)
async def post_renovation_row(
    request: RenovationRowAddRequest,
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationPlanRowRead:
    """Append a new working-plan row (any project user; the asset is required)."""
    try:
        row = await add_plan_row(
            session,
            user=access.user,
            assets=access.assets,
            asset_id=request.assetId,
        )
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _publish_rows_changed(access)
    return row


@router.delete("/plan/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_renovation_row(
    row_id: UUID,
    session: SessionDependency,
    access: AccessDependency,
) -> None:
    """Delete a working-plan row (rows of the approved baseline are protected)."""
    try:
        await delete_plan_row(session, assets=access.assets, row_id=row_id)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _publish_rows_changed(access)


@router.get("/plan/diff", response_model=RenovationDiffRead)
async def get_renovation_diff(
    session: SessionDependency,
    _access: AccessDependency,
    plan_id: Annotated[UUID, Query()],
) -> RenovationDiffRead:
    """Cell diff of an archived version vs the previous archived version."""
    try:
        return await read_plan_diff(session, plan_id=plan_id)
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/plan/export")
async def export_renovation_plan(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
) -> StreamingResponse:
    """Excel export of a plan version (default — the working plan)."""
    try:
        plan = await read_plan(session, access.assets, plan_id=plan_id, state=RPlanState.WORKING)
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    content = render_renovation_workbook(plan)
    filename = f"remont_plan_v{plan.baselineGeneration}_{plan.updatedAt:%Y-%m-%d}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/plan/submit", response_model=RenovationPlanVersionRead)
async def post_renovation_submit(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationPlanVersionRead:
    """Send the working plan for approval (admin or a block owner)."""
    try:
        plan = await submit_plan(session, user=access.user, roles=access.roles)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _publish_rows_changed(access)
    return await _version_read(session, plan)


@router.post("/plan/approve", response_model=RenovationApproveRead)
async def post_renovation_approve(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationApproveRead:
    """Approve the pending plan (admin or approver): archive + new working copy."""
    try:
        archived, working = await approve_plan(session, user=access.user, roles=access.roles)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _publish_rows_changed(access)
    return RenovationApproveRead(
        archived=await _version_read(session, archived),
        working=await _version_read(session, working),
    )


async def _publish_rows_changed(access: RenovationAccess) -> None:
    await publish_rows_changed(
        get_collab_store(),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )


async def _version_read(session: AsyncSession, plan: RenovationPlan) -> RenovationPlanVersionRead:
    row_count = (
        await session.exec(
            select(func.count()).select_from(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id)
        )
    ).one()
    return RenovationPlanVersionRead(
        id=plan.id,
        state=plan.state.value,
        baselineGeneration=plan.baseline_generation,
        rowCount=int(row_count),
        createdAt=plan.created_at,
        createdBy=plan.created_by,
        updatedAt=plan.updated_at,
        updatedBy=plan.updated_by,
    )


@router.get("/undo-state", response_model=UndoStateRead)
async def undo_state_cells(
    access: AccessDependency,
    session_id: str,
    section: str = RENOVATION_SECTION,
    report_month: str | None = None,
) -> UndoStateRead:
    """Undo/redo availability from the user's session action stack on the plan."""
    scope = f"{section}/{report_month or ''}"
    can_undo, can_redo = await get_collab_store().undo_state(str(access.user.id), session_id, scope)
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
        store=get_collab_store(),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(outcome, access)


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
        store=get_collab_store(),
        scope=scope,
        session_id=session_id,
        skip_action_id=request.skipActionId if request is not None else None,
    )
    return await _undo_response(outcome, access)


async def _undo_response(outcome: UndoOutcome, access: RenovationAccess) -> UndoResponse:
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
    cell = outcome.applied_cell
    if cell is None:
        return UndoResponse(applied=True, reason="applied", section=outcome.section, reportMonth=outcome.report_month)
    await publish_cell_changed(
        get_collab_store(),
        project_key=outcome.project_key,
        section=outcome.section,
        report_month=outcome.report_month.isoformat() if outcome.report_month is not None else None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=[
            AppliedCell(
                target_table=cell.target_table,
                row_id=cell.row_id,
                field=cell.field,
                old_value=cell.old_value,
                new_value=outcome.applied_value,
                revision=cell.revision,
                action_id=cell.action_id,
            )
        ],
    )
    return UndoResponse(
        applied=True,
        reason="applied",
        section=outcome.section,
        reportMonth=outcome.report_month,
        rowId=cell.row_id,
        field=cell.field,
        value=outcome.applied_value,
        revision=cell.revision,
    )
