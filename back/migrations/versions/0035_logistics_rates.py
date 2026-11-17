"""
Create the «Ставки» main table.

The table references the corporate `dict` schema by FOREIGN KEY. That is a
deliberate requirement: a physically deleted dictionary row must be refused,
while a soft-deleted one (`deleted_at`) remains valid and the read model shows
«УДАЛЕНО: …». The external schema is configurable for tests
(`LOGISTICS_DICT_SCHEMA`), so the test fixture never touches the real `dict`.

Computed values (`Направление`, `Опасный`, the dollar equivalents and the
month-over-month deviations) are NOT stored.

Revision ID: 0035_logistics_rates
Revises: 0034_logistics_table_prefix
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.config import get_settings

revision: str = "0035_logistics_rates"
down_revision: str | None = "0034_logistics_table_prefix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXTERNAL_TABLES = (
    "orgs",
    "countries",
    "transport_types",
    "vehicle_types",
    "transport_types_vehicle_types",
)
_PRICE_KIND = postgresql.ENUM("INDICATIVE", "PRICE", name="logistics_price_kind", create_type=False)


def _require_external_tables() -> str:
    """Fail loudly when the corporate dictionaries are absent (the FK targets)."""
    schema = get_settings().logistics_dict_schema
    bind = op.get_bind()
    for table in _EXTERNAL_TABLES:
        exists = bind.execute(
            sa.text("SELECT to_regclass(:name) IS NOT NULL").bindparams(name=f"{schema}.{table}")
        ).scalar()
        if not exists:
            message = (
                "Для таблицы «Ставки» требуется внешняя схема "
                f"{schema}: не найдена {schema}.{table}. "
                "Загрузите справочники dict или укажите LOGISTICS_DICT_SCHEMA."
            )
            raise RuntimeError(message)
    return schema


def upgrade() -> None:
    schema = _require_external_tables()
    op.create_table(
        "l_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False, comment="Период ставки; первое число выбранного месяца"),
        sa.Column("org_id", sa.Uuid(), nullable=False, comment="ID актива → dict.orgs.id"),
        sa.Column(
            "hazard_class_value",
            sa.Integer(),
            nullable=True,
            comment="Класс опасности → l_hazard_classes.value; NULL — груз не опасный",
        ),
        sa.Column("country_from_id", sa.Uuid(), nullable=False, comment="Страна отправления → dict.countries.id"),
        sa.Column("country_to_id", sa.Uuid(), nullable=False, comment="Страна назначения → dict.countries.id"),
        sa.Column(
            "transport_type_id",
            sa.Uuid(),
            nullable=False,
            comment="Тип транспортного средства → dict.transport_types.id",
        ),
        sa.Column(
            "vehicle_type_id",
            sa.Uuid(),
            nullable=True,
            comment="Тип подвижного состава → dict.vehicle_types.id; NULL — у типа транспортного средства нет соответствий",
        ),
        sa.Column("loading_tons", sa.Numeric(18, 2), nullable=False, comment="Загрузка в транспортное средство, т"),
        sa.Column("freight_rate_rub", sa.Numeric(18, 2), nullable=False, comment="Ставка фрахта, руб./т"),
        sa.Column(
            "ppr_rate_rub",
            sa.Numeric(18, 2),
            nullable=False,
            comment="Ставка погрузочно-разгрузочных работ, руб",
        ),
        sa.Column(
            "delivery_rate_rub",
            sa.Numeric(18, 2),
            nullable=False,
            comment="Отправка из пункта А до пункта Б, руб",
        ),
        sa.Column(
            "net_rate_rub",
            sa.Numeric(18, 2),
            nullable=False,
            comment="Ставка за 1 тонну нетто без НДС, руб",
        ),
        sa.Column(
            "price_kind",
            _PRICE_KIND,
            nullable=False,
            comment="Индикатив: INDICATIVE — индикатив, PRICE — прайс",
        ),
        sa.Column("product", sa.Text(), nullable=True, comment="Продукт (плейсхолдер; пока всегда NULL)"),
        sa.Column(
            "origin_point",
            sa.Text(),
            nullable=True,
            comment="Пункт отправления (А) (плейсхолдер; пока всегда NULL)",
        ),
        sa.Column(
            "destination_point",
            sa.Text(),
            nullable=True,
            comment="Пункт назначения (Б) (плейсхолдер; пока всегда NULL)",
        ),
        sa.Column("note", sa.Text(), nullable=True, comment="Примечание"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updater_user_id",
            sa.Uuid(),
            nullable=True,
            comment="ID автора последнего изменения записи → app_users.id",
        ),
        sa.ForeignKeyConstraint(["hazard_class_value"], ["l_hazard_classes.value"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updater_user_id"], ["app_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="Логистика → Ставки: направления, транспорт, загрузка и ставки по периоду",
    )
    op.create_index("ix_l_rates_period", "l_rates", ["period_date"])
    for name, column, target in (
        ("l_rates_org_id_fkey", "org_id", "orgs"),
        ("l_rates_country_from_id_fkey", "country_from_id", "countries"),
        ("l_rates_country_to_id_fkey", "country_to_id", "countries"),
        ("l_rates_transport_type_id_fkey", "transport_type_id", "transport_types"),
        ("l_rates_vehicle_type_id_fkey", "vehicle_type_id", "vehicle_types"),
    ):
        op.create_foreign_key(
            name,
            "l_rates",
            target,
            [column],
            ["id"],
            referent_schema=schema,
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    op.drop_table("l_rates")
