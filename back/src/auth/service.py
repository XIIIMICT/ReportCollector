import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest, new
from urllib.parse import unquote
from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.app_logging import log_event
from src.auth.emails import normalize_email
from src.auth.models import Project, ProjectRole, Theme, User, UserRole
from src.auth.passwords import (
    hash_password_async,
    validate_new_password,
    verify_password_async,
)
from src.auth.schemas import AssetAccessRead, CurrentUserRead, ProjectAccessCheckRead, ProjectAccessRead
from src.collab.events import publish_rows_changed
from src.collab.persistence import JournalStore
from src.config import get_settings
from src.logistics.constants import LOGISTICS_PROJECT_KEY
from src.raw_materials.models import RawMaterialAsset, RawMaterialUserAsset
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.models import RProjectRole, RUserRole

LOGGER = logging.getLogger("reportcollector.application")


class InvalidCredentialsError(Exception):
    """Login or password doesn't match."""


_INVALID_CREDENTIALS = InvalidCredentialsError("Неверный email или пароль.")


@dataclass(frozen=True)
class CurrentUserIdentity:
    email: str | None
    email_verified: bool | None
    family_name: str | None
    given_name: str | None
    issuer: str | None
    name: str | None
    oidc_sub: str | None
    preferred_username: str | None


class UnknownUserError(Exception):
    """User is not registered in ReportCollector."""


class InvalidIdentityError(Exception):
    """Identity signature is missing, stale or invalid."""


# Identity signature headers (match auth/router.read_signed_identity).
IDENTITY_HEADER_NAMES = (
    "X-ReportCollector-User-Email",
    "X-ReportCollector-User-Name",
    "X-ReportCollector-User-Sub",
    "X-ReportCollector-User-Issuer",
    "X-ReportCollector-Preferred-Username",
    "X-ReportCollector-Given-Name",
    "X-ReportCollector-Family-Name",
    "X-ReportCollector-Email-Verified",
)

# WebSocket tickets are minted per connection attempt; keep them short-lived
# regardless of the wider AUTH_IDENTITY_TTL (mirrors the frontend
# TICKET_TTL_SECONDS in /api/collab/ticket).
IDENTITY_TICKET_MAX_AGE = 60


def verify_signed_identity(headers: Mapping[str, str], *, max_age_seconds: int | None = None) -> CurrentUserIdentity:
    """Verify the identity signature from headers (for WebSocket and the proxy)."""
    lookup = {name.casefold(): value for name, value in headers.items()}
    values = [unquote(lookup.get(name.casefold()) or "") or None for name in IDENTITY_HEADER_NAMES]
    timestamp = lookup.get("x-reportcollector-identity-timestamp")
    signature = lookup.get("x-reportcollector-identity-signature")
    if not timestamp or not signature:
        msg = "Отсутствует подпись идентичности."
        raise InvalidIdentityError(msg)
    try:
        signed_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (ValueError, OSError) as error:
        msg = "Некорректное время подписи."
        raise InvalidIdentityError(msg) from error
    allowed_age = get_settings().auth_identity_ttl if max_age_seconds is None else max_age_seconds
    if abs((datetime.now(UTC) - signed_at).total_seconds()) > allowed_age:
        msg = "Подпись идентичности устарела."
        raise InvalidIdentityError(msg)
    payload = "\n".join([timestamp, *(value or "" for value in values)])
    expected = new(get_settings().auth_secret.encode(), payload.encode(), sha256).hexdigest()
    if not compare_digest(signature, expected):
        msg = "Подпись идентичности неверна."
        raise InvalidIdentityError(msg)
    email, name, subject, oidc_issuer, preferred, given, family, verified = values
    return CurrentUserIdentity(
        email=email,
        email_verified=_parse_optional_bool(verified),
        family_name=family,
        given_name=given,
        issuer=oidc_issuer,
        name=name,
        oidc_sub=subject,
        preferred_username=preferred,
    )


def _parse_optional_bool(value: str | None) -> bool | None:
    """
    Parse a signed boolean claim (`email_verified`).

    Only the explicit truthy/falsy spellings are accepted; anything else is an
    error rather than a silent `False`, so a typo cannot quietly disable the
    feature the flag guards (AUD-19).
    """
    if value is None or value == "":
        return None
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "да"}:
        return True
    if normalized in {"false", "0", "no", "нет"}:
        return False
    msg = f"Некорректное логическое значение в подписи идентичности: {value!r}."
    raise InvalidIdentityError(msg)


