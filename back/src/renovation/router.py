"""«Ремонт» project API: reading/editing the renovation plan, plan lifecycle, undo/redo."""

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from src.auth.models import User
    from src.renovation.models import RBlock, RProjectRole

from src.auth.models import Project
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.collab.events import publish_cell_changed, publish_rows_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.persistence import JournalStore
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.common.export import safe_sheet_name
from src.common.export_jobs import ExportJobRead, export_table_or_job, table_export_jobs
from src.database import get_session
from src.downloads import XLSX_MEDIA_TYPE
from src.renovation.assets import (
    create_renovation_asset,
    delete_renovation_asset,
    read_renovation_asset_rows,
    update_renovation_asset,
)
from src.renovation.catalogs import (
    create_renovation_catalog_option,
    delete_renovation_catalog_option,
    read_renovation_catalog_rows,
    update_renovation_catalog_option,
)
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.errors import RenovationDictionaryError
from src.renovation.export import (
    REFERENCE_LISTS,
    ExportValidationError,
    plan_export_filename,
    section_sheet_name,
    stream_renovation_workbook,
    validate_export_request,
)
from src.renovation.export_jobs import ExportJob, ExportJobSpec, ExportQueueFullError, export_jobs
from src.renovation.models import RAsset, RProjectRole, RUserRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.schemas import (
    RenovationApproveRead,
    RenovationAssetCreateRequest,
    RenovationAssetRowRead,
    RenovationAssetUpdateRequest,
    RenovationCatalogOptionCreateRequest,
    RenovationCatalogOptionRowRead,
    RenovationCatalogOptionUpdateRequest,
    RenovationColumnFilter,
    RenovationDictionaryBatchRequest,
    RenovationDiffRead,
    RenovationExportJobRead,
    RenovationExportViewRequest,
    RenovationFacetsRead,
    RenovationMetaRead,
    RenovationPlanExportRequest,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanRowsRead,
    RenovationPlanSummaryRead,
    RenovationPlanVersionRead,
    RenovationRowAssignRequest,
    RenovationRowCreateRequest,
    RenovationUpdateRead,
    RenovationUpdateRequest,
    RenovationUserCreateRequest,
    RenovationUserRowRead,
    RenovationUserUpdateRequest,
)
from src.renovation.service import (
    CellValidationError,
    NoRenovationAccessError,
    RenovationPermissionError,
    RenovationPlanExportData,
    RenovationPlanNotFoundError,
    RenovationPlanStateError,
    add_plan_row,
    approve_plan,
    assign_plan_row_asset,
    delete_plan_row,
    read_meta,
    read_plan,
    read_plan_diff,
    read_plan_export_data,
    read_plan_export_meta,
    read_plan_facets,
    read_plan_rows,
    read_plan_summary,
    read_plan_versions,
    resolve_renovation_access,
    submit_plan,
    update_plan_cells,
)
from src.renovation.users import (
    create_renovation_user,
    delete_renovation_user,
    read_renovation_user_rows,
    update_renovation_user,
)

router = APIRouter(prefix="/renovation", tags=["renovation"])

SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
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


async def require_renovation_admin(access: AccessDependency) -> RenovationAccess:
    """Require the project admin role for dictionary mutations."""
    if RProjectRole.ADMIN not in access.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора проекта «Ремонт».",
        )
    return access


AdminDependency = Annotated[RenovationAccess, Depends(require_renovation_admin)]


