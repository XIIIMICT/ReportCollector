"""«Ремонт» project API: reading/editing the renovation plan, plan lifecycle, undo/redo."""

from collections.abc import Sequence
from io import BytesIO
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
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
from src.collab.schemas import UndoConflictRead, UndoRequest, UndoResponse, UndoStateRead
from src.collab.store import get_collab_store
from src.collab.undo import UndoOutcome, redo_last_undo, undo_last_edit
from src.database import get_session
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.export import (
    ExportValidationError,
    render_renovation_workbook,
    render_workbook,
    validate_export_request,
)
from src.renovation.models import RProjectRole
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.schemas import (
    RenovationApproveRead,
    RenovationAssetCreateRequest,
    RenovationAssetRowRead,
    RenovationAssetUpdateRequest,
    RenovationCatalogOptionCreateRequest,
    RenovationCatalogOptionRowRead,
    RenovationCatalogOptionUpdateRequest,
    RenovationDiffRead,
    RenovationExportViewRequest,
    RenovationMetaRead,
    RenovationPlanRead,
    RenovationPlanRowRead,
    RenovationPlanRowsRead,
    RenovationPlanVersionRead,
    RenovationRowAddRequest,
    RenovationUpdateRead,
    RenovationUpdateRequest,
    RenovationUserCreateRequest,
    RenovationUserRowRead,
    RenovationUserUpdateRequest,
)
from src.renovation.service import (
    CellValidationError,
    NoRenovationAccessError,
    RenovationDictionaryError,
    RenovationPermissionError,
    RenovationPlanNotFoundError,
    RenovationPlanStateError,
    add_plan_row,
    approve_plan,
    create_renovation_asset,
    create_renovation_catalog_option,
    create_renovation_user,
    delete_plan_row,
    delete_renovation_asset,
    delete_renovation_catalog_option,
    read_meta,
    read_plan,
    read_plan_diff,
    read_plan_rows,
    read_plan_versions,
    read_renovation_asset_rows,
    read_renovation_catalog_rows,
    read_renovation_user_rows,
    resolve_renovation_access,
    submit_plan,
    update_plan_cells,
    update_renovation_asset,
    update_renovation_catalog_option,
    update_renovation_user,
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


@router.get("/plan/rows", response_model=RenovationPlanRowsRead)
async def get_renovation_plan_rows(
    session: SessionDependency,
    access: AccessDependency,
    plan_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[RPlanState | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    asset: Annotated[str | None, Query()] = None,
) -> RenovationPlanRowsRead:
    """Return a window of renovation plan rows plus the total (lazy loading)."""
    try:
        return await read_plan_rows(
            session,
            access.assets,
            plan_id=plan_id,
            state=state if state is not None else RPlanState.WORKING,
            offset=offset,
            limit=limit,
            asset_name=asset,
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


# ─── Editable reference dictionaries (project admin only) ───────────────

RENOVATION_ASSETS_SECTION = "renovationAssets"
RENOVATION_USERS_SECTION = "renovationUsers"
EXPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
) -> RenovationAssetRowRead:
    """Renovation asset creation (project admin only)."""
    try:
        return await create_renovation_asset(
            session,
            name=request.name,
            repair_code=request.repairCode,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


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
    await publish_cell_changed(
        get_collab_store(),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_ASSETS_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_ASSETS_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
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
        get_collab_store(),
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
) -> RenovationCatalogOptionRowRead:
    """Catalog option creation (project admin only)."""
    try:
        return await create_renovation_catalog_option(
            session,
            catalog=catalog,
            label=request.label,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


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
    await publish_cell_changed(
        get_collab_store(),
        project_key=RENOVATION_PROJECT_KEY,
        section=section,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=section,
        report_month=None,
        session_id=session_id,
        applied=applied,
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
        get_collab_store(),
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
            updated_by=access.user.name or access.user.email or "",
            **block_kwargs,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await publish_cell_changed(
        get_collab_store(),
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_USERS_SECTION,
        report_month=None,
        user_id=access.user.id,
        user_name=access.user.name or "",
        user_email=access.user.email or "",
        cells=applied,
    )
    await record_cell_updates(
        get_collab_store(),
        user_id=access.user.id,
        user_name=access.user.name or "",
        project_key=RENOVATION_PROJECT_KEY,
        section=RENOVATION_USERS_SECTION,
        report_month=None,
        session_id=session_id,
        applied=applied,
    )
    return (await read_renovation_user_rows(session, user_id=user_id))[0]


@router.post("/dictionary/export")
async def export_renovation_dictionary(
    request: RenovationExportViewRequest,
    session: SessionDependency,
    _access: AdminDependency,
) -> Response:
    """Excel export of a renovation dictionary grid («вся» / «видимая»)."""
    try:
        validate_export_request(request)
    except ExportValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    rows = await _dictionary_export_rows(session, request)
    if request.rowIds is not None:
        by_id = {str(getattr(row, "id", "") or getattr(row, "valueId", "")): row for row in rows}
        rows = [by_id[str(row_id)] for row_id in request.rowIds if str(row_id) in by_id]
    asset_names = [asset.name for asset in await read_renovation_asset_rows(session)]
    payload = await run_in_threadpool(render_workbook, request, rows, asset_names)
    return Response(
        content=payload,
        media_type=EXPORT_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(request.fileName.strip())}"},
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
    cells = outcome.applied_cells
    if not cells:
        return UndoResponse(applied=True, reason="applied", section=outcome.section, reportMonth=outcome.report_month)
    await publish_cell_changed(
        get_collab_store(),
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
