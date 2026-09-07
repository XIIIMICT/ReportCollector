"""
API schemas of the «Сырьё» project (read-only).

Field names mirror the Tauri frontend format (camelCase) so the «Движение»
and «Цены» forms render unchanged.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from src.auth.emails import normalize_email

SectionId = Literal[
    "materialMovement",
    "rawMaterialPrices",
    "rawMaterials",
    "assets",
    "deliveryBases",
    "users",
]
"""Workspace section ids (match the frontend)."""


class ExportColumnRequest(BaseModel):
    """Exported table column: key, header path, width, number format."""

    key: str = Field(min_length=1, max_length=64)
    headerPath: list[str] = Field(min_length=1, max_length=3)
    width: float = Field(default=12.0, ge=1.0, le=100.0)
    numberFormat: str | None = None


class ExportViewRequest(BaseModel):
    """Excel export request («вся» — without rowIds, «видимая» — with rowIds)."""

    section: SectionId
    reportMonth: date | None = None
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=64)
    rowIds: list[UUID] | None = None
    snapshotId: UUID | None = None
    fileName: str = Field(min_length=1, max_length=255)


class MovementRowRead(BaseModel):
    """«Движение» form row for an asset-raw-material link in the report month."""

    id: UUID
    key: UUID
    assetId: UUID
    assetName: str
    rawMaterialId: UUID
    rawMaterialName: str
    businessPlanTons: float | None
    demandPlanTons: float | None
    consumptionFactTons: float | None
    demandDeviationTons: float | None
    deliveryPlanTons: float | None
    deliveryFactTons: float | None
    deliveryDeviationTons: float | None
    balanceStartTons: float | None
    balanceOnSiteTons: float | None
    balanceInTransitTons: float | None
    balanceEndTons: float | None
    storageStandardTons: float | None
    deviationReason: str | None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str
    priceId: UUID


class PriceRowRead(BaseModel):
    """«Цены» form row for an asset-raw-material link."""

    id: UUID
    key: UUID
    assetId: UUID
    assetName: str
    rawMaterialId: UUID
    rawMaterialName: str
    deliveryBasisName: str | None
    fact2025: float | None
    businessPlanQuantityTons: float | None
    businessPlanUsdPerTon: float | None
    businessPlanRubPerTon: float | None
    deliveryCostRub: float | None
    storageStandardTons: float | None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class AssetOptionRead(BaseModel):
    """Asset with id: the grid maps asset names to their ids."""

    id: UUID
    name: str


class RawMaterialsMetaRead(BaseModel):
    """Form dictionaries: available assets, movement periods, delivery bases."""

    assets: list[AssetOptionRead]
    movementPeriods: list[date]
    deliveryBases: list[str]


class RawMaterialRowRead(BaseModel):
    """«Сырьевые позиции» dictionary row."""

    id: UUID
    name: str
    assetNames: list[str]
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class AssetRowRead(BaseModel):
    """«Активы» dictionary row."""

    id: UUID
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class DeliveryBasisRowRead(BaseModel):
    """«Базисы поставки» dictionary row."""

    id: UUID
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class UserRowRead(BaseModel):
    """«Пользователи» dictionary row (admin section)."""

    id: UUID
    email: str | None
    fullname: str
    isAdmin: bool
    assetNames: list[str]
    isActive: bool
    # Last successful authentication (Keycloak or local), null if never logged in.
    lastAuthenticatedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class CellUpdate(BaseModel):
    """Single form/dictionary cell edit (rowId — the row id)."""

    rowId: UUID
    field: str = Field(min_length=1, max_length=64)
    value: str | float | bool | list[str] | None


class MovementUpdateRequest(BaseModel):
    """Batch of «Движение» cell edits for the report month."""

    reportMonth: date
    updates: list[CellUpdate] = Field(min_length=1, max_length=10000)


class PriceUpdateRequest(BaseModel):
    """Batch of «Цены» cell edits."""

    updates: list[CellUpdate] = Field(min_length=1, max_length=10000)


class MovementSnapshotRead(BaseModel):
    """Fixed «Движение» version for the report month."""

    id: UUID
    reportMonth: date
    fixedAt: datetime
    fixedByFullname: str
    isFinal: bool


class MovementMetaRead(BaseModel):
    """«Движение» form status: completeness, finality, version archive."""

    reportMonth: date
    isComplete: bool
    isFinal: bool
    snapshots: list[MovementSnapshotRead]


class FixMovementRequest(BaseModel):
    """«Движение» fixing request for the report month."""

    reportMonth: date


class SnapshotMovementRowRead(BaseModel):
    """Row of a fixed «Движение» version (value copy at fixing time)."""

    sourceAssetRawMaterialId: UUID
    assetName: str
    rawMaterialName: str
    businessPlanTons: float | None
    demandPlanTons: float | None
    consumptionFactTons: float | None
    demandDeviationTons: float | None
    deliveryPlanTons: float | None
    deliveryFactTons: float | None
    deliveryDeviationTons: float | None
    balanceStartTons: float | None
    balanceOnSiteTons: float | None
    balanceInTransitTons: float | None
    balanceEndTons: float | None
    storageStandardTons: float | None
    deviationReason: str | None


class SnapshotViewRead(BaseModel):
    """View version: the snapshot, the previous snapshot for comparison, and rows."""

    snapshot: MovementSnapshotRead
    comparisonSnapshotId: UUID | None
    rows: list[SnapshotMovementRowRead]


class RawMaterialCreateRequest(BaseModel):
    """Raw-material item creation with asset links."""

    name: str = Field(min_length=1, max_length=255)
    assetIds: list[UUID] = []


class RawMaterialUpdateRequest(BaseModel):
    """Raw-material item edit: name and/or assets."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    assetIds: list[UUID] | None = None


class AssetCreateRequest(BaseModel):
    """Asset creation."""

    name: str = Field(min_length=1, max_length=255)


class AssetUpdateRequest(BaseModel):
    """Asset edit."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class DeliveryBasisCreateRequest(BaseModel):
    """Delivery basis creation."""

    name: str = Field(min_length=1, max_length=255)


class DeliveryBasisUpdateRequest(BaseModel):
    """Delivery basis edit."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class UserCreateRequest(BaseModel):
    """«Сырьё» project user creation (local account)."""

    email: EmailStr = Field(min_length=3, max_length=255)
    fullname: str = Field(min_length=1, max_length=255)
    isAdmin: bool = False
    assetNames: list[str] = []

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        # EmailStr validates the format; here we lowercase it.
        return normalize_email(str(value))


class UserUpdateRequest(BaseModel):
    """«Сырьё» project user edit."""

    email: EmailStr | None = Field(default=None, min_length=3, max_length=255)
    fullname: str | None = Field(default=None, min_length=1, max_length=255)
    isAdmin: bool | None = None
    assetNames: list[str] | None = None
    isActive: bool | None = None
    resetPassword: bool = False

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr | None) -> str | None:
        if value is None:
            return None
        return normalize_email(str(value))