@router.get("/meta", response_model=RenovationMetaRead)
async def get_renovation_meta(
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationMetaRead:
    """Form dictionaries: assets, reference catalogs, field descriptions."""
    return await read_meta(session, access.assets, user_block=access.block, user_roles=access.roles)


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


@router.get("/plan/rows", response_model=RenovationPlanRowsRead)
async def get_renovation_plan_rows(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    asset: Annotated[str | None, Query()] = None,
    filters: Annotated[str | None, Query()] = None,
    position_from: Annotated[int | None, Query(ge=1)] = None,
    position_to: Annotated[int | None, Query(ge=1)] = None,
    anchor_position: Annotated[int | None, Query(ge=1)] = None,
) -> RenovationPlanRowsRead:
    """
    Return a window of renovation plan rows plus the total (lazy loading).

    `filters` is a JSON array of `{column, values, includeNulls}` (the header
    filters) — combined with AND and applied in SQL so the window is correct.
    `position_from`/`position_to` narrow the window to a row-number («№») range.
    `anchor_position` resolves a stored row position onto the filtered scope
    (`anchorOffset` in the response) — the client centres the same row after a
    reload or a filter change.
    """
    try:
        return await read_plan_rows(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
            offset=offset,
            limit=limit,
            asset_name=asset,
            filters=parse_column_filters(filters),
            position_from=position_from,
            position_to=position_to,
            anchor_position=anchor_position,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/plan/summary", response_model=RenovationPlanSummaryRead)
async def get_renovation_plan_summary(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    filters: Annotated[str | None, Query()] = None,
    position_from: Annotated[int | None, Query(ge=1)] = None,
    position_to: Annotated[int | None, Query(ge=1)] = None,
) -> RenovationPlanSummaryRead:
    """
    Sum every numeric plan field over the same scope as `GET /plan/rows`.

    Powers the pinned summary row: the totals follow the asset, header and
    row-number filters, so they match the rows currently shown.
    """
    try:
        values = await read_plan_summary(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
            asset_name=asset,
            filters=parse_column_filters(filters),
            position_from=position_from,
            position_to=position_to,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return RenovationPlanSummaryRead(values=values)


def parse_column_filters(raw: str | None) -> list[RenovationColumnFilter]:
    """Parse the `filters` query param (JSON array); empty/invalid → no filters."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        message = "Некорректный параметр фильтров."
        raise CellValidationError(message) from error
    if not isinstance(payload, list):
        message = "Некорректный параметр фильтров."
        raise CellValidationError(message)
    return [RenovationColumnFilter.model_validate(item) for item in payload]


@router.get("/plans", response_model=list[RenovationPlanVersionRead])
async def get_renovation_plans(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[RenovationPlanVersionRead]:
    """All renovation plan versions (newest first) with row counts."""
    return await read_plan_versions(session)


@router.get("/plan/facets", response_model=RenovationFacetsRead)
async def get_renovation_plan_facets(
    session: SessionDependency,
    access: AccessDependency,
    column: Annotated[str, Query(min_length=1)],
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> RenovationFacetsRead:
    """Distinct display values of one plan column (the header-filter options)."""
    try:
        return await read_plan_facets(
            session,
            access.assets,
            column=column,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
            asset_name=asset,
            offset=offset,
            limit=limit,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


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
    # The renovation ID (col_003) is derived too, but it is NOT part of
    # `calculated` (the formula engine doesn't own it) — publish it explicitly
    # so other clients update the code live. The editor refreshes its window
    # from the PATCH response; everyone else only gets cell:changed.
    cells.extend(
        AppliedCell(
            target_table=RENOVATION_PLAN_ROWS_TABLE,
            row_id=renovation_id.rowId,
            field="col_003",
            old_value=None,
            new_value=renovation_id.value,
            revision=0,
            action_id=0,
        )
        for renovation_id in renovation_ids
        if renovation_id.changed
    )
    await publish_cell_changed(
        JournalStore(session),
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
    session: SessionDependency,
    access: AccessDependency,
    request: RenovationRowCreateRequest | None = None,
    session_id: str = "",
) -> RenovationPlanRowRead:
    """Append an empty plan row (asset pre-filled from the request or for single-asset workers)."""
    try:
        row, applied = await add_plan_row(
            session,
            user=access.user,
            roles=access.roles,
            block=access.block,
            assets=access.assets,
            asset_id=request.assetId if request is not None else None,
        )
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _journal_plan_row(session, access, session_id, [applied])
    await _publish_rows_changed(session, access)
    return row


@router.patch("/plan/rows/{row_id}", response_model=RenovationPlanRowRead)
async def patch_renovation_row_asset(
    row_id: UUID,
    request: RenovationRowAssignRequest,
    session: SessionDependency,
    access: AccessDependency,
    session_id: str = "",
) -> RenovationPlanRowRead:
    """Point an asset-less draft row at an asset (asset assignment)."""
    try:
        row, applied = await assign_plan_row_asset(
            session,
            user=access.user,
            roles=access.roles,
            block=access.block,
            assets=access.assets,
            row_id=row_id,
            asset_id=request.assetId,
        )
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _journal_plan_row(session, access, session_id, [applied])
    await _publish_rows_changed(session, access)
    return row


@router.delete("/plan/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_renovation_row(
    row_id: UUID,
    session: SessionDependency,
    access: AccessDependency,
    session_id: str = "",
) -> None:
    """Delete a working-plan row (rows of the approved baseline are protected)."""
    try:
        applied = await delete_plan_row(session, assets=access.assets, roles=access.roles, row_id=row_id)
    except RenovationPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _journal_plan_row(session, access, session_id, applied)
    await _publish_rows_changed(session, access)


async def _journal_plan_row(
    session: AsyncSession,
    access: RenovationAccess,
    session_id: str,
    applied: list[AppliedCell],
) -> None:
    """
    Journal a plan row lifecycle change (add / asset assignment / delete).

    Without a session id nothing is journaled (page-lifetime history, like the
    cell edits); the room is notified by the caller's `rows:changed` anyway.
    """
    if not session_id or not applied:
        return
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )


@router.get("/plan/diff", response_model=RenovationDiffRead)
async def get_renovation_diff(
    session: SessionDependency,
    _access: AccessDependency,
    plan_id: Annotated[UUID, Query()],
) -> RenovationDiffRead:
    """Cell diff of an archived version vs the previous archived version."""
    try:
        return await read_plan_diff(session, plan_id=plan_id, assets=_access.assets)
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/plan/export")
async def export_renovation_plan(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """Excel export of a plan version (default — the working plan)."""
    try:
        plan = await read_plan_export_data(session, access.assets, plan_id=plan_id, state=RPlanState.WORKING)
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return await _plan_export_response(plan)


@router.post("/plan/export")
async def post_export_renovation_plan(
    session: SessionDependency,
    access: AccessDependency,
    payload: RenovationPlanExportRequest,
) -> Response:
    """Excel export of a plan version: all rows, an exact set, or the filtered view."""
    try:
        plan = await read_plan_export_data(
            session,
            access.assets,
            plan_id=payload.planId,
            state=RPlanState.WORKING,
            row_ids=payload.rowIds,
            asset_name=payload.asset,
            block=payload.block,
            filters=payload.filters,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return await _plan_export_response(plan)


async def _plan_export_response(plan: RenovationPlanExportData) -> StreamingResponse:
    """Stream a plan workbook so nginx receives data before its read timeout."""
    filename = plan_export_filename(plan.state, plan.stamp)
    quoted = quote(filename)
    return StreamingResponse(
        stream_renovation_workbook(plan),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )


def _job_read(job: ExportJob) -> RenovationExportJobRead:
    return RenovationExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
    )


@router.post(
    "/plan/export/jobs",
    response_model=RenovationExportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_plan_export_job(
    payload: RenovationPlanExportRequest,
    session: SessionDependency,
    access: AccessDependency,
) -> RenovationExportJobRead:
    """
    Start (or reuse) a background export of a plan version.

    The full plan is minutes of CPU and hundreds of MB, so it is generated by a
    single sequential worker; the response is immediate and the client polls
    `GET /plan/export/jobs/{id}` then downloads `/file`. An identical request
    (same plan, filters and last-edit stamp) reuses the ready file.
    """
    asset_ids = [asset.id for asset in access.assets]
    if payload.asset is not None:
        asset_ids = [asset.id for asset in access.assets if asset.name == payload.asset]
    try:
        meta = await read_plan_export_meta(
            session,
            asset_ids,
            plan_id=payload.planId,
            state=RPlanState.WORKING,
            row_ids=payload.rowIds,
            block=payload.block,
            filters=payload.filters,
            include_unassigned=payload.asset is None,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    file_name = plan_export_filename(meta.state, meta.stamp)
    cache_key = hashlib.sha256(
        json.dumps(
            [
                str(meta.plan_id),
                meta.state.value,
                sorted(str(asset_id) for asset_id in asset_ids),
                sorted(str(row_id) for row_id in payload.rowIds or []),
                payload.block.value if payload.block is not None else None,
                [item.model_dump() for item in payload.filters or []],
                meta.stamp.isoformat(),
            ],
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    spec = ExportJobSpec(
        plan_id=meta.plan_id,
        state=meta.state,
        asset_ids=asset_ids,
        row_ids=payload.rowIds,
        block=payload.block,
        filters=payload.filters,
        include_unassigned=payload.asset is None,
        file_name=file_name,
        total=meta.total,
        cache_key=cache_key,
    )
    try:
        job = await export_jobs.create(spec, str(access.user.id))
    except ExportQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    return _job_read(job)


@router.get("/plan/export/jobs/{job_id}", response_model=RenovationExportJobRead)
async def get_plan_export_job(
    job_id: str,
    access: AccessDependency,
) -> RenovationExportJobRead:
    """Status of a background plan export (progress / done / failed)."""
    try:
        job = export_jobs.get(job_id, str(access.user.id))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание выгрузки не найдено.") from error
    return _job_read(job)


@router.get("/plan/export/jobs/{job_id}/file")
async def get_plan_export_job_file(
    job_id: str,
    access: AccessDependency,
) -> StreamingResponse:
    """
    Stream the XLSX of a background plan export AS it is being generated.

    The browser downloads this URL directly (a native download), so its own save
    dialog and progress UI open immediately. The response streams the growing
    `.part` file the background worker writes, so the download progress advances
    from the first byte instead of staying silent for the whole generation (minutes
    for a 300k-row plan). There is no Content-Length (the size is unknown until
    generation ends), so the browser shows downloaded bytes rather than a percent -
    but the bar moves continuously. Reloading or closing the tab does not stop the
    generation (it runs in the worker) or the browser-owned download.
    """
    try:
        job = export_jobs.get(job_id, str(access.user.id))
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задание выгрузки не найдено.",
        ) from error
    if job.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Не удалось сформировать файл выгрузки.",
        )
    filename = job.spec.file_name
    quoted = quote(filename)
    return StreamingResponse(
        export_jobs.stream_growing(job_id, str(access.user.id)),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
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
    except RenovationPlanStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _publish_rows_changed(session, access)
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
    await _publish_rows_changed(session, access)
    return RenovationApproveRead(
        archived=await _version_read(session, archived),
        working=await _version_read(session, working),
    )


# ─── Editable reference dictionaries (project admin only) ───────────────

RENOVATION_ASSETS_SECTION = "renovationAssets"
RENOVATION_USERS_SECTION = "renovationUsers"


def _catalog_section(catalog: str) -> str:
    return f"renovationCatalog:{catalog}"


@router.get("/dictionary/assets", response_model=list[RenovationAssetRowRead])
async def get_renovation_assets(
    session: SessionDependency,
    _access: AdminDependency,
) -> list[RenovationAssetRowRead]:
    """«Активы» dictionary of the renovation project."""
    return await read_renovation_asset_rows(session)


@router.post("/dictionary/assets", response_model=RenovationAssetRowRead, status_code=status.HTTP_201_CREATED)
async def post_renovation_asset(
    request: RenovationAssetCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> RenovationAssetRowRead:
    """Renovation asset creation (project admin only); journaled as an undoable «__created» record."""
    try:
        created, row = await create_renovation_asset(
            session,
            name=request.name,
            repair_code=request.repairCode,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=RENOVATION_PROJECT_KEY,
            section=RENOVATION_ASSETS_SECTION,
            report_month=None,
            session_id=session_id,
            applied=[
                AppliedCell(
                    target_table=RAsset.__tablename__,
                    row_id=created.id,
                    field="__created",
                    old_value=None,
                    new_value={
                        "code": created.code,
                        "name": created.name,
                        "repairCode": created.repair_code,
                    },
                )
            ],
        )
    return row


@router.patch("/dictionary/assets/{asset_id}", response_model=RenovationAssetRowRead)
async def patch_renovation_asset(
    asset_id: UUID,
    request: RenovationAssetUpdateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> RenovationAssetRowRead:
    """Renovation asset edit (project admin only)."""
    try:
        updated, applied = await update_renovation_asset(
            session,
            asset_id=asset_id,
            name=request.name,
            repair_code=request.repairCode,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_ASSETS_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await publish_cell_changed(
        JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_ASSETS_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    return (await read_renovation_asset_rows(session, asset_id=updated.id))[0]


@router.delete("/dictionary/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_renovation_asset_endpoint(
    asset_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> None:
    """Renovation asset deletion (project admin only, undoable)."""
    try:
        applied = await delete_renovation_asset(session, asset_id=asset_id)
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_ASSETS_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )


@router.get("/dictionary/catalogs/{catalog}", response_model=list[RenovationCatalogOptionRowRead])
async def get_renovation_catalog_options(
    catalog: str,
    session: SessionDependency,
    _access: AdminDependency,
) -> list[RenovationCatalogOptionRowRead]:
    """Options of a renovation reference catalog."""
    return await read_renovation_catalog_rows(session, catalog=catalog)


@router.post(
    "/dictionary/catalogs/{catalog}/options",
    response_model=RenovationCatalogOptionRowRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_renovation_catalog_option(
    catalog: str,
    request: RenovationCatalogOptionCreateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> RenovationCatalogOptionRowRead:
    """Catalog option creation (project admin only); journaled as an undoable «__created» record."""
    try:
        created, row = await create_renovation_catalog_option(
            session,
            catalog=catalog,
            label=request.label,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=RENOVATION_PROJECT_KEY,
            section=_catalog_section(catalog),
            report_month=None,
            session_id=session_id,
            applied=[
                AppliedCell(
                    target_table=RenovationReferenceValue.__tablename__,
                    row_id=created.id,
                    field="__created",
                    old_value=None,
                    new_value={
                        "catalog": created.catalog.value,
                        "valueKey": created.value_key,
                        "label": created.label,
                        "sortOrder": created.sort_order,
                        "payload": created.payload,
                    },
                )
            ],
        )
    return row


@router.patch(
    "/dictionary/catalogs/{catalog}/options/{option_id}",
    response_model=RenovationCatalogOptionRowRead,
)
async def patch_renovation_catalog_option(
    catalog: str,
    option_id: UUID,
    request: RenovationCatalogOptionUpdateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> RenovationCatalogOptionRowRead:
    """Catalog option edit (project admin only)."""
    try:
        updated, applied = await update_renovation_catalog_option(
            session,
            catalog=catalog,
            option_id=option_id,
            label=request.label,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    section = _catalog_section(catalog)
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=section,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await publish_cell_changed(
        JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        section=section,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    return (await read_renovation_catalog_rows(session, catalog=catalog, option_id=updated.id))[0]


@router.delete("/dictionary/catalogs/{catalog}/options/{option_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_renovation_catalog_option_endpoint(
    catalog: str,
    option_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> None:
    """Catalog option deletion (project admin only, undoable)."""
    try:
        applied = await delete_renovation_catalog_option(session, catalog=catalog, option_id=option_id)
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=_catalog_section(catalog),
        report_month=None,
        session_id=session_id,
        applied=applied,
    )


@router.get("/dictionary/users", response_model=list[RenovationUserRowRead])
async def get_renovation_users(
    session: SessionDependency,
    _access: AdminDependency,
) -> list[RenovationUserRowRead]:
    """«Пользователи» dictionary of the renovation project."""
    return await read_renovation_user_rows(session)


@router.post("/dictionary/users", response_model=RenovationUserRowRead, status_code=status.HTTP_201_CREATED)
async def post_renovation_user(
    request: RenovationUserCreateRequest,
    session: SessionDependency,
    _access: AdminDependency,
) -> RenovationUserRowRead:
    """Renovation project user creation (project admin only)."""
    project_id = await _renovation_project_id_or_error(session)
    try:
        created = await create_renovation_user(
            session,
            project_id=project_id,
            email=request.email,
            fullname=request.fullname,
            role=request.role,
            block=request.block,
            asset_names=request.assetNames,
            updater_user_id=_access.user.id,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return (await read_renovation_user_rows(session, user_id=created.id))[0]


@router.patch("/dictionary/users/{user_id}", response_model=RenovationUserRowRead)
async def patch_renovation_user(
    user_id: UUID,
    request: RenovationUserUpdateRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> RenovationUserRowRead:
    """Renovation project user edit (project admin only)."""
    if user_id == access.user.id:
        raise HTTPException(
            status_code=409, detail="Нельзя редактировать собственную запись в справочнике пользователей."
        )
    project_id = await _renovation_project_id_or_error(session)
    try:
        # `block` is only sent when the client explicitly sets it (null = clear);
        # omitting the kwarg keeps the current value.
        block_kwargs: dict[str, RBlock | None] = {}
        if "block" in request.model_fields_set:
            block_kwargs["block"] = request.block
        _, applied = await update_renovation_user(
            session,
            project_id=project_id,
            user_id=user_id,
            fullname=request.fullname,
            role=request.role,
            asset_names=request.assetNames,
            is_active=request.isActive,
            updater_user_id=access.user.id,
            **block_kwargs,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_USERS_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await publish_cell_changed(
        JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_USERS_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    return (await read_renovation_user_rows(session, user_id=user_id))[0]


@router.delete("/dictionary/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_renovation_user_endpoint(
    user_id: UUID,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str = "",
) -> None:
    """
    Remove a user from the «Ремонт» project (project admin only, undoable).

    The account is soft-deleted when no project link is left, so the person
    really disappears from every «Пользователи» list. An admin cannot remove
    themselves — the last admin would otherwise lock the project out.
    """
    if user_id == access.user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя удалить себя из проекта.",
        )
    project_id = await _renovation_project_id_or_error(session)
    link = await session.get(RUserRole, (user_id, project_id))
    if link is not None and link.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала снимите доступ пользователя к проекту, затем удаляйте.",
        )
    try:
        applied = await delete_renovation_user(session, project_id=project_id, user_id=user_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователя нельзя удалить: на нём зафиксированные периоды или другие данные.",
        ) from error
    if session_id:
        await record_cell_updates(
            JournalStore(session),
            user_id=access.user.id,
            user_name=access.user.name or "",
            project_key=RENOVATION_PROJECT_KEY,
            section=RENOVATION_USERS_SECTION,
            report_month=None,
            session_id=session_id,
            applied=applied,
        )
    await publish_rows_changed(
        JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_USERS_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
    )


@router.patch("/dictionary")
async def patch_renovation_dictionary_batch(
    request: RenovationDictionaryBatchRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> dict[str, int]:
    """
    Batch renovation dictionary edits (project admin only).

    One request applies every cell and journals them as a single undo action.
    """
    by_row: dict[UUID, dict[str, object]] = {}
    for update in request.updates:
        by_row.setdefault(update.rowId, {})[update.field] = update.value
    if request.section == "assets":
        section_name = RENOVATION_ASSETS_SECTION
    elif request.section == "users":
        section_name = RENOVATION_USERS_SECTION
    else:
        if not request.catalog:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Не указан каталог.")
        section_name = _catalog_section(request.catalog)
    project_id = await _renovation_project_id_or_error(session) if request.section == "users" else None
    applied: list[AppliedCell] = []
    try:
        for row_id, fields in by_row.items():
            if request.section == "assets":
                asset = RenovationAssetUpdateRequest.model_validate(fields)
                _, row_applied = await update_renovation_asset(
                    session,
                    asset_id=row_id,
                    name=asset.name,
                    repair_code=asset.repairCode,
                    user=access.user,
                )
            elif request.section == "users":
                if row_id == access.user.id:
                    raise HTTPException(
                        status_code=409, detail="Нельзя редактировать собственную запись в справочнике пользователей."
                    )
                user_update = RenovationUserUpdateRequest.model_validate(fields)
                # `block` is only applied when the client sent it (null = clear).
                block_kwargs: dict[str, RBlock | None] = {}
                if "block" in fields:
                    block_kwargs["block"] = user_update.block
                _, row_applied = await update_renovation_user(
                    session,
                    project_id=project_id,
                    user_id=row_id,
                    fullname=user_update.fullname,
                    role=user_update.role,
                    asset_names=user_update.assetNames,
                    is_active=user_update.isActive,
                    updater_user_id=access.user.id,
                    **block_kwargs,
                )
            else:
                option = RenovationCatalogOptionUpdateRequest.model_validate(fields)
                _, row_applied = await update_renovation_catalog_option(
                    session,
                    catalog=request.catalog or "",
                    option_id=row_id,
                    label=option.label,
                    user=access.user,
                )
            applied.extend(row_applied)
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await record_cell_updates(
        JournalStore(session),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=section_name,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    await publish_cell_changed(
        JournalStore(session),
        project_key=RENOVATION_PROJECT_KEY,
        section=section_name,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    return {"applied": len(applied)}


@router.post("/dictionary/export")
async def export_renovation_dictionary(
    request: RenovationExportViewRequest,
    session: SessionDependency,
    access: AdminDependency,
) -> Response:
    """
    Excel export of a renovation dictionary grid («вся» / «видимая»).

    Small tables render straight into this response; past
    ``INLINE_EXPORT_MAX_ROWS`` the response is 202 + a job description and
    the workbook is built by the shared background worker.
    """
    try:
        validate_export_request(request)
    except ExportValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    rows = await _dictionary_export_rows(session, request)
    if request.rowIds is not None:
        by_id = {str(getattr(row, "id", "") or getattr(row, "valueId", "")): row for row in rows}
        rows = [by_id[str(row_id)] for row_id in request.rowIds if str(row_id) in by_id]
    asset_names = [asset.name for asset in await read_renovation_asset_rows(session)]
    needs_lists = any(column.key in REFERENCE_LISTS for column in request.columns)
    # The «Предприятия» tab pins a summary row in the grid (the total repair
    # count and cost); the export mirrors it over the SAME rows — the ones
    # surviving the rowIds filter («видимая таблица»).
    totals_row: dict[str, float] | None = None
    if request.section == RENOVATION_ASSETS_SECTION and any(
        column.key in ("repairCount", "totalCost") for column in request.columns
    ):
        exported_keys = {column.key for column in request.columns}
        totals_row = {}
        for aggregate_key in ("repairCount", "totalCost"):
            if aggregate_key in exported_keys:
                totals_row[aggregate_key] = sum(float(getattr(row, aggregate_key) or 0) for row in rows)
    return await export_table_or_job(
        sheet_title=safe_sheet_name(section_sheet_name(request.section)),
        columns=request.columns,
        rows=rows,
        header_pinned=request.headerPinned,
        file_name=request.fileName,
        user_id=str(access.user.id),
        lists={"assets": asset_names} if needs_lists else None,
        column_lists=REFERENCE_LISTS,
        totals_row=totals_row,
    )


@router.get("/dictionary/export/jobs/{job_id}", response_model=ExportJobRead)
async def get_renovation_dictionary_export_job(
    job_id: str,
    access: AdminDependency,
) -> ExportJobRead:
    """Status of a background dictionary export (progress / done / failed)."""
    try:
        job = table_export_jobs.get(job_id, str(access.user.id))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание выгрузки не найдено.") from error
    return ExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
    )


@router.get("/dictionary/export/jobs/{job_id}/file")
async def get_renovation_dictionary_export_job_file(
    job_id: str,
    access: AdminDependency,
) -> StreamingResponse:
    """Stream the XLSX of a background dictionary export AS it is being generated."""
    user_id = str(access.user.id)
    try:
        job = table_export_jobs.get(job_id, user_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задание выгрузки не найдено.",
        ) from error
    if job.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Не удалось сформировать файл выгрузки.",
        )
    quoted = quote(job.spec.file_name)
    return StreamingResponse(
        table_export_jobs.stream_growing(job_id, user_id),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )


async def _dictionary_export_rows(
    session: AsyncSession,
    request: RenovationExportViewRequest,
) -> Sequence[BaseModel]:
    if request.section == RENOVATION_ASSETS_SECTION:
        return await read_renovation_asset_rows(session)
    if request.section == RENOVATION_USERS_SECTION:
        return await read_renovation_user_rows(session)
    if request.section.startswith("renovationCatalog:"):
        catalog = request.section.removeprefix("renovationCatalog:")
        return await read_renovation_catalog_rows(session, catalog=catalog)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Некорректная секция экспорта.")


async def _renovation_project_id_or_error(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект «Ремонт» не найден.")
    return project_id


async def _publish_rows_changed(session: AsyncSession, access: RenovationAccess) -> None:
    await publish_rows_changed(
        JournalStore(session),
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
