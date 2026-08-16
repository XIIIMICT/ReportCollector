from pydantic import BaseModel, Field

from src.auth.models import Theme


class AssetAccessRead(BaseModel):
    name: str


class ProjectAccessRead(BaseModel):
    key: str
    name: str
    roles: list[str]
    assets: list[AssetAccessRead]


class CurrentUserRead(BaseModel):
    """Current user: profile and available (activated) projects."""

    email: str | None
    key: str
    name: str | None
    theme: Theme = Theme.SYSTEM
    access: list[ProjectAccessRead]


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class LocalLoginResponse(BaseModel):
    """User identity for the browser session (no password hash)."""

    key: str
    email: str | None
    name: str | None
    sub: str
    issuer: str


class ResetPasswordRequest(BaseModel):
    """new_password=None or empty — reset the password (hash → NULL)."""

    new_password: str | None = None


class UpdateProfileRequest(BaseModel):
    """
    Profile update.

    theme — system/light/dark; password — a new password (only meaningful in
    the local auth mode, ignored when Keycloak is enabled).
    """

    theme: Theme | None = None
    password: str | None = Field(default=None, max_length=1024)
