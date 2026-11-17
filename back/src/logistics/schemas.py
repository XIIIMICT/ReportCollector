"""Read models of the «Логистика» dictionaries backed by the external `dict` schema."""

from typing import Literal

from pydantic import BaseModel


class LogisticsCountryRowRead(BaseModel):
    """One country: the short name and the computed sales market."""

    id: str
    shortname: str
    # RU → РФ; ts or sng → БЗ; otherwise ДЗ. Computed by the reader, never stored.
    market: Literal["РФ", "БЗ", "ДЗ"]


class LogisticsTransportRowRead(BaseModel):
    """
    One transport type × vehicle type pair.

    The dictionary is rendered as a grouped table: the transport type cell is
    merged over its vehicle types. A type with no mapped vehicles still gets a
    single row with an empty ``vehicleType`` — the type itself must not vanish.
    """

    id: str
    transportTypeId: str
    transportType: str
    vehicleType: str


class LogisticsOrgRowRead(BaseModel):
    """One organization, represented by its Russian prefix."""

    id: str
    prefixRu: str


class LogisticsHazardClassRowRead(BaseModel):
    """One «Класс опасности» value (1..9)."""

    value: int
    name: str


class LogisticsDictionaryRead[T](BaseModel):
    """
    A dictionary payload plus where it came from.

    ``external`` — rows read from the corporate ``dict`` schema;
    ``fallback`` — the table is absent and the grid shows the built-in demo
    values (the UI warns about it).
    """

    source: Literal["external", "fallback"]
    rows: list[T]
