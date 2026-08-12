"""
Доменные модели проекта «Сырьё» (префикс rm_).

Схема повторяет таблицы SQLite-приложения ReportCollector-RawMaterials
(экспорт v14): активы, сырьевые позиции, связки актив-сырьё, периоды,
движения сырья, базисы поставки и цены. Пользователи живут в app_user,
привязка к активам — в rm_user_asset.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class RawMaterialAsset(SQLModel, table=True):
    """Актив (площадка)."""

    __tablename__ = "rm_asset"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class RawMaterialUserAsset(SQLModel, table=True):
    """Привязка работника по активам к активам проекта «Сырьё»."""

    __tablename__ = "rm_user_asset"
    __table_args__ = (UniqueConstraint("user_id", "asset_id"),)

    user_id: UUID = Field(primary_key=True, foreign_key="app_user.id", ondelete="CASCADE")
    asset_id: UUID = Field(primary_key=True, foreign_key="rm_asset.id", ondelete="CASCADE")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RawMaterial(SQLModel, table=True):
    """Сырьевая позиция (наименование сырья)."""

    __tablename__ = "rm_raw_material"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class AssetRawMaterial(SQLModel, table=True):
    """Связка «актив — сырьевая позиция» (строка форм «Движение» и «Цены»)."""

    __tablename__ = "rm_asset_raw_material"
    __table_args__ = (UniqueConstraint("asset_id", "raw_material_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_id: UUID = Field(foreign_key="rm_asset.id", ondelete="RESTRICT")
    raw_material_id: UUID = Field(foreign_key="rm_raw_material.id", ondelete="RESTRICT")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class MovementPeriod(SQLModel, table=True):
    """Отчётный месяц формы «Движение»."""

    __tablename__ = "rm_movement_period"

    report_month: date = Field(sa_column=Column(Date, primary_key=True, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class MaterialMovement(SQLModel, table=True):
    """Значения ячеек формы «Движение» для связки актив-сырьё в отчётном месяце."""

    __tablename__ = "rm_material_movement"
    __table_args__ = (UniqueConstraint("asset_raw_material_id", "report_month"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_raw_material_id: UUID = Field(foreign_key="rm_asset_raw_material.id", ondelete="RESTRICT")
    report_month: date = Field(
        sa_column=Column(
            Date,
            ForeignKey("rm_movement_period.report_month", ondelete="CASCADE"),
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
    storage_standard_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    deviation_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class DeliveryBasis(SQLModel, table=True):
    """Базис поставки (FOB, FCA, CPT)."""

    __tablename__ = "rm_delivery_basis"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class RawMaterialPrice(SQLModel, table=True):
    """Цены формы «Цены»: факт 2025 и бизнес-план 2026 для связки актив-сырьё."""

    __tablename__ = "rm_raw_material_price"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_raw_material_id: UUID = Field(
        unique=True,
        index=True,
        foreign_key="rm_asset_raw_material.id",
        ondelete="RESTRICT",
    )
    delivery_basis_id: UUID | None = Field(default=None, foreign_key="rm_delivery_basis.id", ondelete="RESTRICT")
    fact_2025: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_quantity_tons: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_usd_per_ton: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    business_plan_rub_per_ton: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    delivery_cost_rub: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    business_plan_updated_at: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    business_plan_updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
