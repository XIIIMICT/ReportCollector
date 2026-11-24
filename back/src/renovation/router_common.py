"""
Shared renovation-router setup: auth, upload/scope and response helpers.

Imported by every sub-router (Phase 4).
"""

import asyncio
import contextlib
import hashlib
import os
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, UploadFile, status
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from src.auth.models import User
    from src.renovation.models import RBlock, RProjectRole

from src.auth.models import Project
from src.auth.router import read_signed_identity
from src.auth.service import CurrentUserIdentity
from src.collab.events import publish_rows_changed
from src.collab.persistence import JournalStore
from src.common.durable_imports import import_dir
from src.config import get_settings
from src.database import get_session
from src.metrics import IMPORT_REJECTIONS, IMPORT_UPLOAD_BYTES
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.import_plan import (
    PlanFileScope,
    _asset_name_key,
)
from src.renovation.models import RAsset, RProjectRole
from src.renovation.plan import (
    RenovationPlan,
    RenovationPlanRow,
    RenovationReferenceValue,
    RReferenceCatalog,
)
from src.renovation.schemas import (
    RenovationPlanRowsRead,
    RenovationPlanVersionRead,
)
from src.renovation.service import (
    NoRenovationAccessError,
    resolve_renovation_access,
)

# Bump when XLSX rendering/materialization changes to invalidate older artifacts.
PLAN_EXPORT_RENDER_VERSION = 2
_settings = get_settings()
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _upload_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail="Файл имеет слишком большой размер.",
    )


async def _rewind_and_hash_upload(file: UploadFile) -> str:
    """Hash an upload in bounded chunks and rewind it for the parser."""
    digest = hashlib.sha256()
    size = 0
    await file.seek(0)
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > _settings.renovation_import_max_bytes:
            IMPORT_REJECTIONS.labels(reason="size").inc()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Файл имеет слишком большой размер.",
            )
        digest.update(chunk)
    await file.seek(0)
    IMPORT_UPLOAD_BYTES.observe(size)
    return digest.hexdigest()


async def _stage_upload(file: UploadFile) -> tuple[Path, str, int]:
    """Copy a validated upload to durable storage without retaining it in RAM."""
    target = import_dir() / f"{uuid.uuid4().hex}.upload"

    def copy_upload() -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        file.file.seek(0)
        with target.open("wb") as sink:
            while chunk := file.file.read(_UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > _settings.renovation_import_max_bytes:
                    IMPORT_REJECTIONS.labels(reason="size").inc()
                    raise _upload_too_large()
                digest.update(chunk)
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        return digest.hexdigest(), size

    copying = asyncio.create_task(asyncio.to_thread(copy_upload))
    try:
        digest, size = await asyncio.shield(copying)
    except BaseException:
        # A cancelled request must not close the spooled file or unlink the
        # destination while a thread still reads/writes them.
        with contextlib.suppress(Exception):
            await copying
        target.unlink(missing_ok=True)
        raise
    IMPORT_UPLOAD_BYTES.observe(size)
    return target, digest, size


# One adapter, reused: building it per request would cost more than the dump
# saves. It serializes the plan window to JSON in a single pass.
PLAN_ROWS_JSON: TypeAdapter[RenovationPlanRowsRead] = TypeAdapter(RenovationPlanRowsRead)

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


def _token(count: int, updated_at: datetime | None) -> str:
    """`count(*) + max(updated_at)` of a dictionary — the self-invalidating token."""
    stamp = updated_at.isoformat() if updated_at is not None else "none"
    return f"{count}@{stamp}"


def _import_cache_scope(
    asset_ids: list[UUID],
    latest_change: datetime | None,
    *,
    row_count: int | None = None,
    dictionaries: str = "",
) -> str:
    """
    Cache fingerprint of the plan and the dictionaries an upload was checked against.

    The plan part names the uploaded assets and the `count(*) + max(updated_at)`
    token of their working plan rows: an edit in any other asset keeps the
    cached report valid, while an edit/insert/delete of a row in a touched asset
    re-keys it (the max alone missed a deleted row that was not the newest). The
    dictionary part carries each used dictionary's own token (see
    `_dictionary_fingerprint`), so adding the value a cached report called
    missing changes the key instead of serving the stale «has errors» answer
    until the TTL. An empty plan part plus no dictionary token means no scope at
    all.
    """
    parts: list[str] = []
    if asset_ids:
        stamp = latest_change.isoformat() if latest_change is not None else "none"
        ids = ",".join(sorted(str(asset_id) for asset_id in asset_ids))
        plan_token = f"{row_count}@{stamp}" if row_count is not None else stamp
        parts.append(f"{ids}@{plan_token}")
    if dictionaries:
        parts.append(dictionaries)
    return "|".join(parts)


async def _dictionary_fingerprint(
    session: AsyncSession,
    *,
    file_scope: PlanFileScope,
    touched_assets: list[RAsset],
) -> str:
    """
    Change tokens of the dictionaries the file validates against.

    Only the reference catalogs the file has a column for are queried: a catalog
    the file never mentions cannot change its report. The asset dictionary is
    scoped to the assets the file names — by repair code or, in a drop without
    «Наименование ДО», by name — including unknown ones (the report lists them
    under «Предприятие не найдено», and adding the asset must let the same file
    through). Both parts use `count(*)` + `max(updated_at)`: a rename or an
    added/deleted option changes the key with no call site to remember.
    """
    parts: list[str] = []
    catalogs = sorted(RReferenceCatalog(value) for value in file_scope.catalogs)
    if catalogs:
        rows = (
            await session.exec(
                select(
                    col(RenovationReferenceValue.catalog),
                    func.count(),
                    func.max(col(RenovationReferenceValue.updated_at)),
                )
                .where(col(RenovationReferenceValue.catalog).in_(catalogs))
                .group_by(col(RenovationReferenceValue.catalog))
            )
        ).all()
        tokens = {catalog.value: _token(count, updated_at) for catalog, count, updated_at in rows}
        parts.extend(f"{catalog.value}={tokens.get(catalog.value, _token(0, None))}" for catalog in catalogs)
    latest_asset_change = max((asset.updated_at for asset in touched_assets), default=None)
    parts.append(f"assets={_token(len(touched_assets), latest_asset_change)}")
    return ";".join(parts)


def _asset_scope_lookup(assets: Iterable[RAsset], *, assets_are_names: bool) -> dict[str, RAsset]:
    """
    Assets keyed exactly as `scan_file_scope` keys the uploaded file.

    The scope uses case-folded keys for BOTH modes (a repair code `bszr` and a
    legal-entity name). Keying codes in upper case here silently resolved no
    touched asset for every code-based file, which dropped the plan state from
    the cache scope.
    """
    if assets_are_names:
        return {_asset_name_key(asset.name): asset for asset in assets}
    return {asset.code.casefold(): asset for asset in assets if asset.code}


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
