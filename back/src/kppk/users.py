"""Project-scoped user dictionary and journal replay for KPPK."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole, User, UserRole
from src.auth.service import sync_project_memberships
from src.collab.journal import AppliedCell
from src.collab.locking import project_write
from src.config import get_settings
from src.kppk.constants import KPPK_PROJECT_KEY

from .core import KppkNotFoundError, KppkValidationError, _now, kppk_project_id
from .schemas import KppkUserCreateRequest, KppkUserRowRead, KppkUserUpdateRequest


async def read_kppk_user_rows(session: AsyncSession) -> list[KppkUserRowRead]:
    """Return the «КППК» project members (the users with a link to the project)."""
    project_id = await kppk_project_id(session)
    rows = (
        await session.exec(
            select(User, UserRole)
            .join(UserRole, col(UserRole.user_id) == col(User.id))
            .where(col(UserRole.project_id) == project_id, col(User.is_deleted).is_(False))
            .order_by(func.coalesce(User.name, User.email))
        )
    ).all()
    return [
        KppkUserRowRead(
            id=user.id,
            email=str(user.email or ""),
            fullname=user.name or "",
            isAdmin=link.role == ProjectRole.ADMIN,
            isActive=link.is_active,
            lastAuthenticatedAt=user.last_authenticated_at,
            createdAt=user.created_at,
            updatedAt=link.updated_at,
            updatedBy=link.updated_by,
        )
        for user, link in rows
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def create_kppk_user(
    session: AsyncSession,
    *,
    request: KppkUserCreateRequest,
    editor: User,
) -> tuple[User, list[AppliedCell]]:
    """Provision a project administrator without touching raw-material asset grants."""
    if not request.isAdmin:
        msg = "В КППК пока доступна только роль администратора."
        raise KppkValidationError(msg)
    project_id = await kppk_project_id(session)
    person = (await session.exec(select(User).where(func.lower(col(User.email)) == str(request.email)))).one_or_none()
    if person is not None and await session.get(UserRole, (person.id, project_id)) is not None:
        msg = "Пользователь уже добавлен в проект."
        raise KppkValidationError(msg)
    if person is None:
        person = User(email=request.email, name=request.fullname.strip())
        session.add(person)
        await session.flush()
    person.is_deleted = False
    session.add(person)
    link = UserRole(
        user_id=person.id, project_id=project_id, role=ProjectRole.ADMIN, is_active=True, updater_user_id=editor.id
    )
    session.add(link)
    await sync_project_memberships(session, person.id)
    await session.flush()
    snapshot = {
        "email": str(person.email or ""),
        "fullname": person.name or "",
        "role": link.role.value,
        "isActive": link.is_active,
    }
    return person, [
        AppliedCell(
            target_table=User.__tablename__, row_id=person.id, field="__created", old_value=None, new_value=snapshot
        )
    ]


@project_write(KPPK_PROJECT_KEY, exclusive=True)
async def update_kppk_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    request: KppkUserUpdateRequest,
    editor: User,
) -> list[AppliedCell]:
    if user_id == editor.id:
        msg = "Нельзя редактировать собственную запись в справочнике пользователей."
        raise KppkValidationError(msg)
    project_id = await kppk_project_id(session)
    person = await session.get(User, user_id)
    link = await session.get(UserRole, (user_id, project_id))
    if person is None or person.is_deleted or link is None:
        msg = "Пользователь не найден."
        raise KppkNotFoundError(msg)
    values = request.model_dump(exclude_unset=True)
    if get_settings().keycloak_enabled and any(
        key in values and values[key] != old for key, old in (("email", person.email), ("fullname", person.name))
    ):
        msg = "Email и ФИО управляются корпоративной учётной записью."
        raise KppkValidationError(msg)
    if request.email is not None:
        duplicate = (
            await session.exec(
                select(User).where(func.lower(col(User.email)) == str(request.email), col(User.id) != user_id)
            )
        ).first()
        if duplicate is not None:
            msg = "Пользователь с таким email уже существует."
            raise KppkValidationError(msg)
    applied: list[AppliedCell] = []
    for field, model, attribute, value in (
        ("email", person, "email", request.email),
        ("fullname", person, "name", request.fullname.strip() if request.fullname is not None else None),
        (
            "isAdmin",
            link,
            "role",
            None if request.isAdmin is None else ProjectRole.ADMIN if request.isAdmin else ProjectRole.ASSET_WORKER,
        ),
        ("isActive", link, "is_active", request.isActive),
    ):
        if value is None:
            continue
        old = getattr(model, attribute)
        if old == value:
            continue
        setattr(model, attribute, value)
        applied.append(
            AppliedCell(
                target_table=User.__tablename__,
                row_id=user_id,
                field=field,
                old_value=(old == ProjectRole.ADMIN) if field == "isAdmin" else old,
                new_value=(value == ProjectRole.ADMIN) if field == "isAdmin" else value,
            )
        )
    if request.resetPassword:
        if get_settings().keycloak_enabled:
            msg = "Пароль управляется корпоративной учётной записью."
            raise KppkValidationError(msg)
        person.password_hash = None
    person.updated_at = _now()
    link.updated_at = _now()
    link.updater_user_id = editor.id
    session.add(person)
    session.add(link)
    await session.flush()
    return applied


async def apply_kppk_user_value(
    session: AsyncSession,
    *,
    row_id: UUID,
    field: str,
    value: object,
    editor: User,
) -> None:
    """Replay only the KPPK membership; never dispatch shared users to the raw project."""
    if field not in {"__created", "__deleted"}:
        await update_kppk_user(
            session, user_id=row_id, request=KppkUserUpdateRequest.model_validate({field: value}), editor=editor
        )
        return
    if value is None:
        if field == "__deleted":
            project_id = await kppk_project_id(session)
            link = await session.get(UserRole, (row_id, project_id))
            if row_id == editor.id or (link is not None and link.is_active):
                msg = "Сначала снимите доступ пользователя к проекту; удалить себя нельзя."
                raise KppkValidationError(msg)
        await delete_kppk_project_user(session, user_id=row_id)
        return
    if not isinstance(value, dict):
        msg = "Некорректное действие над пользователем."
        raise KppkValidationError(msg)
    person = await session.get(User, row_id)
    if person is None:
        msg = "Пользователь не найден."
        raise KppkNotFoundError(msg)
    project_id = await kppk_project_id(session)
    person.is_deleted = False
    session.add(person)
    link = await session.get(UserRole, (row_id, project_id))
    if link is None:
        link = UserRole(user_id=row_id, project_id=project_id, role=ProjectRole.ADMIN)
    link.role = ProjectRole(str(value["role"]))
    link.is_active = bool(value["isActive"])
    link.updater_user_id = editor.id
    link.updated_at = _now()
    session.add(link)
    await session.flush()


async def delete_kppk_project_user(session: AsyncSession, *, user_id: UUID) -> None:
    """
    Remove a user from «КППК» (the КППК subscription is its `UserRole` link).

    The account is soft-deleted once no project link is left, so the person
    disappears from the dictionaries instead of lingering as an empty row.
    """
    project_id = await kppk_project_id(session)
    user = await session.get(User, user_id)
    if user is None or user.is_deleted:
        message = "Пользователь не найден."
        raise KppkNotFoundError(message)
    link = await session.get(UserRole, (user_id, project_id))
    if link is None:
        message = "Пользователь не найден."
        raise KppkNotFoundError(message)
    await session.delete(link)
    await session.flush()
    remaining = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).first()
    if remaining is None:
        user.is_deleted = True
        user.updated_at = datetime.now(UTC)
        session.add(user)
        await session.flush()
