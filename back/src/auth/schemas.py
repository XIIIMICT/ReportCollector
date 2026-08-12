from typing import Literal

from pydantic import BaseModel, Field


class AssetAccessRead(BaseModel):
    name: str


class ProjectAccessRead(BaseModel):
    key: str
    name: str
    roles: list[str]
    assets: list[AssetAccessRead]


class CurrentUserRead(BaseModel):
    """Текущий пользователь: профиль и доступные (активированные) проекты."""

    email: str | None
    key: str
    name: str | None
    theme: str = "system"
    access: list[ProjectAccessRead]


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class LocalLoginResponse(BaseModel):
    """Идентичность пользователя для сессии браузера (без хэша пароля)."""

    key: str
    email: str | None
    name: str | None
    sub: str
    issuer: str


class ResetPasswordRequest(BaseModel):
    """new_password=None или пусто — сбросить пароль (хэш → NULL)."""

    new_password: str | None = None


class UpdateProfileRequest(BaseModel):
    """
    Обновление профиля пользователя.

    theme — системная/светлая/тёмная тема; password — новый пароль (только
    для локального режима аутентификации, при Keycloak игнорируется).
    """

    theme: Literal["system", "light", "dark"] | None = None
    password: str | None = Field(default=None, max_length=1024)
