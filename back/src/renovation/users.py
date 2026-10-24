"""Project-user dictionary operations for the renovation project."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.emails import is_valid_email, normalize_email
from src.auth.models import Project, User, UserRole
from src.auth.service import set_project_role_active, sync_project_memberships
from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.raw_materials.service import CellValidationError
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RAsset, RBlock, RProjectRole, RUserAsset, RUserRole
from src.renovation.schemas import RenovationUserRowRead

from .errors import RenovationDictionaryError, RenovationPlanNotFoundError

_DICT_USER_EXISTS = RenovationDictionaryError("Пользователь с таким email уже существует.")
_DICT_USER_NOT_FOUND = ValueError("Пользователь не найден.")
_DICT_USER_INVALID_EMAIL = RenovationDictionaryError("Некорректный email.")
_DICT_BLOCK_REQUIRED = RenovationDictionaryError("Для этой роли укажите блок ответственности")
_DICT_ASSET_REQUIRED = RenovationDictionaryError("Работнику по активу укажите хотя бы один актив.")
_UNSET = object()
_ERR_NO_RENOVATION_PROJECT = RenovationPlanNotFoundError("Проект «Ремонт» не найден.")


async def _renovation_project_id_required(session: AsyncSession) -> UUID:
    project_id = (
        await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))
    ).one_or_none()
    if project_id is None:
        raise _ERR_NO_RENOVATION_PROJECT
    return project_id


async def _apply_renovation_user_cell(
    session: AsyncSession,
    row_id: UUID,
    field: str,
    value: object,
    user: User,
) -> None:
    """Apply a renovation project-user dictionary cell (undo/redo)."""
    now = datetime.now(UTC)
    user_row = await session.get(User, row_id)
    if user_row is None or user_row.is_deleted:
        raise _DICT_USER_NOT_FOUND
    project_id = await _renovation_project_id_required(session)
    link = (
        await session.exec(select(RUserRole).where(RUserRole.user_id == row_id, RUserRole.project_id == project_id))
    ).one_or_none()
    if field == "email":
        new_email = value if isinstance(value, str) and value.strip() else None
        if new_email is not None:
            user_row.email = new_email
    elif field == "fullname":
        user_row.name = value if isinstance(value, str) else ""
    elif field == "role":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        try:
            new_role = RProjectRole(value) if isinstance(value, str) else RProjectRole.ASSET_WORKER
        except ValueError as error:
            message = f"Неизвестная роль «{value}»."
            raise CellValidationError(message) from error
        link.role = new_role
        if new_role in (RProjectRole.ADMIN, RProjectRole.APPROVER):
            link.block = None
        if new_role != RProjectRole.ASSET_WORKER:
            # Central roles and block owners implicitly see all assets — clear grants.
            await replace_renovation_user_assets(session, project_id, user_row.id, [])
    elif field == "block":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        try:
            link.block = RBlock(value) if isinstance(value, str) and value else None
        except ValueError as error:
            message = f"Неизвестный блок «{value}»."
            raise CellValidationError(message) from error
    elif field == "isActive":
        if link is None:
            raise _DICT_USER_NOT_FOUND
        link.is_active = bool(value)
    elif field == "assetNames":
        await replace_renovation_user_assets(
            session, project_id, user_row.id, value if isinstance(value, list) else None
        )
    else:
        message = f"Неизвестное поле «{field}» справочника «Пользователи»."
        raise CellValidationError(message)
    user_row.updated_at = now
    session.add(user_row)
    if link is not None:
        link.updated_at = now
        link.updater_user_id = user.id
        session.add(link)
        # Undo/redo of an activation must mirror onto the cross-project auth link
        # (see update_renovation_user); otherwise a Ctrl+Z (undo) would hand the
        # user access they don't have, or revoke access they keep in the UI.
        await set_project_role_active(
            session,
            user_id=row_id,
            project_id=project_id,
            active=link.is_active,
        )


async def read_renovation_user_rows(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
) -> list[RenovationUserRowRead]:
    """
    «Пользователи» dictionary rows (the «Ремонт» project, without deleted).

    Shows all users with an r_user_roles link (including inactive ones —
    the project admin activates them). isActive and isAdmin come from the
    per-project link, not from the user's global fields.
    """
    project_id = (await session.exec(select(col(Project.id)).where(col(Project.key) == RENOVATION_PROJECT_KEY))).first()
    if project_id is None:
        return []
    query = (
        select(User, RUserRole)
        .join(RUserRole, col(RUserRole.user_id) == col(User.id))
        .where(col(RUserRole.project_id) == project_id, col(User.is_deleted).is_(False))
        .order_by(col(RUserRole.is_active).desc(), func.coalesce(User.name, ""))
    )
    if user_id is not None:
        query = query.where(col(User.id) == user_id)
    rows = (await session.exec(query)).all()
    users = [user for user, _ in rows]
    links_by_user = {user.id: link for user, link in rows}
    asset_links = (
        await session.exec(
            select(RUserAsset, RAsset)
            .join(RAsset, col(RAsset.id) == col(RUserAsset.asset_id))
            .where(col(RUserAsset.user_id).in_([user.id for user in users]))
            .order_by(RAsset.name)
        )
    ).all()
    names_by_user: dict[UUID, list[str]] = {}
    for link, asset in asset_links:
        names_by_user.setdefault(link.user_id, []).append(asset.name)
    rows: list[RenovationUserRowRead] = []
    for user, link in ((user, links_by_user[user.id]) for user in users):
        # Admin/approver/block_owner implicitly see all assets — no list shown.
        central = link.role in (RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER)
        rows.append(
            RenovationUserRowRead(
                id=user.id,
                email=user.email or "",
                fullname=user.name or "",
                role=link.role,
                block=link.block,
                assetNames=[] if central else names_by_user.get(user.id, []),
                isActive=link.is_active,
                lastAuthenticatedAt=user.last_authenticated_at,
                createdAt=user.created_at,
                updatedAt=link.updated_at,
                updatedBy=link.updated_by,
            )
        )
    return rows


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def create_renovation_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    email: str,
    fullname: str,
    role: RProjectRole,
    block: RBlock | None,
    asset_names: list[str],
    updater_user_id: UUID | None = None,
) -> User:
    """
    Add a user to the «Ремонт» project with role and assets.

    The existing app_users record is re-used when present; the renovation
    link (r_user_roles) is created with the chosen role, and the user is
    provisioned into all projects through sync_project_memberships (the
    same cross-project visibility as the «Сырьё» users dictionary).
    Admin, approver and block owners implicitly see all assets — no per-asset
    links are stored; only an asset worker carries an asset grant list.
    """
    needs_block = role in (RProjectRole.BLOCK_OWNER, RProjectRole.ASSET_WORKER)
    implicit_all_assets = role in (RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER)
    if needs_block and block is None:
        raise _DICT_BLOCK_REQUIRED
    if role == RProjectRole.ASSET_WORKER and not asset_names:
        raise _DICT_ASSET_REQUIRED
    email = normalize_email(email)
    if not is_valid_email(email):
        raise _DICT_USER_INVALID_EMAIL
    existing = (await session.exec(select(User).where(func.lower(col(User.email)) == email))).one_or_none()
    if existing is not None:
        link = (
            await session.exec(
                select(RUserRole).where(RUserRole.user_id == existing.id, RUserRole.project_id == project_id)
            )
        ).one_or_none()
        if link is not None:
            raise _DICT_USER_EXISTS
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
    now = datetime.now(UTC)
    session.add(
        RUserRole(
            user_id=user.id,
            project_id=project_id,
            role=role,
            block=None if not needs_block else block,
            is_active=True,
            created_at=now,
            updated_at=now,
            updater_user_id=updater_user_id,
        )
    )
    await replace_renovation_user_assets(
        session,
        project_id,
        user.id,
        asset_names if not implicit_all_assets else [],
    )
    await sync_project_memberships(session, user.id)
    await session.flush()
    return user


@project_write(RENOVATION_PROJECT_KEY, exclusive=True)
async def update_renovation_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    fullname: str | None = None,
    role: RProjectRole | None = None,
    block: RBlock | object | None = _UNSET,
    asset_names: list[str] | None = None,
    is_active: bool | None = None,
    updater_user_id: UUID | None = None,
) -> tuple[User, list[AppliedCell]]:
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_USER_NOT_FOUND
    link = (
        await session.exec(select(RUserRole).where(RUserRole.user_id == user_id, RUserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_USER_NOT_FOUND
    old_fullname = user.name
    old_role = link.role
    old_block = link.block
    old_is_active = link.is_active
    old_asset_names = await _renovation_user_asset_names(session, project_id, user.id)
    new_fullname = fullname.strip() if fullname is not None else old_fullname
    new_role = role if role is not None else old_role
    block_provided = block is not _UNSET
    new_block = block if isinstance(block, RBlock) else (old_block if not block_provided else None)
    if (
        new_role in (RProjectRole.BLOCK_OWNER, RProjectRole.ASSET_WORKER)
        and new_block is None
        and (role is not None or block_provided)
    ):
        raise _DICT_BLOCK_REQUIRED
    new_is_active = is_active if is_active is not None else old_is_active
    if fullname is not None:
        user.name = new_fullname
    if is_active is not None:
        link.is_active = is_active
    role_changed = new_role != old_role
    if role is not None:
        link.role = new_role
    if block_provided:
        link.block = new_block
    # Central admins/approvers drop their block; a block owner keeps theirs
    # (block_owner is a block role too). Only an asset worker carries per-asset
    # grants — admin/approver/block_owner implicitly see all assets, so stored
    # r_user_assets links are cleared for them.
    central_no_block = new_role in (RProjectRole.ADMIN, RProjectRole.APPROVER)
    implicit_all_assets = new_role in (RProjectRole.ADMIN, RProjectRole.APPROVER, RProjectRole.BLOCK_OWNER)
    if role_changed and central_no_block:
        link.block = None
    if implicit_all_assets:
        await replace_renovation_user_assets(session, project_id, user.id, [])
        role_asset_names: list[str] = []
    else:
        if role_changed and asset_names is None:
            raise _DICT_ASSET_REQUIRED
        if asset_names is not None:
            await replace_renovation_user_assets(session, project_id, user.id, asset_names or None)
        role_asset_names = asset_names if asset_names is not None else old_asset_names
    new_asset_names = role_asset_names if role_changed else old_asset_names
    now = datetime.now(UTC)
    link.updated_at = now
    link.updater_user_id = updater_user_id
    user.updated_at = now
    session.add(user)
    session.add(link)
    # Keep the cross-project auth link in step with the renovation membership,
    # so the user can actually enter the project (the root redirect and /api/me
    # are gated on the auth UserRole, not the r_user_roles dictionary row).
    await set_project_role_active(
        session,
        user_id=user.id,
        project_id=project_id,
        active=link.is_active,
    )
    await session.flush()
    applied: list[AppliedCell] = []
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
    if role_changed:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="role",
                old_value=old_role.value,
                new_value=new_role.value,
            )
        )
    if block_provided and new_block != old_block:
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user.id,
                field="block",
                old_value=old_block.value if old_block is not None else None,
                new_value=new_block.value if new_block is not None else None,
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


async def _renovation_user_asset_names(session: AsyncSession, project_id: UUID, user_id: UUID) -> list[str]:
    rows = (
        await session.exec(
            select(RAsset.name)
            .join(RUserAsset, col(RUserAsset.asset_id) == col(RAsset.id))
            .where(RUserAsset.user_id == user_id, RUserAsset.project_id == project_id)
            .order_by(RAsset.name)
        )
    ).all()
    return list(rows)


async def delete_renovation_user(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> list[AppliedCell]:
    """
    Delete a user from the «Ремонт» project.

    The renovation link (role, block, activity) and the asset grants are
    removed; the account itself stays while it is linked to another project —
    it is only soft-deleted when no project link is left, so the person really
    disappears instead of lingering as an empty row. The deletion is journaled
    as an undoable «__deleted» snapshot that restores both.

    A user who fixed a movement month cannot be deleted (the fix records who
    did it) — the RESTRICT key is a real constraint, surfaced as a clear 409.
    """
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        raise _DICT_USER_NOT_FOUND
    link = (
        await session.exec(select(RUserRole).where(RUserRole.user_id == user_id, RUserRole.project_id == project_id))
    ).one_or_none()
    if link is None:
        raise _DICT_USER_NOT_FOUND
    grant_rows = (
        await session.exec(
            select(RUserAsset).where(col(RUserAsset.user_id) == user_id, col(RUserAsset.project_id) == project_id)
        )
    ).all()
    asset_names = await _renovation_user_asset_names(session, project_id, user_id)
    snapshot = {
        "email": str(user.email or ""),
        "fullname": user.name or "",
        "role": link.role.value,
        "block": link.block.value if link.block is not None else None,
        "isActive": link.is_active,
        "assetNames": asset_names,
    }
    for grant in grant_rows:
        await session.delete(grant)
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


async def restore_renovation_user(
    session: AsyncSession,
    *,
    row_id: UUID,
    snapshot: dict[str, object],
    updater_user_id: UUID,
) -> None:
    """Restore a deleted renovation user link (undo of the deletion)."""
    user = await session.get(User, row_id)
    if user is not None and user.is_deleted:
        user.is_deleted = False
        user.updated_at = datetime.now(UTC)
        session.add(user)
    project_id = await _renovation_project_id_required(session)
    now = datetime.now(UTC)
    role_value = str(snapshot.get("role") or RProjectRole.ASSET_WORKER.value)
    role = next((item for item in RProjectRole if item.value == role_value), RProjectRole.ASSET_WORKER)
    block_value = snapshot.get("block")
    block = next((item for item in RBlock if item.value == block_value), None) if block_value else None
    fullname = str(snapshot.get("fullname") or "")
    if user is not None and fullname:
        user.name = fullname
        session.add(user)
    link = await session.get(RUserRole, (row_id, project_id))
    if link is None:
        link = RUserRole(
            user_id=row_id,
            project_id=project_id,
            role=role,
            block=block,
            is_active=bool(snapshot.get("isActive", True)),
            created_at=now,
            updated_at=now,
            updater_user_id=updater_user_id,
        )
    else:
        link.role = role
        link.block = block
        link.is_active = bool(snapshot.get("isActive", True))
        link.updated_at = now
        link.updater_user_id = updater_user_id
    session.add(link)
    await session.flush()
    asset_names = snapshot.get("assetNames")
    await replace_renovation_user_assets(
        session,
        project_id,
        row_id,
        [str(name) for name in asset_names] if isinstance(asset_names, list) else [],
    )


async def _soft_delete_if_orphaned(session: AsyncSession, user_id: UUID) -> None:
    """
    Close the account once it has no project link left.

    A user is visible in «Пользователи» through a link; with the last one gone
    the account would linger as a ghost, so it is soft-deleted instead. The
    row and its history stay for the journal/undo.
    """
    raw_link = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).first()
    renovation_link = (await session.exec(select(RUserRole).where(col(RUserRole.user_id) == user_id))).first()
    if raw_link is not None or renovation_link is not None:
        return
    user = await session.get(User, user_id)
    if user is None:
        return
    user.is_deleted = True
    user.updated_at = datetime.now(UTC)
    session.add(user)
    await session.flush()


async def replace_renovation_user_assets(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    asset_names: list[str] | None,
) -> None:
    """Replace the user's renovation asset assignment (None — only remove links)."""
    existing = (
        await session.exec(
            select(RUserAsset).where(col(RUserAsset.user_id) == user_id, col(RUserAsset.project_id) == project_id)
        )
    ).all()
    for link in existing:
        await session.delete(link)
    await session.flush()
    if asset_names:
        assets = (await session.exec(select(RAsset).where(col(RAsset.name).in_(asset_names)))).all()
        names_found = {asset.name for asset in assets}
        missing = [name for name in asset_names if name not in names_found]
        if missing:
            message = f"Активы не найдены: {', '.join(missing)}"
            raise RenovationDictionaryError(message)
        now = datetime.now(UTC)
        for asset in assets:
            session.add(RUserAsset(user_id=user_id, project_id=project_id, asset_id=asset.id, created_at=now))
    await session.flush()
