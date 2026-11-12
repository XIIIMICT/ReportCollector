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
    rules: list[dict[str, object]] = []
    allowNull: bool = True


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
    # The current user's responsibility block (None for central roles).
    userBlock: str | None = None
    # The user's renovation roles (source of truth for plan edit rights; the
    # auth ProjectRole only carries admin/asset_worker).
    userRoles: list[str] = []


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
    assetId: UUID | None = None
    assetCode: str | None = None
    assetName: str | None = None
    assetRepairCode: str | None = None
    position: int
    revision: int
    createdAt: datetime
    updatedAt: datetime
    updatedBy: str
    values: dict[str, str | float | bool | date | None]
    renovationId: str | None = None
    missingComponents: list[str] = []
    # Fields whose stored value breaks a form rule (red highlight in the grid).
    invalidFields: list[str] = []


class RenovationPlanRead(BaseModel):
    """A renovation plan version with its rows."""

    id: UUID
    state: str
    baselineGeneration: int
    updatedAt: datetime
    updatedBy: str
    rows: list[RenovationPlanRowRead]
    baselineBoundary: int | None = None


class RenovationPlanRowsRead(BaseModel):
    """A window of renovation plan rows plus the total row count (lazy loading)."""

    id: UUID
    state: str
    baselineGeneration: int
    updatedAt: datetime
    updatedBy: str
    rows: list[RenovationPlanRowRead]
    total: int
    baselineBoundary: int | None = None
    # Total rows in the plan WITHOUT the asset filter (for the «всего» counter,
    # which must not shrink when a filter hides rows).
    totalAll: int | None = None
    # Rows before the requested `anchor_position` within the filtered scope
    # (the anchor row's 0-based index; `total` when it is past the last row).
    anchorOffset: int | None = None


class RenovationPlanSummaryRead(BaseModel):
    """Numeric totals of the filtered plan (the pinned summary row)."""

    values: dict[str, float]


class RenovationColumnFilter(BaseModel):
    """
    One active header filter on a plan column.

    `values` are the display strings the user kept; `includeNulls` keeps the
    «Пустые» bucket. Columns combine with AND; a column with every value kept is
    simply omitted by the client (no constraint).
    """

    column: str = Field(min_length=1)
    values: list[str] = []
    includeNulls: bool = False
    # Explicit values distinguish a literal code from legacy validation labels.
    valueMode: Literal["exact"] | None = None
    # A MIN–MAX window INSTEAD of `values`, for a `timestamptz` column: the server
    # then matches the rows between the two instants. A value list makes no sense
    # there (every row has its own timestamp), so only one of the two is set.
    # `from_` is `from`: the alias keeps the field off SQLModel's expression API.
    #
    # Typed as STRINGS on purpose: parsing happens in the service, which can
    # answer 422 with a message instead of failing validation (a 500 for the
    # whole window). `from_` is `from` — the name stays off SQLModel's API.
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class RenovationSortColumn(BaseModel):
    """One server-side plan sort level, in priority order."""

    column: str = Field(min_length=1, max_length=32)
    order: Literal["asc", "desc"]


class RenovationPlanQuery(BaseModel):
    """Column constraints and server sort levels travel in JSON, never in the URL."""

    filters: list[RenovationColumnFilter] = Field(default_factory=list, max_length=256)
    sorting: list[RenovationSortColumn] = Field(default_factory=list, max_length=256)
    position_from: int | None = Field(default=None, ge=1)
    position_to: int | None = Field(default=None, ge=1)
    anchor_position: int | None = Field(default=None, ge=1)
    tz: str | None = None
    search_kind: Literal["text", "range", "datetime"] | None = None
    search_query: str | None = None
    search_min: float | None = None
    search_max: float | None = None


class RenovationRowAddRequest(BaseModel):
    """A new working-plan row (the asset is the only required field)."""

    assetId: UUID


class RenovationRowCreateRequest(BaseModel):
    """Append a plan row, optionally pre-assigning an asset."""

    assetId: UUID | None = None


class RenovationRowAssignRequest(BaseModel):
    """Point an asset-less draft row at an asset."""

    assetId: UUID


class RenovationFacetValueRead(BaseModel):
    """One distinct display value of a plan column (a filter option)."""

    # `None` is the «Пустые» bucket (NULL / empty string collapse to it).
    value: str | None = None
    count: int


class RenovationFacetsRead(BaseModel):
    """Distinct values of one plan column, sorted, for the header filter."""

    column: str
    values: list[RenovationFacetValueRead]
    presets: list[RenovationFacetValueRead] = Field(default_factory=list)
    totalWithoutSearch: int | None = None
    # Distinct values in total for the current filtered/search scope, not just this window.
    total: int
    rowCount: int = 0
    # Whether the column has any NULL/empty value (the «Пустые» option exists).
    hasNulls: bool
    nullCount: int = 0


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


class RenovationDiffRead(BaseModel):
    """Cell diff of an archived version vs the previous archived version."""

    planId: UUID
    comparedToPlanId: UUID | None = None
    rows: list[RenovationDiffRowRead] = []
    # Full previous-version rows for the rows deleted in the target, so the
    # client can render the deleted ghosts without a second full plan read.
    deletedRows: list[RenovationPlanRowRead] = []


