"""
Plan import (inspect/apply/jobs) endpoints.

Moved verbatim from ``router.py`` (Phase 4). Routes keep the same paths,
methods, dependencies and response models; the main router includes each
sub-router with the shared ``/renovation`` prefix.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.durable_imports import ImportJob  # noqa: TC001
from src.common.job_models import ACTIVE_STATES, DataJob
from src.config import get_settings
from src.database import async_session_factory
from src.observability import observe_phase
from src.renovation.export_jobs import ExportQueueFullError
from src.renovation.import_jobs import import_jobs
from src.renovation.import_plan import (
    ExistingCodeOracle,
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


def _working_plan_import_scope(
    plan_id: UUID | None,
    revision: int,
    asset_ids: list[UUID],
    *,
    dictionaries: str = "",
) -> str:
    """
    Preflight cache scope, bound to the CURRENT working plan and its generation.

    Validation compares the file's codes with the working plan only, so both the
    plan id and `revision` are part of the key: a new version (or any
    write that can change its code set) must re-key an otherwise identical
    workbook. The revision is bumped by DB triggers on `r_plan_rows` in the
    mutating transaction — no `count(*)`/`max(updated_at)` token that could miss
    a delete of a non-latest row, and no app write path to remember. `plan=none`
    keeps an upload cacheable before a working plan exists without sharing a
    report with a plan that does.

    The file's touched assets and dictionary tokens stay in the scope: a catalog
    or asset the file never mentions cannot change its report.
    """
    parts = [f"plan={plan_id or 'none'}", f"rev={revision}"]
    if asset_ids:
        parts.append("assets=" + ",".join(sorted(str(asset_id) for asset_id in asset_ids)))
    if dictionaries:
        parts.append(dictionaries)
    return "|".join(parts)


# Keyset pages of the working plan's codes. The DB read is bounded by this page
# size; the oracle external-sorts the digests so no Python set of codes exists.
_EXISTING_CODE_PAGE_SIZE = 5000

# Optimistic preflight concurrency: a report is sealed/cached/returned only when
# the working plan's identity and revision still match the token read before the
# oracle was filled. A mismatch discards the oracle and restarts from a fresh
# token; a bounded number of restarts keeps a busy plan from spinning forever.
_PREFLIGHT_MAX_ATTEMPTS = 3
_PREFLIGHT_CONFLICT_DETAIL = "План изменился во время проверки файла. Повторите загрузку."

# One statement reads both columns, so the token is a single READ COMMITTED
# observation rather than two reads that could straddle a commit.
_PlanToken = tuple[UUID | None, int]


async def _read_plan_token(session: AsyncSession) -> _PlanToken:
    """Return the current working plan's `(id, revision)` in one SELECT."""
    state = (
        await session.exec(
            select(col(RenovationPlan.id), col(RenovationPlan.revision))
            .where(col(RenovationPlan.state) == RPlanState.WORKING)
            .order_by(col(RenovationPlan.id))
        )
    ).first()
    return (state[0], int(state[1])) if state is not None else (None, 0)


