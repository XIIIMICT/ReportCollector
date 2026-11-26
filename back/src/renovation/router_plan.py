"""
Plan read, cell/row and diff endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.events import publish_cell_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.models import RENOVATION_PLAN_ROWS_TABLE
from src.collab.persistence import JournalStore
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.plan import (
    RPlanState,
)
from src.renovation.plan_reads import PlanFullReadTooLargeError
from src.renovation.schemas import (
    PLAN_FULL_READ_ERROR_MESSAGE,
    PlanFullReadTooLargeDetail,
    RenovationDiffRead,
    RenovationFacetsRead,
    RenovationMetaRead,
    RenovationPlanQuery,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanRowsRead,
    RenovationPlanSummaryRead,
    RenovationPlanVersionRead,
    RenovationRowAssignRequest,
    RenovationRowCreateRequest,
    RenovationUpdateRead,
    RenovationUpdateRequest,
)
from src.renovation.service import (
    CellValidationError,
    RenovationPermissionError,
    RenovationPlanNotFoundError,
    RenovationPlanStateError,
    add_plan_row,
    assign_plan_row_asset,
    delete_plan_row,
    read_meta,
    read_plan,
    read_plan_diff,
    read_plan_facets,
    read_plan_rows,
    read_plan_summary,
    read_plan_versions,
    update_plan_cells,
)

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
    """
    Compatibility full-plan read; default — the working plan.

    Kept for clients that consume a whole plan in one response. Bounded to
    `FULL_PLAN_READ_MAX_ROWS` rows (an owner-approved inline-work budget, not a
    product value cap): a larger scope returns 422 with the fixed code
    `PLAN_FULL_READ_TOO_LARGE` and NO rows — never a silent truncation. New
    clients must page through `GET /plan/rows` or `POST /plan/rows/query`.
    Contract: docs/LIMITS.md, docs/RENOVATION-DESIGN.md.
    """
    try:
        return await read_plan(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except PlanFullReadTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=PlanFullReadTooLargeDetail(
                message=PLAN_FULL_READ_ERROR_MESSAGE.format(max_rows=error.limit),
                maxRows=error.limit,
            ).model_dump(),
        ) from error


def reject_column_query_filters(request: Request) -> None:
    """Do not silently ignore a retired column-filter URL contract."""
    if "filters" in request.query_params:
        raise HTTPException(status_code=422, detail="Фильтры столбцов передаются в JSON-теле запроса.")


@router.get("/plan/rows", response_model=RenovationPlanRowsRead, dependencies=[Depends(reject_column_query_filters)])
@router.post("/plan/rows/query", response_model=RenovationPlanRowsRead)
async def get_renovation_plan_rows(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    asset: Annotated[str | None, Query()] = None,
    payload: RenovationPlanQuery | None = None,
    position_from: Annotated[int | None, Query(ge=1)] = None,
    position_to: Annotated[int | None, Query(ge=1)] = None,
    anchor_position: Annotated[int | None, Query(ge=1)] = None,
    # The browser's IANA zone: a `timestamptz` window is typed as LOCAL date and
    # time (what the cells print), so its bounds are read in this zone. Absent —
    # UTC.
    tz: Annotated[str | None, Query()] = None,
) -> Response:
    """
    Return a window of renovation plan rows plus the total (lazy loading).

    Header constraints are a typed JSON body on POST /plan/rows/query,
    combined with AND in SQL. GET remains for unconstrained compatibility reads.
    `position_from`/`position_to` narrow the window to a row-number («№») range.
    `anchor_position` resolves a stored row position onto the filtered scope
    (`anchorOffset` in the response) — the client centres the same row after a
    reload or a filter change.
    """
    try:
        page = await read_plan_rows(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
            offset=offset,
            limit=limit,
            asset_name=asset,
            filters=payload.filters if payload else None,
            sorting=payload.sorting if payload else None,
            position_from=payload.position_from if payload else position_from,
            position_to=payload.position_to if payload else position_to,
            anchor_position=payload.anchor_position if payload else anchor_position,
            time_zone=payload.tz if payload else tz,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    # Hand over the serialized body instead of the model: FastAPI would dump the
    # model to Python objects and encode them again, while `dump_json` writes the
    # same JSON in one pass (~36% faster on a 100-row window). `response_model`
    # still drives the OpenAPI schema and still validates the return value.
    return Response(content=PLAN_ROWS_JSON.dump_json(page), media_type="application/json")


@router.get(
    "/plan/summary", response_model=RenovationPlanSummaryRead, dependencies=[Depends(reject_column_query_filters)]
)
@router.post("/plan/summary/query", response_model=RenovationPlanSummaryRead)
async def get_renovation_plan_summary(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    payload: RenovationPlanQuery | None = None,
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
            filters=payload.filters if payload else None,
            position_from=payload.position_from if payload else position_from,
            position_to=payload.position_to if payload else position_to,
            time_zone=payload.tz if payload else None,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return RenovationPlanSummaryRead(values=values)


@router.get("/plans", response_model=list[RenovationPlanVersionRead])
async def get_renovation_plans(
    session: SessionDependency,
    _access: AccessDependency,
) -> list[RenovationPlanVersionRead]:
    """All renovation plan versions (newest first) with row counts."""
    return await read_plan_versions(session)


@router.get("/plan/facets", response_model=RenovationFacetsRead, dependencies=[Depends(reject_column_query_filters)])
@router.post("/plan/facets/query", response_model=RenovationFacetsRead)
async def get_renovation_plan_facets(
    session: SessionDependency,
    access: AccessDependency,
    column: Annotated[str, Query(min_length=1)],
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    search_kind: Annotated[Literal["text", "range", "datetime"] | None, Query()] = None,
    search_query: Annotated[str | None, Query()] = None,
    search_min: Annotated[float | None, Query()] = None,
    search_max: Annotated[float | None, Query()] = None,
    search_tz: Annotated[str | None, Query()] = None,
    payload: RenovationPlanQuery | None = None,
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
            search_kind=payload.search_kind if payload else search_kind,
            search_query=payload.search_query if payload else search_query,
            search_min=payload.search_min if payload else search_min,
            search_max=payload.search_max if payload else search_max,
            search_timezone=payload.tz if payload else search_tz,
            filters=payload.filters if payload else None,
        )
    except RenovationPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CellValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error


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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
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
    # The renovation ID (work_or_repair_code) is derived too, but it is NOT part of
    # `calculated` (the formula engine doesn't own it) — publish it explicitly
    # so other clients update the code live. The editor refreshes its window
    # from the PATCH response; everyone else only gets cell:changed.
    cells.extend(
        AppliedCell(
            target_table=RENOVATION_PLAN_ROWS_TABLE,
            row_id=renovation_id.rowId,
            field="work_or_repair_code",
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