class CellUpdate(BaseModel):
    """Single form cell edit (rowId — the row id, field — the db name)."""

    rowId: UUID
    field: str = Field(min_length=1, max_length=16)
    value: str | float | bool | None = None


class DictionaryCellUpdate(BaseModel):
    """Single dictionary cell edit (values may be lists, e.g. assetNames)."""

    rowId: UUID
    field: str = Field(min_length=1, max_length=64)
    value: str | float | bool | list[str] | None = None


class RenovationDictionaryBatchRequest(BaseModel):
    """Batch of renovation dictionary edits (one request, one undo action)."""

    section: Literal["assets", "users", "catalog"]
    catalog: str | None = None
    updates: list[DictionaryCellUpdate] = Field(min_length=1, max_length=10000)


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
    # True when the recomputed id differs from the stored one (the router
    # publishes it to the room so other clients update live).
    changed: bool = False


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
    # Computed: sum of «Стоимость итого, руб. без НДС» (col_031) over the
    # working plan's rows for this asset.
    totalCost: float = 0.0
    # Computed: the number of repair works — the working plan's rows for this
    # asset.
    repairCount: int = 0


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


class RenovationCatalogReplaceRequest(BaseModel):
    """Catalog value replacement: `null` deletes the value instead."""

    targetId: UUID | None = None


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


class RenovationPlanExportRequest(BaseModel):
    """
    Excel export of a plan version: all rows or only the visible ones.

    `rowIds` exports an exact set; when it is null, `asset` narrows the rows to
    one asset, `block` narrows the exported columns to one responsibility block
    and `filters` are the grid's active header filters (the same shape
    `POST /plan/rows/query` takes) and `sorting` carries ordered server-side sort
    levels — together the server-side equivalent of the grid's «видимая» view.
    """

    planId: UUID | None = None
    rowIds: list[UUID] | None = None
    asset: str | None = None
    block: RBlock | None = None
    filters: list[RenovationColumnFilter] | None = None
    sorting: list[RenovationSortColumn] | None = None


class RenovationPlanImportCellIssue(BaseModel):
    location: str
    value: str
    # Human-readable field label (without the header hierarchy). The dialog
    # shows it before the Excel column letter, so «Столбец H» becomes
    # «Наименование производства (столбец H)».
    column_label: str | None = None


class RenovationPlanImportResult(BaseModel):
    status: Literal["invalid_format", "has_errors", "ready"]
    # Human-readable names of required headings absent from a READABLE file:
    # the work code, the asset, the reference fields and the work dates. This is
    # a content error (`has_errors`), never `invalid_format` — that one is kept
    # for a workbook the analyzer cannot open at all.
    missing_required_columns: list[str] = Field(default_factory=list)
    duplicate_codes: list[str] = Field(default_factory=list)
    invalid_calculated_codes: list[str] = Field(default_factory=list)
    invalid_data_types: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    # Numeric cells that parse but violate the field's own rules: a fractional
    # value in an integer column, or a value outside its min/max/percentage
    # bounds. Shown separately so the operator sees the restriction, not a
    # generic «wrong type».
    invalid_values: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    # Excel cached «#REF!» values: a formula whose referenced cell/range is
    # gone. Its own category — in a text field it is not a type error, so it
    # would otherwise be stored as ordinary text.
    formula_reference_errors: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    # Required cells (the work code, a reference choice or a work date) left
    # empty in a row: an error of the file — the row is not written and the
    # load is refused until the cells are filled.
    missing_required_values: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    missing_dictionary_values: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    unknown_assets: list[RenovationPlanImportCellIssue] = Field(default_factory=list)
    # Sheet rows the load will not write because the ASSET cell is empty. That
    # is the only skip criterion and it is not an error: the rest of the file
    # still loads. Reported under «Пропущенные строки».
    skipped_rows: list[int] = Field(default_factory=list)
    # Every non-empty data row lacked the asset: the file has nothing to load,
    # and that is the one case where skipping alone fails the preflight.
    all_rows_skipped: bool = False
    assets: list[str] = Field(default_factory=list)
    new_rows: int = 0
    rows_to_update: int = 0
    # Human-readable labels of the columns the load will write, in the order the
    # template presents them. The analyser already resolves every header to a form
    # field; this exposes that set instead of making the UI parse the workbook.
    columns: list[str] = Field(default_factory=list)


class RenovationPlanImportApplyResult(BaseModel):
    """Outcome of a file load into the working plan."""

    new_rows: int = 0
    updated_rows: int = 0


class RenovationImportJobRead(BaseModel):
    """Durable Excel import status and bounded result."""

    id: str
    status: Literal["pending", "running", "done", "failed"]
    progress: int = 0
    total: int = 0
    file_name: str
    error: str | None = None
    new_rows: int = 0
    updated_rows: int = 0
    phase: str | None = None
    attempt: int = 0
    startedAt: datetime | None = None
    heartbeatAt: datetime | None = None


class RenovationExportJobRead(BaseModel):
    """A background plan-export job: status polling and the ready file name."""

    id: str
    status: str
    progress: int
    total: int
    fileName: str
    error: str | None = None
    phase: str | None = None
    attempt: int = 0
    startedAt: datetime | None = None
    heartbeatAt: datetime | None = None
