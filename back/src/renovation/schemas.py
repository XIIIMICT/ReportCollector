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

from pydantic import BaseModel, Field

SectionId = Literal["renovationPlan"]
"""Workspace section ids (match the frontend)."""


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
