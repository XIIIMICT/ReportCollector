"use client";

import { Button, Dropdown, Label, type Selection } from "@heroui/react";
import {
    Editor,
    RevoGrid,
    type BeforeRangeSaveDataDetails,
    type BeforeSaveDataDetails,
    type ColumnRegular,
    type Editors,
    type HyperFunc,
    type RangeArea,
    type RowHeaders,
    type VNode,
} from "@revolist/react-datagrid";
import { MoreVert, Refresh } from "iconoir-react";
import {
    forwardRef,
    useCallback,
    useEffect,
    useImperativeHandle,
    useMemo,
    useRef,
    useState,
    type CSSProperties,
    type Ref,
} from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";
import type {
    RenovationCatalog,
    RenovationField,
    RenovationHeaderCell,
} from "@/shared/api/renovation";
import type { ReferenceOption } from "@/shared/grid/grid-config";
import { ReferenceEditor } from "@/shared/grid/grid-editors";
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";
import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/**
 * The «Ремонт» plan grid.
 *
 * The column headers are rendered by the page itself (an overlay above the
 * grid): the native revo-grid header only supports two levels, while the
 * Excel form has 11 (meta.headerRows). The grid hides its own header
 * (CSS .renovation-grid revogr-header) and reports horizontal scroll
 * (onViewportscroll) to slide the overlay.
 *
 * Row headers («№» = position + 1) stay native (revogr-row-headers is a
 * sibling of the hidden column-header element), the 52px corner is covered
 * by the overlay's opaque corner.
 */

export const RENOVATION_PLAN_ROWS_TABLE = "renovation_plan_rows";

const HEADER_LEVEL_HEIGHT = 24;
const HEADER_LEVEL_COUNT = 11;
const ROW_HEADER_WIDTH = 52;
const MIN_COLUMN_WIDTH = 60;
const MAX_COLUMN_WIDTH = 600;
const WIDTH_STORAGE_PREFIX = "renovation:colWidth:";
const DEFAULT_COLUMN_WIDTH: Record<string, number> = {
    number: 110,
    date: 130,
    boolean: 90,
    reference: 160,
    text: 200,
};

/** The synthetic first column showing the row's asset; an asset picker in the placeholder row. */
const ASSET_COLUMN_PROP = "__assetName";
const ASSET_COLUMN_WIDTH = 220;

/** Service columns revealed by the corner menu («Показать служебные столбцы»). */
const SERVICE_COLUMNS: ReadonlyArray<{ dbName: string; label: string; width: number }> = [
    { dbName: "id", label: "Идентификатор", width: 120 },
    { dbName: "position", label: "Позиция", width: 90 },
    { dbName: "revision", label: "Ревизия", width: 90 },
    { dbName: "updatedAt", label: "Обновлено", width: 160 },
    { dbName: "updatedBy", label: "Кем обновлено", width: 200 },
];

/** Row-model fields that are not plan values (the overlay never renders them). */
const SERVICE_DB_NAMES = new Set(SERVICE_COLUMNS.map((column) => column.dbName));

export type RenovationGridRow = {
    /** Canonical row id (UUID). */
    __id: string;
    __position: number;
    __assetId: string;
    __assetName: string;
    __renovationId: string | null;
    /** Diff state: "deleted" rows are ghosts from the previous archived plan. */
    __rowState: "unchanged" | "new" | "changed" | "deleted";
    __changedFields: string[];
    __missingComponents: string[];
    /** Trailing add-row row: only its asset cell is editable. */
    isPlaceholder?: boolean;
} & Record<string, unknown>;

export type RenovationGridProps = {
    fields: RenovationField[];
    headerCells: RenovationHeaderCell[];
    catalogs: RenovationCatalog[];
    assets: ReferenceOption[];
    rows: RenovationGridRow[];
    /** Asset-name filter (client-side); null — show all rows. */
    assetFilter: Set<string> | null;
    /** Only columns of this responsibility block (null — all). Always-visible columns stay. */
    blockFilter: string | null;
    baselineBoundary: number | null;
    readOnly: boolean;
    editableFieldKeys: Set<string>;
    /** Rows of these assets are editable; null — every row (admin/approver). */
    userAssetIds: Set<string> | null;
    remoteLocks: ReadonlyMap<string, { fullname: string; email: string }>;
    remotePresence: ReadonlyMap<string, { fullname: string; email: string }>;
    remoteCellInfo: ReadonlyMap<string, { name: string; label: string | null }>;
    onAcquireLease: (keys: string[]) => void;
    onReleaseLease: (keys: string[]) => void;
    onLockedCellEdit: (key: string, owner: string) => void;
    onCommit: (updates: CellUpdate[]) => Promise<boolean>;
    /** The user picked an asset in the placeholder row — add a plan row. */
    onPlaceholderCommit: (assetName: string) => Promise<boolean>;
    onCommitFailed: () => void;
    onValidationError: (message: string) => void;
    onPasteSkipped: (description: string) => void;
    onSelectionChange: (
        selection: {
            label: string | null;
            cellKeys: string[];
            /** The row the selection started in (for row-level actions like delete). */
            rowId: string | null;
        } | null,
    ) => void;
    onClipboardStateChange: (state: { canPaste: boolean }) => void;
    onViewStatsChange: (stats: GridViewStats) => void;
    /** Clears the workspace asset/block filters (corner menu). */
    onResetFilters: () => void;
};

export type RenovationGridHandle = {
    copySelection: () => void;
    clearSelection: () => void;
    pasteIntoSelection: () => void;
};

export function canonicalRenovationKey(rowId: string, field: string): string {
    return `${RENOVATION_PLAN_ROWS_TABLE}/${rowId}/${field}`;
}

function isGridTarget(event: Event, grid: HTMLRevoGridElement): boolean {
    const target = event.target as Node | null;
    if (!target) return false;
    return target === grid || grid.contains(target) || (grid.shadowRoot?.contains(target) ?? false);
}

function clampWidth(width: number): number {
    return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, width));
}

