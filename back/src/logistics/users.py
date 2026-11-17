"""
Project-scoped user dictionary for «Логистика» (administrators only).

Roles: `admin` (everything), `reviewer` (read the whole rates table), `asset_worker`
(add/edit the rates of the assets granted to them) and `blocked` (no access).
`UserRole.is_active` is DERIVED from the role (blocked → false): every access
resolver keeps reading the flag, while the UI shows a role instead.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.emails import normalize_email
from src.auth.models import ProjectRole, User, UserRole
from src.auth.service import sync_project_memberships
from src.collab.locking import project_write
from src.config import get_settings
from src.logistics.constants import LOGISTICS_PROJECT_KEY

from .core import LogisticsNotFoundError, LogisticsValidationError, logistics_project_id
from .dictionaries import read_org_labels
from .models import LogisticsUserAsset
from .schemas import LogisticsUserCreateRequest, LogisticsUserRowRead, LogisticsUserUpdateRequest

_USER_EXISTS = "Пользователь уже добавлен в проект."
_USER_NOT_FOUND = "Пользователь не найден."
_SELF_EDIT = "Нельзя редактировать собственную запись в справочнике пользователей."
_SELF_DELETE = "Нельзя удалить себя из проекта."
_DELETE_ACTIVE = "Сначала заблокируйте пользователя, затем удаляйте."
_KEYCLOAK_NAME = "ФИО управляется корпоративной учётной записью."
_UNKNOWN_ASSET = "Выбранный актив отсутствует в справочнике."
_DELETED_ASSET = "Выбранный актив помечен на удаление и не может быть закреплён."
_BAD_ROLE = "Недопустимая роль для проекта «Логистика»."
# Every project member role the logistics UI offers.
LOGISTICS_ROLES: frozenset[ProjectRole] = frozenset(
    {ProjectRole.ADMIN, ProjectRole.REVIEWER, ProjectRole.ASSET_WORKER, ProjectRole.BLOCKED}
)


def _deleted_label(label: str, deleted_at: object) -> str:
    return f"УДАЛЕНО: {label}" if deleted_at is not None else label


async def _asset_rows(session: AsyncSession, user_ids: list[UUID]) -> dict[UUID, list[UUID]]:
    """Return the granted asset ids per user (the dictionary is small; one query)."""
    if not user_ids:
        return {}
    rows = (await session.exec(select(LogisticsUserAsset).where(col(LogisticsUserAsset.user_id).in_(user_ids)))).all()
    result: dict[UUID, list[UUID]] = {}
    for row in rows:
        result.setdefault(row.user_id, []).append(row.org_id)
    return result


async def _validate_asset_ids(session: AsyncSession, asset_ids: list[UUID]) -> list[UUID]:
    """Keep the grants that point at existing, non-deleted assets."""
    labels = await read_org_labels(session)
    if not labels:
        raise LogisticsValidationError(_UNKNOWN_ASSET)
    unique: list[UUID] = []
    for asset_id in asset_ids:
        if asset_id in unique:
            continue
        entry = labels.get(asset_id)
        if entry is None:
            raise LogisticsValidationError(_UNKNOWN_ASSET)
        if entry[1] is not None:
            raise LogisticsValidationError(_DELETED_ASSET)
        unique.append(asset_id)
    return unique


def _role_keeps_assets(role: ProjectRole) -> bool:
    return role == ProjectRole.ASSET_WORKER


async def read_logistics_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    row_ids: list[UUID] | None = None,
) -> list[LogisticsUserRowRead]:
    """Return the project members (active first, then by name)."""
    project_id = await logistics_project_id(session)
    query = (
        select(User, UserRole)
        .join(UserRole, col(UserRole.user_id) == col(User.id))
        .where(col(UserRole.project_id) == project_id, col(User.is_deleted).is_(False))
        .order_by(col(UserRole.is_active).desc(), func.coalesce(User.name, User.email))
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    if row_ids is not None:
        query = query.where(col(User.id).in_(row_ids))
    rows = (await session.exec(query)).all()
    assets = await _asset_rows(session, [person.id for person, _link in rows])
    labels = await read_org_labels(session)
    result: list[LogisticsUserRowRead] = []
    for person, link in rows:
        asset_ids = assets.get(person.id, [])
        result.append(
            LogisticsUserRowRead(
                id=person.id,
                email=str(person.email or ""),
                fullname=person.name or "",
                role=link.role,
                isActive=link.is_active,
                assetIds=asset_ids,
                assetNames=[_deleted_label(*labels[asset_id]) if asset_id in labels else "" for asset_id in asset_ids],
                lastAuthenticatedAt=person.last_authenticated_at,
                createdAt=person.created_at,
                updatedAt=link.updated_at,
                updatedBy=link.updated_by,
            )
        )
    return result


async def _replace_assets(session: AsyncSession, *, user_id: UUID, asset_ids: list[UUID], editor: User) -> None:
    """Rewrite the grants of a worker (the set is small and fully managed here)."""
    existing = (await session.exec(select(LogisticsUserAsset).where(col(LogisticsUserAsset.user_id) == user_id))).all()
    for row in existing:
        if row.org_id not in asset_ids:
            await session.delete(row)
    known = {row.org_id for row in existing}
    for asset_id in asset_ids:
        if asset_id not in known:
            session.add(LogisticsUserAsset(user_id=user_id, org_id=asset_id, updater_user_id=editor.id))
    await session.flush()


@project_write(LOGISTICS_PROJECT_KEY, exclusive=True)
async def create_logistics_user(
    session: AsyncSession,
    *,
    request: LogisticsUserCreateRequest,
    editor: User,
) -> LogisticsUserRowRead:
    """Provision a project member with a role and (for workers) asset grants."""
    project_id = await logistics_project_id(session)
    email = normalize_email(str(request.email))
    person = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
    if person is not None and await session.get(UserRole, (person.id, project_id)) is not None:
        raise LogisticsValidationError(_USER_EXISTS)
    role = request.role
    if role not in LOGISTICS_ROLES:
        raise LogisticsValidationError(_BAD_ROLE)
    asset_ids = await _validate_asset_ids(session, request.assetIds) if _role_keeps_assets(role) else []
    if person is None:
        person = User(email=email, name=request.fullname.strip())
        session.add(person)
        await session.flush()
    person.is_deleted = False
    session.add(person)
    session.add(
        UserRole(
            user_id=person.id,
            project_id=project_id,
            role=role,
            # Blocked is the only role without access; the flag stays derived.
            is_active=role != ProjectRole.BLOCKED,
            updater_user_id=editor.id,
        )
    )
    await _replace_assets(session, user_id=person.id, asset_ids=asset_ids, editor=editor)
    # The same cross-project visibility as the other users dictionaries: the
    # person appears in the other projects as a blocked member.
    await sync_project_memberships(session, person.id)
    await session.flush()
    return (await read_logistics_user_rows(session, user_id=person.id))[0]


@project_write(LOGISTICS_PROJECT_KEY, exclusive=True)
async def update_logistics_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    request: LogisticsUserUpdateRequest,
    editor: User,
) -> LogisticsUserRowRead:
    """Change the full name, the role or the asset grants; email is immutable."""
    if user_id == editor.id:
        raise LogisticsValidationError(_SELF_EDIT)
    project_id = await logistics_project_id(session)
    person = await session.get(User, user_id)
    link = await session.get(UserRole, (user_id, project_id))
    if person is None or person.is_deleted or link is None:
        raise LogisticsNotFoundError(_USER_NOT_FOUND)
    if request.fullname is not None:
        fullname = request.fullname.strip()
        if get_settings().keycloak_enabled and fullname != (person.name or ""):
            raise LogisticsValidationError(_KEYCLOAK_NAME)
        person.name = fullname
    if request.role is not None:
        if request.role not in LOGISTICS_ROLES:
            raise LogisticsValidationError(_BAD_ROLE)
        link.role = request.role
        # Blocked → no access; any working role → access (an asset worker
        # without grants is treated as blocked at ACCESS time, so the admin can
        # save the role first and pick the asset afterwards).
        link.is_active = request.role != ProjectRole.BLOCKED
        if not _role_keeps_assets(request.role):
            await _replace_assets(session, user_id=user_id, asset_ids=[], editor=editor)
    requested_assets = request.assetIds
    if requested_assets is None and request.assetNames is not None:
        # The grid edits assets by name; resolve them through the dictionary.
        labels = await read_org_labels(session)
        by_name = {label: asset_id for asset_id, (label, deleted) in labels.items() if deleted is None}
        requested_assets = []
        for name in request.assetNames:
            asset_id = by_name.get(name)
            if asset_id is None:
                raise LogisticsValidationError(_UNKNOWN_ASSET)
            requested_assets.append(asset_id)
    if requested_assets is not None:
        if not _role_keeps_assets(link.role):
            # Assets belong to the asset-worker role only; a stale edit is a
            # no-op rather than an error (the UI clears them with the role).
            asset_ids: list[UUID] = []
        else:
            asset_ids = await _validate_asset_ids(session, requested_assets)
        await _replace_assets(session, user_id=user_id, asset_ids=asset_ids, editor=editor)
    now = datetime.now(UTC)
    person.updated_at = now
    link.updated_at = now
    link.updater_user_id = editor.id
    session.add(person)
    session.add(link)
    await session.flush()
    return (await read_logistics_user_rows(session, user_id=user_id))[0]


@project_write(LOGISTICS_PROJECT_KEY, exclusive=True)
async def delete_logistics_user(session: AsyncSession, *, user_id: UUID, editor: User) -> None:
    """
    Remove the person from «Логистика» (the subscription is the `UserRole` link).

    A working member must be blocked first, and an admin cannot delete
    themselves. The account is soft-deleted once no project link is left.
    """
    if user_id == editor.id:
        raise LogisticsValidationError(_SELF_DELETE)
    project_id = await logistics_project_id(session)
    link = await session.get(UserRole, (user_id, project_id))
    if link is None:
        raise LogisticsNotFoundError(_USER_NOT_FOUND)
    if link.is_active:
        raise LogisticsValidationError(_DELETE_ACTIVE)
    await _replace_assets(session, user_id=user_id, asset_ids=[], editor=editor)
    await session.delete(link)
    await session.flush()
    remaining = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).first()
    if remaining is None:
        person = await session.get(User, user_id)
        if person is not None:
            person.is_deleted = True
            person.updated_at = datetime.now(UTC)
            session.add(person)
            await session.flush()