def verify_identity_ticket(ticket: str) -> CurrentUserIdentity:
    """
    Verify the WebSocket ticket: signed identity headers in the query.

    The browser can't set custom headers, so the frontend route handler packs
    the signed headers into a base64url ticket {"h": {header: value, ...}}.
    The ticket is valid for a short fixed window (``IDENTITY_TICKET_MAX_AGE``),
    independent of the wider ``AUTH_IDENTITY_TTL`` that bounds plain identity
    headers: the browser mints a fresh ticket for every connection attempt, so
    a long-lived one would only outlive its purpose (AUD-20).
    """
    try:
        ticket = ticket + "=" * (-len(ticket) % 4)
        payload = json.loads(base64.urlsafe_b64decode(ticket.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        msg = "Некорректный тикет."
        raise InvalidIdentityError(msg) from error
    headers = payload.get("h")
    if not isinstance(headers, dict):
        msg = "Некорректный тикет."
        raise InvalidIdentityError(msg)
    mapping = {str(name): str(value) for name, value in headers.items()}
    return verify_signed_identity(mapping, max_age_seconds=IDENTITY_TICKET_MAX_AGE)


class BlockedUserError(Exception):
    """User is blocked in ReportCollector."""


_UNKNOWN_USER = ValueError("Пользователь не найден.")
_USER_BLOCKED = BlockedUserError("Пользователь заблокирован в ReportCollector.")


async def upsert_current_user(
    session: AsyncSession, identity: CurrentUserIdentity, *, create: bool = True
) -> User | None:
    """
    Register or update the user from OIDC identity data.

    A new user appears for the admins of all projects: links with all
    existing projects are created right away, but with is_active=False.
    Access to a specific project is granted by the admin enabling is_active
    in their project. The AUTH_SEED_ADMIN_EMAIL user is created active and
    gets the admin role in all projects.

    With create=False an unknown user is not written at all (None is
    returned): visiting the root URL must not provision anything. Existing
    users are still refreshed with the identity data. The all-projects
    provisioning runs only for the seed admin; a regular user gets a project
    membership through request_project_access instead.
    """
    log_event(
        LOGGER,
        "keycloak_identity_received",
        "Получены идентификационные данные пользователя.",
        user_email=identity.email,
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
    is_seed_admin = bool(seed_admin_email) and normalize_email(identity.email or "") == normalize_email(
        seed_admin_email
    )

    if user is None:
        if not create:
            return None
        user = User(
            email=normalize_email(identity.email) if identity.email else None,
            name=identity.name,
            oidc_sub=identity.oidc_sub,
            preferred_username=identity.preferred_username,
            email_verified=identity.email_verified,
        )
        log_event(
            LOGGER,
            "user_created",
            "Создан локальный пользователь по данным OIDC.",
            user_email=user.email,
            user_created_active=is_seed_admin,
        )

    user.email = normalize_email(identity.email) if identity.email else user.email
    user.name = identity.name or user.name
    user.oidc_sub = identity.oidc_sub or user.oidc_sub
    user.preferred_username = identity.preferred_username or user.preferred_username
    user.email_verified = identity.email_verified if identity.email_verified is not None else user.email_verified
    user.last_authenticated_at = datetime.now(UTC)
    session.add(user)
    await session.flush()

    # Provisioning: the seed admin is linked to every project (active, admin
    # role). Regular users are NOT auto-provisioned — a project membership is
    # created when they open that project's URL (request_project_access) or
    # when an admin adds them.
    if is_seed_admin:
        await sync_project_memberships(session, user.id, activate_admin=True)

    await session.commit()
    await session.refresh(user)

    if user.is_deleted:
        log_event(
            LOGGER,
            "blocked_user_attempt",
            "Заблокированный пользователь попытался получить доступ.",
            user_email=user.email,
            user_sub=user.oidc_sub,
        )
        blocked_message = "Пользователь заблокирован в ReportCollector."
        raise BlockedUserError(blocked_message)

    return user


async def sync_project_memberships(session: AsyncSession, user_id: UUID, *, activate_admin: bool = False) -> None:
    """
    Guarantees the user's links to all projects.

    Missing links are created with is_active=False and the asset_worker role;
    for the seed admin (activate_admin=True) new links are active with the
    admin role. Existing links are never changed.

    Renovation-only users (those with a role in r_user_roles) skip raw_materials
    and kppk — they don't need those workspaces.  The seed admin bypasses this
    check and gets links to every project.
    """
    # Projects the user should be linked to.
    projects = (await session.exec(select(Project).order_by(Project.key))).all()
    existing = (await session.exec(select(UserRole).where(col(UserRole.user_id) == user_id))).all()
    existing_by_project = {role.project_id: role for role in existing}
    now = datetime.now(UTC)

    # Skip non-renovation projects for users that only have renovation roles;
    # auto-activate the renovation project when an active RUserRole exists.
    if not activate_admin:
        r_roles = list((await session.exec(select(RUserRole).where(col(RUserRole.user_id) == user_id))).all())
        has_renovation_role = bool(r_roles)
        if has_renovation_role:
            skip_keys = {"raw_materials", "kppk", LOGISTICS_PROJECT_KEY}
            projects = [p for p in projects if p.key not in skip_keys]
        # Active r_user_roles → the corresponding user_role should be active too.
        r_active_project_ids = {rur.project_id for rur in r_roles if rur.is_active}

    for project in projects:
        if project.id in existing_by_project:
            continue
        # Auto-activate when the user already has an active renovation role.
        should_activate = activate_admin or (
            not activate_admin and has_renovation_role and project.id in r_active_project_ids
        )
        session.add(
            UserRole(
                user_id=user_id,
                project_id=project.id,
                # An inactive link is «Заблокирован» — the flag is derived from
                # the role now. An auto-activated renovation link keeps the
                # asset-worker role it always had.
                role=(
                    ProjectRole.ADMIN
                    if activate_admin
                    else ProjectRole.ASSET_WORKER
                    if should_activate
                    else ProjectRole.BLOCKED
                ),
                is_active=should_activate,
                created_at=now,
                updated_at=now,
                updater_user_id=None,
            )
        )
    await session.flush()


async def authenticate_local_user(session: AsyncSession, email: str, password: str) -> User:
    """
    Login by email and password.

    A user with an empty password_hash (e.g. created by an admin without a
    password) sets the password on first login. On a password mismatch or a
    missing user, InvalidCredentialsError is raised.
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
            )
            raise _INVALID_CREDENTIALS from None
        user.password_hash = await hash_password_async(password)
        log_event(
            LOGGER,
            "local_password_set",
            "Установлен первоначальный пароль локального пользователя.",
            user_email=user.email,
        )
    elif not await verify_password_async(password, stored_hash):
        log_event(
            LOGGER,
            "local_login_failed",
            "Неудачная попытка локального входа: неверный пароль.",
            user_email=user.email,
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
    )
    return user


async def reset_user_password(session: AsyncSession, user_id: UUID, new_password: str) -> None:
    """
    Set the user's password (called by an admin).

    With an empty new_password the hash is reset (password_hash=NULL) and the
    user sets a new password themselves on the next login.
    """
    user = await get_user_by_id(session, user_id)
    if user is None or user.is_deleted:
        msg = "Пользователь не найден."
        raise ValueError(msg)
    if new_password:
        validate_new_password(new_password)
        user.password_hash = await hash_password_async(new_password)
    else:
        user.password_hash = None
    session.add(user)
    await session.commit()


async def get_current_user(
    session: AsyncSession, identity: CurrentUserIdentity, *, create: bool = True
) -> CurrentUserRead:
    user = await upsert_current_user(session, identity, create=create)
    if user is None:
        # Unknown user on the root URL: nothing is provisioned, no access.
        return CurrentUserRead(access=[], email=identity.email, name=identity.name)
    access = await read_user_access(session, user.id)
    return CurrentUserRead(
        access=access,
        email=user.email,
        name=user.name,
        theme=user.theme,
    )


async def request_project_access(
    session: AsyncSession, identity: CurrentUserIdentity, project_key: str
) -> ProjectAccessCheckRead:
    """
    Self-requested membership in one project (opened the project's URL).

    Creates the user record when missing and a single inactive project link,
    so the project admin sees the person in the «Пользователи» dictionary and
    can grant access. Existing links are never modified. hasAccess reports
    whether the project is already usable right now.
    """
    project = (await session.exec(select(Project).where(col(Project.key) == project_key))).one_or_none()
    if project is None:
        message = f"Проект «{project_key}» не найден."
        raise ValueError(message)

    user = await upsert_current_user(session, identity, create=True)
    if user is None:
        # Unreachable with create=True; keeps the type narrow for user.id.
        message = "Пользователь не найден."
        raise ValueError(message)
    link = (
        await session.exec(select(UserRole).where(UserRole.user_id == user.id, UserRole.project_id == project.id))
    ).one_or_none()
    created_membership = link is None
    if link is None:
        now = datetime.now(UTC)
        session.add(
            UserRole(
                user_id=user.id,
                project_id=project.id,
                role=ProjectRole.ASSET_WORKER,
                is_active=False,
                created_at=now,
                updated_at=now,
                updater_user_id=None,
            )
        )
        await session.flush()
        link = (
            await session.exec(select(UserRole).where(UserRole.user_id == user.id, UserRole.project_id == project.id))
        ).one()
    if project_key == RENOVATION_PROJECT_KEY:
        # The renovation «Пользователи» dictionary lists users by r_user_roles:
        # create the inactive renovation link too (the admin picks the block).
        r_link = (
            await session.exec(
                select(RUserRole).where(RUserRole.user_id == user.id, RUserRole.project_id == project.id)
            )
        ).one_or_none()
        if r_link is None:
            now = datetime.now(UTC)
            session.add(
                RUserRole(
                    user_id=user.id,
                    project_id=project.id,
                    role=RProjectRole.ASSET_WORKER,
                    block=None,
                    is_active=False,
                    created_at=now,
                    updated_at=now,
                    updater_user_id=None,
                )
            )
    await session.commit()
    if created_membership and project_key == "kppk":
        # The КППК users dictionary is an admin collaboration room. Notify
        # connected admins immediately; the row is now committed and the
        # frontend refetch is authoritative.
        await publish_rows_changed(
            JournalStore(session),
            project_key="kppk",
            section="kppkUsers",
            report_month=None,
            user_id=user.id,
            user_name=user.name or "",
            user_email=user.email or "",
        )
    return ProjectAccessCheckRead(hasAccess=bool(link.is_active))


async def update_profile(
    session: AsyncSession,
    identity: CurrentUserIdentity,
    *,
    theme: Theme | None,
    password: str | None,
) -> CurrentUserRead:
    """
    Update the current user's theme and/or local password.

    Changing the password only matters in the local auth mode; with Keycloak
    enabled it isn't used but is kept for switching modes. The login (email)
    and FIO can't be changed — they come from the identity.
    """
    user = await get_user_by_identity(session, identity)
    if user is None or user.is_deleted:
        raise _UNKNOWN_USER

    if theme is not None:
        user.theme = theme
    if password:
        validate_new_password(password)
        user.password_hash = await hash_password_async(password)
    user.updated_at = datetime.now(UTC)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    access = await read_user_access(session, user.id)
    return CurrentUserRead(
        access=access,
        email=user.email,
        name=user.name,
        theme=user.theme,
    )


async def read_user_access(session: AsyncSession, user_id: UUID) -> list[ProjectAccessRead]:
    """
    Return the user's projects where they're activated: role and assets.

    A user-project link may exist with is_active=False — such projects aren't
    included in the access (the admin hasn't enabled access yet). A project
    admin sees all assets; an asset_worker only the ones assigned to them.
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
    # Asset grants are shared by these memberships; resolve each permission
    # class once per request. Never reuse admin assets for an asset worker.
    assets_by_admin: dict[bool, list[AssetAccessRead]] = {}
    for (_, key, name), roles in sorted(combined.items()):
        is_admin = ProjectRole.ADMIN in roles
        if is_admin not in assets_by_admin:
            assets_by_admin[is_admin] = await read_project_assets(session, user_id, roles)
        assets = assets_by_admin[is_admin]
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
        user = await get_user_by_oidc_identity(session, identity.oidc_sub)
        if user is not None:
            return user
    if identity.email:
        return await get_user_by_email(session, identity.email)
    return None


async def get_user_by_oidc_identity(session: AsyncSession, subject: str) -> User | None:
    return (await session.exec(select(User).where(col(User.oidc_sub) == subject))).one_or_none()


async def get_user_by_email(session: AsyncSession, email: str | None) -> User | None:
    """Case-insensitive email lookup: addresses are case-insensitive."""
    if email is None:
        return None
    return (await session.exec(select(User).where(func.lower(col(User.email)) == normalize_email(email)))).one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def grant_project_role(
    session: AsyncSession,
    user_id: UUID,
    project_key: str,
    role: ProjectRole,
) -> None:
    """Assign the user a role in the project (ignores duplicate assignment)."""
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
    """Enable the user's project access (is_active=true on the link)."""
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


async def set_project_role_active(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID,
    active: bool,
) -> None:
    """
    Align a user's project access with the desired state without committing.

    Unlike activate_project_user this covers the negative side too (deactivating
    an existing but empty project link) and leaves transaction ownership to the
    caller, so it can be called inside a larger edit. A missing link (a project
    the user was never routed through) is created with the asset_worker role and
    the requested active state — otherwise the user would have a renovation
    membership nowhere visible to cross-project provisioning.
    """
    existing = (
        await session.exec(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.project_id == project_id,
            )
        )
    ).one_or_none()
    if existing is None:
        session.add(
            UserRole(
                user_id=user_id,
                project_id=project_id,
                role=ProjectRole.ASSET_WORKER,
                is_active=active,
            )
        )
        return
    if existing.is_active != active:
        existing.is_active = active
        session.add(existing)


async def grant_project_assets(
    session: AsyncSession,
    user_id: UUID,
    asset_names: list[str],
) -> None:
    """Assign project assets to the user (for the asset_worker role)."""
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
