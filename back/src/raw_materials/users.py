"""Raw-material project memberships, asset grants and user replay."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.constants import RAW_MATERIALS_PROJECT_KEY
from src.auth.emails import is_valid_email, normalize_email
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.service import sync_project_memberships
from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.config import get_settings
from src.raw_materials.models import RawMaterialAsset, RawMaterialUserAsset
from src.raw_materials.schemas import UserRowRead

from .errors import DICT_CREATED_SNAPSHOT_INVALID as _DICT_CREATED_SNAPSHOT_INVALID
from .errors import DictionaryReferenceError

_DICT_REF_USER_NOT_FOUND = ValueError("Пользователь не найден.")

_DICT_REF_USER_MANAGED_BY_KEYCLOAK = DictionaryReferenceError(
    "Нельзя изменить email и ФИО: учётные записи управляются Keycloak."
)

_DICT_REF_USER_DELETE_FORBIDDEN = DictionaryReferenceError(
    "Удаление пользователей недоступно. Чтобы ограничить доступ, отключите флаг «Активен»."
)

_DICT_REF_USER_BAD_EMAIL = DictionaryReferenceError("Некорректный email.")


async def read_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
) -> list[UserRowRead]:
    """
    «Пользователи» dictionary rows (the «Сырьё» project, without deleted).

    Shows all users who have a link with the project (including inactive
    ones — the project admin activates them). isActive and isAdmin come
    from the per-project link, not from the user's global fields.
    """
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).first()
    if project_id is None:
        return []
    query = (
        select(User, UserRole)
        .join(UserRole, col(UserRole.user_id) == col(User.id))
        .where(col(UserRole.project_id) == project_id, col(User.is_deleted).is_(False))
        .order_by(col(UserRole.is_active).desc(), func.coalesce(User.name, ""))
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    rows = (await session.exec(query)).all()
    users = [user for user, _ in rows]
    links_by_user = {user.id: link for user, link in rows}
    asset_links = (
        await session.exec(
            select(RawMaterialUserAsset, RawMaterialAsset)
            .join(RawMaterialAsset, col(RawMaterialAsset.id) == col(RawMaterialUserAsset.asset_id))
            .where(col(RawMaterialUserAsset.user_id).in_([user.id for user in users]))
            .order_by(RawMaterialAsset.name)
        )
    ).all()
    names_by_user: dict[UUID, list[str]] = {}
    for link, asset in asset_links:
        names_by_user.setdefault(link.user_id, []).append(asset.name)
    return [
        UserRowRead(
            id=user.id,
            email=user.email,
            fullname=user.name or "",
            isAdmin=link.role == ProjectRole.ADMIN,
            assetNames=names_by_user.get(user.id, []),
            isActive=link.is_active,
            lastAuthenticatedAt=user.last_authenticated_at,
            createdAt=user.created_at,
            updatedAt=link.updated_at,
            updatedBy=link.updated_by,
        )
        for user, link in ((user, links_by_user[user.id]) for user in users)
    ]


async def _apply_deleted_user(session: AsyncSession, *, row_id: UUID, snapshot: object, updater_user_id: UUID) -> None:
    """
    Undo/redo of a deleted «Сырьё» user: the project link and the asset grants.

    The account follows the snapshot: restoring it clears the orphan soft-delete,
    redo removes the link again (and soft-deletes the account when it is the
    last one).
    """
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RAW_MATERIALS_PROJECT_KEY))
    ).first()
    if project_id is None:
        return
    link = await session.get(UserRole, (row_id, project_id))
    if snapshot is None:
        if link is None:
            return
        grants = (await session.exec(select(RawMaterialUserAsset).where(RawMaterialUserAsset.user_id == row_id))).all()
        for grant in grants:
            await session.delete(grant)
        await session.delete(link)
        await session.flush()
        await _soft_delete_if_orphaned(session, row_id)
        return
    if not isinstance(snapshot, dict):
        raise _DICT_CREATED_SNAPSHOT_INVALID
    user = await session.get(User, row_id)
    if user is None:
        return
    if user.is_deleted:
        user.is_deleted = False
        user.updated_at = datetime.now(UTC)
        session.add(user)
    fullname = snapshot.get("fullname")
    if isinstance(fullname, str) and fullname:
        user.name = fullname
        session.add(user)
    now = datetime.now(UTC)
    if link is None:
        link = UserRole(
            user_id=row_id,
            project_id=project_id,
            role=ProjectRole.ADMIN if snapshot.get("isAdmin") else ProjectRole.ASSET_WORKER,
            is_active=bool(snapshot.get("isActive", True)),
            created_at=now,
            updated_at=now,
            updater_user_id=updater_user_id,
        )
    else:
        link.role = ProjectRole.ADMIN if snapshot.get("isAdmin") else ProjectRole.ASSET_WORKER
        link.is_active = bool(snapshot.get("isActive", True))
        link.updated_at = now
        link.updater_user_id = updater_user_id
    session.add(link)
    await session.flush()
    asset_ids = snapshot.get("assetIds")
    for asset_id in asset_ids if isinstance(asset_ids, list) else []:
        grant = await session.get(RawMaterialUserAsset, (row_id, UUID(str(asset_id))))
        if grant is None:
            session.add(
                RawMaterialUserAsset(
                    user_id=row_id,
                    asset_id=UUID(str(asset_id)),
                    created_at=now,
                )
            )


async def delete_raw_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a user from the «Сырьё» project.

    The «Сырьё» link and the per-asset grants go; the account itself is
    soft-deleted only when no project link is left, so the person really
    disappears from the dictionaries. Journaled as an undoable «__deleted».
    """
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_REF_USER_NOT_FOUND
    link = await session.get(UserRole, (user_id, project_id))
    if link is None:
        raise _DICT_REF_USER_NOT_FOUND
    asset_rows = (await session.exec(select(RawMaterialUserAsset).where(RawMaterialUserAsset.user_id == user_id))).all()
    snapshot = {
        "email": str(user.email or ""),
        "fullname": user.name or "",
        "isAdmin": link.role == ProjectRole.ADMIN,
        "isActive": link.is_active,
        "assetIds": [str(row.asset_id) for row in asset_rows],
    }
    for row in asset_rows:
        await session.delete(row)
    await session.delete(link)
    await session.flush()
    await _soft_delete_if_orphaned(session, user_id)
    return [
        AppliedCell(
            target_table=User.__tablename__,
            row_id=user_id,
            field="__deleted",
            old_value=snapshot,
            new_value=None,
        )
    ]


