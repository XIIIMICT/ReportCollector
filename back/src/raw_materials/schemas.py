"""
Схемы API проекта «Сырьё» (read-only).

Имена полей повторяют формат фронтенда Tauri-приложения (camelCase),
чтобы формы «Движение» и «Цены» отображались без изменений.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SectionId = Literal[
    "materialMovement",
    "rawMaterialPrices",
    "rawMaterials",
    "assets",
    "deliveryBases",
    "users",
]
"""Идентификаторы секций рабочего пространства (совпадают с фронтендом)."""


class ExportColumnRequest(BaseModel):
    """Столбец выгружаемой таблицы: ключ, путь заголовка, ширина, формат числа."""

    key: str = Field(min_length=1, max_length=64)
    headerPath: list[str] = Field(min_length=1, max_length=3)
    width: float = Field(default=12.0, ge=1.0, le=100.0)
    numberFormat: str | None = None


class ExportViewRequest(BaseModel):
    """Запрос выгрузки таблицы в Excel («вся» — без rowIds, «видимая» — с rowIds)."""

    section: SectionId
    reportMonth: date | None = None
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=64)
    rowIds: list[UUID] | None = None
    snapshotId: UUID | None = None
    fileName: str = Field(min_length=1, max_length=255)


class MovementRowRead(BaseModel):
    """Строка формы «Движение» для связки актив-сырьё в отчётном месяце."""

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
    """Строка формы «Цены» для связки актив-сырьё."""

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
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class RawMaterialsMetaRead(BaseModel):
    """Справочники для форм: доступные активы, периоды движения, базисы поставки."""

    assets: list[str]
    movementPeriods: list[date]
    deliveryBases: list[str]


class RawMaterialRowRead(BaseModel):
    """Строка справочника «Сырьевые позиции»."""

    id: UUID
    name: str
    assetNames: list[str]
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class AssetRowRead(BaseModel):
    """Строка справочника «Активы»."""

    id: UUID
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class DeliveryBasisRowRead(BaseModel):
    """Строка справочника «Базисы поставки»."""

    id: UUID
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class UserRowRead(BaseModel):
    """Строка справочника «Пользователи» (администраторский раздел)."""

    id: UUID
    email: str | None
    fullname: str
    isAdmin: bool
    assetNames: list[str]
    isActive: bool
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class CellUpdate(BaseModel):
    """Изменение одной ячейки формы или справочника (rowId — id строки)."""

    rowId: UUID
    field: str
    value: str | float | bool | list[str] | None


class MovementUpdateRequest(BaseModel):
    """Пакет правок ячеек формы «Движение» за отчётный месяц."""

    reportMonth: date
    updates: list[CellUpdate]


class PriceUpdateRequest(BaseModel):
    """Пакет правок ячеек формы «Цены»."""

    updates: list[CellUpdate]


class MovementSnapshotRead(BaseModel):
    """Зафиксированная версия формы «Движение» за отчётный месяц."""

    id: UUID
    reportMonth: date
    fixedAt: datetime
    fixedByFullname: str
    isFinal: bool


class MovementMetaRead(BaseModel):
    """Статус формы «Движение»: полнота, финальность, архив версий."""

    reportMonth: date
    isComplete: bool
    isFinal: bool
    snapshots: list[MovementSnapshotRead]


class FixMovementRequest(BaseModel):
    """Запрос фиксации формы «Движение» за отчётный месяц."""

    reportMonth: date


class SnapshotMovementRowRead(BaseModel):
    """Строка зафиксированной версии «Движения» (копия значений на момент фиксации)."""

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
    """Версия для просмотра: снимок, предыдущий снимок для сравнения и строки."""

    snapshot: MovementSnapshotRead
    comparisonSnapshotId: UUID | None
    rows: list[SnapshotMovementRowRead]


class RawMaterialCreateRequest(BaseModel):
    """Создание сырьевой позиции с привязкой к активам."""

    name: str = Field(min_length=1, max_length=255)
    assetIds: list[UUID] = []


class RawMaterialUpdateRequest(BaseModel):
    """Правка сырьевой позиции: наименование и/или активы."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    assetIds: list[UUID] | None = None


class AssetCreateRequest(BaseModel):
    """Создание актива."""

    name: str = Field(min_length=1, max_length=255)


class AssetUpdateRequest(BaseModel):
    """Правка актива."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class DeliveryBasisCreateRequest(BaseModel):
    """Создание базиса поставки."""

    name: str = Field(min_length=1, max_length=255)


class DeliveryBasisUpdateRequest(BaseModel):
    """Правка базиса поставки."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class UserCreateRequest(BaseModel):
    """Создание пользователя проекта «Сырьё» (локальная учётка)."""

    email: str = Field(min_length=3, max_length=255)
    fullname: str = Field(min_length=1, max_length=255)
    isAdmin: bool = False
    assetNames: list[str] = []


class UserUpdateRequest(BaseModel):
    """Правка пользователя проекта «Сырьё»."""

    email: str | None = Field(default=None, min_length=3, max_length=255)
    fullname: str | None = Field(default=None, min_length=1, max_length=255)
    isAdmin: bool | None = None
    assetNames: list[str] | None = None
    isActive: bool | None = None
    resetPassword: bool = False
