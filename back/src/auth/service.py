import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.app_logging import log_event
from src.auth.constants import LOCAL_USER_KEY
from src.auth.models import Project, ProjectRole, User, UserRole
from src.auth.passwords import hash_password, validate_new_password, verify_password
from src.auth.schemas import AssetAccessRead, CurrentUserRead, ProjectAccessRead
from src.config import get_settings
from src.raw_materials.models import RawMaterialAsset, RawMaterialUserAsset

LOGGER = logging.getLogger("reportcollector.application")


class InvalidCredentialsError(Exception):
    """Логин или пароль не подходят."""


_INVALID_CREDENTIALS = InvalidCredentialsError("Неверный логин или пароль.")


@dataclass(frozen=True)
class CurrentUserIdentity:
    email: str | None
    email_verified: bool | None
    family_name: str | None
    given_name: str | None
    issuer: str | None
    key: str
    name: str | None
    oidc_sub: str | None
    preferred_username: str | None


class UnknownUserError(Exception):
    """Пользователь не зарегистрирован в ReportCollector."""


class BlockedUserError(Exception):
    """Пользователь заблокирован в ReportCollector."""


_UNKNOWN_USER = ValueError("Пользователь не найден.")
_USER_BLOCKED = BlockedUserError("Пользователь заблокирован в ReportCollector.")


async def upsert_current_user(session: AsyncSession, identity: CurrentUserIdentity) -> User:
    """
    Регистрирует или обновляет пользователя по данным OIDC-идентичности.

    Новый пользователь появляется у администраторов всех проектов: связь со
    всеми существующими проектами создаётся сразу, но с is_active=False.
    Доступ к конкретному проекту даёт администратор, включив is_active
    в своей проекте. Пользователь из AUTH_SEED_ADMIN_* создаётся активным
    и получает роль admin во всех проектах.
    """
    canonical_key = identity.email or identity.preferred_username or identity.key
    log_event(
        LOGGER,
        "keycloak_identity_received",
        "Получены идентификационные данные пользователя.",
        user_email=identity.email,
        user_key=identity.key,
        user_name=identity.name,
        user_sub=identity.oidc_sub,
        oidc_issuer=identity.issuer,
        preferred_username=identity.preferred_username,
        given_name=identity.given_name,
        family_name=identity.family_name,
        email_verified=identity.email_verified,
    )

    user = await get_user_by_identity(session, identity)
    seed_admin_email = get_settings().auth_seed_admin_email
    is_seed_admin = bool(seed_admin_email) and identity.email == seed_admin_email

    if user is None:
        user = User(
            key=canonical_key,
            email=identity.email,
            name=identity.name,
            oidc_sub=identity.oidc_sub,
            oidc_issuer=identity.issuer,
            preferred_username=identity.preferred_username,
            email_verified=identity.email_verified,
        )
        log_event(
            LOGGER,
            "user_created",
            "Создан локальный пользователь по данным OIDC.",
            user_email=user.email,
            user_key=user.key,
            user_created_active=is_seed_admin,
        )

    user.email = identity.email or user.email
    user.key = canonical_key
    user.name = identity.name or user.name
    user.oidc_sub = identity.oidc_sub or user.oidc_sub
    user.oidc_issuer = identity.issuer or user.oidc_issuer
    user.preferred_username = identity.preferred_username or user.preferred_username
    user.email_verified = identity.email_verified if identity.email_verified is not None else user.email_verified
    user.last_authenticated_at = datetime.now(UTC)
    session.add(user)
    await session.flush()

    # Провижининг: у пользователя есть связь со всеми проектами. Новые связи
    # создаются неактивными; seed-админ активен и администрирует все проекты.
    await sync_project_memberships(session, user.id, activate_admin=is_seed_admin)

    await session.commit()
    await session.refresh(user)

    if user.is_deleted:
        log_event(
            LOGGER,
            "blocked_user_attempt",
            "Заблокированный пользователь попытался получить доступ.",
            user_email=user.email,
            user_key=user.key,
            user_sub=user.oidc_sub,
        )
        blocked_message = "Пользователь заблокирован в ReportCollector."
        raise BlockedUserError(blocked_message)

    return user


async def sync_project_memberships(session: AsyncSession, user_id: UUID, *, activate_admin: bool = False) -> None:
    """
    Гарантирует связь пользователя со всеми проектами.

    Недостающие связи создаются с is_active=False и ролью asset_worker; для
    seed-админа (activate_admin=True) новые связи активны с ролью admin.
    Существующие связи не изменяются.
    """
    projects = (await session.exec(select(Project).order_by(Project.key))).all()
    existing = (
        await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))
    ).all()
    existing_by_project = {role.project_id: role for role in existing}
    now = datetime.now(UTC)
    for project in projects:
        if project.id in existing_by_project:
            continue
        session.add(
            UserRole(
                user_id=user_id,
                project_id=project.id,
                role=ProjectRole.ADMIN if activate_admin else ProjectRole.ASSET_WORKER,
                is_active=activate_admin,
                created_at=now,
                updated_at=now,
                updated_by="",
            )
        )
    await session.flush()


