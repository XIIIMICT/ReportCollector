from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from src.auth.models import utc_now
from src.common.audit import UpdaterDisplay, configure_updater
from src.common.data_catalog import apply_table_comments


class RProjectRole(StrEnum):
    """Fixed set of renovation project roles. Hardcoded; the DB stores an enum."""

    ADMIN = "admin"
    APPROVER = "approver"
    BLOCK_OWNER = "block_owner"
    ASSET_WORKER = "asset_worker"
    # «Заблокирован»: the renovation membership is off. It replaces the
    # «Доступ» flag at the UI level; `r_user_roles.is_active` stays as the
    # derived access bit every resolver already reads.
    BLOCKED = "blocked"


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


class RAsset(UpdaterDisplay, table=True):
    """A renovation asset (legal entity). Separate from the raw-materials assets."""

    __tablename__ = "r_assets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Official legal-entity code from the renovation ID, e.g. «VOZR».
    code: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


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


class RUserRole(UpdaterDisplay, table=True):
    """
    User-project link for the renovation project: role and responsibility block.

    Mirrors UserRole (is_active/activation semantics) but with the
    renovation-specific r_project_role enum and the optional r_block. The
    shared user_roles.role column keeps the raw-materials rm_project_role enum,
    so renovation roles live in their own table.
    """

    __tablename__ = "r_user_roles"
    # The composite primary key leads with user_id; listing a project's members
    # filters by project_id alone, so it gets its own index.
    __table_args__ = (Index("ix_r_user_roles_project", "project_id"),)

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
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


for _model in (RAsset, RUserRole, RUserAsset):
    apply_table_comments(SQLModel.metadata.tables[_model.__tablename__])
    if "updater_user_id" in _model.model_fields:
        configure_updater(_model)