async def _fill_existing_code_oracle(
    session: AsyncSession,
    oracle: ExistingCodeOracle,
    plan_id: UUID,
    *,
    on_page: Callable[[int], Awaitable[None]] | None = None,
) -> None:
    """
    Stream one plan's non-blank work codes into the bounded membership oracle.

    Keyset pagination over the primary key is stable while the plan is being
    edited by others; `id > last_id` never skips a row on a page boundary. The
    caller owns the oracle and closes it on every path, including cancellation.
    `on_page` is an optional awaitable seam used by concurrency tests to commit
    a mutation between two pages without sleeping; production callers omit it.
    """
    last_id: UUID | None = None
    page_number = 0
    while True:
        statement = select(col(RenovationPlanRow.id), col(RenovationPlanRow.work_or_repair_code)).where(
            col(RenovationPlanRow.plan_id) == plan_id,
            col(RenovationPlanRow.work_or_repair_code).is_not(None),
        )
        if last_id is not None:
            statement = statement.where(col(RenovationPlanRow.id) > last_id)
        page = (await session.exec(statement.order_by(col(RenovationPlanRow.id)).limit(_EXISTING_CODE_PAGE_SIZE))).all()
        if not page:
            return
        for _row_id, code in page:
            if code:
                oracle.add(code)
        page_number += 1
        if on_page is not None:
            await on_page(page_number)
        last_id = page[-1][0]
        if len(page) < _EXISTING_CODE_PAGE_SIZE:
            return


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
    # Phase 5.2: scope discovery parses the whole workbook in a worker thread.
    # No DB read has happened yet, so no pooled connection is held during it.
    await session.close()
    # Which assets and dictionaries the file touches decides the cache scope: a
    # plan edit in an asset the file does not mention, or a catalog edit the
    # file has no column for, must not invalidate this report. The discovery is
    # a full read of the workbook, so its result is cached by the file digest
    # too: otherwise every HIT still paid that read and large files looked just
    # as slow as the first analysis.
    with observe_phase("import", "discover_assets"):
        file_scope = await cache.get_scope_digest(digest, suffix=suffix)
        if file_scope is None:
            file_scope = await asyncio.to_thread(scan_file_scope, file.file, suffix)
            if file_scope.assets:
                await cache.set_scope_digest(digest, file_scope, suffix=suffix)
    await file.seek(0)
    # Optimistic point-in-time contract: the plan token is captured before the
    # scope lookup, checked again after the LAST keyset page and once more before
    # a cache hit is returned or a fresh report is cached. A concurrent commit
    # (insert/delete/code edit/plan transfer) bumps `r_plans.revision` in the
    # same transaction (migration 0043) or changes the working plan's id; either
    # mismatch discards the oracle/report and restarts with a fresh token. No
    # DB connection is held during either `to_thread` parsing phase.
    for _attempt in range(_PREFLIGHT_MAX_ATTEMPTS):
        token = await _read_plan_token(session)
        assets = (await session.exec(select(RAsset))).all()
        # The file names its assets either by repair code («Наименование ДО») or,
        # when that column is absent, by name («Предприятие»).
        by_key = _asset_scope_lookup(assets, assets_are_names=file_scope.assets_are_names)
        touched = [by_key[key] for key in file_scope.assets if key in by_key]
        touched_asset_ids = [asset.id for asset in touched]
        dictionaries = (
            await _dictionary_fingerprint(session, file_scope=file_scope, touched_assets=touched)
            if file_scope.assets
            else ""
        )
        scope = _working_plan_import_scope(token[0], token[1], touched_asset_ids, dictionaries=dictionaries)
        cached = await cache.get_digest(digest, scope=scope, suffix=suffix) if file_scope.assets else None
        if cached is not None:
            still_current = await _read_plan_token(session)
            await session.close()
            if still_current == token:
                return cached
            continue
        # Only the CURRENT working plan's codes decide «new» vs «update».
        # Including archived plans here made a newer version inherit codes of an
        # older one and silently turn an insert into an overwrite. The oracle
        # external-sorts those codes into fixed-width digests, so a 300k-row plan
        # no longer materializes a 300k-entry Python set that stays alive for the
        # whole CPU-bound parse. It is created only after the cache miss: a cheap
        # HIT must never rebuild a large index.
        oracle = ExistingCodeOracle()
        try:
            if token[0] is not None:
                with observe_phase("import", "existing_codes"):
                    await _fill_existing_code_oracle(session, oracle, token[0])
            # A page boundary can straddle a committed write, so the token read
            # after the final page is the gate for accepting these codes.
            if await _read_plan_token(session) != token:
                await session.close()
                continue
            oracle.seal()
            references: dict[str, set[str]] = {}
            for value in (await session.exec(select(RenovationReferenceValue))).all():
                references.setdefault(value.catalog.value, set()).add(value.label.casefold())
            asset_names_by_code = {asset.code: asset.name for asset in assets if asset.code}
            # Phase 5.2: the reference snapshot above is complete and consistent;
            # the analysis that follows is pure CPU in a worker thread and must
            # not keep a DB connection checked out of the pool.
            await session.close()
            await file.seek(0)
            with observe_phase("import", "inspect"):
                result = await asyncio.to_thread(
                    inspect_plan_file_streaming,
                    file.file,
                    existing_codes=oracle,
                    asset_names_by_code=asset_names_by_code,
                    reference_values=references,
                    suffix=suffix,
                    issue_limit=get_settings().renovation_import_issue_limit,
                )
            # A commit during the CPU parse must not be cached or returned as
            # current either; this is the report's linearization point.
            if await _read_plan_token(session) != token:
                await session.close()
                continue
            await session.close()
            if file_scope.assets:
                await cache.set_digest(digest, result, scope=scope, suffix=suffix)
            return result
        finally:
            # Covers success, parse errors, token conflicts, cancellation and
            # exhaustion; close() is idempotent and removes the OS-backed runs
            # even when to_thread was interrupted.
            oracle.close()
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PREFLIGHT_CONFLICT_DETAIL)


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
            user_name=_access.user.name or _access.user.email or "",
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
        user_name=job.user_name,
        error=job.error,
        new_rows=int(result.get("newRows", 0)),
        updated_rows=int(result.get("updatedRows", 0)),
        phase=job.phase,
        attempt=job.attempt,
        startedAt=job.started_at,
        heartbeatAt=job.heartbeat_at,
    )


@router.get("/plan/import/active", response_model=RenovationImportJobRead | None)
async def get_active_plan_import(access: AccessDependency) -> RenovationImportJobRead | None:
    """
    Return the current import blocking the plan form, for every member.

    This is a status poll, not a source of truth for the import itself: the job
    row in PostgreSQL is. A missed poll only delays showing the red chip.
    """
    del access  # any working member may see who is loading the plan
    async with async_session_factory() as session:
        job = (
            await session.exec(
                select(DataJob)
                .where(DataJob.kind == import_jobs.kind, col(DataJob.status).in_(ACTIVE_STATES))
                .order_by(col(DataJob.created_at).desc())
                .limit(1)
            )
        ).first()
    return None if job is None else _import_job_read(import_jobs.job_from_record(job))


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
