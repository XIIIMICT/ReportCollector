"""«Логистика» domain models and enums used by the main table."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from src.auth.models import utc_now
from src.common.audit import UpdaterDisplay, configure_updater
from src.common.data_catalog import apply_table_comments


class LogisticsPriceKind(StrEnum):
    """
    The nature of a price in the «Логистика» main table.

    ``INDICATIVE`` is an indicative (estimate) price; ``PRICE`` is the firm
    price the shipment is actually billed at. The DB type is
    ``logistics_price_kind`` (migration 0032), so a future main-table column
    can reference the same enum the API exposes.
    """

    INDICATIVE = "INDICATIVE"
    PRICE = "PRICE"


LOGISTICS_PRICE_KIND = SAEnum(
    LogisticsPriceKind,
    name="logistics_price_kind",
    values_callable=lambda enum: [item.value for item in enum],
)


class LogisticsHazardClass(SQLModel, table=True):
    """
    The «Класс опасности» dictionary: values 1..9.

    It is application-owned reference data (unlike the external `dict` schema):
    migration 0033 creates the table and seeds the nine classes, and
    `just seed` restores them. It has no tab yet — the future main table uses
    it as a reference column.
    """

    __tablename__ = "l_hazard_classes"

    value: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=False))
    name: str = Field(sa_column=Column(Text, nullable=False))


class LogisticsRate(UpdaterDisplay, table=True):
    """
    One «Ставка» row of the main logistics table.

    Computed values are NOT stored: the direction, «Опасный» (derived from the
    hazard class), the dollar equivalents and the month-over-month deviations
    are produced by the read model. The rub rates and the loading weight are
    stored as decimals with two places.
    """

    __tablename__ = "l_rates"
    __table_args__ = (Index("ix_l_rates_period", "period_date"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    period_date: date = Field(sa_column=Column(Date, nullable=False))
    # External references: the DB has FKs to the corporate `dict` schema
    # (migration 0035); the model stays plain UUIDs so the metadata can be
    # compared/created without the external tables.
    org_id: UUID = Field(sa_column=Column(Uuid, nullable=False))
    hazard_class_value: int | None = Field(
        default=None,
        foreign_key="l_hazard_classes.value",
        ondelete="RESTRICT",
    )
    country_from_id: UUID = Field(sa_column=Column(Uuid, nullable=False))
    country_to_id: UUID = Field(sa_column=Column(Uuid, nullable=False))
    transport_type_id: UUID = Field(sa_column=Column(Uuid, nullable=False))
    vehicle_type_id: UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    loading_tons: Decimal = Field(sa_column=Column(Numeric(18, 2), nullable=False))
    freight_rate_rub: Decimal = Field(sa_column=Column(Numeric(18, 2), nullable=False))
    ppr_rate_rub: Decimal = Field(sa_column=Column(Numeric(18, 2), nullable=False))
    delivery_rate_rub: Decimal = Field(sa_column=Column(Numeric(18, 2), nullable=False))
    net_rate_rub: Decimal = Field(sa_column=Column(Numeric(18, 2), nullable=False))
    price_kind: LogisticsPriceKind = Field(sa_column=Column(LOGISTICS_PRICE_KIND, nullable=False))
    product: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    origin_point: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    destination_point: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


class LogisticsUserAsset(UpdaterDisplay, table=True):
    """
    An asset (a `dict.orgs` row) granted to a «Работник на активе».

    The worker sees only the rates of these assets and may add/edit rows for
    them; the admin manages the grants from «Пользователи». A worker without a
    single grant is equivalent to a blocked member.
    """

    __tablename__ = "l_user_assets"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="l_user_assets_user_org_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(sa_column=Column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True))
    # External reference (FK to `dict.orgs` in migration 0037); plain UUID here
    # so the metadata builds without the corporate schema.
    org_id: UUID = Field(sa_column=Column(Uuid, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updater_user_id: UUID | None = Field(default=None, foreign_key="app_users.id", ondelete="SET NULL")


for _model in (LogisticsHazardClass, LogisticsRate, LogisticsUserAsset):
    apply_table_comments(SQLModel.metadata.tables[_model.__tablename__])
    if "updater_user_id" in _model.model_fields:
        configure_updater(_model)