async def _soft_delete_if_orphaned(session: AsyncSession, user_id: UUID) -> None:
    """
    Close the account once it has no project link left.

    A user is visible in «Пользователи» through a link; with the last one gone
    the account would linger as a ghost, so it is soft-deleted instead. The
    row and its history stay for the journal/undo.
    """
    raw_link = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).first()
    user = await session.get(User, user_id)
    if user is None or raw_link is not None:
        return
    user.is_deleted = True
    user.updated_at = datetime.now(UTC)
    session.add(user)
    await session.flush()


async def _user_asset_names(session: AsyncSession, user_id: UUID) -> list[str]:
    rows = (
        await session.exec(
            select(RawMaterialAsset.name)
            .join(RawMaterialUserAsset, col(RawMaterialUserAsset.asset_id) == col(RawMaterialAsset.id))
            .where(RawMaterialUserAsset.user_id == user_id)
            .order_by(RawMaterialAsset.name)
        )
    ).all()
    return list(rows)


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def create_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    is_admin: bool,
    asset_names: list[str],
    updater_user_id: UUID | None = None,
) -> User:
    """
    Add a user to the «Сырьё» project with role and assets.

    The user is immediately provisioned into all projects: active with the
    chosen role in the current one, inactive with the asset-worker role in
    the others (the admin opens project access with the «Активен» flag). If
    a user with this email already exists (e.g. created by another admin or
    logged in via Keycloak) but has no link with the current project — only
    the project link is created; the user's existing data is not
    overwritten (FIO/email come from the identity after a Keycloak login).
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise _DICT_REF_USER_BAD_EMAIL
    # Case-insensitive lookup: addresses are case-insensitive and the DB may
    # hold records created before normalization.
    existing = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
    if existing is not None:
        link = (
            await session.exec(
                select(UserRole).where(UserRole.user_id == existing.id, UserRole.project_id == project_id)
            )
        ).one_or_none()
        if link is not None:
            message = f"Пользователь с email {email} уже существует."
            raise DictionaryReferenceError(message)
        user = existing
    else:
        now = datetime.now(UTC)
        user = User(
            email=email,
            name=fullname.strip(),
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
    role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    now = datetime.now(UTC)
    session.add(
        UserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
            updater_user_id=updater_user_id,
        )
    )
    await replace_user_assets(session, user.id, asset_names)
    # Provisioning into all other projects: new links are inactive with the
    # asset-worker role; existing links are left untouched.
    await sync_project_memberships(session, user.id)
    await session.flush()
    return user


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def update_project_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    email: str | None,
    fullname: str | None,
    is_admin: bool | None,
    asset_names: list[str] | None,
    is_active: bool | None,
    reset_password: bool,
    updater_user_id: UUID | None = None,
) -> tuple[User, list[AppliedCell]]:
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_REF_USER_NOT_FOUND
    link = (
        await session.exec(select(UserRole).where(UserRole.user_id == user_id, UserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_REF_USER_NOT_FOUND
    if get_settings().keycloak_enabled:
        # With Keycloak enabled, email and FIO come from the identity on every
        # login (upsert_current_user), so editing them here is forbidden.
        if email is not None and normalize_email(email) != normalize_email(user.email or ""):
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
        if fullname is not None and fullname.strip() != (user.name or ""):
            raise _DICT_REF_USER_MANAGED_BY_KEYCLOAK
    old_email = user.email
    old_fullname = user.name
    old_is_admin = link.role == ProjectRole.ADMIN
    old_is_active = link.is_active
    old_asset_names = await _user_asset_names(session, user.id)
    new_email = normalize_email(email) if email is not None else old_email
    new_fullname = fullname.strip() if fullname is not None else old_fullname
    new_is_admin = is_admin if is_admin is not None else old_is_admin
    new_is_active = is_active if is_active is not None else old_is_active
    new_asset_names = asset_names if asset_names is not None else ([] if is_admin is not None else old_asset_names)
    if email is not None:
        candidate = normalize_email(email)
        if not is_valid_email(candidate):
            raise _DICT_REF_USER_BAD_EMAIL
        other = (await session.exec(select(User).where(func.lower(col(User.email)) == candidate))).one_or_none()
        if other is not None and other.id != user.id:
            message = f"Пользователь с email {candidate} уже существует."
            raise DictionaryReferenceError(message)
        user.email = candidate
    if fullname is not None:
        user.name = fullname.strip()
    if is_active is not None:
        link.is_active = is_active
    if reset_password:
        user.password_hash = None
    role_changed = is_admin is not None and (link.role == ProjectRole.ADMIN) != is_admin
    if is_admin is not None:
        link.role = ProjectRole.ADMIN if is_admin else ProjectRole.ASSET_WORKER
    if asset_names is not None or role_changed:
        await replace_user_assets(
            session,
            user.id,
            asset_names if asset_names is not None else ([] if is_admin else None),
        )
    link.updated_at = datetime.now(UTC)
    link.updater_user_id = updater_user_id
    user.updated_at = datetime.now(UTC)
    session.add(user)
    session.add(link)
    await session.flush()
    applied: list[AppliedCell] = []
    if new_email != old_email:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="email",
                old_value=old_email,
                new_value=new_email,
            )
        )
    if new_fullname != old_fullname:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="fullname",
                old_value=old_fullname,
                new_value=new_fullname,
            )
        )
    if new_is_admin != old_is_admin:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="isAdmin",
                old_value=old_is_admin,
                new_value=new_is_admin,
            )
        )
    if new_is_active != old_is_active:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="isActive",
                old_value=old_is_active,
                new_value=new_is_active,
            )
        )
    if new_asset_names != old_asset_names:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="assetNames",
                old_value=old_asset_names,
                new_value=new_asset_names,
            )
        )
    return user, applied


@project_write(RAW_MATERIALS_PROJECT_KEY, exclusive=True)
async def delete_project_user(
    session: AsyncSession,  # noqa: ARG001 — call contract
    *,
    project_id: UUID,  # noqa: ARG001 — call contract
    user_id: UUID,  # noqa: ARG001 — call contract
) -> None:
    """
    Reject user deletion from the project.

    Instead of deleting, the admin disables access with the «Активен» flag
    (is_active=False): the account and project links stay, only the access
    to the «Сырьё» project is closed.
    """
    raise _DICT_REF_USER_DELETE_FORBIDDEN


async def replace_user_assets(session: AsyncSession, user_id: UUID, asset_names: list[str] | None) -> None:
    """Replace the user's asset assignment (None — only remove admin links)."""
    existing = (
        await session.exec(select(RawMaterialUserAsset).where(col(RawMaterialUserAsset.user_id) == user_id))
    ).all()
    for link in existing:
        await session.delete(link)
    # Deletions flush before inserts: the links have a unique key (user_id, asset_id).
    await session.flush()
    if asset_names:
        assets = (await session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).in_(asset_names)))).all()
        names_found = {asset.name for asset in assets}
        missing = [name for name in asset_names if name not in names_found]
        if missing:
            message = f"Активы не найдены: {', '.join(missing)}"
            raise DictionaryReferenceError(message)
        now = datetime.now(UTC)
        for asset in assets:
            session.add(RawMaterialUserAsset(user_id=user_id, asset_id=asset.id, created_at=now))
    await session.flush()