async def authenticate_local_user(session: AsyncSession, email: str, password: str) -> User:
    """
    Вход по логину (email) и паролю.

    Пользователь с пустым password_hash (например, заведённый администратором
    без пароля) устанавливает пароль при первом входе. При несовпадении пароля
    или отсутствии пользователя возвращается InvalidCredentialsError.
    """
    normalized_email = email.strip()
    user = (
        await session.exec(
            select(User).where(
                func.lower(col(User.email)) == normalized_email.casefold(),
                col(User.is_deleted).is_(False),
            )
        )
    ).one_or_none()
    if user is None:
        log_event(
            LOGGER,
            "local_login_failed",
            "Неудачная попытка локального входа: пользователь не найден.",
            login_email=normalized_email,
        )
        raise _INVALID_CREDENTIALS

    stored_hash = user.password_hash
    if stored_hash is None:
        try:
            validate_new_password(password)
        except ValueError:
            log_event(
                LOGGER,
                "local_login_failed",
                "Неудачная попытка локального входа: слабый первоначальный пароль.",
                user_email=user.email,
                user_key=user.key,
            )
            raise _INVALID_CREDENTIALS from None
        user.password_hash = hash_password(password)
        log_event(
            LOGGER,
            "local_password_set",
            "Установлен первоначальный пароль локального пользователя.",
            user_email=user.email,
            user_key=user.key,
        )
    elif not verify_password(password, stored_hash):
        log_event(
            LOGGER,
            "local_login_failed",
            "Неудачная попытка локального входа: неверный пароль.",
            user_email=user.email,
            user_key=user.key,
        )
        raise _INVALID_CREDENTIALS

    user.last_authenticated_at = datetime.now(UTC)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    log_event(
        LOGGER,
        "local_login_succeeded",
        "Локальный вход выполнен.",
        user_email=user.email,
        user_key=user.key,
    )
    return user


async def reset_user_password(session: AsyncSession, user_id: UUID, new_password: str) -> None:
    """
    Задаёт пароль пользователю (вызывается администратором).

    При пустом new_password хэш сбрасывается (password_hash=NULL), и
    пользователь при следующем входе установит новый пароль сам.
    """
    user = await get_user_by_id(session, user_id)
    if user is None or user.is_deleted:
        msg = "Пользователь не найден."
        raise ValueError(msg)
    if new_password:
        validate_new_password(new_password)
        user.password_hash = hash_password(new_password)
    else:
        user.password_hash = None
    session.add(user)
    await session.commit()


async def get_current_user(session: AsyncSession, identity: CurrentUserIdentity) -> CurrentUserRead:
    user = await upsert_current_user(session, identity)
    access = await read_user_access(session, user.id)
    return CurrentUserRead(
        access=access,
        email=user.email,
        key=user.key,
        name=user.name,
        theme=user.theme,
    )


