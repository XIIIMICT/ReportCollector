"""Read models of the «Логистика» dictionaries backed by the external `dict` schema."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.auth.models import ProjectRole

from .models import LogisticsPriceKind  # noqa: TC001 — Pydantic needs the runtime enum


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
    # The vehicle-type id (a source UUID); null for a type without mappings.
    vehicleTypeId: str | None
    vehicleType: str


class LogisticsOrgRowRead(BaseModel):
    """One organization, represented by its Russian prefix."""

    id: str
    prefixRu: str


class LogisticsHazardClassRowRead(BaseModel):
    """One «Класс опасности» value (1..9)."""

    value: int
    name: str


class LogisticsUserRowRead(BaseModel):
    """One member of the «Логистика» project with its role and asset grants."""

    id: UUID
    email: str
    fullname: str
    # admin | reviewer | asset_worker | blocked. `isActive` is derived from it
    # (blocked → false) and stays for the shared access checks.
    role: ProjectRole
    isActive: bool
    assetIds: list[UUID]
    assetNames: list[str]
    lastAuthenticatedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class LogisticsUserCreateRequest(BaseModel):
    """Add a project member with a role (blocked by default) and its assets."""

    email: EmailStr
    fullname: str
    role: ProjectRole = ProjectRole.BLOCKED
    assetIds: list[UUID] = Field(default_factory=list)


class LogisticsUserUpdateRequest(BaseModel):
    """Edit the mutable fields of a membership; email is immutable."""

    fullname: str | None = None
    role: ProjectRole | None = None
    # The grid edits the assets by NAME (the generic multi-asset cell carries
    # names), the dialog sends ids; either is accepted.
    assetIds: list[UUID] | None = None
    assetNames: list[str] | None = None


# A rate/weight: non-negative, two decimal places, at most 18 digits.
RateDecimal = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
HazardClassValue = Annotated[int, Field(ge=1, le=9)]


class LogisticsRateRowRead(BaseModel):
    """One «Ставка» row with its computed columns."""

    id: UUID
    periodDate: date
    assetId: UUID
    assetName: str
    # Computed: the row is dangerous exactly when a hazard class is set.
    dangerous: bool
    hazardClassValue: int | None
    product: str | None
    # Computed from the two countries: the destination market for a shipment
    # from Russia (РФ/БЗ/ДЗ), otherwise «Импорт»/«Экспорт».
    direction: Literal["РФ", "БЗ", "ДЗ", "Импорт", "Экспорт"]
    countryFromId: UUID
    countryFromName: str
    originPoint: str | None
    countryToId: UUID
    countryToName: str
    destinationPoint: str | None
    transportTypeId: UUID
    transportTypeName: str
    vehicleTypeId: UUID | None
    vehicleTypeName: str
    loadingTons: float
    freightRateRub: float
    # Computed: the rub value × the USD rate; never stored.
    freightRateUsd: float
    pprRateRub: float
    pprRateUsd: float
    deliveryRateRub: float
    deliveryRateUsd: float
    netRateRub: float
    netRateUsd: float
    priceKind: LogisticsPriceKind
    note: str | None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class LogisticsRateCreateRequest(BaseModel):
    """Insert a complete rate row; the placeholders are always NULL."""

    periodDate: date
    # Omitted by an asset worker: the server stamps their own asset.
    assetId: UUID | None = None
    hazardClassValue: HazardClassValue | None = None
    countryFromId: UUID
    countryToId: UUID
    transportTypeId: UUID
    vehicleTypeId: UUID | None = None
    loadingTons: RateDecimal
    freightRateRub: RateDecimal
    pprRateRub: RateDecimal
    deliveryRateRub: RateDecimal
    netRateRub: RateDecimal
    priceKind: LogisticsPriceKind
    note: str | None = None


class LogisticsRateCellUpdate(BaseModel):
    """One editable cell of a rate; the service validates the field/value pair."""

    field: str
    value: object


class LogisticsRateViewRead(BaseModel):
    """The period view: its rows plus the FX rate used for the dollar columns."""

    periodDate: date
    usdRate: float
    rows: list[LogisticsRateRowRead]


class LogisticsDictionaryRead[T](BaseModel):
    """
    A dictionary payload plus where it came from.

    ``external`` — rows read from the corporate ``dict`` schema;
    ``fallback`` — the table is absent and the grid shows the built-in demo
    values (the UI warns about it).
    """

    source: Literal["external", "fallback"]
    rows: list[T]
