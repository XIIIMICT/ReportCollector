from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import EmailStr
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProjectRole(StrEnum):
    """Fixed set of project roles. Hardcoded; the DB stores an enum."""

    ADMIN = "admin"
    ASSET_WORKER = "asset_worker"


class User(SQLModel, table=True):
    __tablename__ = "app_user"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    oidc_sub: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    oidc_issuer: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    preferred_username: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # EmailStr: the format is validated by pydantic on create/update.
    # Case is normalized separately (normalize_email) — addresses are case-insensitive.
    email: EmailStr | None = Field(default=None, sa_column=Column(Text, unique=True, index=True, nullable=True))
    name: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    email_verified: bool | None = Field(default=None, sa_column=Column(Boolean, nullable=True))
    password_hash: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    last_authenticated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    # Global account ban. Access to a specific project is determined by the
    # per-project is_active flag in UserRole.
    is_deleted: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    theme: str = Field(default="system", sa_column=Column(Text, nullable=False, server_default="system"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class UserRole(SQLModel, table=True):
    """
    User-project link: per-project role and activation.

    A link with is_active=True grants the user access to the project.
    Provisioning creates links to all projects on first login (is_active=False);
    the project admin enables the flag — and the user can open the project.
    """

    __tablename__ = "user_role"

    user_id: UUID = Field(
        sa_column=Column(ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    project_id: UUID = Field(
        sa_column=Column(ForeignKey("project.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    role: ProjectRole = Field(
        sa_column=Column(
            SAEnum(ProjectRole, name="project_role", values_callable=lambda enum: [item.value for item in enum]),
            nullable=False,
        )
    )
    is_active: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
