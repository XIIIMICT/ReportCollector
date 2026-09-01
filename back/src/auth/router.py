from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole
from src.database import get_session

from . import constants as auth_constants
from .schemas import (
    CurrentUserRead,
    LocalLoginRequest,
    LocalLoginResponse,
    ProjectAccessCheckRead,
    ResetPasswordRequest,
    UpdateProfileRequest,
)
from .service import (
    IDENTITY_HEADER_NAMES,
    BlockedUserError,
    CurrentUserIdentity,
    InvalidCredentialsError,
    InvalidIdentityError,
    authenticate_local_user,
    get_current_user,
    request_project_access,
    reset_user_password,
    update_profile,
    verify_signed_identity,
)

router = APIRouter(tags=["auth"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def read_signed_identity(
    user_email: Annotated[str | None, Header(alias="X-ReportCollector-User-Email")] = None,
    user_name: Annotated[str | None, Header(alias="X-ReportCollector-User-Name")] = None,
    user_sub: Annotated[str | None, Header(alias="X-ReportCollector-User-Sub")] = None,
    issuer: Annotated[str | None, Header(alias="X-ReportCollector-User-Issuer")] = None,
    preferred_username: Annotated[str | None, Header(alias="X-ReportCollector-Preferred-Username")] = None,
    given_name: Annotated[str | None, Header(alias="X-ReportCollector-Given-Name")] = None,
    family_name: Annotated[str | None, Header(alias="X-ReportCollector-Family-Name")] = None,
    email_verified: Annotated[str | None, Header(alias="X-ReportCollector-Email-Verified")] = None,
    timestamp: Annotated[str | None, Header(alias="X-ReportCollector-Identity-Timestamp")] = None,
    signature: Annotated[str | None, Header(alias="X-ReportCollector-Identity-Signature")] = None,
) -> CurrentUserIdentity:
    """Parse and verify the signed identity headers set by the frontend proxy."""
    headers = {
        name: value
        for name, value in zip(
            IDENTITY_HEADER_NAMES,
            (
                user_email,
                user_name,
                user_sub,
                issuer,
                preferred_username,
                given_name,
                family_name,
                email_verified,
            ),
            strict=True,
        )
        if value is not None
    }
    if timestamp:
        headers["X-ReportCollector-Identity-Timestamp"] = timestamp
    if signature:
        headers["X-ReportCollector-Identity-Signature"] = signature
    try:
        return verify_signed_identity(headers)
    except InvalidIdentityError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


@router.post("/auth/login", response_model=LocalLoginResponse)
async def login_local(
    body: LocalLoginRequest,
    session: SessionDependency,
) -> LocalLoginResponse:
    """
    Локальный вход по email и паролю.

    Возвращает идентичность пользователя; сессию браузера формирует фронтенд.
    """
    try:
        user = await authenticate_local_user(session, body.email, body.password)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    return LocalLoginResponse(
        email=user.email,
        issuer=auth_constants.LOCAL_USER_KEY,
        name=user.name,
        sub=f"{auth_constants.LOCAL_USER_KEY}:{user.email}",
    )


async def _current_user_dependency(
    session: SessionDependency,
    identity: Annotated[CurrentUserIdentity, Depends(read_signed_identity)],
) -> CurrentUserRead:
    # create=False: API calls never auto-provision a user — the membership is
    # created by request_project_access (opening a project URL) or by an admin.
    return await get_current_user(session, identity, create=False)


@router.post("/auth/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_user_password(
    user_id: str,
    body: ResetPasswordRequest,
    session: SessionDependency,
    current: Annotated[CurrentUserRead, Depends(_current_user_dependency)],
) -> None:
    """
    Устанавливает или сбрасывает пароль пользователя (только для администратора).

    При пустом new_password хэш удаляется — пользователь установит пароль при
    следующем входе. Сброс работает только в локальном режиме аутентификации;
    при включённом Keycloak пароль игнорируется, и фронтенд не вызывает endpoint.
    """
    if not any(ProjectRole.ADMIN.value in project.roles for project in current.access):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав.")
    try:
        await reset_user_password(session, UUID(user_id), body.new_password or "")
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/me", response_model=CurrentUserRead)
async def read_current_user(
    session: SessionDependency,
    identity: Annotated[CurrentUserIdentity, Depends(read_signed_identity)],
) -> CurrentUserRead:
    """
    Возвращает профиль пользователя, статус активации и доступ к проектам.

    Пользователь создаётся при первом входе (is_active=False). Запрос всегда
    отвечает 200: решение о блокировке принимает интерфейс по полю is_active.
    Неизвестный пользователь не создаётся и не получает связей с проектами:
    членыство оформляется запросом на URL конкретного проекта
    (request-access). Корневой URL просто отвечает пустым access.
    """
    try:
        return await get_current_user(session, identity, create=False)
    except BlockedUserError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.post(
    "/me/projects/{project_key}/request-access",
    response_model=ProjectAccessCheckRead,
)
async def request_project_access_endpoint(
    project_key: str,
    session: SessionDependency,
    identity: Annotated[CurrentUserIdentity, Depends(read_signed_identity)],
) -> ProjectAccessCheckRead:
    """
    Self-requested membership in one project (the user opened its URL).

    Creates the user record when missing and a single inactive project link,
    so the project admin sees the person in «Пользователи» and can grant
    access. hasAccess reports whether the project is already usable.
    """
    try:
        return await request_project_access(session, identity, project_key)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.patch("/me", response_model=CurrentUserRead)
async def update_current_user(
    body: UpdateProfileRequest,
    session: SessionDependency,
    identity: Annotated[CurrentUserIdentity, Depends(read_signed_identity)],
) -> CurrentUserRead:
    """
    Обновляет профиль пользователя: тему оформления и/или локальный пароль.

    Логин (email) и ФИО приходят из идентичности и изменению не подлежат.
    Пароль работает только в локальном режиме аутентификации.
    """
    try:
        return await update_profile(
            session,
            identity,
            theme=body.theme,
            password=body.password,
        )
    except BlockedUserError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
