"""«Логистика» domain models and enums used by the main table."""

from enum import StrEnum

from sqlalchemy import Column, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

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


for _model in (LogisticsHazardClass,):
    apply_table_comments(SQLModel.metadata.tables[_model.__tablename__])
