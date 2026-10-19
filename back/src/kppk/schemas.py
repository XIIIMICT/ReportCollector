"""API schemas of the «КППК» dictionary phase (camelCase contracts)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.raw_materials.schemas import ExportColumnRequest  # noqa: TC001


class KppkProductPositionRowRead(BaseModel):
    """One row of «Продуктовые позиции» (a product with its ДЗО set)."""

    id: UUID
    # Source dictionary number («№») — editable and unique; `None` for products
    # the seeder created from the matrix outside the source dictionary or added
    # by hand.
    productNum: int | None
    name: str
    orgNames: list[str]
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class KppkProductPositionCreateRequest(BaseModel):
    """A new «Продуктовые позиции» row (a product with its ДЗО set)."""

    name: str = Field(min_length=1, max_length=255)
    orgNames: list[str] = Field(default_factory=list)
    # Optional source number («№») — unique; `None` starts the row unnumbered.
    productNum: int | None = None


class KppkProductPositionCellUpdate(BaseModel):
    """One dictionary cell edit of a «Продуктовые позиции» row."""

    field: str
    value: object = None


class KppkRawRowRead(BaseModel):
    """One row of «Сырьевые позиции»."""

    id: UUID
    # Source dictionary number («№») — editable and unique; `None` for raws
    # added by hand.
    rawNum: int | None
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class KppkRawCreateRequest(BaseModel):
    """A new raw item."""

    name: str = Field(min_length=1, max_length=2000)
    # Optional source number («№») — unique; `None` starts the row unnumbered.
    rawNum: int | None = None


class KppkRawCellUpdate(BaseModel):
    """One dictionary cell edit of a «Сырьевые позиции» row."""

    field: str
    value: object = None


class KppkOrgRowRead(BaseModel):
    """One row of «ДЗО»."""

    id: UUID
    code: str
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class KppkOrgCreateRequest(BaseModel):
    """A new ДЗО."""

    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)


class KppkOrgCellUpdate(BaseModel):
    """One dictionary cell edit of a ДЗО row."""

    field: str
    value: object = None


class KppkUserRowRead(BaseModel):
    """One row of the «Пользователи» dictionary of «КППК»."""

    id: UUID
    email: str
    fullname: str
    isAdmin: bool
    isActive: bool


class KppkPeriodRowRead(BaseModel):
    """One row of «Периоды»."""

    id: UUID
    monthName: str
    yearNum: int
    firstDate: date
    lastDate: date
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class KppkPeriodCreateRequest(BaseModel):
    """A new report month."""

    monthName: str = Field(min_length=1, max_length=20)
    yearNum: int
    firstDate: date
    lastDate: date


class KppkPeriodCellUpdate(BaseModel):
    """One dictionary cell edit of a period row."""

    field: str
    value: object = None


# ─── «Реализация» (Таблицы.xlsx sheet «Реализация») ────────────────────────


class KppkRealizationPeriodRead(BaseModel):
    """The banner period of the «Реализация» view (месяц/год of the form header)."""

    id: UUID
    monthName: str
    yearNum: int
    repDate: date | None


class KppkRealizationRowRead(BaseModel):
    """One row of a «Реализация» slice (the slice leaves its columns filled)."""

    id: UUID
    product: str
    region: str
    # ── Срез «Бизнес-план» (kppk_realization_plan, `_bp_plan` columns) ──
    buyoutBpPlan: float | None = None
    fcaPriceBpPlan: float | None = None
    monthBpPlan: float | None = None
    fcaPriceMarketBpPlan: float | None = None
    # ── Срез «Оперплан» (`_max_plan` columns) ──
    buyoutMaxPlan: float | None = None
    fcaPriceMaxPlan: float | None = None
    monthMaxPlan: float | None = None
    fcaPriceMarketMaxPlan: float | None = None
    # ── Срез «Факт» (kppk_realization) ──
    buyoutFact: float | None = None
    fcaPriceFact: float | None = None
    fact: float | None = None
    fcaPriceMarketFact: float | None = None


class KppkRealizationViewRead(BaseModel):
    """The «Реализация» view: the banner period, the рынки, the filtered rows."""

    period: KppkRealizationPeriodRead
    markets: list[str]
    rows: list[KppkRealizationRowRead]


class KppkRealizationCellUpdate(BaseModel):
    """One «Реализация» cell edit (the field names follow DB attributes)."""

    field: str
    value: object = None


class KppkAppliedRead(BaseModel):
    """Mutation result with the new row payload (grid-friendly)."""

    row: KppkProductPositionRowRead | KppkRawRowRead | KppkOrgRowRead | KppkPeriodRowRead | KppkRealizationRowRead


class KppkExportViewRequest(BaseModel):
    """Excel export of a «КППК» dictionary grid («вся» / «видимая»)."""

    section: Literal["kppkPositions", "kppkRaws", "kppkOrgs"]
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=64)
    rowIds: list[UUID] | None = None
    fileName: str = Field(min_length=1, max_length=255)
