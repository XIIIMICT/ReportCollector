"""
Admin dictionary and dictionary-export endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from src.renovation.models import RBlock

from src.collab.events import publish_cell_changed, publish_rows_changed
from src.collab.journal import AppliedCell, record_cell_updates
from src.collab.persistence import JournalStore
from src.common.export_jobs import ExportJobRead
from src.common.query_exports import new_query_spec, query_export_jobs, query_export_response
from src.downloads import XLSX_MEDIA_TYPE
from src.renovation.assets import (
    create_renovation_asset,
    delete_renovation_asset,
    read_renovation_asset_rows,
    replace_renovation_asset,
    update_renovation_asset,
)
from src.renovation.catalogs import (
    create_renovation_catalog_option,
    delete_renovation_catalog_option,
    read_renovation_catalog_rows,
    replace_renovation_catalog_option,
    update_renovation_catalog_option,
)
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.errors import RenovationDictionaryError
from src.renovation.export import (
    ExportValidationError,
    validate_export_request,
)
from src.renovation.models import RAsset, RUserRole
from src.renovation.plan import (
    RenovationReferenceValue,
)
from src.renovation.schemas import (
    RenovationAssetCreateRequest,
    RenovationAssetRowRead,
    RenovationAssetUpdateRequest,
    RenovationCatalogOptionCreateRequest,
    RenovationCatalogOptionRowRead,
    RenovationCatalogOptionUpdateRequest,
    RenovationCatalogReplaceRequest,
    RenovationDictionaryBatchRequest,
    RenovationExportViewRequest,
    RenovationUserCreateRequest,
    RenovationUserRowRead,
    RenovationUserUpdateRequest,
)
from src.renovation.users import (
    create_renovation_user,
    delete_renovation_user,
    read_renovation_user_rows,
    update_renovation_user,
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
                        "repairCode": created.code,
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


@router.post("/dictionary/assets/{asset_id}/replace", status_code=status.HTTP_204_NO_CONTENT)
async def replace_renovation_asset_endpoint(
    asset_id: UUID,
    request: RenovationCatalogReplaceRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> None:
    """Merge a renovation asset into another (or delete it)."""
    try:
        applied = await replace_renovation_asset(
            session, asset_id=asset_id, target_id=request.targetId, user=access.user
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
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
                        "label": created.label,
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


@router.post(
    "/dictionary/catalogs/{catalog}/options/{option_id}/replace",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def replace_renovation_catalog_option_endpoint(
    catalog: str,
    option_id: UUID,
    request: RenovationCatalogReplaceRequest,
    session: SessionDependency,
    access: AdminDependency,
    session_id: str,
) -> None:
    """Replace a catalog option with another (or delete it) across the plan."""
    try:
        applied = await replace_renovation_catalog_option(
            session,
            catalog=catalog,
            option_id=option_id,
            target_id=request.targetId,
            user=access.user,
        )
    except RenovationDictionaryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
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
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Не указан каталог.")
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return await query_export_response(
        session,
        new_query_spec("renovation_dictionary", request),
        str(access.user.id),
    )


@router.get("/dictionary/export/jobs/{job_id}", response_model=ExportJobRead)
async def get_renovation_dictionary_export_job(
    job_id: str,
    access: AdminDependency,
) -> ExportJobRead:
    """Status of a background dictionary export (progress / done / failed)."""
    try:
        job = await query_export_jobs.get(job_id, str(access.user.id))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание выгрузки не найдено.") from error
    return ExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
        phase=job.phase,
        attempt=job.attempt,
        startedAt=job.started_at,
        heartbeatAt=job.heartbeat_at,
    )


@router.delete("/dictionary/export/jobs/{job_id}", response_model=ExportJobRead)
async def cancel_renovation_dictionary_export_job(
    job_id: str,
    access: AdminDependency,
) -> ExportJobRead:
    try:
        await query_export_jobs.cancel(job_id, str(access.user.id))
        job = await query_export_jobs.get(job_id, str(access.user.id))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание выгрузки не найдено.") from error
    return ExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
        phase=job.phase,
        attempt=job.attempt,
        startedAt=job.started_at,
        heartbeatAt=job.heartbeat_at,
    )


@router.get("/dictionary/export/jobs/{job_id}/file")
async def get_renovation_dictionary_export_job_file(
    job_id: str,
    access: AdminDependency,
) -> StreamingResponse:
    """Stream the XLSX of a background dictionary export AS it is being generated."""
    user_id = str(access.user.id)
    try:
        job = await query_export_jobs.get(job_id, user_id)
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
        query_export_jobs.stream_growing(job_id, user_id),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )
