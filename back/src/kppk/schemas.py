"""API schemas of the «КППК» dictionary phase (camelCase contracts)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.auth.emails import normalize_email
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
    orgNames: list[str] = Field(default_factory=list)
    lastAuthenticatedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class KppkUserCreateRequest(BaseModel):
    """Create an administrator; organization grants are not implemented yet."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    fullname: str = Field(min_length=1, max_length=255)
    isAdmin: bool = True

    @field_validator("email")
    @classmethod
    def normalize_address(cls, value: EmailStr) -> str:
        return normalize_email(str(value))


class KppkUserUpdateRequest(BaseModel):
    """User fields shared with the raw-materials grid, without asset grants."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    fullname: str | None = Field(default=None, min_length=1, max_length=255)
    isAdmin: bool | None = None
    isActive: bool | None = None
    resetPassword: bool = False

    @field_validator("email")
    @classmethod
    def normalize_address(cls, value: EmailStr | None) -> str | None:
        return None if value is None else normalize_email(str(value))


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

    periodDate: date
    monthName: str
    yearNum: int
    repDate: date


class KppkRealizationRowRead(BaseModel):
    """One row of a «Реализация» slice (the slice leaves its columns filled)."""

    id: UUID
    product: str
    # The «№» of the product in «Продуктовые позиции», resolved by name; NULL
    # when the name has no dictionary entry (hand-added names). Read-only —
    # it is the dictionary's number, not a property of the realization row.
    productNum: int | None = None
    region: str
    # ── Срез «Бизнес-план» (kppk_realization_plan, `_bp_plan` columns) ──
    buyoutBpPlan: float | None = None
    fcaPriceBpPlan: float | None = None
    monthBpPlan: float | None = None
    fcaPriceMarketBpPlan: float | None = None
    exciseBp: float = 0
    # ── Срез «Оперплан» (`_max_plan` columns) ──
    buyoutMaxPlan: float | None = None
    fcaPriceMaxPlan: float | None = None
    monthMaxPlan: float | None = None
    fcaPriceMarketMaxPlan: float | None = None
    exciseMaxPlan: float = 0
    # ── Срез «Факт» (kppk_realization) ──
    buyoutFact: float | None = None
    fcaPriceFact: float | None = None
    fact: float | None = None
    fcaPriceMarketFact: float | None = None
    exciseFact: float = 0


class KppkRealizationViewRead(BaseModel):
    """The «Реализация» view: the banner period, the рынки, the filtered rows."""

    period: KppkRealizationPeriodRead
    markets: list[str]
    rows: list[KppkRealizationRowRead]


class KppkCostRowRead(BaseModel):
    """One product's production costs; no market dimension."""

    id: UUID
    product: str
    productNum: int | None = None
    fullCostBp: float | None = None
    productionCostBp: float | None = None
    variableCostBp: float | None = None
    fullCostOp: float | None = None
    productionCostOp: float | None = None
    variableCostOp: float | None = None
    fullCostFact: float | None = None
    productionCostFact: float | None = None
    variableCostFact: float | None = None


class KppkCostViewRead(BaseModel):
    period: KppkRealizationPeriodRead
    rows: list[KppkCostRowRead]


class KppkRealizationCellUpdate(BaseModel):
    """One «Реализация» cell edit (the field names follow DB attributes)."""

    field: str
    value: object = None


class KppkRealizationExportRequest(BaseModel):
    """
    Excel export of the «Реализация» view.

    The form is not a dictionary row list but one slice × ДЗО × period, so the
    export replays the toolbar's selection instead of sending row ids: the
    server re-reads the same view (and therefore the same ordering).
    """

    slice: Literal["bp", "op", "fact"]
    orgId: UUID
    month: int | None = Field(default=None, ge=1, le=12)
    year: int | None = Field(default=None, ge=2000, le=2100)
    # The view has no row selection to narrow, so this is always None; it exists
    # because the shared export-shape validator speaks in terms of it.
    rowIds: list[UUID] | None = None
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=16)
    fileName: str = Field(min_length=1, max_length=255)


class KppkAppliedRead(BaseModel):
    """Mutation result with the new row payload (grid-friendly)."""

    row: KppkProductPositionRowRead | KppkRawRowRead | KppkOrgRowRead | KppkRealizationRowRead | KppkCostRowRead


class KppkExportViewRequest(BaseModel):
    """Excel export of a «КППК» dictionary grid («вся» / «видимая»)."""

    section: Literal["kppkPositions", "kppkRaws", "kppkOrgs", "kppkUsers"]
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=64)
    rowIds: list[UUID] | None = None
    fileName: str = Field(min_length=1, max_length=255)
