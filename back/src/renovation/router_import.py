"""
Plan import (inspect/apply/jobs) endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

import asyncio
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlmodel import col, select

from src.common.durable_imports import ImportJob  # noqa: TC001
from src.observability import observe_phase
from src.renovation.export_jobs import ExportQueueFullError
from src.renovation.import_jobs import import_jobs
from src.renovation.import_plan import (
    inspect_plan_file_streaming,
    plan_file_suffix,
    scan_file_scope,
)
from src.renovation.inspect_cache import get_inspect_cache
from src.renovation.models import RAsset
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RPlanState,
)
from src.renovation.schemas import (
    RenovationImportJobRead,
    RenovationPlanImportResult,
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


@router.post("/plan/import/inspect", response_model=RenovationPlanImportResult)
async def inspect_renovation_plan_import(
    session: SessionDependency,
    _access: AdminDependency,
    file: Annotated[UploadFile, File()],
) -> RenovationPlanImportResult:
    """Validate an Excel technical-directorate plan without persisting rows."""
    suffix = plan_file_suffix(file.filename)
    if suffix is None:
        return RenovationPlanImportResult(status="invalid_format")
    with observe_phase("import", "hash_upload"):
        digest = await _rewind_and_hash_upload(file)
    cache = get_inspect_cache()
    # Which assets and dictionaries the file touches decides the cache scope: a
    # plan edit in an asset the file does not mention, or a catalog edit the
    # file has no column for, must not invalidate this report. The discovery is
    # a full read of the workbook, so its result is cached by the file digest
    # too: otherwise every HIT still paid that read and large files looked just
    # as slow as the first analysis.
    with observe_phase("import", "discover_assets"):
        file_scope = await cache.get_scope_digest(digest)
        if file_scope is None:
            file_scope = await asyncio.to_thread(scan_file_scope, file.file, suffix)
            if file_scope.assets:
                await cache.set_scope_digest(digest, file_scope)
    await file.seek(0)
    assets = (await session.exec(select(RAsset))).all()
    # The file names its assets either by repair code («Наименование ДО») or,
    # when that column is absent, by name («Предприятие»).
    by_key = _asset_scope_lookup(assets, assets_are_names=file_scope.assets_are_names)
    touched = [by_key[key] for key in file_scope.assets if key in by_key]
    touched_asset_ids = [asset.id for asset in touched]
    plan_row_count = 0
    latest_change: datetime | None = None
    if touched_asset_ids:
        plan_row_count, latest_change = (
            await session.exec(
                select(func.count(), func.max(RenovationPlanRow.updated_at)).where(
                    col(RenovationPlanRow.asset_id).in_(touched_asset_ids)
                )
            )
        ).one()
    dictionaries = (
        await _dictionary_fingerprint(session, file_scope=file_scope, touched_assets=touched)
        if file_scope.assets
        else ""
    )
    scope = _import_cache_scope(
        touched_asset_ids,
        latest_change,
        row_count=plan_row_count,
        dictionaries=dictionaries,
    )
    cached = await cache.get_digest(digest, scope=scope) if file_scope.assets else None
    if cached is not None:
        return cached
    existing_codes = {
        value for value in (await session.exec(select(col(RenovationPlanRow.work_or_repair_code)))).all() if value
    }
    references: dict[str, set[str]] = {}
    for value in (await session.exec(select(RenovationReferenceValue))).all():
        references.setdefault(value.catalog.value, set()).add(value.label.casefold())
    asset_names_by_code = {asset.code: asset.name for asset in assets if asset.code}
    await file.seek(0)
    with observe_phase("import", "inspect"):
        result = await asyncio.to_thread(
            inspect_plan_file_streaming,
            file.file,
            existing_codes=existing_codes,
            asset_names_by_code=asset_names_by_code,
            reference_values=references,
            suffix=suffix,
        )
    if file_scope.assets:
        await cache.set_digest(digest, result, scope=scope)
    return result


@router.post("/plan/import/apply", response_model=RenovationImportJobRead, status_code=status.HTTP_202_ACCEPTED)
async def apply_renovation_plan_import(
    session: SessionDependency,
    _access: AdminDependency,
    file: Annotated[UploadFile, File()],
    expected_rows: Annotated[int, Form(ge=0)] = 0,
) -> RenovationImportJobRead:
    """
    Accept a validated Excel file as a durable background job (admin only).

    Parsing, project locking and the write transaction run after the request
    ends. The load is not journaled into undo/redo because it rewrites many
    rows at once. `expected_rows` is the preflight's `new_rows + rows_to_update`
    and only sizes the progress bar (write + recompute); it does not gate the
    load.
    """
    file_name = file.filename
    suffix = plan_file_suffix(file_name)
    if file_name is None or suffix is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ожидается файл .xlsb или .xlsx.")
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет рабочего плана для загрузки.")
    staged, digest, size = await _stage_upload(file)
    try:
        job = await import_jobs.create_from_path(
            source=staged,
            file_name=file_name,
            digest=digest,
            size=size,
            user_id=str(_access.user.id),
            plan_id=plan.id,
            expected_rows=expected_rows,
        )
    except ExportQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    finally:
        staged.unlink(missing_ok=True)
    return _import_job_read(job)


def _import_job_read(job: ImportJob) -> RenovationImportJobRead:
    result = job.result or {}
    return RenovationImportJobRead(
        id=job.id,
        status=cast("Literal['pending', 'running', 'done', 'failed']", job.status),
        progress=job.progress,
        total=job.total,
        file_name=job.file_name,
        error=job.error,
        new_rows=int(result.get("newRows", 0)),
        updated_rows=int(result.get("updatedRows", 0)),
        phase=job.phase,
        attempt=job.attempt,
        startedAt=job.started_at,
        heartbeatAt=job.heartbeat_at,
    )


@router.get("/plan/import/jobs/{job_id}", response_model=RenovationImportJobRead)
async def get_renovation_plan_import_job(job_id: str, access: AdminDependency) -> RenovationImportJobRead:
    try:
        return _import_job_read(await import_jobs.get(job_id, str(access.user.id)))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание загрузки не найдено.") from error


@router.delete("/plan/import/jobs/{job_id}", response_model=RenovationImportJobRead)
async def cancel_renovation_plan_import_job(job_id: str, access: AdminDependency) -> RenovationImportJobRead:
    try:
        return _import_job_read(await import_jobs.cancel(job_id, str(access.user.id)))
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание загрузки не найдено.") from error
