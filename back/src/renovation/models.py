from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text, text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from src.auth.models import utc_now


class RProjectRole(StrEnum):
    """Fixed set of renovation project roles. Hardcoded; the DB stores an enum."""

    ADMIN = "admin"
    APPROVER = "approver"
    BLOCK_OWNER = "block_owner"
    ASSET_WORKER = "asset_worker"


class RBlock(StrEnum):
    """
    Responsibility block of the renovation plan (Excel columns grouped by owner).

    The three blocks map to company directorates (the legacy surname-based names
    were replaced): tech = Техническая дирекция, ksir = Дирекция КСиР (капитальное
    строительство и ремонт), mto = Дирекция МТО (материально-техническое
    обеспечение). Required for block_owner/asset_worker roles; empty for
    admin/approver.
    """

    TECH = "tech"
    KSIR = "ksir"
    MTO = "mto"


class RAsset(SQLModel, table=True):
    """A renovation asset (legal entity). Separate from the raw-materials assets."""

    __tablename__ = "r_assets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Internal stable code, e.g. «vos» (used in asset-scoped user names).
    code: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    # Official legal-entity repair code from the renovation ID, e.g. «VOZR».
    repair_code: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class RUserAsset(SQLModel, table=True):
    """Per-user asset grants for the renovation project (central roles get all)."""

    __tablename__ = "r_user_assets"

    user_id: UUID = Field(
        sa_column=Column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    project_id: UUID = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    asset_id: UUID = Field(
        sa_column=Column(ForeignKey("r_assets.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class RUserRole(SQLModel, table=True):
    """
    User-project link for the renovation project: role and responsibility block.

    Mirrors UserRole (is_active/activation semantics) but with the
    renovation-specific r_project_role enum and the optional r_block. The
    shared user_roles.role column keeps the raw-materials rm_project_role enum,
    so renovation roles live in their own table.
    """

    __tablename__ = "r_user_roles"

    user_id: UUID = Field(
        sa_column=Column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    project_id: UUID = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    )
    role: RProjectRole = Field(
        sa_column=Column(
            SAEnum(RProjectRole, name="r_project_role", values_callable=lambda enum: [item.value for item in enum]),
            nullable=False,
        )
    )
    # Responsibility block; required for block_owner/asset_worker, empty otherwise.
    block: RBlock | None = Field(
        default=None,
        sa_column=Column(
            SAEnum(RBlock, name="r_block", values_callable=lambda enum: [item.value for item in enum]),
            nullable=True,
        ),
    )
    is_active: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
