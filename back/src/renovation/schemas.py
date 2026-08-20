"""
API schemas of the «Ремонт» (renovation) project.

Field names mirror the legacy Tauri format: the form grid uses the db names
(col_xxx) as cell keys, reference fields are transported as display labels
(the server resolves them to value ids), and the renovation ID (col_003)
comes with the list of missing components for UI highlighting.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

_EMPTY_ASSET_UPDATE = "Задайте хотя бы одно поле: name или repairCode"
_EMPTY_OPTION_UPDATE = "Задайте поле label"

from src.raw_materials.schemas import ExportColumnRequest  # noqa: TC001
from src.renovation.models import RBlock, RProjectRole

SectionId = Literal["renovationPlan"]
"""Workspace section ids (match the frontend)."""

# Dictionary collab sections: «renovationAssets», «renovationUsers» and
# «renovationCatalog:{catalog_id}» (one room/undo scope per catalog).
DictionarySectionId = str


class RenovationAssetRead(BaseModel):
    """A renovation asset (legal entity) with its repair code."""

    id: UUID
    code: str
    repairCode: str
    name: str


class RenovationCatalogOptionRead(BaseModel):
    """A selectable option of a renovation reference catalog."""

    valueId: str
    label: str


class RenovationCatalogRead(BaseModel):
    """A renovation reference catalog (criticality, work type, ...)."""

    id: str
    label: str
    options: list[RenovationCatalogOptionRead]


class RenovationFieldRead(BaseModel):
    """Form field description: the grid renders cells from these."""

    dbName: str
    label: str
    cellType: str
    catalog: str | None = None
    isCalculated: bool
    isReadOnly: bool
    decimals: int | None = None
    position: int
    ordinal: int
    responsibilityBlock: str | None = None


class RenovationHeaderCellRead(BaseModel):
    """A merged header cell of the legacy form (11 levels, Excel ranges)."""

    id: str
    text: str
    startPosition: int
    colSpan: int
    rowSpan: int
    level: int
    excelRange: str


class RenovationHeaderRowRead(BaseModel):
    """One header level of the legacy form (a row of merged cells)."""

    level: int
    cells: list[RenovationHeaderCellRead]


class RenovationMetaRead(BaseModel):
    """Form dictionaries: assets, reference catalogs, field descriptions."""

    assets: list[RenovationAssetRead]
    catalogs: list[RenovationCatalogRead]
    fields: list[RenovationFieldRead]
    headerCells: list[RenovationHeaderCellRead] = []
    headerRows: list[RenovationHeaderRowRead] = []
    workingPlanId: UUID | None = None
    pendingPlanId: UUID | None = None


class RenovationPlanVersionRead(BaseModel):
    """A renovation plan version (working, pending approval, or archived)."""

    id: UUID
    state: str
    baselineGeneration: int
    rowCount: int
    createdAt: datetime
    createdBy: str
    updatedAt: datetime
    updatedBy: str


class RenovationPlanRowRead(BaseModel):
    """A plan row: flat typed values plus the computed renovation ID."""

    id: UUID
    assetId: UUID
    assetCode: str
    assetName: str
    assetRepairCode: str
    position: int
    revision: int
    updatedAt: datetime
    updatedBy: str
    values: dict[str, str | float | bool | date | None]
    renovationId: str | None = None
    missingComponents: list[str] = []


class RenovationPlanRead(BaseModel):
    """A renovation plan version with its rows."""

    id: UUID
    state: str
    baselineGeneration: int
    updatedAt: datetime
    updatedBy: str
    rows: list[RenovationPlanRowRead]
    baselineBoundary: int | None = None


class RenovationRowAddRequest(BaseModel):
    """A new working-plan row (the asset is the only required field)."""

    assetId: UUID


class RenovationDiffCellRead(BaseModel):
    """A cell of an archived version that differs from the previous version."""

    field: str
    oldValue: str | float | bool | date | None = None
    newValue: str | float | bool | date | None = None


class RenovationDiffRowRead(BaseModel):
    """A diff row: new rows carry every filled cell, changed rows only the changed ones."""

    rowId: UUID
    position: int
    assetName: str
    isNew: bool
    cells: list[RenovationDiffCellRead] = []


class RenovationDeletedRowRead(BaseModel):
    """A row present in the previous archived version but missing in the target."""

    position: int
    assetName: str


class RenovationDiffRead(BaseModel):
    """Cell diff of an archived version vs the previous archived version."""

    planId: UUID
    comparedToPlanId: UUID | None = None
    rows: list[RenovationDiffRowRead] = []
    deletedRows: list[RenovationDeletedRowRead] = []


class CellUpdate(BaseModel):
    """Single form cell edit (rowId — the row id, field — the db name)."""

    rowId: UUID
    field: str = Field(min_length=1, max_length=16)
    value: str | float | bool | None = None


class RenovationUpdateRequest(BaseModel):
    """Batch of renovation plan cell edits (working plan only)."""

    updates: list[CellUpdate] = Field(min_length=1, max_length=500)


class RenovationCalculatedCellRead(BaseModel):
    """A recalculated cell (formula field or the renovation ID)."""

    rowId: UUID
    field: str
    value: str | float | bool | date | None = None


class RenovationIdRead(BaseModel):
    """The recomputed renovation ID of a row after edits."""

    rowId: UUID
    value: str
    missingComponents: list[str] = []


class RenovationUpdateRead(BaseModel):
    """PATCH result: applied edits plus recalculated formula cells and IDs."""

    applied: int
    calculated: list[RenovationCalculatedCellRead] = []
    renovationIds: list[RenovationIdRead] = []


class RenovationApproveRead(BaseModel):
    """Approve result: the archived plan and the new working copy."""

    archived: RenovationPlanVersionRead
    working: RenovationPlanVersionRead


class RenovationAssetRowRead(BaseModel):
    """A dictionary grid row of a renovation asset (legal entity)."""

    id: UUID
    repairCode: str
    name: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class RenovationCatalogOptionRowRead(BaseModel):
    """A dictionary grid row of a renovation reference catalog option."""

    valueId: str
    label: str
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class RenovationUserRowRead(BaseModel):
    """A dictionary grid row of a renovation project user."""

    id: UUID
    email: str
    fullname: str
    role: RProjectRole
    block: RBlock | None = None
    assetNames: list[str] = []
    isActive: bool
    lastAuthenticatedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str


class RenovationAssetCreateRequest(BaseModel):
    """A new renovation asset (name and the official repair code)."""

    name: str = Field(min_length=1, max_length=255)
    repairCode: str = Field(min_length=1, max_length=64)


class RenovationAssetUpdateRequest(BaseModel):
    """Editable fields of a renovation asset (at least one must be set)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    repairCode: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _at_least_one(self) -> RenovationAssetUpdateRequest:
        if self.name is None and self.repairCode is None:
            raise ValueError(_EMPTY_ASSET_UPDATE)
        return self


