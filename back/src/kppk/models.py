"""
Domain models of the «КППК» project (prefix kppk_).

This phase works with the normalized copies of the source tables: the seeder
fills `raw.kppk_*` verbatim from the dumps in `docs/kppk/`, then copies them
into the program schema. Every copy is uuid-keyed; the source integer id stays
as a unique column (`org_id`, `period_id`, `product_num`, `raw_num`) — both the
seeder's re-copy key and the join key the fact/plan tables reference;
`product_num`/`raw_num` are admin-editable in the UI and stay unique. The
dictionary names are unique as well (exact matching: spaces are significant).

The `kppk_realization*` copies hold the «Реализация» form data (Таблицы.xlsx
sheet «Реализация»): the form renders one of the three slices — «Бизнес-план»
and «Оперплан» read the plan copy, «Факт» reads the fact copy of the current
period with the ДЗО filtered out. The buyout columns (the template's
«Выкуп / Объем», the seederless "новая фигня") are program-side only: the
source dumps don't carry them yet and start NULL.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


def _updated_at_column() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class KppkOrg(SQLModel, table=True):
    """A ДЗО (enterprise) of the «КППК» portal (the source time zone is not copied)."""

    __tablename__ = "kppk_orgs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Source integer id of raw.kppk_d_org — the seeder's re-copy key; unique,
    # because the fact/plan copies reference it as a foreign key.
    org_id: int | None = Field(default=None, sa_column=Column(Integer, unique=True, nullable=True))
    code: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=_updated_at_column())
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class KppkPeriod(SQLModel, table=True):
    """A report month of the «КППК» project (the source period dictionary)."""

    __tablename__ = "kppk_periods"
    __table_args__ = (UniqueConstraint("month_name", "year_num", name="uq_kppk_periods_month_year"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Source integer id — unique: the fact/plan copies reference it.
    period_id: int | None = Field(default=None, sa_column=Column(Integer, unique=True, nullable=True))
    month_name: str = Field(sa_column=Column(Text, nullable=False))
    year_num: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    first_date: date = Field(sa_column=Column(Date, nullable=False))
    last_date: date = Field(sa_column=Column(Date, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=_updated_at_column())
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class KppkProduct(SQLModel, table=True):
    """A product of the source product dictionary (raw.kppk_d_product_seq)."""

    __tablename__ = "kppk_products"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Source integer num of raw.kppk_d_product_seq; NULL for products added
    # from the Матрица продуктов.xlsx that are missing in the source dictionary
    # (the seeder adds those under an «@»-prefixed name).
    product_num: int | None = Field(default=None, sa_column=Column(Integer, unique=True, nullable=True))
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=_updated_at_column())
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class KppkRaw(SQLModel, table=True):
    """A raw-material item (the «Сырьевые позиции» dictionary, no ДЗО scope)."""

    __tablename__ = "kppk_raws"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Source integer num of raw.kppk_d_raw_seq; NULL for rows added in the UI.
    raw_num: int | None = Field(default=None, sa_column=Column(Integer, unique=True, nullable=True))
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=_updated_at_column())
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class KppkOrgProduct(SQLModel, table=True):
    """«Продуктовые позиции»: a product × ДЗО link (Матрица продуктов.xlsx)."""

    __tablename__ = "kppk_org_products"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", name="uq_kppk_org_products_org_product"),
        Index("ix_kppk_org_products_product", "product_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(sa_column=Column(ForeignKey("kppk_orgs.id", ondelete="RESTRICT"), nullable=False, index=True))
    product_id: UUID = Field(sa_column=Column(ForeignKey("kppk_products.id", ondelete="RESTRICT"), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=_updated_at_column())
    updated_by: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))


class KppkRealization(SQLModel, table=True):
    """Copy of raw.kppk_f_realization (uuid PK, FKs by source ids)."""

    __tablename__ = "kppk_realization"
    __table_args__ = (
        Index("ix_kppk_realization_scope", "period_id", "org_id", "product"),
        Index("ix_kppk_realization_region_product", "region", "product"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: int = Field(sa_column=Column(ForeignKey("kppk_orgs.org_id", ondelete="RESTRICT"), nullable=False))
    period_id: int = Field(sa_column=Column(ForeignKey("kppk_periods.period_id", ondelete="RESTRICT"), nullable=False))
    load_id: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    rep_date: date = Field(sa_column=Column(Date, nullable=False))
    product: str = Field(sa_column=Column(Text, nullable=False))
    region: str = Field(sa_column=Column(Text, nullable=False))

    # «Выкуп / Объем» of the «Факт» slice (the template's column slot before
    # fca_price_fact) — a program-side addition, NULL on the source dumps.
    buyout_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    month_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    month_part_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    fca_price_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    var_exp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    export_duty_proc: Decimal | None = Field(default=None, sa_column=Column(Numeric(10, 3), nullable=True))
    fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    fca_price_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    var_exp_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    stock: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    plan_to_sell: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    export_duty_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    export_duty_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    excise_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    excise_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    full_cost_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    full_cost_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    input_cost_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    input_cost_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    stock_current: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    fca_price_market_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_market_fact: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    lastdate: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class KppkRealizationPlan(SQLModel, table=True):
    """Copy of raw.kppk_f_realization_plan (uuid PK, FKs by source ids)."""

    __tablename__ = "kppk_realization_plan"
    __table_args__ = (
        Index("ix_kppk_realization_plan_scope", "period_id", "org_id", "product"),
        Index("ix_kppk_realization_plan_region_product", "region", "product"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: int = Field(sa_column=Column(ForeignKey("kppk_orgs.org_id", ondelete="RESTRICT"), nullable=False))
    period_id: int = Field(sa_column=Column(ForeignKey("kppk_periods.period_id", ondelete="RESTRICT"), nullable=False))
    load_id: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    rep_date: date = Field(sa_column=Column(Date, nullable=False))
    product: str = Field(sa_column=Column(Text, nullable=False))
    region: str = Field(sa_column=Column(Text, nullable=False))

    # «Выкуп / Объем» of the plan slices (the template's column slot before
    # fca_price_*_plan) — a program-side addition, NULL on the source dumps.
    buyout_bp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    buyout_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    month_bp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    month_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    month_opt_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 3), nullable=True))
    fca_price_bp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_opt_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    var_exp_bp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    var_exp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    export_duty_bp: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    export_duty_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    export_duty_opt_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    excise_bp: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    excise_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    excise_opt_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    input_cost_bp: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    input_cost_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_market_bp_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_market_max_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    fca_price_market_opt_plan: Decimal | None = Field(default=None, sa_column=Column(Numeric(20, 2), nullable=True))
    lastdate: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
