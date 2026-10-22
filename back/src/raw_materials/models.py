"""
Domain models of the «Сырьё» project (prefix rm_).

The schema mirrors the tables of the SQLite app ReportCollector-RawMaterials
(export v14): assets, raw-material items, asset-raw-material links, periods,
raw-material movements, delivery bases and prices. Users live in app_users,
asset assignment — in rm_user_assets.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlmodel import Field, SQLModel

from src.common.audit import UpdaterDisplay, configure_updater
from src.common.data_catalog import apply_table_comments


class RawMaterialAsset(UpdaterDisplay, table=True):
    """Asset (site)."""

    __tablename__ = "rm_assets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class RawMaterialUserAsset(SQLModel, table=True):
    """Asset-worker link to the assets of the «Сырьё» project."""

    __tablename__ = "rm_user_assets"
    __table_args__ = (Index("ix_rm_user_assets_asset_id", "asset_id"),)

    user_id: UUID = Field(primary_key=True, foreign_key="app_users.id", ondelete="CASCADE")
    asset_id: UUID = Field(primary_key=True, foreign_key="rm_assets.id", ondelete="CASCADE")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RawMaterial(UpdaterDisplay, table=True):
    """Raw-material item (raw-material name)."""

    __tablename__ = "rm_raw_materials"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class AssetRawMaterial(UpdaterDisplay, table=True):
    """Asset-raw-material link (the row of the «Движение» and «Цены» forms)."""

    __tablename__ = "rm_asset_raw_materials"
    __table_args__ = (
        UniqueConstraint("asset_id", "raw_material_id"),
        Index("ix_rm_asset_raw_materials_raw_material_id", "raw_material_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_id: UUID = Field(foreign_key="rm_assets.id", ondelete="RESTRICT")
    raw_material_id: UUID = Field(foreign_key="rm_raw_materials.id", ondelete="RESTRICT")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class MovementPeriod(SQLModel, table=True):
    """Report month of the «Движение» form."""

    __tablename__ = "rm_movement_periods"

    report_month: date = Field(sa_column=Column(Date, primary_key=True, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class MaterialMovement(UpdaterDisplay, table=True):
    """«Движение» cell values for an asset-raw-material link in the report month."""

    __tablename__ = "rm_material_movements"
    __table_args__ = (
        UniqueConstraint("asset_raw_material_id", "report_month"),
        Index("ix_rm_material_movements_report_month", "report_month"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_raw_material_id: UUID = Field(foreign_key="rm_asset_raw_materials.id", ondelete="RESTRICT")
    report_month: date = Field(
        sa_column=Column(
            Date,
            ForeignKey("rm_movement_periods.report_month", ondelete="CASCADE"),
            nullable=False,
        )
    )
    demand_plan_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    consumption_fact_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_plan_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_fact_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_start_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_on_site_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_in_transit_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_end_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    deviation_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class DeliveryBasis(UpdaterDisplay, table=True):
    """Delivery basis (FOB, FCA, CPT)."""

    __tablename__ = "rm_delivery_bases"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class RawMaterialPrice(UpdaterDisplay, table=True):
    """«Цены» form prices: 2025 fact and 2026 business plan for an asset-raw-material link."""

    __tablename__ = "rm_raw_material_prices"
    __table_args__ = (Index("ix_rm_raw_material_prices_delivery_basis_id", "delivery_basis_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_raw_material_id: UUID = Field(
        unique=True,
        index=True,
        foreign_key="rm_asset_raw_materials.id",
        ondelete="RESTRICT",
    )
    delivery_basis_id: UUID | None = Field(default=None, foreign_key="rm_delivery_bases.id", ondelete="RESTRICT")
    fact_2025: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_quantity_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_usd_per_ton: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_rub_per_ton: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_cost_rub: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    storage_standard_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")
    business_plan_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    business_plan_updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    storage_standard_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    storage_standard_updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class ReportSnapshot(SQLModel, table=True):
    """
    Fixed «Движение» version for the report month.

    Only one snapshot can exist per month: a preliminary fix is overwritten,
    the final one is immutable. previous_snapshot_id holds a reference to the
    overwritten version (no FK — it may be deleted).
    """

    __tablename__ = "rm_report_snapshots"
    __table_args__ = (
        Index("ix_rm_report_snapshots_report_month", "report_month"),
        Index("ix_rm_report_snapshots_fixed_at", "fixed_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    report_month: date = Field(sa_column=Column(Date, unique=True, nullable=False))
    fixed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    fixed_by_user_id: UUID = Field(foreign_key="app_users.id", ondelete="RESTRICT")
    fixed_by_email: str = Field(sa_column=Column(Text, nullable=False))
    fixed_by_fullname: str = Field(sa_column=Column(Text, nullable=False))
    is_final: bool = Field(default=False)
    previous_snapshot_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True, index=True),
    )


class SnapshotMaterialMovement(SQLModel, table=True):
    """
    Rows of a fixed «Движение» version (immutable archive).

    Stores copies of values and names: no FKs to the working tables, so the
    snapshot doesn't depend on their lifecycle.
    """

    __tablename__ = "rm_snapshot_material_movements"

    snapshot_id: UUID = Field(primary_key=True, foreign_key="rm_report_snapshots.id", ondelete="CASCADE")
    source_asset_raw_material_id: UUID = Field(primary_key=True)
    source_row_id: UUID = Field(default_factory=uuid4)
    source_asset_id: UUID = Field(default_factory=uuid4)
    source_raw_material_id: UUID = Field(default_factory=uuid4)
    asset_name: str = Field(sa_column=Column(Text, nullable=False))
    raw_material_name: str = Field(sa_column=Column(Text, nullable=False))
    business_plan_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    demand_plan_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    consumption_fact_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    demand_deviation_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_plan_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_fact_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_deviation_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_start_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_on_site_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_in_transit_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    balance_end_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    storage_standard_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    deviation_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


for _model in (
    RawMaterialAsset,
    RawMaterialUserAsset,
    RawMaterial,
    AssetRawMaterial,
    MovementPeriod,
    MaterialMovement,
    DeliveryBasis,
    RawMaterialPrice,
    ReportSnapshot,
    SnapshotMaterialMovement,
):
    apply_table_comments(SQLModel.metadata.tables[_model.__tablename__])
    if "updater_user_id" in _model.model_fields:
        configure_updater(_model)
