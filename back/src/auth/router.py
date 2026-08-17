from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest, new
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import ProjectRole
from src.config import get_settings
from src.database import get_session

from . import constants as auth_constants
from .schemas import (
    CurrentUserRead,
    LocalLoginRequest,
    LocalLoginResponse,
    ResetPasswordRequest,
    UpdateProfileRequest,
)
from .service import (
    BlockedUserError,
    CurrentUserIdentity,
    InvalidCredentialsError,
    authenticate_local_user,
    get_current_user,
    reset_user_password,
    update_profile,
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
    values = [
        decode_identity_header(value)
        for value in (
            user_email,
            user_name,
            user_sub,
            issuer,
            preferred_username,
            given_name,
            family_name,
            email_verified,
        )
    ]
    _verify_identity_signature(values, timestamp=timestamp, signature=signature)
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
    return await get_current_user(session, identity)


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
    """
    try:
        return await get_current_user(session, identity)
    except BlockedUserError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


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


def _verify_identity_signature(values: list[str | None], *, timestamp: str | None, signature: str | None) -> None:
    if not timestamp or not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Отсутствует подпись идентичности.")
    try:
        signed_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Некорректное время подписи.") from error
    if abs((datetime.now(UTC) - signed_at).total_seconds()) > 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Подпись идентичности устарела.")
    payload = "\n".join([timestamp, *(value or "" for value in values)])
    expected = new(get_settings().auth_secret.encode(), payload.encode(), sha256).hexdigest()
    if not compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Подпись идентичности неверна.")


def decode_identity_header(value: str | None) -> str | None:
    return unquote(value) if value else None


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.casefold() == "true"