class RenovationCatalogOptionCreateRequest(BaseModel):
    """A new option of a renovation reference catalog."""

    label: str = Field(min_length=1, max_length=255)


class RenovationCatalogOptionUpdateRequest(BaseModel):
    """Editable fields of a renovation catalog option (at least one must be set)."""

    label: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _at_least_one(self) -> RenovationCatalogOptionUpdateRequest:
        if self.label is None:
            raise ValueError(_EMPTY_OPTION_UPDATE)
        return self


class RenovationUserCreateRequest(BaseModel):
    """A new renovation project user (existing accounts are re-used)."""

    email: str = Field(min_length=3, max_length=255)
    fullname: str = Field(min_length=1, max_length=255)
    role: RProjectRole = RProjectRole.ASSET_WORKER
    block: RBlock | None = None
    assetNames: list[str] = []


class RenovationUserUpdateRequest(BaseModel):
    """Editable fields of a renovation project user."""

    fullname: str | None = Field(default=None, min_length=1, max_length=255)
    role: RProjectRole | None = None
    block: RBlock | None = None
    assetNames: list[str] | None = None
    isActive: bool | None = None


class RenovationDictionaryUpdateRequest(BaseModel):
    """Batch of dictionary cell edits (assets, catalog options, users)."""

    updates: list[CellUpdate] = Field(min_length=1, max_length=500)


class RenovationExportViewRequest(BaseModel):
    """Excel export of a renovation dictionary grid («вся» / «видимая»)."""

    section: str
    headerPinned: bool = True
    columns: list[ExportColumnRequest] = Field(min_length=1, max_length=64)
    rowIds: list[UUID] | None = None
    fileName: str = Field(min_length=1, max_length=255)