function numberTemplate(decimals: number | null | undefined) {
    const digits = decimals ?? 0;
    const formatter = new Intl.NumberFormat("ru-RU", {
        minimumFractionDigits: digits,
        maximumFractionDigits: Math.max(digits, 6),
    });
    return (h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode => {
        if (value === null || value === undefined || value === "") return h("span", {});
        const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
        const label = Number.isFinite(parsed) ? formatter.format(parsed) : String(value);
        return h("span", { class: "report-cell__number" }, label);
    };
}

function normalizeDate(text: string): string | null {
    const dotted = text.split(".");
    if (dotted.length === 3) {
        const day = Number(dotted[0]);
        const month = Number(dotted[1]);
        const year = Number(dotted[2]);
        if (
            Number.isInteger(day) &&
            Number.isInteger(month) &&
            Number.isInteger(year) &&
            day >= 1 &&
            day <= 31 &&
            month >= 1 &&
            month <= 12 &&
            year >= 1900 &&
            year <= 2100
        ) {
            const pad = (item: number): string => String(item).padStart(2, "0");
            return `${year}-${pad(month)}-${pad(day)}`;
        }
        return null;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
    return null;
}

type NormalizeResult = { ok: true; value: unknown } | { ok: false; message: string };

function RenovationGridInner(
    {
        fields,
        headerCells,
        catalogs,
        assets,
        rows,
        assetFilter,
        blockFilter,
        baselineBoundary,
        readOnly,
        editableFieldKeys,
        userAssetIds,
        remoteLocks,
        remotePresence,
        remoteCellInfo,
        onAcquireLease,
        onReleaseLease,
        onLockedCellEdit,
        onCommit,
        onPlaceholderCommit,
        onCommitFailed,
        onValidationError,
        onPasteSkipped,
        onSelectionChange,
        onClipboardStateChange,
        onViewStatsChange,
        onResetFilters,
    }: RenovationGridProps,
    forwardedRef: Ref<RenovationGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const shellRef = useRef<HTMLDivElement>(null);
    const verticalScrollRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const [scrollLeft, setScrollLeft] = useState(0);
    const [selectionRange, setSelectionRange] = useState<RangeArea | null>(null);
    const selectionRangeRef = useRef<RangeArea | null>(null);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);
    const activeLeaseRef = useRef<string | null>(null);
    const rowValuesRef = useRef<Map<string, Readonly<Record<string, unknown>>>>(new Map());
    const [headerPinned, setHeaderPinned] = useState(true);
    const [showServiceColumns, setShowServiceColumns] = useState(false);
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    const [hasHorizontalOverflow, setHasHorizontalOverflow] = useState(false);
    const [widths, setWidths] = useState<Map<string, number>>(() => {
        const result = new Map<string, number>();
        for (const field of fields) {
            const saved = window.localStorage.getItem(WIDTH_STORAGE_PREFIX + field.dbName);
            if (saved) {
                const parsed = Number(saved);
                if (Number.isFinite(parsed)) result.set(field.dbName, clampWidth(parsed));
            }
        }
        return result;
    });
    // Live overlay geometry while a resize drag is in progress; the grid
    // columns themselves only rebuild on drop.
    const [overlayWidths, setOverlayWidths] = useState<Map<string, number>>(new Map());
    const [resizingField, setResizingField] = useState<string | null>(null);
    const dragRef = useRef<{ dbName: string; startX: number; startWidth: number } | null>(null);

    const catalogLabelsByField = useMemo(() => {
        const optionsByCatalog = new Map<string, string[]>();
        for (const catalog of catalogs) {
            optionsByCatalog.set(catalog.id, [
                "",
                ...catalog.options.map((option) => option.label.trim()),
            ]);
        }
        const byField = new Map<string, string[]>();
        for (const field of fields) {
            if (field.cellType !== "reference" || !field.catalog) continue;
            byField.set(field.dbName, optionsByCatalog.get(field.catalog) ?? [""]);
        }
        return byField;
    }, [catalogs, fields]);

    const visibleFields = useMemo(() => {
        const ALWAYS_VISIBLE = new Set(["col_002", "col_003"]);
        return [...fields]
            .filter(
                (field) =>
                    blockFilter === null ||
                    ALWAYS_VISIBLE.has(field.dbName) ||
                    field.responsibilityBlock === blockFilter,
            )
            .sort((a, b) => a.position - b.position);
    }, [blockFilter, fields]);

    const fieldByDbName = useMemo(() => {
        const result = new Map<string, RenovationField>();
        for (const field of visibleFields) {
            result.set(field.dbName, field);
        }
        return result;
    }, [visibleFields]);

    const visibleIndexByPosition = useMemo(() => {
        const result = new Map<number, number>();
        for (const [index, field] of visibleFields.entries()) {
            result.set(field.position, index);
        }
        return result;
    }, [visibleFields]);

    // Service columns (corner menu toggle) are appended after the plan fields;
    // they have no Excel header cells, so they get synthetic overlay cells.
    const displayFields = useMemo<RenovationField[]>(() => {
        if (!showServiceColumns) return visibleFields;
        return [
            ...visibleFields,
            ...SERVICE_COLUMNS.map((column) => ({
                dbName: column.dbName,
                label: column.label,
                cellType: "text",
                catalog: null,
                isCalculated: false,
                isReadOnly: true,
                decimals: null,
                position:
                    10_000 + SERVICE_COLUMNS.findIndex((item) => item.dbName === column.dbName),
                ordinal:
                    10_000 + SERVICE_COLUMNS.findIndex((item) => item.dbName === column.dbName),
                responsibilityBlock: null,
            })),
        ];
    }, [showServiceColumns, visibleFields]);

    const serviceWidthOf = useCallback((dbName: string): number => {
        return SERVICE_COLUMNS.find((column) => column.dbName === dbName)?.width ?? 160;
    }, []);

    const widthOf = useCallback(
        (field: RenovationField): number => {
            const override = overlayWidths.get(field.dbName);
            if (override !== undefined) return override;
            return widths.get(field.dbName) ?? DEFAULT_COLUMN_WIDTH[field.cellType] ?? 200;
        },
        [overlayWidths, widths],
    );

    const fieldWidth = useCallback(
        (field: RenovationField): number => {
            if (SERVICE_DB_NAMES.has(field.dbName)) return serviceWidthOf(field.dbName);
            return widthOf(field);
        },
        [serviceWidthOf, widthOf],
    );

    // Left edge of every visible field (grid-relative x, the 52px row header
    // and the synthetic asset column included) and the total content width —
    // live during a resize drag.
    const geometry = useMemo(() => {
        const offsets = new Map<string, number>();
        let acc = ROW_HEADER_WIDTH + ASSET_COLUMN_WIDTH;
        for (const field of displayFields) {
            offsets.set(field.dbName, acc);
            acc += fieldWidth(field);
        }
        return { offsets, totalWidth: acc };
    }, [displayFields, fieldWidth]);

    // The header is a static grid of cells (text, colSpan/rowSpan from the
    // backend). A cell's startPosition is 0-based (0 = the first field, whose
    // position is 1), so the lookup and the span end must add 1 to land on the
    // 1-based position keys of visibleIndexByPosition. Cells whose start field
    // is hidden by the block filter are dropped; spans over hidden fields
    // render the visible part only.
    const overlayCells = useMemo(() => {
        const cells: Array<{
            id: string;
            text: string;
            left: number;
            top: number;
            width: number;
            height: number;
        }> = [];
        for (const cell of headerCells) {
            const startIndex = visibleIndexByPosition.get(cell.startPosition + 1);
            if (startIndex === undefined) continue;
            const endPosition = cell.startPosition + cell.colSpan + 1;
            let width = 0;
            for (
                let i = startIndex;
                i < visibleFields.length && visibleFields[i].position < endPosition;
                i += 1
            ) {
                width += widthOf(visibleFields[i]);
            }
            if (width === 0) continue;
            cells.push({
                id: cell.id,
                text: cell.text,
                left: geometry.offsets.get(visibleFields[startIndex].dbName) ?? ROW_HEADER_WIDTH,
                top: cell.level * HEADER_LEVEL_HEIGHT,
                width,
                height: cell.rowSpan * HEADER_LEVEL_HEIGHT,
            });
        }
        cells.sort((a, b) => a.top - b.top || a.left - b.left);
        cells.push({
            id: `asset:${ASSET_COLUMN_PROP}`,
            text: "Предприятие",
            left: ROW_HEADER_WIDTH,
            top: 0,
            width: ASSET_COLUMN_WIDTH,
            height: HEADER_LEVEL_COUNT * HEADER_LEVEL_HEIGHT,
        });
        if (showServiceColumns) {
            for (const column of SERVICE_COLUMNS) {
                cells.push({
                    id: `service:${column.dbName}`,
                    text: column.label,
                    left: geometry.offsets.get(column.dbName) ?? ROW_HEADER_WIDTH,
                    top: 0,
                    width: serviceWidthOf(column.dbName),
                    height: HEADER_LEVEL_COUNT * HEADER_LEVEL_HEIGHT,
                });
            }
        }
        return cells;
    }, [
        geometry.offsets,
        headerCells,
        serviceWidthOf,
        showServiceColumns,
        visibleFields,
        visibleIndexByPosition,
        widthOf,
    ]);

    const editableCell = useCallback(
        (field: RenovationField, row: RenovationGridRow): boolean => {
            if (readOnly) return false;
            if (row.isPlaceholder) return false;
            if (field.isCalculated || field.isReadOnly) return false;
            if (!editableFieldKeys.has(field.dbName)) return false;
            if (row.__rowState === "deleted") return false;
            if (userAssetIds && !userAssetIds.has(row.__assetId)) return false;
            return !remoteLocks.has(canonicalRenovationKey(row.__id, field.dbName));
        },
        [editableFieldKeys, readOnly, remoteLocks, userAssetIds],
    );

    const editors = useMemo<Editors>(() => ({ reference: Editor(ReferenceEditor) }), []);

    const columns = useMemo<ColumnRegular[]>(() => {
        const commitBoolean = (rowId: string, field: string, value: boolean) => {
            void onCommit([{ rowId, field, value }]).then((ok) => {
                if (!ok) onCommitFailed();
            });
        };
        const assetColumn: ColumnRegular = {
            prop: ASSET_COLUMN_PROP,
            name: "Предприятие",
            size: ASSET_COLUMN_WIDTH,
            minSize: MIN_COLUMN_WIDTH,
            maxSize: MAX_COLUMN_WIDTH,
            editor: "reference",
            source: assets.map((asset) => asset.name),
            readonly: ({ model }) => {
                const row = model as RenovationGridRow;
                return readOnly || !row.isPlaceholder;
            },
            cellProperties: ({ model }) => {
                const row = model as RenovationGridRow;
                return {
                    className: {
                        "report-cell": true,
                        "report-cell--readonly": !row.isPlaceholder,
                        "report-cell--placeholder": Boolean(row.isPlaceholder),
                        "report-cell--empty": Boolean(row.isPlaceholder && !row.__assetName),
                    },
                };
            },
        };
        return [
            assetColumn,
            ...displayFields.map((field, index) => {
                const column: ColumnRegular = {
                    prop: field.dbName,
                    name: field.label,
                    size: fieldWidth(field),
                    minSize: MIN_COLUMN_WIDTH,
                    maxSize: MAX_COLUMN_WIDTH,
                    readonly: ({ model }) =>
                        SERVICE_DB_NAMES.has(field.dbName)
                            ? true
                            : !editableCell(field, model as RenovationGridRow),
                    cellProperties: ({ model }) => {
                        const row = model as RenovationGridRow;
                        const canonicalKey = canonicalRenovationKey(row.__id, field.dbName);
                        const remoteUser = remoteCellInfo.get(canonicalKey);
                        const changed =
                            row.__rowState === "changed" &&
                            row.__changedFields.includes(field.dbName);
                        const value = row[field.dbName];
                        const empty =
                            value === null ||
                            value === undefined ||
                            value === "" ||
                            (Array.isArray(value) && value.length === 0);
                        return {
                            className: {
                                "report-cell": true,
                                "report-cell--number": field.cellType === "number",
                                "report-cell--readonly": SERVICE_DB_NAMES.has(field.dbName)
                                    ? true
                                    : !editableCell(field, row),
                                "report-cell--remote-locked": Boolean(remoteUser),
                                "report-cell--last-column": index === displayFields.length - 1,
                                "report-cell--deleted": row.__rowState === "deleted",
                                "report-cell--new": row.__rowState === "new" && !field.isCalculated,
                                "report-cell--changed": changed && !field.isCalculated,
                                "report-cell--baseline-boundary":
                                    baselineBoundary !== null &&
                                    row.__position === baselineBoundary,
                                "report-cell--selected": selectedRowIdSet.has(row.__id),
                                "report-cell--empty":
                                    row.__rowState !== "deleted" &&
                                    row.__rowState !== "new" &&
                                    !changed &&
                                    !field.isCalculated &&
                                    empty,
                            },
                            "data-remote-lock": remoteUser
                                ? remoteUser.label
                                    ? `${remoteUser.name} — ${remoteUser.label}`
                                    : remoteUser.name
                                : undefined,
                        };
                    },
                };
                if (field.cellType === "number") {
                    column.cellTemplate = numberTemplate(field.decimals);
                }
                if (field.cellType === "boolean") {
                    column.cellTemplate = (h, props) => {
                        const row = props.model as RenovationGridRow;
                        const checked = props.value === true;
                        const disabled = !editableCell(field, row);
                        return h(
                            "button",
                            {
                                type: "button",
                                class: {
                                    "report-cell__boolean": true,
                                    "report-cell__boolean--checked": checked,
                                    "report-cell__boolean--disabled": disabled,
                                },
                                role: "checkbox",
                                "aria-checked": checked,
                                "aria-label": field.label,
                                disabled,
                                onPointerDown: (event: PointerEvent) => event.stopPropagation(),
                                onClick: (event: MouseEvent) => {
                                    event.stopPropagation();
                                    if (disabled) return;
                                    commitBoolean(row.__id, field.dbName, !checked);
                                },
                            },
                            checked ? "✓" : "",
                        );
                    };
                }
                if (field.cellType === "reference") {
                    column.editor = "reference";
                    column.source = catalogLabelsByField.get(field.dbName) ?? [""];
                }
                return column;
            }),
        ];
    }, [
        assets,
        baselineBoundary,
        catalogLabelsByField,
        displayFields,
        editableCell,
        fieldWidth,
        onCommit,
        onCommitFailed,
        readOnly,
        remoteCellInfo,
        selectedRowIdSet,
        visibleFields,
    ]);

    // Client-side asset filter: the workspace passes every row, the grid
    // reports how much is shown.
    const displaySource = useMemo<RenovationGridRow[]>(() => {
        if (!assetFilter) return rows;
        return rows.filter((row) => assetFilter.has(row.__assetName));
    }, [assetFilter, rows]);

    const clearGridSelection = useCallback(() => {
        const grid = gridRef.current;
        setSelectionRange(null);
        setSelectedRowIds([]);
        rowSelectionAnchorRef.current = null;
        if (!grid) return;
        // Landmine: without clearing the pending focus restore, the next
        // render resurrects the old single-cell range.
        (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore =
            undefined;
        void grid.clearFocus().then(() => grid.refresh());
    }, []);

    // Row-number click: select the whole row (shift — range, Ctrl/Cmd —
    // toggle). Mirrors the raw-materials grid; the cell range is cleared
    // BEFORE the row state updates, or the columns rebuild (selectedRowIds
    // is a dependency) resurrects the old focus as a single-cell range.
    const handleRowNumberClick = useCallback(
        (event: MouseEvent, rowIndex: number, id: string) => {
            event.preventDefault();
            event.stopPropagation();
            const row = displaySource[rowIndex];
            if (row?.isPlaceholder) return;
            const modifiers = event.ctrlKey || event.metaKey;
            if (event.shiftKey && !modifiers) {
                const anchor = rowSelectionAnchorRef.current ?? rowIndex;
                const start = Math.min(anchor, rowIndex);
                const end = Math.max(anchor, rowIndex);
                clearGridSelection();
                setSelectedRowIds(
                    displaySource
                        .slice(start, end + 1)
                        .map((row) => row.__id)
                        .filter(Boolean),
                );
                return;
            }
            if (modifiers) {
                const next = new Set(selectedRowIdSet);
                if (next.has(id)) {
                    next.delete(id);
                    rowSelectionAnchorRef.current = null;
                } else {
                    next.add(id);
                    rowSelectionAnchorRef.current = rowIndex;
                }
                clearGridSelection();
                setSelectedRowIds([...next]);
                return;
            }
            rowSelectionAnchorRef.current = rowIndex;
            clearGridSelection();
            setSelectedRowIds([id]);
        },
        [clearGridSelection, displaySource, selectedRowIdSet],
    );

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: ROW_HEADER_WIDTH,
            columnProperties: () => ({
                className: {
                    "report-row-header": true,
                    "report-row-header--heading": true,
                },
            }),
            cellProperties: ({ model }) => {
                const row = model as RenovationGridRow;
                return {
                    className: {
                        "report-row-header": true,
                        "report-row-header--deleted": row.__rowState === "deleted",
                        "report-row-header--boundary":
                            baselineBoundary !== null && row.__position === baselineBoundary,
                        "report-row-header--selected": selectedRowIds.includes(row.__id),
                    },
                };
            },
            cellTemplate: (h: HyperFunc<VNode>, { rowIndex, model }) => {
                const row = model as RenovationGridRow;
                const selected = selectedRowIds.includes(row.__id);
                return h(
                    "button",
                    {
                        type: "button",
                        class: {
                            "report-row-header__button": true,
                            "report-row-header__button--selected": selected,
                        },
                        title: row.isPlaceholder
                            ? "Добавить строку"
                            : selected
                              ? `Снять выделение строки ${row.__position + 1}`
                              : `Выделить строку ${row.__position + 1}`,
                        onMouseDown: (event: MouseEvent) => {
                            event.stopPropagation();
                        },
                        onClick: (event: MouseEvent) => {
                            event.stopPropagation();
                            handleRowNumberClick(event, rowIndex, row.__id);
                        },
                    },
                    row.isPlaceholder ? "+" : String(row.__position + 1),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [baselineBoundary, handleRowNumberClick, selectedRowIds],
    );

    useEffect(() => {
        onViewStatsChange({
            filteredRows: displaySource.length,
            totalRows: rows.length,
            totalCols: displayFields.length,
            filtersApplied: assetFilter !== null,
            immutableCols: displayFields.filter((field) => field.isReadOnly && !field.isCalculated)
                .length,
            calculatedCols: displayFields.filter((field) => field.isCalculated).length,
            editableCols: displayFields.filter(
                (field) =>
                    !field.isCalculated && !field.isReadOnly && editableFieldKeys.has(field.dbName),
            ).length,
        });
    }, [
        assetFilter,
        displayFields,
        displaySource.length,
        editableFieldKeys,
        onViewStatsChange,
        rows.length,
    ]);

    useEffect(() => {
        rowValuesRef.current = new Map(rows.map((row) => [row.__id, { ...row }]));
    }, [rows]);

    function normalizeValue(value: unknown, field: RenovationField): NormalizeResult {
        if (field.cellType === "number") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const text = String(value)
                .trim()
                .replace("\u00a0", " ")
                .replace(/\s/g, "")
                .replace(",", ".");
            const parsed = Number.parseFloat(text);
            if (!Number.isFinite(parsed)) {
                return { ok: false, message: `Введите число в ячейку «${field.label}».` };
            }
            return { ok: true, value: parsed };
        }
        if (field.cellType === "date") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const iso = normalizeDate(String(value).trim());
            if (!iso) {
                return {
                    ok: false,
                    message: `Введите дату в ячейку «${field.label}» в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.`,
                };
            }
            return { ok: true, value: iso };
        }
        if (field.cellType === "boolean") {
            const text = String(value).trim().toLocaleLowerCase("ru-RU");
            if (text === "да" || text === "true" || text === "1") return { ok: true, value: true };
            if (text === "нет" || text === "false" || text === "0" || text === "") {
                return { ok: true, value: text === "" ? null : false };
            }
            return { ok: false, message: `Выберите «Да» или «Нет» в ячейке «${field.label}».` };
        }
        if (field.cellType === "reference") {
            const requested = String(value ?? "").trim();
            if (!requested) return { ok: true, value: "" };
            const options = catalogLabelsByField.get(field.dbName) ?? [];
            const byLabel = new Map(
                options
                    .filter((option) => Boolean(option))
                    .map((option) => [option.toLocaleLowerCase("ru-RU"), option]),
            );
            const canonical = byLabel.get(requested.toLocaleLowerCase("ru-RU"));
            if (!canonical) {
                return {
                    ok: false,
                    message: `Значение «${requested}» отсутствует в справочнике поля «${field.label}».`,
                };
            }
            return { ok: true, value: canonical };
        }
        return {
            ok: true,
            value: value === null || value === undefined ? null : String(value).trim(),
        };
    }

    function cellAllowed(field: RenovationField, row: RenovationGridRow): string | null {
        // null — allowed; a string is the skip reason.
        if (readOnly) return "таблица открыта только для просмотра";
        if (row.isPlaceholder) return "добавление строки — выберите предприятие";
        if (field.isCalculated) return "расчётное поле";
        if (field.isReadOnly) return "поле доступно только для чтения";
        if (!editableFieldKeys.has(field.dbName)) return "поле недоступно для редактирования";
        if (row.__rowState === "deleted") return "строка удалена";
        if (userAssetIds && !userAssetIds.has(row.__assetId))
            return "нет доступа к строке этого актива";
        return null;
    }

    function beforeEditStart(event: CustomEvent<BeforeSaveDataDetails>) {
        if (event.detail.prop === ASSET_COLUMN_PROP) {
            const row = event.detail.model as RenovationGridRow;
            if (readOnly || !row.isPlaceholder) {
                event.preventDefault();
                return;
            }
            // The placeholder row takes no lease.
            return;
        }
        const field = fieldByDbName.get(String(event.detail.prop));
        const row = event.detail.model as RenovationGridRow;
        const reason = field ? cellAllowed(field, row) : "поле недоступно для редактирования";
        if (reason) {
            event.preventDefault();
            return;
        }
        if (!field) {
            event.preventDefault();
            return;
        }
        const key = canonicalRenovationKey(row.__id, field.dbName);
        const owner = remoteLocks.get(key) ?? remotePresence.get(key);
        if (owner) {
            event.preventDefault();
            onLockedCellEdit(key, owner.fullname || owner.email || "другой пользователь");
            return;
        }
        activeLeaseRef.current = key;
        onAcquireLease([key]);
    }

    function beforeEdit(event: CustomEvent<BeforeSaveDataDetails>) {
        if (event.detail.prop === ASSET_COLUMN_PROP) {
            const row = event.detail.model as RenovationGridRow;
            if (readOnly || !row.isPlaceholder) {
                event.preventDefault();
                return;
            }
            event.detail.val = String(event.detail.val ?? "").trim();
            return;
        }
        const field = fieldByDbName.get(String(event.detail.prop));
        const row = event.detail.model as RenovationGridRow;
        if (!field) {
            event.preventDefault();
            return;
        }
        const reason = cellAllowed(field, row);
        if (reason) {
            event.preventDefault();
            return;
        }
        const key = canonicalRenovationKey(row.__id, field.dbName);
        const byLease = remoteLocks.get(key);
        const byPresence =
            remotePresence.get(key) && activeLeaseRef.current !== key
                ? remotePresence.get(key)
                : undefined;
        const owner = byLease ?? byPresence;
        if (owner) {
            event.preventDefault();
            onLockedCellEdit(key, owner.fullname || owner.email || "другой пользователь");
            return;
        }
        const normalized = normalizeValue(event.detail.val, field);
        if (!normalized.ok) {
            event.preventDefault();
            onValidationError(normalized.message);
            return;
        }
        event.detail.val = normalized.value;
    }

    function beforeRangeEdit(event: CustomEvent<BeforeRangeSaveDataDetails>) {
        const patches = event.detail.data as Record<string, RenovationGridRow>;
        const models = event.detail.models as Record<string, RenovationGridRow | undefined>;
        const skipped = new Map<string, string>();
        for (const [rowIndex, patch] of Object.entries(patches)) {
            const currentRow = models[Number(rowIndex)] ?? displaySource[Number(rowIndex)];
            for (const prop of Object.keys(patch)) {
                const field = fieldByDbName.get(String(prop));
                if (!field) {
                    delete patch[prop];
                    continue;
                }
                const reason = currentRow ? cellAllowed(field, currentRow) : "строка недоступна";
                if (reason) {
                    delete patch[prop];
                    skipped.set(field.label, reason);
                    continue;
                }
                const key = canonicalRenovationKey(currentRow.__id, field.dbName);
                if (remoteLocks.has(key) || remotePresence.has(key)) {
                    delete patch[prop];
                    skipped.set(field.label, "ячейка занята другим пользователем");
                    continue;
                }
                const normalized = normalizeValue(patch[prop], field);
                if (!normalized.ok) {
                    event.preventDefault();
                    onValidationError(normalized.message);
                    return;
                }
                patch[prop] = normalized.value;
            }
        }
        if (skipped.size > 0) {
            const reasons = new Map<string, string[]>();
            for (const [label, reason] of skipped) {
                const labels = reasons.get(reason) ?? [];
                labels.push(label);
                reasons.set(reason, labels);
            }
            const description = [...reasons.entries()]
                .map(
                    ([reason, labels]) =>
                        `${labels.map((label) => `«${label}»`).join(", ")}: ${reason}`,
                )
                .join("; ");
            onPasteSkipped(description);
        }
    }

    function extractEdits(detail: BeforeSaveDataDetails | Record<string, unknown>): {
        updates: CellUpdate[];
        placeholderPatch: Record<string, unknown>;
    } {
        const updates: CellUpdate[] = [];
        const placeholderPatch: Record<string, unknown> = {};
        if ("data" in detail) {
            const models = (detail as { models?: RenovationGridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                detail.data as Record<string, Record<string, unknown>>,
            )) {
                const previous = models[Number(rowIndex)] as RenovationGridRow | undefined;
                const row = (previous ?? patch) as RenovationGridRow;
                if (!row?.__id || row.__rowState === "deleted") continue;
                const known = rowValuesRef.current.get(row.__id);
                for (const [field, value] of Object.entries(patch)) {
                    if (row.isPlaceholder) {
                        if (field === ASSET_COLUMN_PROP)
                            placeholderPatch[field] = toCellValue(value);
                        continue;
                    }
                    if (!editableFieldKeys.has(field)) continue;
                    if (known && known[field] === value) continue;
                    updates.push({ rowId: row.__id, field, value: toCellValue(value) });
                }
            }
        } else {
            const field = String(detail.prop);
            const row = detail.model as RenovationGridRow;
            if (!row?.__id || row.__rowState === "deleted") {
                return { updates, placeholderPatch };
            }
            if (row.isPlaceholder) {
                if (field === ASSET_COLUMN_PROP) placeholderPatch[field] = toCellValue(detail.val);
            } else if (editableFieldKeys.has(field)) {
                updates.push({ rowId: row.__id, field, value: toCellValue(detail.val) });
            }
        }
        const deduplicated = new Map<string, CellUpdate>();
        for (const update of updates) {
            deduplicated.set(`${update.rowId}\u0000${update.field}`, update);
        }
        return { updates: [...deduplicated.values()], placeholderPatch };
    }

    function afterEdit(event: CustomEvent<BeforeSaveDataDetails | Record<string, unknown>>) {
        const { updates, placeholderPatch } = extractEdits(event.detail);
        const releaseKeys: string[] = [];
        if ("data" in event.detail) {
            const models = (event.detail as { models?: RenovationGridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                event.detail.data as Record<string, Record<string, unknown>>,
            )) {
                const row = (models[Number(rowIndex)] ?? patch) as RenovationGridRow;
                if (!row?.__id || row.__rowState === "deleted" || row.isPlaceholder) continue;
                for (const field of Object.keys(patch)) {
                    if (editableFieldKeys.has(field)) {
                        releaseKeys.push(canonicalRenovationKey(row.__id, field));
                    }
                }
            }
        } else {
            const row = event.detail.model as RenovationGridRow;
            const key =
                row && !row.isPlaceholder && row.__id
                    ? canonicalRenovationKey(row.__id, String(event.detail.prop))
                    : null;
            if (key) releaseKeys.push(key);
        }
        void (async () => {
            try {
                const assetName = placeholderPatch[ASSET_COLUMN_PROP];
                if (typeof assetName === "string" && assetName) {
                    const ok = await onPlaceholderCommit(assetName);
                    if (!ok) onCommitFailed();
                } else if (updates.length > 0) {
                    const ok = await onCommit(updates);
                    if (!ok) onCommitFailed();
                }
            } finally {
                activeLeaseRef.current = null;
                onReleaseLease(releaseKeys);
            }
        })();
    }

    const getVisibleRows = useCallback(async (): Promise<RenovationGridRow[]> => {
        const grid = gridRef.current;
        if (!grid) return [];
        return (await grid.getVisibleSource("rgRow")) as RenovationGridRow[];
    }, []);

    const applyGridRange = useCallback(
        async (start: { x: number; y: number }, end: { x: number; y: number }) => {
            const grid = gridRef.current;
            if (!grid) return;
            grid.setCellsFocus(start, end);
            await grid.refresh();
        },
        [],
    );

    const updateSelectionRange = useCallback((range: RangeArea | null) => {
        setSelectionRange(range);
    }, []);

    const buildCopyText = useCallback(async (): Promise<string | null> => {
        const range = selectionRangeRef.current;
        if (!range) return null;
        const visible = await getVisibleRows();
        const lines: string[] = [];
        for (let y = range.y; y <= Math.min(range.y1, visible.length - 1); y += 1) {
            const row = visible[y];
            if (!row) continue;
            const cells: string[] = [];
            for (let x = range.x; x <= range.x1; x += 1) {
                const column = columns[x] as ColumnRegular | undefined;
                if (!column || column.prop === undefined) continue;
                cells.push(serializeCellValue(row[String(column.prop)]));
            }
            lines.push(cells.join("\t"));
        }
        return lines.join("\n");
    }, [columns, getVisibleRows]);

    const copySelection = useCallback(() => {
        void (async () => {
            const text = await buildCopyText();
            if (text == null || text === "") return;
            try {
                await navigator.clipboard.writeText(text);
            } catch {
                try {
                    const textarea = document.createElement("textarea");
                    textarea.value = text;
                    textarea.style.position = "fixed";
                    textarea.style.opacity = "0";
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand("copy");
                    textarea.remove();
                } catch {
                    // Clipboard unavailable — nothing to do.
                }
            }
        })();
    }, [buildCopyText]);

    const pasteIntoSelection = useCallback(
        async (text?: string) => {
            const grid = gridRef.current;
            if (!grid) return;
            if (text == null) {
                try {
                    text = await navigator.clipboard.readText();
                } catch {
                    return;
                }
            }
            if (!text) return;
            // Excel leaves a trailing line break (and trailing tabs) in the
            // buffer — without trimming, an extra empty cell below overwrites
            // a neighbor nobody asked for.
            const split = text.split(/\r\n|\n|\r/).map((row) => row.split("\t"));
            while (
                split.length > 0 &&
                split[split.length - 1].every((cell) => cell.trim() === "")
            ) {
                split.pop();
            }
            if (split.length === 0) return;
            let width = Math.max(...split.map((cells) => cells.length));
            while (
                width > 0 &&
                split.every((cells) => width - 1 >= cells.length || cells[width - 1].trim() === "")
            ) {
                width -= 1;
            }
            const parsed = split.map((cells) => cells.slice(0, width));
            const range = selectionRangeRef.current;
            if (!range) return;

            const pasteHeight = parsed.length;
            const pasteWidth = Math.max(0, ...parsed.map((row) => row.length));
            const selectedHeight = range.y1 - range.y + 1;
            const selectedWidth = range.x1 - range.x + 1;
            const singleCell = selectedHeight === 1 && selectedWidth === 1;

            const visible = await getVisibleRows();
            let toY = range.y;
            let toY1 = range.y1;
            let toX = range.x;
            let toX1 = range.x1;
            if (singleCell) {
                toY1 = Math.min(visible.length - 1, range.y + pasteHeight - 1);
                toX1 = Math.min(columns.length - 1, range.x + pasteWidth - 1);
            } else if (pasteHeight > selectedHeight || pasteWidth > selectedWidth) {
                onPasteSkipped("Буфер шире или выше выделения — вставлена только его часть.");
            }

            const data: Record<string, Record<string, unknown>> = {};
            const models: RenovationGridRow[] = [];
            for (let y = toY; y <= toY1; y += 1) {
                models[y] = visible[y];
                const patch: Record<string, unknown> = {};
                for (let x = toX; x <= toX1; x += 1) {
                    const column = columns[x] as ColumnRegular | undefined;
                    if (!column || column.prop === undefined) continue;
                    const sourceCell = parsed[y - range.y]?.[x - range.x];
                    if (sourceCell === undefined) continue;
                    patch[String(column.prop)] = sourceCell;
                }
                if (Object.keys(patch).length > 0) data[String(y)] = patch;
            }
            const newRange: RangeArea = { y: toY, y1: toY1, x: toX, x1: toX1 };
            grid.dispatchEvent(
                new CustomEvent("rangeeditapply", {
                    detail: { data, models, type: "rgRow", oldRange: range, newRange },
                    bubbles: true,
                    composed: true,
                }),
            );
            void applyGridRange({ x: toX, y: toY }, { x: toX1, y: toY1 });
        },
        [applyGridRange, columns, getVisibleRows, onPasteSkipped],
    );

    useImperativeHandle(
        forwardedRef,
        () => ({
            copySelection,
            clearSelection: clearGridSelection,
            pasteIntoSelection: () => void pasteIntoSelection(),
        }),
        [clearGridSelection, copySelection, pasteIntoSelection],
    );

    // Window-level copy/paste (capture): works regardless of the focus inside
    // the grid's shadow DOM.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const handleCopy = (event: ClipboardEvent) => {
            if (!isGridTarget(event, grid)) return;
            const active = document.activeElement as HTMLElement | null;
            if (
                active &&
                (active.tagName === "INPUT" ||
                    active.tagName === "TEXTAREA" ||
                    active.isContentEditable)
            ) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            void copySelection();
        };
        const handlePaste = (event: ClipboardEvent) => {
            if (!isGridTarget(event, grid)) return;
            const active = document.activeElement as HTMLElement | null;
            if (
                active &&
                (active.tagName === "INPUT" ||
                    active.tagName === "TEXTAREA" ||
                    active.isContentEditable)
            ) {
                return;
            }
            const text = event.clipboardData?.getData("text/plain");
            if (text == null || text === "") return;
            event.preventDefault();
            event.stopPropagation();
            void pasteIntoSelection(text);
        };
        const handleSetRange = (event: Event) => {
            updateSelectionRange((event as CustomEvent<RangeArea & { type?: string }>).detail);
            setSelectedRowIds([]);
            rowSelectionAnchorRef.current = null;
        };
        const handleFocusCell = (event: Event) => {
            const detail = (
                event as CustomEvent<{
                    focus: { x: number; y: number };
                    end: { x: number; y: number };
                    range?: RangeArea;
                }>
            ).detail;
            if (!detail) return;
            updateSelectionRange(
                detail.range ?? {
                    x: detail.focus.x,
                    y: detail.focus.y,
                    x1: detail.end.x,
                    y1: detail.end.y,
                },
            );
            setSelectedRowIds([]);
            rowSelectionAnchorRef.current = null;
        };
        const handleCancelEdit = () => {
            if (!activeLeaseRef.current) return;
            onReleaseLease([activeLeaseRef.current]);
            activeLeaseRef.current = null;
        };
        window.addEventListener("copy", handleCopy, true);
        window.addEventListener("paste", handlePaste, true);
        grid.addEventListener("setrange", handleSetRange);
        grid.addEventListener("focuscell", handleFocusCell);
        grid.addEventListener("canceledit", handleCancelEdit);
        return () => {
            window.removeEventListener("copy", handleCopy, true);
            window.removeEventListener("paste", handlePaste, true);
            grid.removeEventListener("setrange", handleSetRange);
            grid.removeEventListener("focuscell", handleFocusCell);
            grid.removeEventListener("canceledit", handleCancelEdit);
        };
    }, [copySelection, onReleaseLease, pasteIntoSelection, updateSelectionRange]);

    // Keyboard shortcuts (event.code — works in any keyboard layout).
    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !document.fullscreenElement) {
                const active = document.activeElement as HTMLElement | null;
                if (active && (active.tagName === "INPUT" || active.isContentEditable)) return;
                clearGridSelection();
                return;
            }
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
            if (event.code !== "KeyC" && event.code !== "KeyV") return;
            const grid = gridRef.current;
            if (!grid || !isGridTarget(event, grid)) return;
            const active = document.activeElement as HTMLElement | null;
            if (active && (active.tagName === "INPUT" || active.isContentEditable)) return;
            event.preventDefault();
            if (event.code === "KeyC") {
                void copySelection();
            } else {
                void pasteIntoSelection();
            }
        };
        window.addEventListener("keydown", handleKeyDown, true);
        return () => window.removeEventListener("keydown", handleKeyDown, true);
    }, [clearGridSelection, copySelection, pasteIntoSelection]);

    useEffect(() => {
        selectionRangeRef.current = selectionRange;
        onClipboardStateChange({ canPaste: selectionRange !== null && !readOnly });
        if (selectedRowIds.length > 0) {
            // Row-number selection wins over the cell range (row clicks clear
            // the range): report the rows for row-level actions like delete.
            const positions = displaySource
                .filter((row) => selectedRowIdSet.has(row.__id))
                .map((row) => row.__position)
                .sort((a, b) => a - b);
            if (positions.length === 0) {
                onSelectionChange(null);
                return;
            }
            const first = positions[0]!;
            const last = positions[positions.length - 1]!;
            const label =
                first === last ? `строка ${first + 1}` : `строки ${first + 1}–${last + 1}`;
            onSelectionChange({
                label,
                cellKeys: [],
                rowId: displaySource.find((row) => row.__position === last)?.__id ?? null,
            });
            return;
        }
        if (!selectionRange) {
            onSelectionChange(null);
            return;
        }
        const firstColumn = columns[selectionRange.x] as ColumnRegular | undefined;
        const lastColumn = columns[selectionRange.x1] as ColumnRegular | undefined;
        const rangeRows: RenovationGridRow[] = [];
        for (let y = selectionRange.y; y <= selectionRange.y1 && y < displaySource.length; y += 1) {
            const row = displaySource[y];
            if (row && !row.isPlaceholder) rangeRows.push(row);
        }
        const firstRow = rangeRows[0];
        const lastRow = rangeRows[rangeRows.length - 1];
        const columnText =
            firstColumn && lastColumn
                ? firstColumn === lastColumn
                    ? String(firstColumn.name ?? "")
                    : `${String(firstColumn.name ?? "")} — ${String(lastColumn.name ?? "")}`
                : "";
        const rowText =
            firstRow && lastRow
                ? firstRow.__position === lastRow.__position
                    ? `строка ${firstRow.__position + 1}`
                    : `строки ${firstRow.__position + 1}–${lastRow.__position + 1}`
                : "";
        const label = [columnText, rowText].filter(Boolean).join(", ") || null;
        const cellKeys: string[] = [];
        for (let y = selectionRange.y; y <= selectionRange.y1 && y < displaySource.length; y += 1) {
            const row = displaySource[y];
            if (!row || row.isPlaceholder) continue;
            for (let x = selectionRange.x; x <= selectionRange.x1; x += 1) {
                const column = columns[x] as ColumnRegular | undefined;
                if (!column || column.prop === undefined) continue;
                cellKeys.push(canonicalRenovationKey(row.__id, String(column.prop)));
            }
        }
        onSelectionChange({
            label,
            cellKeys,
            rowId: firstRow?.__id ?? null,
        });
    }, [
        columns,
        displaySource,
        onClipboardStateChange,
        onSelectionChange,
        readOnly,
        selectedRowIdSet,
        selectedRowIds,
        selectionRange,
    ]);

    // Resize: pointer drag on the boundary between visible columns. The
    // overlay geometry updates live; the grid columns rebuild on drop.
    const startResize = useCallback(
        (event: React.PointerEvent, dbName: string) => {
            event.preventDefault();
            const startWidth = widths.get(dbName);
            if (startWidth === undefined) return;
            dragRef.current = { dbName, startX: event.clientX, startWidth };
            event.currentTarget.setPointerCapture(event.pointerId);
            setResizingField(dbName);
        },
        [widths],
    );
    const moveResize = useCallback((event: React.PointerEvent) => {
        const drag = dragRef.current;
        if (!drag) return;
        const next = clampWidth(drag.startWidth + (event.clientX - drag.startX));
        setOverlayWidths((current) => {
            if (current.get(drag.dbName) === next) return current;
            const result = new Map(current);
            result.set(drag.dbName, next);
            return result;
        });
    }, []);
    const endResize = useCallback(() => {
        const drag = dragRef.current;
        if (!drag) return;
        dragRef.current = null;
        const width = clampWidth(overlayWidths.get(drag.dbName) ?? drag.startWidth);
        setWidths((current) => {
            if (current.get(drag.dbName) === width) return current;
            const result = new Map(current);
            result.set(drag.dbName, width);
            return result;
        });
        setOverlayWidths(new Map());
        setResizingField(null);
        window.localStorage.setItem(WIDTH_STORAGE_PREFIX + drag.dbName, String(width));
    }, [overlayWidths]);

    const resetWidths = useCallback(() => {
        for (const field of fields) {
            window.localStorage.removeItem(WIDTH_STORAGE_PREFIX + field.dbName);
        }
        setWidths(new Map());
        setOverlayWidths(new Map());
    }, [fields]);

    const handles = useMemo(() => {
        const result: Array<{ key: string; dbName: string; left: number }> = [];
        for (let i = 0; i < displayFields.length - 1; i += 1) {
            const field = displayFields[i];
            if (SERVICE_DB_NAMES.has(field.dbName)) continue;
            result.push({
                key: field.dbName,
                dbName: field.dbName,
                left: (geometry.offsets.get(field.dbName) ?? 0) + fieldWidth(field) - 3,
            });
        }
        return result;
    }, [displayFields, fieldWidth, geometry.offsets]);

    const headerHeight = HEADER_LEVEL_COUNT * HEADER_LEVEL_HEIGHT;

    // Unpinned mode (corner menu «Закрепить заголовок» off) — the same
    // scrolling model as the «Сырьё» grids: the header scrolls away with the
    // content and an external horizontal scrollbar strip appears at the bottom.
    const contentHeight = headerHeight + displaySource.length * 36 + 18;

    const refreshHorizontalScrollbar = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const size = await grid.getContentSize();
        const verticalScroller = verticalScrollRef.current;
        const scrollbarGutter = verticalScroller
            ? verticalScroller.offsetWidth - verticalScroller.clientWidth
            : 0;
        const width = size.x + scrollbarGutter + ROW_HEADER_WIDTH;
        setHorizontalContentWidth(width);
        const shell = shellRef.current;
        setHasHorizontalOverflow(width > (shell?.clientWidth ?? 0));
    }, []);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            void refreshHorizontalScrollbar();
        });
        const observer = new ResizeObserver(() => {
            void refreshHorizontalScrollbar();
        });
        if (gridRef.current) observer.observe(gridRef.current);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [columns, headerPinned, refreshHorizontalScrollbar, displaySource]);

    // Wheel over the horizontal scrollbar strip scrolls it horizontally
    // (native Chrome behavior scrolls the vertical axis instead). The strip is
    // a real DOM element: revo-grid's virtual horizontal scrollbar in pinned
    // mode and the external one in unpinned mode. Native listener — React's
    // onWheel is passive and can't preventDefault.
    useEffect(() => {
        const shell = shellRef.current;
        if (!shell) return;
        const handleWheel = (event: WheelEvent) => {
            if (event.deltaY === 0 && event.deltaX === 0) return;
            const target = event.target as Node | null;
            if (!target) return;
            const internal = gridRef.current?.querySelector("revogr-scroll-virtual.horizontal");
            const external = horizontalScrollRef.current;
            const strip =
                (internal?.contains(target) ? internal : null) ??
                (external?.contains(target) ? external : null);
            if (!strip) return;
            if (strip.scrollWidth <= strip.clientWidth) return;
            event.preventDefault();
            strip.scrollLeft += event.deltaY + event.deltaX;
        };
        shell.addEventListener("wheel", handleWheel, { passive: false });
        return () => shell.removeEventListener("wheel", handleWheel);
    }, []);

    const headerOverlay = (
        <div className="renovation-grid__header" style={{ height: headerHeight }}>
            <div className="renovation-grid__corner" aria-hidden="true" />
            <div
                className="renovation-grid__header-body"
                style={{
                    width: geometry.totalWidth,
                    transform: `translateX(${-scrollLeft}px)`,
                }}
            >
                {overlayCells.map((cell) => (
                    <div
                        key={cell.id}
                        className="renovation-grid__header-cell"
                        style={{
                            left: cell.left,
                            top: cell.top,
                            width: cell.width,
                            height: cell.height,
                        }}
                    >
                        {cell.text}
                    </div>
                ))}
                {handles.map((handle) => (
                    <div
                        key={handle.key}
                        className={
                            resizingField === handle.dbName
                                ? "renovation-grid__resize-handle is-dragging"
                                : "renovation-grid__resize-handle"
                        }
                        style={{ left: handle.left }}
                        onPointerDown={(event) => startResize(event, handle.dbName)}
                        onPointerMove={moveResize}
                        onPointerUp={endResize}
                        onPointerCancel={endResize}
                    />
                ))}
            </div>
        </div>
    );

    return (
        <div
            ref={shellRef}
            className={[
                "revo-grid-shell renovation-grid-shell",
                headerPinned && "revo-grid-shell--pinned",
                !headerPinned && !hasHorizontalOverflow && "revo-grid-shell--no-hscroll",
            ]
                .filter(Boolean)
                .join(" ")}
            style={
                {
                    "--grid-content-height": `${contentHeight}px`,
                    "--grid-header-height": `${headerHeight}px`,
                } as CSSProperties
            }
        >
            {headerPinned ? headerOverlay : null}
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                {!headerPinned ? headerOverlay : null}
                <RevoGrid
                    ref={gridRef}
                    className="renovation-grid"
                    columns={columns}
                    source={displaySource}
                    theme="compact"
                    range
                    useClipboard={false}
                    rowHeaders={rowHeaders}
                    editors={editors}
                    hideAttribution
                    readonly={readOnly}
                    rowSize={36}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
                    }}
                    onContentsizechanged={() => void refreshHorizontalScrollbar()}
                    onViewportscroll={(event) => {
                        if (event.detail.dimension === "rgCol") {
                            setScrollLeft(event.detail.coordinate);
                            if (!headerPinned) {
                                const scrollbar = horizontalScrollRef.current;
                                if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                                    scrollbar.scrollLeft = event.detail.coordinate;
                                }
                            }
                        }
                    }}
                    onBeforeeditstart={beforeEditStart}
                    onBeforeedit={beforeEdit}
                    onAfteredit={afterEdit}
                    onBeforerangeedit={beforeRangeEdit}
                    onBeforecellfocus={(event) =>
                        updateSelectionRange({
                            x: event.detail.colIndex,
                            y: event.detail.rowIndex,
                            x1: event.detail.colIndex,
                            y1: event.detail.rowIndex,
                        })
                    }
                    onBeforerange={(event) => updateSelectionRange(event.detail.newRange)}
                />
            </div>
            {/* The corner menu is rendered in the regular tree over the
                grid's top-left corner. Mounting it into the grid's shadow DOM
                breaks react-aria dropdowns, so it lives at the shell level —
                above the header overlay, which keeps the button in place while
                the header scrolls away in unpinned mode. */}
            <div className="renovation-grid__menu-corner" style={{ height: headerHeight }}>
                <RenovationGridOptionsMenu
                    headerPinned={headerPinned}
                    showServiceColumns={showServiceColumns}
                    onHeaderPinnedChange={setHeaderPinned}
                    onShowServiceColumnsChange={setShowServiceColumns}
                    onResetWidths={resetWidths}
                    onResetFilters={onResetFilters}
                />
            </div>
            {!headerPinned ? (
                <div
                    ref={horizontalScrollRef}
                    className="revo-grid-external-scrollbar"
                    style={{ display: hasHorizontalOverflow ? undefined : "none" }}
                    aria-label="Горизонтальная прокрутка таблицы"
                    onScroll={(event) => {
                        void gridRef.current?.scrollToCoordinate({
                            x: event.currentTarget.scrollLeft,
                        });
                    }}
                >
                    <div style={{ width: horizontalContentWidth }} />
                </div>
            ) : null}
        </div>
    );
}