async def update_profile(
    session: AsyncSession,
    identity: CurrentUserIdentity,
    *,
    theme: str | None,
    password: str | None,
) -> CurrentUserRead:
    """
    Обновляет тему и/или локальный пароль текущего пользователя.

    Изменение пароля имеет смысл только в локальном режиме аутентификации;
    при включённом Keycloak он не используется, но сохраняется для переключения
    режима. Логин (email) и ФИО менять нельзя — они приходят из идентичности.
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise _UNKNOWN_USER

    if theme is not None:
        user.theme = theme
    if password:
        validate_new_password(password)
        user.password_hash = hash_password(password)
    user.updated_at = datetime.now(UTC)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    access = await read_user_access(session, user.id)
    return CurrentUserRead(
        access=access,
        email=user.email,
        key=user.key,
        name=user.name,
        theme=user.theme,
    )


async def read_user_access(session: AsyncSession, user_id: UUID) -> list[ProjectAccessRead]:
    """
    Возвращает проекты пользователя, где он активирован: роль и активы.

    Связь пользователя с проектом может существовать с is_active=False — такие
    проекты в доступ не попадают (администратор ещё не включил доступ).
    Администратор проекта (admin) видит все активы; работник по активам
    (asset_worker) — только привязанные к нему.
    """
    query = (
        select(Project, col(UserRole.role))
        .join(UserRole, col(UserRole.project_id) == col(Project.id))
        .where(UserRole.user_id == user_id, col(UserRole.is_active).is_(True))
        .order_by(Project.key, UserRole.role)
    )
    rows = (await session.exec(query)).all()

    combined: dict[tuple[UUID, str, str], list[ProjectRole]] = {}
    for project, role in rows:
        combined.setdefault((project.id, project.key, project.name), []).append(role)

    projects: list[ProjectAccessRead] = []
    for (_, key, name), roles in sorted(combined.items()):
        assets = await read_project_assets(session, user_id, roles)
        projects.append(
            ProjectAccessRead(
                key=key,
                name=name,
                roles=sorted(role.value for role in roles),
                assets=assets,
            )
        )
    return projects


async def read_project_assets(
    session: AsyncSession,
    user_id: UUID,
    roles: list[ProjectRole],
) -> list[AssetAccessRead]:
    if ProjectRole.ADMIN in roles:
        query = select(RawMaterialAsset).order_by(RawMaterialAsset.name)
        assets = (await session.exec(query)).all()
    else:
        query = (
            select(RawMaterialAsset)
            .join(RawMaterialUserAsset, col(RawMaterialUserAsset.asset_id) == col(RawMaterialAsset.id))
            .where(RawMaterialUserAsset.user_id == user_id)
            .order_by(RawMaterialAsset.name)
        )
        assets = (await session.exec(query)).all()
    return [AssetAccessRead(name=asset.name) for asset in assets]


async def get_user_by_identity(session: AsyncSession, identity: CurrentUserIdentity) -> User | None:
    if identity.oidc_sub:
        user = await get_user_by_oidc_identity(session, identity.issuer, identity.oidc_sub)
        if user is not None:
            return user
    if identity.email:
        user = await get_user_by_email(session, identity.email)
        if user is not None:
            return user

    return await get_user_by_key(session, identity.key or LOCAL_USER_KEY)


async def get_user_by_oidc_identity(session: AsyncSession, issuer: str | None, subject: str) -> User | None:
    query = select(User).where(col(User.oidc_sub) == subject)
    if issuer:
        query = query.where((col(User.oidc_issuer) == issuer) | col(User.oidc_issuer).is_(None))
    return (await session.exec(query)).one_or_none()


async def get_user_by_email(session: AsyncSession, email: str | None) -> User | None:
    if email is None:
        return None
    return (await session.exec(select(User).where(col(User.email) == email))).one_or_none()


async def get_user_by_key(session: AsyncSession, key: str) -> User | None:
    return (await session.exec(select(User).where(col(User.key) == key))).one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def grant_project_role(
    session: AsyncSession,
    user_id: UUID,
    project_key: str,
    role: ProjectRole,
) -> None:
    """Назначает пользователю роль в проекте (игнорирует повторное назначение)."""
    project = (await session.exec(select(Project).where(col(Project.key) == project_key))).one_or_none()
    if project is None:
        msg = f"Проект {project_key!r} не найден."
        raise ValueError(msg)

    existing = (
        await session.exec(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.project_id == project.id,
            )
        )
    ).one_or_none()
    if existing is None:
        session.add(
            UserRole(
                user_id=user_id,
                project_id=project.id,
                role=role,
                is_active=False,
            )
        )
    else:
        existing.role = role
        session.add(existing)
    await session.commit()


async def activate_project_user(session: AsyncSession, user_id: UUID, project_key: str) -> None:
    """Включает доступ пользователя к проекту (is_active=true на связи)."""
    project = (await session.exec(select(Project).where(col(Project.key) == project_key))).one_or_none()
    if project is None:
        msg = f"Проект {project_key!r} не найден."
        raise ValueError(msg)
    existing = (
        await session.exec(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.project_id == project.id,
            )
        )
    ).one_or_none()
    if existing is None:
        existing = UserRole(user_id=user_id, project_id=project.id, role=ProjectRole.ASSET_WORKER, is_active=True)
        session.add(existing)
    else:
        existing.is_active = True
        session.add(existing)
    await session.commit()


async def grant_project_assets(
    session: AsyncSession,
    user_id: UUID,
    asset_names: list[str],
) -> None:
    """Привязывает активы к пользователю проекта (для роли asset_worker)."""
    assets = (await session.exec(select(RawMaterialAsset).where(col(RawMaterialAsset.name).in_(asset_names)))).all()
    found_names = {asset.name for asset in assets}
    missing = set(asset_names) - found_names
    if missing:
        msg = f"Активы не найдены: {', '.join(sorted(missing))}."
        raise ValueError(msg)

    for asset in assets:
        existing = (
            await session.exec(
                select(RawMaterialUserAsset).where(
                    RawMaterialUserAsset.user_id == user_id,
                    RawMaterialUserAsset.asset_id == asset.id,
                )
            )
        ).one_or_none()
        if existing is None:
            session.add(RawMaterialUserAsset(user_id=user_id, asset_id=asset.id))
    await session.commit()