export const RenovationGrid = forwardRef(RenovationGridInner);

type RenovationGridOptionsMenuProps = {
    headerPinned: boolean;
    showServiceColumns: boolean;
    onHeaderPinnedChange: (value: boolean) => void;
    onShowServiceColumnsChange: (value: boolean) => void;
    onResetWidths: () => void;
    onResetFilters: () => void;
};

/**
 * The «Настройки таблицы» menu in the top-left corner — the same
 * HoverDropdown + MoreVert widget and items as in the «Сырьё» grids, plus the
 * «Сбросить ширину столбцов» action specific to the renovation grid (the
 * column widths are persisted in localStorage). «Сбросить все фильтры и
 * сортировки» resets the workspace asset/block filters (the grid has no
 * sorting).
 */
function RenovationGridOptionsMenu({
    headerPinned,
    showServiceColumns,
    onHeaderPinnedChange,
    onShowServiceColumnsChange,
    onResetWidths,
    onResetFilters,
}: RenovationGridOptionsMenuProps) {
    const selectedKeys = new Set<string>();
    if (headerPinned) selectedKeys.add("pin-header");
    if (showServiceColumns) selectedKeys.add("service-columns");
    const toggleKeys = ["pin-header", "service-columns", "reset-widths"];
    const applySelection = (selection: Selection) => {
        const keys = selection === "all" ? new Set(toggleKeys) : new Set(selection);
        if (keys.has("reset-widths")) {
            // Momentary action, not a persisted toggle: fire it and drop the
            // key so it never sticks in the controlled selection.
            onResetWidths();
            keys.delete("reset-widths");
        }
        if (keys.has("reset-view")) {
            onResetFilters();
            keys.delete("reset-view");
        }
        onHeaderPinnedChange(keys.has("pin-header"));
        onShowServiceColumnsChange(keys.has("service-columns"));
    };
    return (
        <div className="renovation-grid__options-menu">
            <HoverDropdown
                className="renovation-grid-options-popover min-w-[260px]"
                trigger={({ ref, onMouseEnter }) => (
                    <Button
                        ref={ref}
                        onMouseEnter={onMouseEnter}
                        isIconOnly
                        aria-label="Настройки таблицы"
                        variant="ghost"
                    >
                        <MoreVert aria-hidden="true" />
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        selectedKeys={selectedKeys}
                        selectionMode="multiple"
                        onSelectionChange={applySelection}
                    >
                        <Dropdown.Item id="pin-header" textValue="Закрепить заголовок">
                            <Dropdown.ItemIndicator />
                            <Label>Закрепить заголовок</Label>
                        </Dropdown.Item>
                        <Dropdown.Item id="service-columns" textValue="Показать служебные столбцы">
                            <Dropdown.ItemIndicator />
                            <Label>Показать служебные столбцы</Label>
                        </Dropdown.Item>
                        <Dropdown.Item id="reset-widths" textValue="Сбросить ширину столбцов">
                            <Refresh aria-hidden="true" />
                            <Label>Сбросить ширину столбцов</Label>
                        </Dropdown.Item>
                        <Dropdown.Item
                            id="reset-view"
                            textValue="Сбросить все фильтры и сортировки"
                        >
                            <Refresh aria-hidden="true" />
                            <Label>Сбросить все фильтры и сортировки</Label>
                        </Dropdown.Item>
                    </Dropdown.Menu>
                }
            />
        </div>
    );
}

function toCellValue(value: unknown): CellUpdate["value"] {
    if (Array.isArray(value)) {
        return value
            .filter((item): item is string => typeof item === "string")
            .map((item) => item.trim())
            .filter(Boolean);
    }
    if (value === null || typeof value === "number" || typeof value === "boolean") {
        return value;
    }
    if (typeof value === "string") return value.trim();
    return value === undefined ? null : String(value);
}

function serializeCellValue(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(serializeCellValue).join(", ");
    return String(value);
}
