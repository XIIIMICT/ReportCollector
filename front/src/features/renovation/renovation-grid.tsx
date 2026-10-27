"use client";

import { Tooltip } from "@heroui/react";
import {
    RevoGrid,
    getSourceItem,
    setSourceByPhysicalIndex,
    type BeforeRangeSaveDataDetails,
    type BeforeSaveDataDetails,
    type ColumnRegular,
    type Editors,
    type HyperFunc,
    type RangeArea,
    type RowHeaders,
    type VNode,
} from "@revolist/react-datagrid";
import { InfinityScrollPlugin } from "@revolist/rv-pro-trial";
import {
    forwardRef,
    Fragment,
    useCallback,
    useEffect,
    useImperativeHandle,
    useMemo,
    useRef,
    useState,
    type CSSProperties,
    type Ref,
} from "react";

import "@revolist/rv-pro-trial/styles.css";
import { createChunkLoader } from "@/features/renovation/chunk-loader";
import {
    ASSET_COLUMN_PROP,
    ASSET_COLUMN_WIDTH,
    ASSET_FIELD_DB_NAME,
    BLOCK_HEADER_LABELS,
    DEFAULT_COLUMN_WIDTH,
    HEADER_LEVEL_COUNT,
    HEADER_LEVEL_HEIGHT,
    HEADER_LEVEL_HEIGHT_STORAGE_KEY,
    INFINITY_BUFFER_ROWS,
    INFINITY_CHUNK_ROWS,
    INFINITY_PRELOAD_THRESHOLD,
    MAX_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    MISSING_COMPONENT,
    ROW_HEADER_WIDTH,
    SERVICE_AUDIT_INSTANT_PROPS,
    SERVICE_COLUMNS,
    SERVICE_DB_NAMES,
    WIDTH_STORAGE_PREFIX,
} from "@/features/renovation/renovation-grid-constants";
import {
    dateTemplate,
    dateTimeTemplate,
    normalizeDate,
    numberTemplate,
} from "@/features/renovation/renovation-grid-format";
import {
    clampLevelHeight,
    clampWidth,
    columnNumber,
    defaultLevelHeights,
    handleClipPath,
    isGridTarget,
    loadLevelHeights,
    mergeBands,
    type HandleBand,
} from "@/features/renovation/renovation-grid-header";
import type { CellUpdate } from "@/shared/api/raw-materials";
import type {
    RenovationCatalog,
    RenovationField,
    RenovationHeaderCell,
} from "@/shared/api/renovation";
import { parsePastedText, serializeCellValue } from "@/shared/grid";
import { deduplicateUpdates, parseNumericText, toCellValue } from "@/shared/grid/grid-edit";
import { DateEditor, ReferenceEditor } from "@/shared/grid/grid-editors";
import { SUMMARY_ROW_ID, type GridViewStats } from "@/shared/grid/data-grid";
import { reactEditor } from "@/shared/grid/react-editor";
import { isChunkedSection } from "@/shared/grid/section-policy";
import { selectionDetailForColumn } from "@/shared/grid/selection-label";
import { isFromTextEditor } from "@/shared/keyboard";
import { hasOpenModal } from "@/shared/ui/modal-presence";
import { hasOpenSmartSelect } from "@/shared/ui/smart-select";

import { breaksValueRules } from "./renovation-rules";
import { renovationRowStats } from "./renovation-stats";

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
 *
 * Rows come from RevoGrid Pro's InfinityScrollPlugin: the grid keeps one
 * sparse array of `total` slots and loads chunks through `loadData` as the
 * user scrolls, evicting the far ones. `source` is therefore NOT a prop —
 * the plugin owns it; the workspace only feeds `total` (and the identity of
 * the row scope) and pushes live cell updates into loaded rows.
 */

export const RENOVATION_PLAN_ROWS_TABLE = "r_plan_rows";

/** Stable empty pinned strip — a fresh `[]` per render makes revo-grid reprocess the pinned row. */
const EMPTY_PINNED_SOURCE: RenovationGridRow[] = [];

export type RenovationGridRow = {
    /** Canonical row id (UUID). */
    __id: string;
    /**
     * Row index in the remote source (0-based) — the offset of the row in the
     * FILTERED plan, which is also the index the server pages by. Assigned when
     * the plugin loads the row; the workspace uses it to patch that exact slot.
     */
    __index?: number;
    __position: number;
    __assetId: string | null;
    __assetName: string;
    __renovationId: string | null;
    /** Diff state: "deleted" rows are ghosts from the previous archived plan. */
    __rowState: "unchanged" | "new" | "changed" | "deleted";
    __changedFields: string[];
    __missingComponents: string[];
    /** Fields whose stored value breaks a form rule (red highlight). */
    __invalidFields: string[];
} & Record<string, unknown>;

export type RenovationGridProps = {
    fields: RenovationField[];
    headerCells: RenovationHeaderCell[];
    catalogs: RenovationCatalog[];
    /**
     * Identity of the remote row scope (plan + filters). The grid loads its
     * own chunks; when this changes the plugin reloads from the first chunk,
     * so a filter/version switch shows the same rows the counters describe.
     * Built by the workspace via `renovationGridScopeKey`.
     */
    scopeKey: string;
    /** Filtered row count — `total` for the remote loader (0 = nothing to load). */
    totalRows: number;
    /** Whole-plan row count, independent of asset/header filters. */
    totalRowsAll: number;
    /**
     * Loads one chunk of the scope. `offset`/`limit` are rows of the FILTERED
     * plan in the server's «№» order, so a row's grid index equals its offset
     * (see `RenovationGridHandle.scrollToPosition`).
     */
    loadRows: (
        offset: number,
        limit: number,
    ) => Promise<{ rows: RenovationGridRow[]; total: number }>;
    /**
     * The loaded rows that match the client-side asset filter, in grid order.
     * Only used for display-mode reports (stats, selection labels); the grid
     * itself renders the remote source. Pass an empty array while nothing is
     * loaded yet.
     */
    loadedRows: RenovationGridRow[];
    /** Asset-name filter (client-side); null — show all rows. */
    assetFilter: Set<string> | null;
    /** Only columns of this responsibility block (null — all). Always-visible columns stay. */
    blockFilter: string | null;
    baselineBoundary: number | null;
    readOnly: boolean;
    /** The current role may edit the plan at all (an approver cannot). */
    canEdit: boolean;
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
    onCommitFailed: () => void;
    onValidationError: (message: string) => void;
    onPasteSkipped: (description: string) => void;
    onSelectionChange: (
        selection: {
            label: string | null;
            /**
             * The full header chain of a single-column selection (the status
             * tooltip shows it with «→»); null when it does not apply.
             */
            detail: string | null;
            cellKeys: string[];
            /** The row the selection started in (for row-level actions like delete). */
            rowId: string | null;
        } | null,
    ) => void;
    onClipboardStateChange: (state: { canPaste: boolean }) => void;
    onViewStatsChange: (stats: GridViewStats) => void;
    /** Controlled display toggles (rendered in the view strip). */
    headerPinned: boolean;
    showServiceColumns: boolean;
    /** Controlled RevoGrid data row height. */
    rowHeight: number;
    /** Numeric totals of the filtered plan — the pinned summary row (null hides it). */
    summaryValues?: Record<string, number> | null;
    /** Live updates for rows the plugin already holds (edits, undo, remote cells). */
    rowPatches: RenovationRowPatch[];
    /**
     * Fires whenever the plugin changes which rows it holds (chunk load or
     * eviction). The workspace mirrors the SAME objects so it can patch them
     * in place — the grid never resets its source for an update.
     */
    onLoadedRowsChange?: (rows: RenovationGridRow[]) => void;
    /**
     * Scroll the grid to a row by its SEQUENTIAL number in the current
     * filtered scope (1-based — the number shown in the «№» column). The plan
     * grid opens at the TAIL (rows are appended at the end) and the archive
     * reads top-down — both are expressed through this one method. With
     * `centered` the row lands in the middle of the data viewport instead of
     * its top (the reload/filter anchor).
     */
    scrollToPosition?: { position: number; token: number; centered?: boolean } | null;
    /**
     * The stored position (`__position`) of the row at the vertical centre of
     * the data viewport — reported while scrolling, so the workspace can bring
     * the user back to the same content after a reload or a filter change.
     */
    onCenterPositionChange?: (position: number | null) => void;
    /** Columns with an active header filter (leaf header shows the active marker). */
    activeFilterColumns?: ReadonlySet<string>;
    /** Open the column-filter popover for a leaf header (anchor = its rect). */
    onOpenColumnFilter?: (column: string, anchor: DOMRect) => void;
    /** Open the «go to row» popover from the «№» corner (anchor = its rect). */
    onOpenRowFilter?: (anchor: DOMRect) => void;
};

/** One live update of an already-loaded row (grid index + changed fields). */
export type RenovationRowPatch = {
    index: number;
    fields: Record<string, unknown>;
};

export type RenovationGridHandle = {
    copySelection: () => void;
    clearSelection: () => void;
    pasteIntoSelection: () => void;
    /** Ids of the rows currently on screen (visible source, no placeholder/deleted). */
    getVisibleRowIds: () => Promise<string[]>;
    /**
     * Plan rows the plugin currently holds, in grid order (holes skipped).
     * The workspace patches these objects in place and feeds the changed
     * ones back through `rowPatches` — no full source reset, no flicker.
     */
    getLoadedRows: () => Promise<RenovationGridRow[]>;
    /** Scroll to a row by its sequential number in the filtered scope (1-based). */
    scrollToPosition: (position: number) => Promise<void>;
    /**
     * Scroll to a column by its FIXED strip number (the workbook ordinal, so it
     * never shifts like row numbers do). Columns are fixed, so a block filter
     * may hide the requested one — the nearest visible column is used instead,
     * mirroring how a filtered row number resolves to the nearest row.
     */
    scrollToColumn: (number: number) => Promise<void>;
    /** Drop the persisted per-column widths and header level heights. */
    resetColumnWidths: () => void;
};

export function canonicalRenovationKey(rowId: string, field: string): string {
    return `${RENOVATION_PLAN_ROWS_TABLE}/${rowId}/${field}`;
}

type NormalizeResult = { ok: true; value: unknown } | { ok: false; message: string };

function RenovationGridInner(
    {
        fields,
        headerCells,
        catalogs,
        scopeKey,
        totalRows,
        totalRowsAll,
        loadRows,
        loadedRows,
        assetFilter,
        blockFilter,
        baselineBoundary,
        readOnly,
        canEdit,
        editableFieldKeys,
        userAssetIds,
        remoteLocks,
        remotePresence,
        remoteCellInfo,
        onAcquireLease,
        onReleaseLease,
        onLockedCellEdit,
        onCommit,
        onCommitFailed,
        onValidationError,
        onPasteSkipped,
        onSelectionChange,
        onClipboardStateChange,
        onViewStatsChange,
        headerPinned,
        showServiceColumns,
        rowHeight,
        summaryValues = null,
        rowPatches,
        onLoadedRowsChange,
        onCenterPositionChange,
        scrollToPosition,
        activeFilterColumns,
        onOpenColumnFilter,
        onOpenRowFilter,
    }: RenovationGridProps,
    forwardedRef: Ref<RenovationGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const shellRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const headerBodyRef = useRef<HTMLDivElement>(null);
    const [selectionRange, setSelectionRange] = useState<RangeArea | null>(null);
    const selectionRangeRef = useRef<RangeArea | null>(null);
    // Synchronous mirror of the last range pushed into the state. revo-grid
    // re-emits `beforecellfocus` on every virtualization tick while a cell is
    // focused (the focused row leaves/enters the rendered window), and each
    // emission used to set a fresh object — a full component re-render per
    // scroll tick (measured: 61 fps → 20 fps with one cell selected). Skip
    // identical ranges; copy/paste read the ref, so nothing else changes.
    const lastRangeRef = useRef<RangeArea | null>(null);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    // Bumped on Escape: forces a columns rebuild (see the columns memo) that
    // repaints the selection overlay after the store is cleared — the overlay
    // does not reliably repaint on a plain store change.
    const [selectionEpoch, setSelectionEpoch] = useState(0);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);
    const activeLeaseRef = useRef<string | null>(null);
    const rowValuesRef = useRef<Map<string, Readonly<Record<string, unknown>>>>(new Map());
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    // Content width of the last measurement — used only to detect a change
    // (revo-grid re-fires `contentsizechanged` on every virtualized tick).
    const horizontalContentWidthRef = useRef(0);
    const [hasHorizontalOverflow, setHasHorizontalOverflow] = useState(false);
    const [widths, setWidths] = useState<Map<string, number>>(() => {
        const result = new Map<string, number>();
        // Guard the lazy initializer: it runs during render, which can happen
        // in an environment without `window` (SSR / early hydration / some e2e).
        if (typeof window === "undefined") return result;
        for (const field of fields) {
            const saved = window.localStorage.getItem(WIDTH_STORAGE_PREFIX + field.dbName);
            if (saved) {
                const parsed = Number(saved);
                if (Number.isFinite(parsed)) result.set(field.dbName, clampWidth(parsed));
            }
        }
        const savedAsset = window.localStorage.getItem(WIDTH_STORAGE_PREFIX + ASSET_COLUMN_PROP);
        if (savedAsset) {
            const parsed = Number(savedAsset);
            if (Number.isFinite(parsed)) result.set(ASSET_COLUMN_PROP, clampWidth(parsed));
        }
        return result;
    });
    // Live overlay geometry while a resize drag is in progress; the grid
    // columns themselves only rebuild on drop.
    const [overlayWidths, setOverlayWidths] = useState<Map<string, number>>(new Map());
    const [resizingField, setResizingField] = useState<string | null>(null);
    const dragRef = useRef<{ dbName: string; startX: number; startWidth: number } | null>(null);
    // Header level heights (dragged on a level's bottom boundary). The overlay
    // geometry follows live; the persisted array is written on drop.
    const [levelHeights, setLevelHeights] = useState<number[]>(loadLevelHeights);
    const levelHeightsRef = useRef(levelHeights);
    levelHeightsRef.current = levelHeights;
    const [resizingLevel, setResizingLevel] = useState<number | null>(null);
    const levelDragRef = useRef<{ level: number; startY: number; startHeight: number } | null>(
        null,
    );
    // Top edge of every header level: levelOffsets[i] is the top of level i,
    // levelOffsets[HEADER_LEVEL_COUNT] is the whole 11-level block's height.
    const levelOffsets = useMemo(() => {
        const offsets: number[] = [0];
        let acc = 0;
        for (const height of levelHeights) {
            acc += (height * rowHeight) / 36;
            offsets.push(acc);
        }
        return offsets;
    }, [levelHeights, rowHeight]);
    const headerHeight =
        levelOffsets[HEADER_LEVEL_COUNT] ?? HEADER_LEVEL_COUNT * HEADER_LEVEL_HEIGHT;
    // The totals row is a revo-grid pinned top row (like the assets dictionary).
    // The overlay carries the 11 header levels plus the column-number strip
    // directly above the pinned totals row.
    const columnNumberRowHeight = rowHeight;
    const overlayHeight = headerHeight + columnNumberRowHeight;
    /** The pinned totals row's height (0 when there is no summary). */
    const summaryHeight = summaryValues === null ? 0 : rowHeight;
    // Floating tooltip over a cell occupied by another participant (lease/presence):
    // the owner's name in bold, then the selected range.
    const [remoteTooltip, setRemoteTooltip] = useState<{
        name: string;
        label: string | null;
        left: number;
        top: number;
    } | null>(null);
    const remoteTooltipTargetRef = useRef<Element | null>(null);
    // Floating tooltip over a column number: the column's header chain, from the
    // top level down («1) Техническая дирекция ↓ … ↓ Всего»).
    const [numberTooltip, setNumberTooltip] = useState<{
        lines: string[];
        /** The hovered column's prop — remounts the popover on every switch. */
        column: string;
        left: number;
        top: number;
    } | null>(null);
    const numberTooltipTargetRef = useRef<Element | null>(null);

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
        // col_002 (the form's «Предприятие» field) is replaced by the
        // synthetic asset column (ASSET_COLUMN_PROP) — keep it out of the
        // field columns to avoid a duplicated column. col_003 (the renovation
        // id) and col_217 (the note) are always visible regardless of the
        // block filter, like the synthetic asset column.
        const ALWAYS_VISIBLE = new Set(["col_003", "col_217"]);
        return [...fields]
            .filter(
                (field) =>
                    field.dbName !== ASSET_FIELD_DB_NAME &&
                    (blockFilter === null ||
                        ALWAYS_VISIBLE.has(field.dbName) ||
                        field.responsibilityBlock === blockFilter),
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
    //
    // Their STRIP NUMBER continues the form's own numbering: the last plan column
    // is N, so the audit trio is N+1, N+2, N+3. Without a number they showed an
    // empty strip cell and offered no header filter, which read as "this column
    // cannot be filtered" rather than "it is not part of the workbook".
    const displayFields = useMemo<RenovationField[]>(() => {
        if (!showServiceColumns) return visibleFields;
        const lastOrdinal = visibleFields.reduce((max, field) => Math.max(max, field.ordinal), 0);
        return [
            ...visibleFields,
            ...SERVICE_COLUMNS.map((column, index) => ({
                dbName: column.dbName,
                label: column.label,
                // `datetime` cells render through the same instant formatter as
                // the form's dates; the editor name is plain text.
                cellType: SERVICE_AUDIT_INSTANT_PROPS.has(column.dbName) ? "datetime" : "text",
                catalog: null,
                isCalculated: false,
                isReadOnly: true,
                decimals: null,
                position: 10_000 + index,
                ordinal: lastOrdinal + 1 + index,
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

    // The synthetic asset column is resizable like any other column
    // (its width is persisted under ASSET_COLUMN_PROP).
    const assetWidth = useMemo(
        () =>
            overlayWidths.get(ASSET_COLUMN_PROP) ??
            widths.get(ASSET_COLUMN_PROP) ??
            ASSET_COLUMN_WIDTH,
        [overlayWidths, widths],
    );

    // Left edge of every visible field (grid-relative x, the 52px row header
    // and the synthetic asset column included) and the total content width —
    // live during a resize drag.
    const geometry = useMemo(() => {
        const offsets = new Map<string, number>();
        let acc = ROW_HEADER_WIDTH + assetWidth;
        for (const field of displayFields) {
            offsets.set(field.dbName, acc);
            acc += fieldWidth(field);
        }
        return { offsets, totalWidth: acc };
    }, [assetWidth, displayFields, fieldWidth]);

    // The column-number strip of the overlay: every visible column shows its
    // number in the form (the workbook ordinal shifted so «Предприятие» = 1 —
    // see `columnNumber`), so hiding a field (e.g. the dropped col_218) never
    // leaves a gap in the numbering. The service columns stay empty.
    const assetPosition = useMemo(
        () => fields.find((field) => field.dbName === ASSET_FIELD_DB_NAME)?.position ?? null,
        [fields],
    );
    const assetColumnNumber = useMemo(() => {
        const asset = fields.find((field) => field.dbName === ASSET_FIELD_DB_NAME);
        return asset ? columnNumber(asset) : null;
    }, [fields]);
    const columnNumberCells = useMemo(() => {
        // A column whose totals cell is empty merges with it: the number cell
        // spans the strip AND the totals row (no line between the two). Only
        // when the totals band is shown — without it nothing extends below.
        const heightOf = (dbName: string): number => {
            if (summaryValues === null) return rowHeight;
            return typeof summaryValues[dbName] === "number"
                ? rowHeight
                : rowHeight + summaryHeight;
        };
        const cells: Array<{
            key: string;
            text: string;
            left: number;
            width: number;
            height: number;
            /** Non-null for a numbered column: its prop + label (filter funnel). */
            filterColumn?: string;
            label?: string;
        }> = [
            {
                key: ASSET_COLUMN_PROP,
                text: assetColumnNumber === null ? "" : String(assetColumnNumber),
                left: ROW_HEADER_WIDTH,
                width: assetWidth,
                height: heightOf(ASSET_COLUMN_PROP),
                filterColumn: ASSET_COLUMN_PROP,
                label: "Предприятие",
            },
        ];
        for (const field of displayFields) {
            const left = geometry.offsets.get(field.dbName);
            if (left === undefined) continue;
            // Every column carries a strip number now, the audit trio included.
            const number = columnNumber(field);
            cells.push({
                key: field.dbName,
                text: number === null || !Number.isFinite(number) ? "" : String(number),
                left,
                width: fieldWidth(field),
                height: heightOf(field.dbName),
                ...(number !== null && { filterColumn: field.dbName, label: field.label }),
            });
        }
        return cells;
    }, [
        assetColumnNumber,
        assetWidth,
        displayFields,
        fieldWidth,
        geometry.offsets,
        summaryValues,
        rowHeight,
        summaryHeight,
    ]);

    // The header is a static grid of cells (text, colSpan/rowSpan from the
    // backend). A cell's startPosition is 0-based (0 = the first field, whose
    // position is 1), so the lookup and the span end must add 1 to land on the
    // 1-based position keys of visibleIndexByPosition. Cells whose start field
    // is hidden by the block filter are dropped; spans over hidden fields
    // render the visible part only. Full-height columns that start directly
    // under a responsibility-block row keep their Excel level; columns with no
    // level-0 cell above them («Идентификационный код работы/ремонта»,
    // «Примечание») are raised to the top so their top border becomes the
    // header's top edge instead of leaving a gap (and a spurious second border).
    const overlayCells = useMemo(() => {
        const coveredByTopRow = new Set<number>();
        for (const cell of headerCells) {
            if (cell.level !== 0) continue;
            for (
                let position = cell.startPosition;
                position < cell.startPosition + cell.colSpan;
                position += 1
            ) {
                coveredByTopRow.add(position);
            }
        }
        const cells: Array<{
            id: string;
            text: string;
            left: number;
            top: number;
            width: number;
            height: number;
            /** The cell is in the header's top row — it draws the table's top line. */
            isTop: boolean;
            /** The form levels the cell spans (0-based, like `headerCells`). */
            level: number;
            /**
             * The 1-based form positions the cell covers (start inclusive, end
             * exclusive) — the column-number tooltip walks them to build a
             * column's header chain from the top level down.
             */
            fromPosition: number;
            toPosition: number;
            /** Leaf header of a filterable column (its dbName) — a filter button. */
            filterColumn?: string;
        }> = [];
        for (const cell of headerCells) {
            const startIndex = visibleIndexByPosition.get(cell.startPosition + 1);
            if (startIndex === undefined) continue;
            const endPosition = cell.startPosition + cell.colSpan + 1;
            let width = 0;
            for (let i = startIndex; i < visibleFields.length; i += 1) {
                const field = visibleFields[i];
                if (!field || field.position >= endPosition) break;
                width += widthOf(field);
            }
            if (width === 0) continue;
            const topLevel = coveredByTopRow.has(cell.startPosition) ? cell.level : 0;
            const top = levelOffsets[topLevel] ?? 0;
            // Level-0 directorate group headers carry a short label in the form
            // data; show the same canonical name as the «Все блоки» filter.
            let text = cell.text.trim();
            if (cell.level === 0 && text.startsWith("Блок ")) {
                for (let i = startIndex; i < visibleFields.length; i += 1) {
                    const field = visibleFields[i];
                    if (!field || field.position >= endPosition) break;
                    const fieldBlock = field.responsibilityBlock;
                    if (fieldBlock && fieldBlock !== "read_only") {
                        text = BLOCK_HEADER_LABELS[fieldBlock] ?? text;
                        break;
                    }
                }
            }
            const startField = visibleFields[startIndex];
            if (!startField) continue;
            const filterColumn = cell.colSpan === 1 ? startField.dbName : undefined;
            cells.push({
                id: cell.id,
                text,
                left: geometry.offsets.get(startField.dbName) ?? ROW_HEADER_WIDTH,
                top,
                width,
                height: (levelOffsets[cell.level + cell.rowSpan] ?? headerHeight) - top,
                isTop: top === 0,
                level: cell.level,
                fromPosition: cell.startPosition + 1,
                toPosition: endPosition,
                // A single-field leaf header filters that column. Multi-field
                // group headers have no single column to filter.
                ...(filterColumn && { filterColumn }),
            });
        }
        cells.sort((a, b) => a.top - b.top || a.left - b.left);
        cells.push({
            id: `asset:${ASSET_COLUMN_PROP}`,
            text: "Предприятие",
            left: ROW_HEADER_WIDTH,
            top: 0,
            width: assetWidth,
            height: headerHeight,
            isTop: true,
            // The synthetic column stands in for the form's «Предприятие»
            // (col_002) — its own single-cell chain.
            level: 0,
            fromPosition: assetPosition ?? 1,
            toPosition: (assetPosition ?? 1) + 1,
            filterColumn: ASSET_COLUMN_PROP,
        });
        if (showServiceColumns) {
            for (const column of SERVICE_COLUMNS) {
                cells.push({
                    id: `service:${column.dbName}`,
                    text: column.label,
                    left: geometry.offsets.get(column.dbName) ?? ROW_HEADER_WIDTH,
                    top: 0,
                    width: serviceWidthOf(column.dbName),
                    height: headerHeight,
                    isTop: true,
                    // No form position: service columns are not numbered.
                    level: 0,
                    fromPosition: 0,
                    toPosition: 0,
                });
            }
        }
        return cells;
    }, [
        assetPosition,
        assetWidth,
        geometry.offsets,
        headerCells,
        headerHeight,
        levelOffsets,
        serviceWidthOf,
        showServiceColumns,
        visibleFields,
        visibleIndexByPosition,
        widthOf,
    ]);

    /**
     * The header chain of every numbered column: the cells covering it, from
     * the top level down to the leaf — the column-number tooltip spells them
     * out with «↓», the status tooltip with «→» (the same texts the overlay
     * draws, canonical block labels included).
     */
    const headerChainByProp = useMemo(() => {
        const chains = new Map<string, string[]>();
        const add = (prop: string, position: number | null) => {
            if (position === null) return;
            const texts: string[] = [];
            for (const cell of overlayCells) {
                if (cell.fromPosition > position || position >= cell.toPosition) continue;
                const text = cell.text.trim();
                // Consecutive duplicates (a label repeated on two levels) read
                // as noise in the chain.
                if (text !== "" && text !== texts[texts.length - 1]) texts.push(text);
            }
            if (texts.length > 0) chains.set(prop, texts);
        };
        add(ASSET_COLUMN_PROP, assetPosition);
        for (const field of displayFields) {
            if (SERVICE_DB_NAMES.has(field.dbName)) {
                // An audit column has no header cell of its own: its chain is
                // the column's label, which is what the tooltip should read.
                chains.set(field.dbName, [field.label]);
                continue;
            }
            add(field.dbName, field.position);
        }
        return chains;
    }, [assetPosition, displayFields, overlayCells]);

    const editableCell = useCallback(
        (field: RenovationField, row: RenovationGridRow): boolean => {
            if (readOnly) return false;
            // An unloaded slot is a placeholder object (`{}`) the plugin renders
            // until its chunk arrives — it has no row id and is not editable.
            if (row.__id === undefined) return false;
            if (field.isCalculated || field.isReadOnly) return false;
            if (!editableFieldKeys.has(field.dbName)) return false;
            if (row.__rowState === "deleted") return false;
            if (userAssetIds && (row.__assetId == null || !userAssetIds.has(row.__assetId)))
                return false;
            return !remoteLocks.has(canonicalRenovationKey(row.__id, field.dbName));
        },
        [editableFieldKeys, readOnly, remoteLocks, userAssetIds],
    );

    const editors = useMemo<Editors>(
        () => ({ reference: reactEditor(ReferenceEditor), date: reactEditor(DateEditor) }),
        [],
    );

    const columns = useMemo<ColumnRegular[]>(() => {
        const commitBoolean = (rowId: string, field: string, value: boolean | null) => {
            void onCommit([{ rowId, field, value }]).then((ok) => {
                if (!ok) onCommitFailed();
            });
        };
        const assetColumn: ColumnRegular = {
            prop: ASSET_COLUMN_PROP,
            name: "Предприятие",
            size: assetWidth,
            minSize: MIN_COLUMN_WIDTH,
            maxSize: MAX_COLUMN_WIDTH,
            // The asset is fixed per row — rows are added through the fixed
            // add-row strip below the table, not through this column.
            readonly: true,
            cellProperties: ({ model }) => {
                const row = model as RenovationGridRow;
                const isSummary = row.__id === SUMMARY_ROW_ID;
                return {
                    className: {
                        "report-cell": true,
                        "report-cell--readonly": true,
                        "report-cell--baseline-boundary":
                            baselineBoundary !== null && row.__position === baselineBoundary,
                        "renovation-grid__summary-cell": isSummary,
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
                        const isSummary = row.__id === SUMMARY_ROW_ID;
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
                        const calculated =
                            field.isCalculated &&
                            row.__rowState !== "deleted" &&
                            !row.isPlaceholder;
                        // The «Идентификационный код ремонта» (col_003) is red when it's
                        // incomplete — a component is missing (replaced with
                        // <NULL>) or the code was never computed. Any other
                        // field whose current value breaks a form rule (e.g. a
                        // negative «Продолжительность, дни» = col_025 − col_024)
                        // is red too. The rule is re-evaluated against the value
                        // being rendered NOW, so a recalculated cell turns red in
                        // the same render as its new number instead of waiting
                        // for the next server read; the backend's list adds what
                        // this side cannot tell on its own.
                        const invalid =
                            calculated &&
                            (field.dbName === "col_003"
                                ? (row.__missingComponents?.length ?? 0) > 0 ||
                                  String(row.col_003 ?? "").includes(MISSING_COMPONENT)
                                : breaksValueRules(field, value) ||
                                  (row.__invalidFields?.includes(field.dbName) ?? false));
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
                                "report-cell--calculated": calculated && !invalid,
                                "report-cell--invalid": invalid,
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
                                "renovation-grid__summary-cell": isSummary,
                                "renovation-grid__summary-cell--filled":
                                    isSummary && typeof row[field.dbName] === "number",
                            },
                            "data-remote-lock": remoteUser
                                ? JSON.stringify({
                                      name: remoteUser.name,
                                      label: remoteUser.label ?? null,
                                  })
                                : undefined,
                        };
                    },
                };
                if (field.cellType === "number") {
                    column.cellTemplate = numberTemplate(field.decimals);
                }
                if (field.cellType === "date") {
                    column.cellTemplate = dateTemplate;
                    column.editor = "date";
                }
                // The service «Время добавления»/«Время редактирования» columns
                // carry full timestamps.
                if (field.dbName === "updatedAt" || field.dbName === "createdAt") {
                    column.cellTemplate = dateTimeTemplate;
                }
                if (field.cellType === "boolean") {
                    // The same native checkbox as every other grid. The plan's
                    // boolean fields are nullable (the form schema marks
                    // `allowNull`): a NULL is an EMPTY cell — no box at all —
                    // and the click cycle is пусто → Да → Нет → пусто, with a
                    // click on the unchecked box clearing the value (the same
                    // as Delete). No indeterminate state: only true/false show
                    // a checkbox.
                    column.cellTemplate = (h, props) => {
                        const row = props.model as RenovationGridRow;
                        const disabled = !editableCell(field, row);
                        const allowsNull = field.allowNull !== false;
                        const value =
                            props.value === true ? true : props.value === false ? false : null;
                        const next: boolean | null = allowsNull
                            ? value === true
                                ? false
                                : value === false
                                  ? null
                                  : true
                            : value !== true;
                        if (allowsNull && value === null) {
                            return h("label", {
                                class: "report-cell__checkbox report-cell__checkbox--empty",
                                "aria-label": field.label,
                                onPointerDown: (event: PointerEvent) => event.stopPropagation(),
                                onClick: (event: MouseEvent) => {
                                    event.stopPropagation();
                                    if (disabled) return;
                                    commitBoolean(row.__id, field.dbName, true);
                                },
                            });
                        }
                        return h(
                            "label",
                            { class: "report-cell__checkbox" },
                            h("input", {
                                type: "checkbox",
                                checked: value === true,
                                disabled,
                                ariaLabel: field.label,
                                onPointerDown: (event: PointerEvent) => event.stopPropagation(),
                                onClick: (event: MouseEvent) => {
                                    event.stopPropagation();
                                    if (disabled) return;
                                    // Commit on CLICK with the native toggle
                                    // cancelled: otherwise the browser flips the
                                    // box first and a clearing click (false →
                                    // пусто) flashes a checked box before the
                                    // server round-trip empties the cell.
                                    event.preventDefault();
                                    commitBoolean(row.__id, field.dbName, next);
                                },
                            }),
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
        // `selectionEpoch` is deliberate: Ctrl+A bumps it only to force this
        // rebuild, since the selection overlay does not repaint from a store
        // change alone. Same for `scopeKey` below — a version/filter switch must
        // rebuild, even though the memo does not read it.
        // oxlint-disable-next-line react-hooks/exhaustive-deps
    }, [
        assetWidth,
        baselineBoundary,
        catalogLabelsByField,
        displayFields,
        editableCell,
        fieldWidth,
        onCommit,
        onCommitFailed,
        remoteCellInfo,
        selectedRowIdSet,
        // `selectionEpoch` is not read here — Ctrl+A bumps it purely to force
        // this rebuild (the selection overlay does not repaint otherwise).
        selectionEpoch,
    ]);

    /**
     * The column behind a grid column index: its strip number (null for the
     * unnumbered service columns), its label and its prop — the selection label
     * and the status tooltip are built from this.
     */
    const columnInfoAt = useCallback(
        (index: number): { number: number | null; label: string; prop: string | null } | null => {
            const column = columns[index] as ColumnRegular | undefined;
            if (!column || column.prop === undefined) return null;
            const prop = String(column.prop);
            if (prop === ASSET_COLUMN_PROP) {
                return { number: assetColumnNumber, label: "Предприятие", prop };
            }
            const field = fieldByDbName.get(prop);
            if (!field) return { number: null, label: String(column.name ?? ""), prop };
            return { number: columnNumber(field), label: field.label, prop };
        },
        [assetColumnNumber, columns, fieldByDbName],
    );

    // Grid row index → loaded row. The remote source is sparse (unloaded slots
    // are `{}`), so a compact array cannot be indexed by the grid's rowIndex —
    // this map carries the authoritative `__index` (the filtered offset). The
    // grid index is also the visible sequential number minus one.
    const rowsByIndex = useMemo(() => {
        const map = new Map<number, RenovationGridRow>();
        for (const row of loadedRows) {
            if (row.__index !== undefined) map.set(row.__index, row);
        }
        return map;
    }, [loadedRows]);

    const clearGridSelection = useCallback(() => {
        const grid = gridRef.current;
        lastRangeRef.current = null;
        setSelectionRange(null);
        if (!grid) return;
        const doClear = () => {
            (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore =
                undefined;
            return grid.clearFocus().then(() => grid.refresh());
        };
        // A drag-created range resurrects as a single-cell range on the first
        // clear: revo-grid re-applies the stale focus during the re-render
        // that `refresh()` triggers. Re-check after that render settles and
        // repeat until the store is really empty — the same pass that works on
        // a second Escape now runs automatically on the first.
        void doClear().then(() => {
            const verify = () =>
                grid
                    .getSelectedRange()
                    .then((range) => {
                        if (range) doClear();
                    })
                    .catch(() => {
                        /* grid disposed */
                    });
            if (typeof requestAnimationFrame === "function") {
                requestAnimationFrame(() => void verify());
            } else {
                void verify();
            }
        });
    }, []);

    // Row-number click: select the whole row (shift — range, Ctrl/Cmd —
    // toggle). Mirrors the raw-materials grid; the cell range is cleared
    // BEFORE the row state updates, or the columns rebuild (selectedRowIds
    // is a dependency) resurrects the old focus as a single-cell range.
    const handleRowNumberClick = useCallback(
        (event: MouseEvent, rowIndex: number, id: string) => {
            event.preventDefault();
            event.stopPropagation();
            const modifiers = event.ctrlKey || event.metaKey;
            if (event.shiftKey && !modifiers) {
                const anchor = rowSelectionAnchorRef.current ?? rowIndex;
                const start = Math.min(anchor, rowIndex);
                const end = Math.max(anchor, rowIndex);
                // The grid row index is the filtered offset; only loaded rows
                // can be selected (an unloaded slot has no id).
                clearGridSelection();
                const ids: string[] = [];
                for (let index = start; index <= end; index += 1) {
                    const row = rowsByIndex.get(index);
                    if (row) ids.push(row.__id);
                }
                setSelectedRowIds(ids);
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
        [clearGridSelection, rowsByIndex, selectedRowIdSet],
    );

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: ROW_HEADER_WIDTH,
            columnProperties: () => ({
                className: {
                    "report-row-header": true,
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
                // The pinned summary row shows no row number.
                if (row.__id === SUMMARY_ROW_ID) {
                    return h("span", { class: "report-row-header__summary" });
                }
                // The number is the SEQUENTIAL position in the current filtered
                // scope (grid index + 1), not the stored plan position — after
                // filtering the user sees 1..N. Unloaded slots show their
                // number too (the number is positional, not data).
                const number = rowIndex + 1;
                // An unloaded slot has no id — render the number without the
                // selection button; it becomes clickable once the chunk loads.
                if (row.__id === undefined) {
                    return h("span", { class: "report-row-header__number" }, String(number));
                }
                const selected = selectedRowIds.includes(row.__id);
                return h(
                    "button",
                    {
                        type: "button",
                        class: {
                            "report-row-header__button": true,
                            "report-row-header__button--selected": selected,
                        },
                        title: selected
                            ? `Снять выделение строки ${number}`
                            : `Выделить строку ${number}`,
                        onMouseDown: (event: MouseEvent) => {
                            event.stopPropagation();
                        },
                        onClick: (event: MouseEvent) => {
                            event.stopPropagation();
                            handleRowNumberClick(event, rowIndex, row.__id);
                        },
                    },
                    String(number),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [baselineBoundary, handleRowNumberClick, selectedRowIds],
    );

    /**
     * The plan's applied filters as header chains, leaf last — the status
     * tooltip lists exactly these. The plan's columns are FLAT (one header
     * level), so a chain is the field's own label; the asset panel filter is not
     * a column and has no chain.
     */
    const filteredColumnPaths = useMemo(
        () =>
            [...(activeFilterColumns ?? [])]
                .map((column) => displayFields.find((field) => field.dbName === column)?.label)
                .filter((label): label is string => label !== undefined)
                .map((label) => [label]),
        [activeFilterColumns, displayFields],
    );

    useEffect(() => {
        onViewStatsChange({
            // «Найдено» — the filtered plan size the server reports for the
            // current scope (never the loaded chunk count: the plugin keeps
            // only a window around the viewport).
            ...renovationRowStats(
                totalRows,
                totalRowsAll,
                assetFilter !== null,
                activeFilterColumns,
                filteredColumnPaths,
            ),
            // The plan reports no header sorting: this grid sorts server-side and
            // keeps no client sort state of its own.
            sortedColumns: [],
            totalCols: displayFields.length,
            immutableCols: displayFields.filter((field) => field.isReadOnly && !field.isCalculated)
                .length,
            calculatedCols: displayFields.filter((field) => field.isCalculated).length,
            editableCols: displayFields.filter(
                (field) =>
                    !field.isCalculated && !field.isReadOnly && editableFieldKeys.has(field.dbName),
            ).length,
        });
    }, [
        activeFilterColumns,
        assetFilter,
        displayFields,
        editableFieldKeys,
        onViewStatsChange,
        totalRowsAll,
        totalRows,
    ]);

    useEffect(() => {
        rowValuesRef.current = new Map(loadedRows.map((row) => [row.__id, { ...row }]));
    }, [loadedRows]);

    // ── Remote rows (InfinityScrollPlugin) ──────────────────────────────────
    // The plugin owns `source`: it keeps a sparse array of `total` slots and
    // fills/evicts chunks through `loadData`. A row's index in that array is
    // its offset in the filtered plan, so `scrollToRow(position - 1)` lands on
    // a «№» exactly. The config is rebuilt when the scope (plan + filters)
    // changes — assigning a new object makes the plugin reload from the first
    // chunk, which is what a version/filter switch must do.
    const scopeKeyRef = useRef(scopeKey);
    scopeKeyRef.current = scopeKey;
    const loadRowsRef = useRef(loadRows);
    loadRowsRef.current = loadRows;
    const onLoadedRowsChangeRef = useRef(onLoadedRowsChange);
    onLoadedRowsChangeRef.current = onLoadedRowsChange;
    const onCenterPositionChangeRef = useRef(onCenterPositionChange);
    onCenterPositionChangeRef.current = onCenterPositionChange;
    /**
     * How many structural jumps are still travelling. While a jump is in flight
     * the viewport is on its way somewhere else, so its current row must not be
     * remembered (see `reportCenterPosition`).
     */
    const jumpInFlightRef = useRef(0);
    /**
     * A restore is waiting for its scope read. While it is pending, an unscrolled
     * viewport is not a position the user chose — see `reportCenterPosition`.
     */
    const restorePendingRef = useRef(false);
    /**
     * Hard stop for the "jump in flight" block. A jump normally clears it in a
     * few hundred milliseconds; the deadline guarantees that a jump which never
     * resolves cannot freeze the remembered position forever.
     */
    const restoreDeadlineRef = useRef(0);
    const totalRowsRef = useRef(totalRows);
    totalRowsRef.current = totalRows;
    const summaryHeightRef = useRef(summaryHeight);
    summaryHeightRef.current = summaryHeight;
    /** The chunk loader — supersedes an in-flight request on a newer one. */
    const chunkLoaderRef = useRef<ReturnType<typeof createChunkLoader<RenovationGridRow>> | null>(
        null,
    );
    if (chunkLoaderRef.current === null) {
        chunkLoaderRef.current = createChunkLoader<RenovationGridRow>((offset, limit) =>
            loadRowsRef.current(offset, limit),
        );
    }
    const chunkLoader = chunkLoaderRef.current;
    const infinityScroll = useMemo(
        () => ({
            chunkSize: INFINITY_CHUNK_ROWS,
            bufferSize: INFINITY_BUFFER_ROWS,
            preloadThreshold: INFINITY_PRELOAD_THRESHOLD,
            total: totalRows,
            // Never resolve a superseded chunk empty and never reject one as
            // "cancelled": the plugin would either mark it loaded forever or
            // surface an unhandled AbortError. See `chunk-loader.ts`.
            loadData: chunkLoader,
        }),
        // oxlint-disable-next-line react-hooks/exhaustive-deps
        [chunkLoader, scopeKey, totalRows],
    );
    // Live updates: patch already-loaded rows in place. A full source reset
    // would evict the loaded window and re-fetch it (the flicker this grid had
    // before); `setSourceByPhysicalIndex` + `refresh` touches only the rows
    // that actually changed.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || rowPatches.length === 0) return;
        void (async () => {
            try {
                const store = await grid.getSourceStore("rgRow");
                const patches: Record<number, Record<string, unknown>> = {};
                for (const patch of rowPatches) {
                    const current = getSourceItem(store, patch.index);
                    // Only loaded slots carry a row; an unloaded placeholder
                    // needs no patch — its chunk fetch reads the fresh value.
                    if (!current || current.__id === undefined) continue;
                    patches[patch.index] = { ...current, ...patch.fields };
                }
                if (Object.keys(patches).length === 0) return;
                setSourceByPhysicalIndex(store, patches, false);
                await grid.refresh("rgRow");
            } catch {
                // The grid was disposed or the store is mid-reset — the next
                // patch (or the chunk load) carries the value anyway.
            }
        })();
    }, [rowPatches]);

    // Jump to a grid row index, retrying until the viewport lands near it. A
    // single `scrollToRow` is often swallowed (the grid is still settling its
    // layout) and the target chunk is loaded on demand, so the call is repeated
    // until the scroll position matches the row (±5 rows of slack — the plugin
    // may align the chunk slightly differently). Returns a cancel function.
    // Used by the structural tail jump and the «go to row» popover.
    const jumpToRow = useCallback(
        (target: number, centered = false): (() => void) => {
            const grid = gridRef.current;
            if (!grid) return () => {};
            let cancelled = false;
            let attempts = 0;
            jumpInFlightRef.current += 1;
            // A jump that never resolves must not block the position report:
            // 50 attempts x 150 ms is the longest useful flight.
            restoreDeadlineRef.current = Date.now() + 12_000;
            // The scroll coordinate the row should end up at: its top, or — for a
            // centred jump — the middle of the data viewport (the pinned totals row
            // is not part of the scrollable rows).
            const targetTop = () => {
                if (!centered) return target * rowHeight;
                const dataHeight = Math.max(0, grid.clientHeight - summaryHeightRef.current);
                return Math.max(0, target * rowHeight - (dataHeight - rowHeight) / 2);
            };
            // How far the viewport actually scrolled, read from the row scroller.
            const scrollTop = () => {
                const internal = grid.querySelector(
                    "revogr-scroll-virtual.vertical",
                ) as HTMLElement | null;
                const external = document.querySelector(
                    ".revo-grid-vertical-scroller",
                ) as HTMLElement | null;
                return internal?.scrollTop || external?.scrollTop || 0;
            };
            // The scroller cannot go further, so the jump is as good as it gets:
            // the target is within the reachable range (e.g. a centred jump near
            // the top) or the content is too short to scroll at all.
            const atLimit = () => {
                const internal = grid.querySelector(
                    "revogr-scroll-virtual.vertical",
                ) as HTMLElement | null;
                // A collapsed viewport (the plan is still measuring) reports a
                // content height that cannot reach the target — that is not a
                // limit, it is "not laid out yet". Only trust the end once the
                // scroller actually has room to move.
                if (!internal || internal.scrollHeight - grid.clientHeight < rowHeight * 3) {
                    return false;
                }
                const max = Math.max(0, internal.scrollHeight - grid.clientHeight);
                return scrollTop() >= max;
            };
            const landed = () => {
                const top = scrollTop();
                if (target === 0) return top <= rowHeight;
                // The scroller is at its end, so this is as far as the jump can
                // go (a short plan, or a centred jump on the last rows).
                if (atLimit()) return true;
                // Symmetric slack: being ABOVE the target is a miss too. Accepting
                // a partial jump left the viewport on an early row and then the
                // centre-row report saved THAT row — every open drifted further up
                // until it stuck on the first row.
                return Math.abs(top - targetTop()) <= rowHeight * 5;
            };
            const attempt = () => {
                if (cancelled || attempts >= 50) return;
                attempts += 1;
                // A grid that has not laid itself out yet cannot scroll anywhere:
                // its scroller is still tiny, so the jump is clamped to a few rows
                // and `landed()` then sees it "at the limit". Wait for real height
                // instead of burning the attempts on a collapsed viewport.
                if (grid.clientHeight < rowHeight * 3) {
                    window.setTimeout(attempt, 100);
                    return;
                }
                void (async () => {
                    try {
                        if (centered) await grid.scrollToCoordinate({ y: targetTop() });
                        else await grid.scrollToRow(target);
                    } catch {
                        // The element may not be upgraded yet — retry below.
                    }
                    if (cancelled) return;
                    window.setTimeout(() => {
                        if (cancelled || landed()) return;
                        attempt();
                    }, 150);
                })();
            };
            attempt();
            return () => {
                cancelled = true;
                jumpInFlightRef.current = Math.max(0, jumpInFlightRef.current - 1);
            };
        },
        [rowHeight],
    );

    /**
     * Scroll horizontally to the column carrying the given strip number.
     *
     * Column numbers are FIXED (the workbook ordinal), unlike the row numbers
     * which re-count in the filtered scope, so a block filter can hide the
     * column the user asked for. The nearest VISIBLE column wins then — the
     * one whose number is closest to the request, preferring the lower number
     * on a tie, mirroring how a hidden row resolves to the nearest remaining
     * one. Only numbered (non-service) columns take part.
     */
    const scrollToPlanColumn = useCallback(
        async (number: number): Promise<void> => {
            const grid = gridRef.current;
            if (!grid) return;
            // `columns` is [the synthetic asset column, ...displayFields], so a
            // field's grid index is its displayFields index plus one; going
            // through `columnInfoAt` keeps the numbering in one place.
            // Pick the visible numbered column whose number is closest to the
            // request; on a tie the lower number wins (the earlier column).
            let target: string | null = null;
            let bestDistance = Number.POSITIVE_INFINITY;
            for (let index = 0; index < displayFields.length; index += 1) {
                const info = columnInfoAt(index + 1);
                if (info?.number === null || info?.number === undefined || !info.prop) continue;
                const distance = Math.abs(info.number - number);
                if (distance < bestDistance) {
                    bestDistance = distance;
                    target = info.prop;
                }
            }
            if (target === null) return;
            const scrollTarget = target;
            // A single `scrollToColumnProp` is often swallowed while the grid is
            // still settling its layout, so it is retried: the target column is
            // "landed" once its prop is inside the horizontal window.
            const landed = () => {
                const viewport = grid.querySelector(
                    "revogr-viewport-scroll.horizontal",
                ) as HTMLElement | null;
                if (!viewport) return true;
                const cells = viewport.querySelectorAll(
                    `[data-prop="${CSS.escape(scrollTarget)}"]`,
                );
                return cells.length > 0;
            };
            let attempts = 0;
            const attempt = () => {
                if (attempts >= 20) return;
                attempts += 1;
                void (async () => {
                    try {
                        await grid.scrollToColumnProp(target);
                    } catch {
                        // The element may not be upgraded yet — retried below.
                    }
                    window.setTimeout(() => {
                        if (landed()) return;
                        attempt();
                    }, 120);
                })();
            };
            attempt();
        },
        [columnInfoAt, displayFields],
    );

    // Structural scroll: open at the remembered centre row (the reload/filter
    // anchor), else at the tail of the working plan / the top of the archive.
    useEffect(() => {
        if (!scrollToPosition) return;
        // The row count arrives with the first scope read, but this effect also
        // runs while it is still 0 (the plugin's total lands a render later).
        // Jumping then would clamp the target to -1 -> row 0 and scroll the
        // grid to the FIRST row, and the follow-up real jump gets cancelled by
        // this effect's own cleanup - which is exactly why a reload opened the
        // top of the plan instead of the remembered row. Wait until the scope
        // is known.
        if (totalRows <= 0) return;
        const target = Math.max(0, Math.min(totalRows - 1, scrollToPosition.position - 1));
        // From here the viewport belongs to the jump, not to the user: a report
        // taken mid-flight would remember whatever row the grid happened to show
        // (on mount that is row 1, which is what clobbered the saved position).
        restorePendingRef.current = scrollToPosition.centered === true;
        return jumpToRow(target, scrollToPosition.centered ?? false);
    }, [scrollToPosition, totalRows, jumpToRow]);

    // The restore finished travelling: from now on the viewport row IS the
    // user's position and may be remembered again. Keyed on the jump itself —
    // a bare mount-time effect ran before the jump had started, left the flag
    // stuck ON and silenced every later report.
    useEffect(() => {
        if (!scrollToPosition || jumpInFlightRef.current > 0) return;
        const timer = window.setTimeout(() => {
            if (jumpInFlightRef.current === 0) restorePendingRef.current = false;
        }, 250);
        return () => window.clearTimeout(timer);
    }, [scrollToPosition]);

    /**
     * The row the user SELECTED — what the remembered position follows.
     *
     * A cell click in this grid fires `afterfocus` and nothing else, so the
     * SELECTION is the best statement of «this is my row». A selection in the
     * pinned rows (`rowPinStart` — the totals) is a miss: that is not a data
     * row, and reading it as one would store the totals' position.
     */
    const selectedPosition = useCallback(async (): Promise<number | null> => {
        const grid = gridRef.current;
        if (!grid) return null;
        const range = await grid.getSelectedRange?.();
        if (!range || range.rowType !== "rgRow") return null;
        const source = (await grid.getSource("rgRow")) as RenovationGridRow[];
        // Search the loaded window for the row whose index the selection names,
        // so a scrolled viewport does not shift the answer by a page.
        const row = source.find((item) => item?.__index === Number(range.y));
        return row && typeof row.__position === "number" ? row.__position : null;
    }, []);

    // Report the remembered position: the selected row when there is one,
    // otherwise the row in the middle of the data viewport. The plugin evicts
    // far chunks, so the centre slot itself may be a hole — walk outward to the
    // nearest loaded row (the window around the viewport is always there). Read
    // straight from the plugin's source: the workspace's mirror is a render
    // behind, so a lookup through it would miss.
    const reportCenterPosition = useCallback(async () => {
        const grid = gridRef.current;
        const total = totalRowsRef.current;
        if (!grid || total <= 0) return;
        // A SELECTION wins over the viewport centre: the user pointing at a cell
        // is the strongest statement of «this is my row».
        const selected = await selectedPosition();
        if (selected !== null) {
            onCenterPositionChangeRef.current?.(selected);
            return;
        }
        // Never report while a structural jump is still travelling. On mount the
        // viewport sits at the top and the remembered row is restored a moment
        // later; the early report wrote THAT top row over the remembered one
        // (100011 -> 12), so the reload always came back to the first rows.
        // The block is bounded by a deadline: a jump that dies for any reason
        // must not silence the saved position for the rest of the session.
        if (jumpInFlightRef.current > 0 && Date.now() < restoreDeadlineRef.current) return;
        const scroll = grid.querySelector("revogr-scroll-virtual.vertical") as HTMLElement | null;
        const scrollTop = scroll?.scrollTop ?? 0;
        // Nothing scrolled yet is not a position the user chose — on mount the
        // viewport sits at the top until the restore lands. Reporting row 1 from
        // there overwrote the remembered row on every reload.
        if (scrollTop <= 0 && restorePendingRef.current) return;
        const dataHeight = Math.max(0, grid.clientHeight - summaryHeightRef.current);
        // The remembered row is read from the VIEWPORT CENTRE, which is exactly
        // the inverse of the centred jump used to restore it. Reading the top row
        // instead desynchronised the two: a restored position that could not be
        // centred (near the last rows the scroller stops early) sat at the BOTTOM
        // edge, so the top row was not the saved one — and the position drifted
        // on every open until it reached row 1.
        const centerIndex = Math.max(
            0,
            Math.min(total - 1, Math.round((scrollTop + dataHeight / 2) / rowHeight)),
        );
        const source = (await grid.getSource("rgRow")) as RenovationGridRow[];
        let row = source[centerIndex];
        for (let step = 1; (!row || row.__id === undefined) && step <= 30; step += 1) {
            row = source[centerIndex - step] ?? source[centerIndex + step];
        }
        if (!row || row.__id === undefined) return;
        onCenterPositionChangeRef.current?.(row.__position);
    }, [rowHeight]);

    // Report the rows the plugin holds. Fires on every chunk load/eviction so
    // the workspace can mirror them (and patch them in place later). Each row
    // carries its source index, which is what the patch targets.
    const reportLoadedRows = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return 0;
        const source = (await grid.getSource("rgRow")) as RenovationGridRow[];
        const rows: RenovationGridRow[] = [];
        for (let index = 0; index < source.length; index += 1) {
            const row = source[index];
            // Unloaded slots are empty placeholders — they are not rows yet.
            if (!row || row.__id === undefined) continue;
            row.__index = index;
            rows.push(row);
        }
        onLoadedRowsChangeRef.current?.(rows);
        return rows.length;
    }, []);

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        let timer: number | undefined;
        // The plugin loads/evicts chunks by patching its store directly and
        // does NOT emit `aftersourceset` for them — only `scrollchange` (when it
        // starts a load). The report is therefore debounced off `scrollchange`,
        // but the FIRST chunk arrives with no event at all: the mount-time
        // report runs while the source is still empty, and nothing re-triggers
        // it until the user scrolls. That left the workspace mirror empty
        // (`loadedRows` = []), which silently broke the in-place patch path —
        // paste/fill validation (`rowsByIndex`), undo/redo live updates and the
        // selection labels — because every consumer of `loadedRows` saw no
        // rows. So the report RETRIES until the source actually has rows (a
        // short backoff, capped), then settles.
        let retries = 0;
        const reportAndRetry = () => {
            void reportLoadedRows().then((count) => {
                // Loaded rows are in: nothing more to chase until the next event.
                if (count > 0) return;
                // Source still empty (the plugin has not landed its first chunk):
                // try again shortly. The retry is bounded so a genuinely empty
                // scope does not spin forever.
                if (retries >= 40) return;
                retries += 1;
                timer = window.setTimeout(reportAndRetry, 150);
            });
        };
        const schedule = () => {
            retries = 0;
            if (timer !== undefined) window.clearTimeout(timer);
            timer = window.setTimeout(reportAndRetry, 300);
        };
        // The remembered row follows a SELECTION, not a scroll. Scrolling only
        // moves rows under the viewport: writing `localStorage` on every stop
        // (`localStorage` is a SYNCHRONOUS disk write, and a smooth scroll makes
        // many stops) bought nothing the user asked for, and it drifted whenever
        // a stop happened mid-flight. A click is deliberate, rare, and means «this
        // is the row I am working with».
        const onSelect = () => {
            void reportCenterPosition();
        };
        grid.addEventListener("aftersourceset", schedule);
        grid.addEventListener("scrollchange", schedule);
        // `afterfocus` is what RevoGrid actually fires when a CELL is clicked in
        // this grid — `cellclick`/`rowselected` never arrive, so subscribing to
        // them alone meant the position was never written.
        grid.addEventListener("afterfocus", onSelect);
        reportAndRetry();
        return () => {
            grid.removeEventListener("aftersourceset", schedule);
            grid.removeEventListener("scrollchange", schedule);
            grid.removeEventListener("afterfocus", onSelect);
            if (timer !== undefined) window.clearTimeout(timer);
        };
    }, [reportCenterPosition, reportLoadedRows, scopeKey]);

    function normalizeValue(value: unknown, field: RenovationField): NormalizeResult {
        if (field.cellType === "number") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const parsed = parseNumericText(value);
            if (parsed === null || !Number.isFinite(parsed)) {
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

    /**
     * Field rules from the source workbook that need the whole row: the local
     * ones (minimum/integer/percentage/allowed_values) are also enforced by the
     * backend, but checking them here gives immediate feedback without a round
     * trip; the cross-field ones (field_compare, calendar_year) are frontend-only.
     * Returns an error message, or null when the value is valid.
     */
    function checkFieldRules(
        field: RenovationField,
        value: unknown,
        row: RenovationGridRow,
    ): string | null {
        if (value === null || value === undefined || String(value).trim() === "") return null;
        const rules = field.rules ?? [];
        for (const rule of rules) {
            if (rule.kind === "minimum" && rule.value !== undefined) {
                const numeric = Number(value);
                if (Number.isFinite(numeric) && numeric < Number(rule.value)) {
                    return `Значение в ячейке «${field.label}» не может быть отрицательным.`;
                }
            } else if (rule.kind === "integer") {
                if (!Number.isInteger(Number(value))) {
                    return `Значение в ячейке «${field.label}» должно быть целым числом.`;
                }
            } else if (rule.kind === "percentage") {
                const numeric = Number(value);
                const minimum = rule.minimum ?? 0;
                const maximum = rule.maximum ?? 100;
                if (Number.isFinite(numeric) && (numeric < minimum || numeric > maximum)) {
                    return `Значение в ячейке «${field.label}» должно быть от ${minimum} до ${maximum}%.`;
                }
            } else if (rule.kind === "allowed_values" && rule.values) {
                if (!rule.values.includes(String(value).trim())) {
                    return `Значение в ячейке «${field.label}» не входит в список допустимых.`;
                }
            } else if (rule.kind === "field_compare" && rule.field && rule.operator === "gte") {
                // e.g. «Дата окончания» ≥ «Дата начала»; ISO date strings compare
                // lexicographically, and a missing counterpart skips the check.
                const other = row[rule.field];
                const otherText = other === null || other === undefined ? "" : String(other);
                if (otherText && String(value) < otherText) {
                    return `Значение в ячейке «${field.label}» не должно быть меньше «${otherText}».`;
                }
            }
        }
        return null;
    }

    // Stable identity: the selection effect below calls this while computing
    // the paste-button state — an inline function would re-run that effect
    // (and re-render the workspace) on every render.
    const cellAllowed = useCallback(
        (field: RenovationField, row: RenovationGridRow): string | null => {
            // null — allowed; a string is the skip reason.
            if (readOnly) return "таблица открыта только для просмотра";
            if (field.isCalculated) return "расчётное поле";
            if (field.isReadOnly) return "поле доступно только для чтения";
            if (!editableFieldKeys.has(field.dbName)) return "поле недоступно для редактирования";
            if (row.__rowState === "deleted") return "строка удалена";
            if (userAssetIds && (row.__assetId == null || !userAssetIds.has(row.__assetId)))
                return "нет доступа к строке этого актива";
            return null;
        },
        [editableFieldKeys, readOnly, userAssetIds],
    );

    function beforeEditStart(event: CustomEvent<BeforeSaveDataDetails>) {
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
        const ruleError = checkFieldRules(field, normalized.value, row);
        if (ruleError) {
            event.preventDefault();
            onValidationError(ruleError);
            return;
        }
        event.detail.val = normalized.value;
    }

    function beforeRangeEdit(event: CustomEvent<BeforeRangeSaveDataDetails>) {
        const patches = event.detail.data as Record<string, RenovationGridRow>;
        const models = event.detail.models as Record<string, RenovationGridRow | undefined>;
        const skipped = new Map<string, string>();
        for (const [rowIndex, patch] of Object.entries(patches)) {
            const currentRow = models[Number(rowIndex)] ?? rowsByIndex.get(Number(rowIndex));
            for (const prop of Object.keys(patch)) {
                const field = fieldByDbName.get(String(prop));
                if (!field) {
                    delete patch[prop];
                    continue;
                }
                if (!currentRow) {
                    delete patch[prop];
                    skipped.set(field.label, "строка недоступна");
                    continue;
                }
                const reason = cellAllowed(field, currentRow);
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
                const ruleError = checkFieldRules(field, normalized.value, currentRow);
                if (ruleError) {
                    event.preventDefault();
                    onValidationError(ruleError);
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
    } {
        const updates: CellUpdate[] = [];
        // Same revo-grid 4.27 shape check as grid-edit.ts extractEdits: plain
        // single-cell edits carry `data` (a source snapshot array) too, so the
        // range branch must key off the keyed-patch + no-prop shape.
        const isRangeSave =
            "data" in detail &&
            !Array.isArray((detail as { data?: unknown }).data) &&
            (detail as { prop?: unknown }).prop === undefined;
        if (isRangeSave) {
            const models = (detail as { models?: RenovationGridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                detail.data as Record<string, Record<string, unknown>>,
            )) {
                const previous = models[Number(rowIndex)] as RenovationGridRow | undefined;
                const row = (previous ?? patch) as RenovationGridRow;
                if (!row?.__id || row.__rowState === "deleted") continue;
                const known = rowValuesRef.current.get(row.__id);
                for (const [field, value] of Object.entries(patch)) {
                    if (!editableFieldKeys.has(field)) continue;
                    if (known && known[field] === value) continue;
                    updates.push({ rowId: row.__id, field, value: toCellValue(value) });
                }
            }
        } else {
            const field = String(detail.prop);
            const row = detail.model as RenovationGridRow;
            if (!row?.__id || row.__rowState === "deleted") {
                return { updates };
            }
            if (editableFieldKeys.has(field)) {
                updates.push({ rowId: row.__id, field, value: toCellValue(detail.val) });
            }
        }
        return { updates: deduplicateUpdates(updates) };
    }

    function afterEdit(event: CustomEvent<BeforeSaveDataDetails | Record<string, unknown>>) {
        const { updates } = extractEdits(event.detail);
        const releaseKeys: string[] = [];
        if ("data" in event.detail) {
            const models = (event.detail as { models?: RenovationGridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                event.detail.data as Record<string, Record<string, unknown>>,
            )) {
                const row = (models[Number(rowIndex)] ?? patch) as RenovationGridRow;
                if (!row?.__id || row.__rowState === "deleted") continue;
                for (const field of Object.keys(patch)) {
                    if (editableFieldKeys.has(field)) {
                        releaseKeys.push(canonicalRenovationKey(row.__id, field));
                    }
                }
            }
        } else {
            const row = event.detail.model as RenovationGridRow;
            const key =
                row && row.__id
                    ? canonicalRenovationKey(row.__id, String(event.detail.prop))
                    : null;
            if (key) releaseKeys.push(key);
        }
        void (async () => {
            try {
                if (updates.length > 0) {
                    const ok = await onCommit(updates);
                    if (!ok) onCommitFailed();
                }
            } finally {
                activeLeaseRef.current = null;
                onReleaseLease(releaseKeys);
            }
        })();
    }

    // Rows the plugin currently holds, in grid order, holes skipped. Copy/
    // paste and row-id reporting run on this (the workspace keeps the same
    // objects, so a patch reaches them without a re-render).
    const getLoadedRows = useCallback(async (): Promise<RenovationGridRow[]> => {
        const grid = gridRef.current;
        if (!grid) return [];
        const source = (await grid.getSource("rgRow")) as RenovationGridRow[];
        return source.filter((row) => row && row.__id !== undefined);
    }, []);

    const getVisibleRowIds = useCallback(async (): Promise<string[]> => {
        const visible = await getLoadedRows();
        return visible.filter((row) => row.__rowState !== "deleted").map((row) => row.__id);
    }, [getLoadedRows]);

    // The visible number is the sequential ordinal in the filtered scope
    // (1-based); the grid index is that number minus one, and the plugin maps
    // it to the owning chunk (loading it on demand). The shared jump helper
    // retries until the viewport actually lands.
    const scrollToPlanPosition = useCallback(
        (position: number): Promise<void> => {
            jumpToRow(Math.max(0, position - 1));
            return Promise.resolve();
        },
        [jumpToRow],
    );

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
        const prev = lastRangeRef.current;
        if (
            prev === range ||
            (prev !== null &&
                range !== null &&
                prev.x === range.x &&
                prev.y === range.y &&
                prev.x1 === range.x1 &&
                prev.y1 === range.y1)
        ) {
            return;
        }
        lastRangeRef.current = range;
        setSelectionRange(range);
    }, []);

    const buildCopyText = useCallback(async (): Promise<string | null> => {
        const range = selectionRangeRef.current;
        if (!range) return null;
        const lines: string[] = [];
        const lastRow = Math.min(range.y1, totalRows - 1);
        for (let y = range.y; y <= lastRow; y += 1) {
            const row = rowsByIndex.get(y);
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
    }, [columns, rowsByIndex, totalRows]);

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
            // buffer — the parser trims them, otherwise an extra empty cell
            // below overwrites a neighbor nobody asked for (landmine 4).
            const parsed = parsePastedText(text);
            if (parsed.length === 0) return;
            const range = selectionRangeRef.current;
            if (!range) return;

            const pasteHeight = parsed.length;
            const pasteWidth = Math.max(0, ...parsed.map((row) => row.length));
            const selectedHeight = range.y1 - range.y + 1;
            const selectedWidth = range.x1 - range.x + 1;
            const singleCell = selectedHeight === 1 && selectedWidth === 1;

            let toY = range.y;
            let toY1 = range.y1;
            let toX = range.x;
            let toX1 = range.x1;
            if (singleCell) {
                toY1 = Math.min(totalRows - 1, range.y + pasteHeight - 1);
                toX1 = Math.min(columns.length - 1, range.x + pasteWidth - 1);
            } else if (pasteHeight > selectedHeight || pasteWidth > selectedWidth) {
                onPasteSkipped("Буфер шире или выше выделения — вставлена только его часть.");
            }

            const data: Record<string, Record<string, unknown>> = {};
            const models: RenovationGridRow[] = [];
            for (let y = toY; y <= toY1; y += 1) {
                const row = rowsByIndex.get(y);
                if (!row) continue;
                models[y] = row;
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
        [applyGridRange, columns, onPasteSkipped, rowsByIndex, totalRows],
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
            // The current role cannot edit the plan (e.g. the approver): leave
            // the event alone so nothing is inserted.
            if (!canEdit) return;
            // isFromTextEditor sees through the shadow DOM (revo-grid's built-in
            // cell editors), so pasting into an open editor stays native.
            if (isFromTextEditor(event)) return;
            const grid = gridRef.current;
            if (!grid) return;
            // Same as the raw grid: a cell click can leave the focus on body,
            // so accept the paste while this grid holds an active selection.
            if (!isGridTarget(event, grid) && selectionRangeRef.current === null) return;
            const text = event.clipboardData?.getData("text/plain");
            if (text == null || text === "") return;
            event.preventDefault();
            event.stopPropagation();
            void pasteIntoSelection(text);
        };
        const handleSetRange = (event: Event) => {
            updateSelectionRange((event as CustomEvent<RangeArea & { type?: string }>).detail);
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
            // Only clear the row selection if there actually is one: assigning
            // a fresh [] on every focuscell (which revo-grid fires continuously
            // during a drag) changed the selectedRowIds reference, rebuilt the
            // 216-column memo and re-rendered the whole grid each tick — the
            // drag stopped tracking the cursor and scrolling janked.
            setSelectedRowIds((prev) => (prev.length === 0 ? prev : []));
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
    }, [canEdit, copySelection, onReleaseLease, pasteIntoSelection, updateSelectionRange]);

    // Keyboard shortcuts (event.code — works in any keyboard layout).
    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !document.fullscreenElement) {
                // Window capture precedes the select's portal handlers. Leave
                // this Escape to the popover, preserving the current range.
                if (hasOpenSmartSelect()) return;
                // A dialog owns Escape while it is open: the overlay lives in
                // document.body, so this window handler would otherwise clear
                // the grid selection underneath it (the open dialog then reads
                // «Удалить записи (0)»).
                if (hasOpenModal()) return;
                if (
                    event.target instanceof Element &&
                    event.target.closest("[data-grid-keyboard-overlay]")
                )
                    return;
                if (isFromTextEditor(event)) return;
                const grid = gridRef.current;
                if (!grid) return;
                // Escape is only swallowed when it can mean "clear the grid
                // selection": the target is the grid itself OR the grid still
                // holds a live range (a cell click can leave the focus on body
                // after revo-grid's re-render — requiring a grid target made Esc
                // dead in exactly that case). With the target outside and no
                // range, the key belongs to whoever has focus (menus, dialogs).
                const hasRange =
                    selectionRangeRef.current !== null || lastRangeRef.current !== null;
                if (!isGridTarget(event, grid) && !hasRange) return;
                // Swallow the event so revo-grid's built-in Escape (which
                // collapses the range to a single cell) never runs: the row
                // selection must clear completely, not shrink to one cell.
                event.preventDefault();
                event.stopImmediatePropagation();
                clearGridSelection();
                setSelectedRowIds([]);
                rowSelectionAnchorRef.current = null;
                // Force a columns rebuild: the selection overlay does not
                // reliably repaint when only the store changes, and a rebuild
                // is safe here because the store was already cleared above.
                setSelectionEpoch((epoch) => epoch + 1);
                return;
            }
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
            // Ctrl+V is intentionally NOT intercepted: the native paste event
            // (the window "paste" listener above) carries the text without the
            // clipboard-read confirmation prompt Chrome shows for
            // navigator.clipboard.readText().
            if (event.code !== "KeyC" && event.code !== "KeyA") return;
            const grid = gridRef.current;
            if (!grid) return;
            if (isFromTextEditor(event)) return;
            if (event.code === "KeyA" && isChunkedSection("renovationPlan")) {
                // Select-all is disabled on the plan: the full form holds
                // ~94k rows and a whole-form copy would overflow the
                // clipboard. Swallow the event so revo-grid's built-in
                // select-all (which selects headers and tears the frame)
                // never runs either.
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            // Copy still needs a grid target or a tracked selection. Select-all
            // above must also be swallowed after programmatic focus, which can
            // leave body active before React observes the grid's new range.
            if (!isGridTarget(event, grid) && selectionRangeRef.current === null) return;
            event.preventDefault();
            void copySelection();
        };
        /**
         * A click anywhere OUTSIDE the grid drops the selection.
         *
         * revo-grid clears its own focus rectangle when the pointer leaves for
         * the page (which is why `Del` stops working), but it emits NOTHING for
         * it вЂ” so the footer status and the collaboration presence kept
         * advertising a selection that no longer existed.
         *
         * Capture phase, so it runs before the click's own handlers. A click
         * inside the shell is ignored (no clear-then-reselect flicker), and so
         * is one on a PORTAL the grid opens вЂ” the popovers and dialogs React
         * renders into `document.body` вЂ” or editing would drop the range being
         * edited.
         */
        const handleOutsidePointerDown = (event: PointerEvent) => {
            const target = event.target;
            if (!(target instanceof Element)) return;
            if (
                target.closest(
                    '[data-slot="popover-dialog"], [role="dialog"], [role="alertdialog"]',
                )
            )
                return;
            const gridElement = gridRef.current;
            if (!gridElement) return;
            const shell = gridElement.closest(".revo-grid-shell") ?? gridElement;
            if (shell.contains(target)) return;
            clearGridSelection();
        };
        window.addEventListener("keydown", handleKeyDown, true);
        document.addEventListener("pointerdown", handleOutsidePointerDown, true);
        return () => {
            window.removeEventListener("keydown", handleKeyDown, true);
            document.removeEventListener("pointerdown", handleOutsidePointerDown, true);
        };
    }, [clearGridSelection, copySelection]);

    useEffect(() => {
        selectionRangeRef.current = selectionRange;
        // The paste button mirrors the raw-materials grid: enabled only when
        // the selection contains at least one editable, non-busy target cell.
        // beforeRangeEdit still re-checks and skips busy cells at paste time.
        const canPasteInto = (): boolean => {
            if (!canEdit || !selectionRange || readOnly) return false;
            const lastRow = Math.min(selectionRange.y1, totalRows - 1);
            for (let y = selectionRange.y; y <= lastRow; y += 1) {
                const row = rowsByIndex.get(y);
                if (!row) continue;
                for (let x = selectionRange.x; x <= selectionRange.x1; x += 1) {
                    const column = columns[x] as ColumnRegular | undefined;
                    if (!column || column.prop === undefined) continue;
                    const field = fieldByDbName.get(String(column.prop));
                    if (!field || cellAllowed(field, row)) continue;
                    const key = canonicalRenovationKey(row.__id, field.dbName);
                    if (remoteLocks.has(key) || remotePresence.has(key)) continue;
                    return true;
                }
            }
            return false;
        };
        onClipboardStateChange({ canPaste: canPasteInto() });
        if (selectedRowIds.length > 0) {
            // Row-number selection wins over the cell range (row clicks clear
            // the range): report the rows for row-level actions like delete.
            // Numbers are the sequential filtered ordinals (index + 1).
            const numbers = loadedRows
                .filter((row) => row.__index !== undefined && selectedRowIdSet.has(row.__id))
                .map((row) => row.__index! + 1)
                .sort((a, b) => a - b);
            if (numbers.length === 0) {
                onSelectionChange(null);
                return;
            }
            const first = numbers[0]!;
            const last = numbers[numbers.length - 1]!;
            const label = first === last ? `строка ${first}` : `строки ${first}–${last}`;
            onSelectionChange({
                label,
                detail: null,
                cellKeys: [],
                rowId:
                    loadedRows.find(
                        (row) =>
                            row.__index !== undefined &&
                            row.__index + 1 === last &&
                            selectedRowIdSet.has(row.__id),
                    )?.__id ?? null,
            });
            return;
        }
        if (!selectionRange) {
            onSelectionChange(null);
            return;
        }
        const rangeRows: RenovationGridRow[] = [];
        const lastRow = Math.min(selectionRange.y1, totalRows - 1);
        for (let y = selectionRange.y; y <= lastRow; y += 1) {
            const row = rowsByIndex.get(y);
            if (row) rangeRows.push(row);
        }
        const firstRow = rangeRows[0];
        // The footer names the selection by COLUMN NUMBER (not the header text):
        // «столбец 2, строка 3». The full header chain of a single-column
        // selection travels as `detail` — the status tooltip spells it out with
        // " → " (the column-number tooltip shows the same chain with "↓").
        const firstColumnInfo = columnInfoAt(selectionRange.x);
        const lastColumnInfo = columnInfoAt(selectionRange.x1);
        let columnText = "";
        if (firstColumnInfo) {
            if (selectionRange.x === selectionRange.x1) {
                columnText =
                    firstColumnInfo.number === null
                        ? `столбец «${firstColumnInfo.label}»`
                        : `столбец ${firstColumnInfo.number}`;
            } else if (lastColumnInfo) {
                const from = firstColumnInfo.number ?? `«${firstColumnInfo.label}»`;
                const to = lastColumnInfo.number ?? `«${lastColumnInfo.label}»`;
                columnText = `столбцы ${from}–${to}`;
            }
        }
        // The numbers are the sequential ordinals of the filtered scope — the
        // range bounds are the grid row indexes (+1), so even a range over a
        // not-yet-loaded chunk is labelled with the numbers the user sees.
        const rowText =
            selectionRange.y === selectionRange.y1
                ? `строка ${selectionRange.y + 1}`
                : `строки ${selectionRange.y + 1}–${selectionRange.y1 + 1}`;
        const label = [columnText, rowText].filter(Boolean).join(", ") || null;
        const chain =
            selectionRange.x === selectionRange.x1 && firstColumnInfo?.prop
                ? headerChainByProp.get(firstColumnInfo.prop)
                : undefined;
        const detail = selectionDetailForColumn(chain ?? [], rowText, firstColumnInfo?.number);
        const cellKeys: string[] = [];
        for (let y = selectionRange.y; y <= lastRow; y += 1) {
            const row = rowsByIndex.get(y);
            if (!row) continue;
            for (let x = selectionRange.x; x <= selectionRange.x1; x += 1) {
                const column = columns[x] as ColumnRegular | undefined;
                if (!column || column.prop === undefined) continue;
                cellKeys.push(canonicalRenovationKey(row.__id, String(column.prop)));
            }
        }
        onSelectionChange({
            label,
            detail,
            cellKeys,
            rowId: firstRow?.__id ?? null,
        });
    }, [
        canEdit,
        cellAllowed,
        columnInfoAt,
        columns,
        fieldByDbName,
        headerChainByProp,
        loadedRows,
        onClipboardStateChange,
        onSelectionChange,
        readOnly,
        remoteLocks,
        remotePresence,
        rowsByIndex,
        selectedRowIdSet,
        selectedRowIds,
        selectionRange,
        totalRows,
    ]);

    // Resize: pointer drag on the boundary between visible columns. The
    // overlay geometry updates live; the grid columns rebuild on drop.
    const startResize = useCallback(
        (event: React.PointerEvent, dbName: string) => {
            event.preventDefault();
            // Fall back to the rendered width when the column was never
            // resized (a fresh column has no persisted width) — otherwise
            // the drag would start from undefined and do nothing.
            const field = fieldByDbName.get(dbName);
            const current =
                dbName === ASSET_COLUMN_PROP ? assetWidth : field ? widthOf(field) : undefined;
            const startWidth = widths.get(dbName) ?? current;
            if (startWidth === undefined) return;
            dragRef.current = { dbName, startX: event.clientX, startWidth };
            event.currentTarget.setPointerCapture(event.pointerId);
            setResizingField(dbName);
        },
        [assetWidth, fieldByDbName, widthOf, widths],
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
    // Mirror of the live preview widths: `endResize` also runs from the
    // window-level pointerup fallback, where a stale closure would persist an
    // outdated width.
    const overlayWidthsRef = useRef(overlayWidths);
    overlayWidthsRef.current = overlayWidths;
    const endResize = useCallback(() => {
        const drag = dragRef.current;
        if (!drag) return;
        dragRef.current = null;
        const width = clampWidth(overlayWidthsRef.current.get(drag.dbName) ?? drag.startWidth);
        setWidths((current) => {
            if (current.get(drag.dbName) === width) return current;
            const result = new Map(current);
            result.set(drag.dbName, width);
            return result;
        });
        setOverlayWidths(new Map());
        setResizingField(null);
        window.localStorage.setItem(WIDTH_STORAGE_PREFIX + drag.dbName, String(width));
    }, []);

    // Header level heights: pointer drag on the boundary under a level. The
    // overlay geometry follows live (the grid columns don't depend on it, so
    // nothing rebuilds in revo-grid); the array is persisted on drop.
    const startLevelResize = useCallback((event: React.PointerEvent, level: number) => {
        event.preventDefault();
        levelDragRef.current = {
            level,
            startY: event.clientY,
            startHeight: levelHeightsRef.current[level] ?? HEADER_LEVEL_HEIGHT,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
        setResizingLevel(level);
    }, []);
    const moveLevelResize = useCallback((event: React.PointerEvent) => {
        const drag = levelDragRef.current;
        if (!drag) return;
        const next = clampLevelHeight(drag.startHeight + (event.clientY - drag.startY));
        setLevelHeights((current) => {
            if (current[drag.level] === next) return current;
            const result = [...current];
            result[drag.level] = next;
            return result;
        });
    }, []);
    const endLevelResize = useCallback(() => {
        if (!levelDragRef.current) return;
        levelDragRef.current = null;
        setResizingLevel(null);
        window.localStorage.setItem(
            HEADER_LEVEL_HEIGHT_STORAGE_KEY,
            JSON.stringify(levelHeightsRef.current),
        );
    }, []);

    const resetWidths = useCallback(() => {
        window.localStorage.removeItem(WIDTH_STORAGE_PREFIX + ASSET_COLUMN_PROP);
        for (const field of fields) {
            window.localStorage.removeItem(WIDTH_STORAGE_PREFIX + field.dbName);
        }
        window.localStorage.removeItem(HEADER_LEVEL_HEIGHT_STORAGE_KEY);
        setWidths(new Map());
        setOverlayWidths(new Map());
        setLevelHeights(defaultLevelHeights());
    }, [fields]);

    // Fallback for a drag whose pointerup never reaches the handle (the
    // capture was lost, the pointer was released outside the window): commit
    // and persist it anyway — the user sees the column resized, so a reload
    // must keep the new size.
    useEffect(() => {
        const finish = () => {
            endResize();
            endLevelResize();
        };
        window.addEventListener("pointerup", finish, true);
        window.addEventListener("pointercancel", finish, true);
        return () => {
            window.removeEventListener("pointerup", finish, true);
            window.removeEventListener("pointercancel", finish, true);
        };
    }, [endLevelResize, endResize]);

    useImperativeHandle(
        forwardedRef,
        () => ({
            copySelection,
            clearSelection: clearGridSelection,
            pasteIntoSelection: () => void pasteIntoSelection(),
            getVisibleRowIds,
            getLoadedRows,
            scrollToPosition: scrollToPlanPosition,
            scrollToColumn: scrollToPlanColumn,
            resetColumnWidths: resetWidths,
        }),
        [
            clearGridSelection,
            copySelection,
            getLoadedRows,
            getVisibleRowIds,
            pasteIntoSelection,
            resetWidths,
            scrollToPlanColumn,
            scrollToPlanPosition,
        ],
    );

    /**
     * Column-resize handles: one per visible boundary between columns, clipped
     * to the y-bands where the boundary LINE is actually drawn. The header
     * cells tile the levels, so a boundary inside a merged (spanned) cell has
     * no line — the handle must not react to the pointer there (the reported
     * "bars appear over merged cells" glitch). Below the levels the number
     * strip and the totals row draw every boundary, so that band is always
     * included — the handle reaches the BOTTOM of the aggregating row.
     */
    const handles = useMemo(() => {
        const edges = new Map<number, HandleBand[]>();
        for (const cell of overlayCells) {
            const key = Math.round((cell.left + cell.width) * 2);
            const bands = edges.get(key);
            if (bands) bands.push({ from: cell.top, to: cell.top + cell.height });
            else edges.set(key, [{ from: cell.top, to: cell.top + cell.height }]);
        }
        const handleHeight = overlayHeight + summaryHeight;
        const result: Array<{
            key: string;
            dbName: string;
            left: number;
            height: number;
            clipPath: string | null;
        }> = [];
        const push = (dbName: string, boundary: number) => {
            const bands = mergeBands([
                ...(edges.get(Math.round(boundary * 2)) ?? []),
                { from: headerHeight, to: handleHeight },
            ]);
            result.push({
                key: dbName,
                dbName,
                left: boundary - 3,
                height: handleHeight,
                clipPath: handleClipPath(bands, handleHeight, true),
            });
        };
        push(ASSET_COLUMN_PROP, ROW_HEADER_WIDTH + assetWidth);
        for (let i = 0; i < displayFields.length - 1; i += 1) {
            const field = displayFields[i];
            if (!field) continue;
            if (SERVICE_DB_NAMES.has(field.dbName)) continue;
            push(field.dbName, (geometry.offsets.get(field.dbName) ?? 0) + fieldWidth(field));
        }
        return result;
    }, [
        assetWidth,
        displayFields,
        fieldWidth,
        geometry.offsets,
        headerHeight,
        overlayCells,
        overlayHeight,
        summaryHeight,
    ]);

    /**
     * Level-height handles: one per level boundary — dragging a boundary
     * changes the height of the level above it (the Excel row-height gesture) —
     * clipped to the x-bands where some header cell actually ends at that
     * boundary. A vertically merged (rowSpan) cell has no line inside it, so
     * the bar must not appear there. A boundary with no visible band at all is
     * skipped: there is nothing to grab.
     */
    const levelHandles = useMemo(() => {
        const edges = new Map<number, HandleBand[]>();
        for (const cell of overlayCells) {
            const key = Math.round((cell.top + cell.height) * 2);
            const bands = edges.get(key);
            if (bands) bands.push({ from: cell.left, to: cell.left + cell.width });
            else edges.set(key, [{ from: cell.left, to: cell.left + cell.width }]);
        }
        const result: Array<{ level: number; top: number; clipPath: string | null }> = [];
        for (let level = 0; level < HEADER_LEVEL_COUNT; level += 1) {
            const top = levelOffsets[level + 1];
            if (top === undefined) continue;
            const bands = mergeBands(edges.get(Math.round(top * 2)) ?? []);
            if (bands.length === 0) continue;
            result.push({
                level,
                top,
                clipPath: handleClipPath(bands, geometry.totalWidth, false),
            });
        }
        return result;
    }, [geometry.totalWidth, levelOffsets, overlayCells]);

    // The totals row is a revo-grid pinned top row (like the assets dictionary).
    // The overlay carries the 11 header levels plus the column-number strip
    // directly above the pinned totals row.
    const pinnedSummaryRow = useMemo<RenovationGridRow[] | undefined>(
        () =>
            summaryValues
                ? [
                      {
                          ...summaryValues,
                          __id: SUMMARY_ROW_ID,
                          __position: 0,
                          __assetId: null,
                          __assetName: "",
                          __renovationId: null,
                          __rowState: "unchanged",
                          __changedFields: [],
                          __missingComponents: [],
                          __invalidFields: [],
                      } as RenovationGridRow,
                  ]
                : undefined,
        [summaryValues],
    );

    // The vertical scrollbar's lane: the width it takes away from the data
    // area at the grid's right edge. The custom horizontal strip must end
    // BEFORE it (the bars may not overlap in the bottom-right corner).
    const [verticalScrollbarLane, setVerticalScrollbarLane] = useState(0);
    const verticalScrollbarLaneRef = useRef(0);
    const measureVerticalScrollbarLane = useCallback(() => {
        const strip = gridRef.current?.querySelector(
            "revogr-scroll-virtual.vertical",
        ) as HTMLElement | null;
        // The library writes the space it reserves into the strip's inline
        // min-width (`componentDidRender`): the measured system scrollbar size
        // while the rows overflow, 0 otherwise. Overlay-scrollbar platforms
        // report 0 — there the bar paints over the grid instead of taking a
        // lane, and the height rule keeps it above the strip on its own.
        const reserved = strip ? Number.parseFloat(strip.style.minWidth) : 0;
        const lane = Number.isFinite(reserved) && reserved > 0 ? Math.round(reserved) : 0;
        if (lane === verticalScrollbarLaneRef.current) return;
        verticalScrollbarLaneRef.current = lane;
        setVerticalScrollbarLane(lane);
    }, []);

    // The grid always fills the shell and scrolls through its OWN native strips
    // (vertical + horizontal). The header overlay is a flex sibling whose
    // margin-top animates to zero height when «Закрепить заголовок» is off — the
    // grid resizes in place (revo-grid re-measures) instead of the old external
    // 11M-px scroller that froze the renderer with the Infinity Scroll source.
    const refreshHorizontalScrollbar = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        measureVerticalScrollbarLane();
        const size = await grid.getContentSize();
        const width = size.x + ROW_HEADER_WIDTH;
        // revo-grid fires contentsizechanged on every virtualized scroll tick;
        // re-setting the same width re-rendered the grid, which rebuilt the
        // 216-column memo and re-fired contentsizechanged — an endless loop
        // that froze the drag cursor and janked horizontal scrolling. Only
        // commit state when the width actually changed.
        if (width === horizontalContentWidthRef.current) return;
        horizontalContentWidthRef.current = width;
        setHorizontalContentWidth(width);
        const shell = shellRef.current;
        setHasHorizontalOverflow(width > (shell?.clientWidth ?? 0));
    }, [measureVerticalScrollbarLane]);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            void refreshHorizontalScrollbar();
        });
        const observer = new ResizeObserver(() => {
            void refreshHorizontalScrollbar();
        });
        const grid = gridRef.current;
        if (grid) {
            observer.observe(grid);
            // The vertical strip appears/disappears with the rows' overflow —
            // its reserved lane changes the horizontal strip's right edge.
            const verticalStrip = grid.querySelector("revogr-scroll-virtual.vertical");
            if (verticalStrip) observer.observe(verticalStrip);
        }
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [columns, headerPinned, refreshHorizontalScrollbar]);

    // Wheel over the external horizontal scrollbar strip scrolls it sideways
    // (native Chrome behavior scrolls the vertical axis instead). Native
    // listener — React's onWheel is passive and can't preventDefault.
    useEffect(() => {
        const shell = shellRef.current;
        if (!shell) return;
        const handleWheel = (event: WheelEvent) => {
            if (event.deltaY === 0 && event.deltaX === 0) return;
            const target = event.target as Node | null;
            if (!target) return;
            const external = horizontalScrollRef.current;
            const strip = external?.contains(target) ? external : null;
            if (!strip) return;
            if (strip.scrollWidth <= strip.clientWidth) return;
            event.preventDefault();
            strip.scrollLeft += event.deltaY + event.deltaX;
        };
        shell.addEventListener("wheel", handleWheel, { passive: false });
        return () => shell.removeEventListener("wheel", handleWheel);
    }, []);

    const headerOverlay = (
        <div className="renovation-grid__header" style={{ height: overlayHeight }}>
            {/* The «№» cell merges every cell below it — the number strip and the
                totals row — into itself: one box whose bottom line sits under the
                totals row (nothing to number, no internal borders). */}
            <div
                className="renovation-grid__corner"
                style={{
                    // Pinned: the corner merges the whole header (levels + the
                    // number strip) with the totals row below. Unpinned: the
                    // levels slide away, so the corner shrinks to the strip plus
                    // the totals row — the «№» label and its funnel stay visible
                    // right above the summary, beside the column numbers.
                    top: headerPinned ? 0 : headerHeight,
                    height: headerPinned
                        ? overlayHeight + summaryHeight
                        : columnNumberRowHeight + summaryHeight,
                }}
            >
                <span className="renovation-grid__corner-label">№</span>
                {onOpenRowFilter ? (
                    <button
                        type="button"
                        className="header-filter-button"
                        aria-label="Перейти к строке"
                        onPointerDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                        }}
                        onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            onOpenRowFilter(
                                (
                                    event.currentTarget.parentElement ?? event.currentTarget
                                ).getBoundingClientRect(),
                            );
                        }}
                    >
                        <span className="header-filter-button__icon" aria-hidden="true" />
                    </button>
                ) : null}
            </div>
            <div
                ref={headerBodyRef}
                className="renovation-grid__header-body"
                style={{
                    width: geometry.totalWidth,
                    height: overlayHeight,
                }}
            >
                {overlayCells.map((cell) => (
                    <div
                        key={cell.id}
                        className={[
                            "renovation-grid__header-cell",
                            cell.isTop ? "renovation-grid__header-cell--top" : "",
                            // A bottom-most cell sits directly above the column-number
                            // strip — drop its bottom line so the strip's own top line
                            // is the only border between them.
                            cell.top + cell.height >= headerHeight - 0.5
                                ? "renovation-grid__header-cell--bottom"
                                : "",
                        ]
                            .filter(Boolean)
                            .join(" ")}
                        style={{
                            left: cell.left,
                            top: cell.top,
                            width: cell.width,
                            height: cell.height,
                        }}
                    >
                        <span className="renovation-grid__header-label">{cell.text}</span>
                        {cell.filterColumn && onOpenColumnFilter ? (
                            <button
                                type="button"
                                className={
                                    activeFilterColumns?.has(cell.filterColumn)
                                        ? "header-filter-button is-active"
                                        : "header-filter-button"
                                }
                                aria-label={`Фильтр: ${cell.text}`}
                                aria-pressed={activeFilterColumns?.has(cell.filterColumn) ?? false}
                                onPointerDown={(event) => {
                                    // Keep the resize handle and the grid from
                                    // treating this as a header drag/click.
                                    event.preventDefault();
                                    event.stopPropagation();
                                }}
                                onClick={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    onOpenColumnFilter(
                                        cell.filterColumn as string,
                                        (
                                            event.currentTarget.parentElement ?? event.currentTarget
                                        ).getBoundingClientRect(),
                                    );
                                }}
                            >
                                {/* The same funnel as every other filter
                                    affordance in the app (iconoir), so the
                                    header button reads as a filter. */}
                                <span className="header-filter-button__icon" aria-hidden="true" />
                            </button>
                        ) : null}
                    </div>
                ))}
                {columnNumberCells.map((cell) => (
                    <div
                        key={cell.key}
                        className="renovation-grid__number-cell"
                        data-column={cell.text === "" ? undefined : cell.key}
                        style={{
                            left: cell.left,
                            top: headerHeight,
                            width: cell.width,
                            height: cell.height,
                        }}
                    >
                        <span className="renovation-grid__number-label">{cell.text}</span>
                        {/* With the header unpinned the level funnels are hidden
                            with it — the number cell carries the same funnel so
                            the column filter stays reachable. */}
                        {!headerPinned && cell.filterColumn && onOpenColumnFilter ? (
                            <button
                                type="button"
                                className={
                                    activeFilterColumns?.has(cell.filterColumn)
                                        ? "header-filter-button is-active"
                                        : "header-filter-button"
                                }
                                aria-label={`Фильтр: ${cell.label ?? cell.text}`}
                                aria-pressed={activeFilterColumns?.has(cell.filterColumn) ?? false}
                                onPointerDown={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                }}
                                onClick={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    onOpenColumnFilter(
                                        cell.filterColumn as string,
                                        (
                                            event.currentTarget.parentElement ?? event.currentTarget
                                        ).getBoundingClientRect(),
                                    );
                                }}
                            >
                                <span className="header-filter-button__icon" aria-hidden="true" />
                            </button>
                        ) : null}
                    </div>
                ))}
                {/* Level-height handles first: the vertical (column) handles
                    rendered after them win the 6×6 intersections, so a column
                    boundary stays grabbable where it crosses a level boundary.
                    Each handle is clipped to the bands where its line is
                    actually drawn (see `handleClipPath`). */}
                {levelHandles.map((handle) => (
                    <div
                        key={`level:${handle.level}`}
                        className={
                            resizingLevel === handle.level
                                ? "renovation-grid__level-resize-handle is-dragging"
                                : "renovation-grid__level-resize-handle"
                        }
                        style={{
                            top: handle.top,
                            clipPath: handle.clipPath ?? undefined,
                        }}
                        onPointerDown={(event) => startLevelResize(event, handle.level)}
                        onPointerMove={moveLevelResize}
                        onPointerUp={endLevelResize}
                        onPointerCancel={endLevelResize}
                    />
                ))}
                {handles.map((handle) => (
                    <div
                        key={handle.key}
                        className={
                            resizingField === handle.dbName
                                ? "renovation-grid__resize-handle is-dragging"
                                : "renovation-grid__resize-handle"
                        }
                        style={{
                            left: handle.left,
                            // The bar runs to the bottom of the totals row, like
                            // the boundary line itself (the header body ends at
                            // the strip — the explicit height overhangs it, the
                            // same way the «№» corner and the merged number
                            // cells do; the header's clip-path allows it).
                            height: handle.height,
                            bottom: "auto",
                            clipPath: handle.clipPath ?? undefined,
                        }}
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
                !hasHorizontalOverflow && "revo-grid-shell--no-hscroll",
            ]
                .filter(Boolean)
                .join(" ")}
            style={
                {
                    "--grid-header-height": `${overlayHeight}px`,
                    // The 11 header levels' height — what the unpinned slot
                    // slides away by (the number strip and the «№» corner stay).
                    "--renovation-header-levels-height": `${headerHeight}px`,
                    // The native vertical strip starts below the pinned summary
                    // row instead of covering it.
                    "--renovation-summary-height": `${summaryHeight}px`,
                } as CSSProperties
            }
            onPointerMove={(event) => {
                // The column-number tooltip first: it only exists while the
                // pointer is over a numbered strip cell. Switching cells FAST
                // (dragging along the strip) used to leave the previous tooltip
                // visible with the NEW content: react-aria keeps its own
                // open/anchor state, so moving the invisible trigger and
                // swapping the text in one commit re-anchors unreliably. Close
                // first, then open on the next frame — a stale box can never
                // survive a target change.
                const numberCell =
                    (event.target as Element).closest?.(
                        ".renovation-grid__number-cell[data-column]",
                    ) ?? null;
                if (numberCell !== numberTooltipTargetRef.current) {
                    numberTooltipTargetRef.current = numberCell;
                    setNumberTooltip(null);
                    const chain = numberCell
                        ? headerChainByProp.get(numberCell.getAttribute("data-column") ?? "")
                        : undefined;
                    if (numberCell && chain && chain.length > 0) {
                        const rect = numberCell.getBoundingClientRect();
                        const next = {
                            lines: chain,
                            column: numberCell.getAttribute("data-column") ?? "",
                            left: rect.left + rect.width / 2,
                            top: rect.bottom,
                        };
                        // Next frame: the trigger has re-rendered at the new
                        // spot and the closed popover re-anchors cleanly.
                        window.requestAnimationFrame(() => {
                            if (numberTooltipTargetRef.current === numberCell) {
                                setNumberTooltip(next);
                            }
                        });
                    }
                }
                const target = (event.target as Element).closest?.("[data-remote-lock]") ?? null;
                if (target === remoteTooltipTargetRef.current) return;
                remoteTooltipTargetRef.current = target;
                if (!target) {
                    setRemoteTooltip(null);
                    return;
                }
                const rect = target.getBoundingClientRect();
                let lock: { name?: string; label?: string | null } | null = null;
                try {
                    const raw = target.getAttribute("data-remote-lock");
                    if (raw) lock = JSON.parse(raw);
                } catch {
                    lock = null;
                }
                setRemoteTooltip({
                    name: lock?.name ?? target.getAttribute("data-remote-lock") ?? "",
                    label: lock?.label ?? null,
                    left: rect.left + rect.width / 2,
                    top: rect.top,
                });
            }}
            onPointerOut={(event) => {
                // Leaving the strip (or the whole grid) closes the tooltip at
                // once, even when no `pointermove` lands on a non-number cell —
                // a fast diagonal exit otherwise skipped `pointermove` entirely.
                const next = event.relatedTarget as Element | null;
                if (next && event.currentTarget.contains(next)) return;
                remoteTooltipTargetRef.current = null;
                numberTooltipTargetRef.current = null;
                setRemoteTooltip(null);
                setNumberTooltip(null);
            }}
            onPointerLeave={() => {
                remoteTooltipTargetRef.current = null;
                setRemoteTooltip(null);
                numberTooltipTargetRef.current = null;
                setNumberTooltip(null);
            }}
        >
            {/* The header overlay is always mounted: toggling «Закрепить
                заголовок» only animates this slot's margin/opacity, so the
                grid keeps its source, scroll position and selection (no
                reload). */}
            <div
                className={[
                    "renovation-grid__header-slot",
                    !headerPinned && "renovation-grid__header-slot--hidden",
                ]
                    .filter(Boolean)
                    .join(" ")}
            >
                {headerOverlay}
            </div>
            <Tooltip
                isOpen={remoteTooltip !== null}
                key={`lock:${remoteTooltip?.left ?? "closed"}:${remoteTooltip?.top ?? ""}:${remoteTooltip?.name ?? ""}`}
            >
                <Tooltip.Trigger
                    className="remote-cell-tooltip-trigger"
                    style={{
                        left: remoteTooltip?.left ?? -1000,
                        top: remoteTooltip?.top ?? -1000,
                    }}
                >
                    <span aria-hidden="true" />
                </Tooltip.Trigger>
                <Tooltip.Content placement="top" className="form-status-tooltip">
                    <span className="lock-tooltip">
                        <strong>{remoteTooltip?.name ?? ""}</strong>
                        {remoteTooltip?.label ? (
                            <>
                                <br />
                                {remoteTooltip.label}
                            </>
                        ) : null}
                    </span>
                </Tooltip.Content>
            </Tooltip>
            {/* The column number's header chain, top level first. `keyedBy`
                remounts the whole Tooltip whenever the hovered column changes:
                react-aria's popover keeps its own anchor/open state, and a
                remount is the only way to guarantee the previous box is gone
                even when the pointer jumps between strip cells mid-frame. */}
            <Tooltip
                isOpen={numberTooltip !== null}
                key={`number:${numberTooltip?.column ?? "closed"}`}
            >
                <Tooltip.Trigger
                    className="remote-cell-tooltip-trigger"
                    style={{
                        left: numberTooltip?.left ?? -1000,
                        top: numberTooltip?.top ?? -1000,
                    }}
                >
                    <span aria-hidden="true" />
                </Tooltip.Trigger>
                <Tooltip.Content placement="bottom" className="form-status-tooltip">
                    <span className="renovation-grid__number-tooltip">
                        {(numberTooltip?.lines ?? []).map((line, index) => (
                            <Fragment key={`${index}:${line}`}>
                                {index > 0 ? (
                                    <>
                                        <br />↓<br />
                                    </>
                                ) : null}
                                {line}
                            </Fragment>
                        ))}
                    </span>
                </Tooltip.Content>
            </Tooltip>
            <div className="revo-grid-vertical-scroller">
                <RevoGrid
                    ref={gridRef}
                    className="renovation-grid"
                    columns={columns}
                    // The plugin owns `source` (a sparse array of `total` slots
                    // filled chunk by chunk); `infinityScroll` is its config.
                    plugins={[InfinityScrollPlugin]}
                    infinityScroll={infinityScroll}
                    pinnedTopSource={pinnedSummaryRow ?? EMPTY_PINNED_SOURCE}
                    theme="compact"
                    range
                    useClipboard={false}
                    rowHeaders={rowHeaders}
                    editors={editors}
                    hideAttribution
                    readonly={readOnly}
                    rowSize={rowHeight}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
                    }}
                    onContentsizechanged={() => void refreshHorizontalScrollbar()}
                    onViewportscroll={(event) => {
                        if (event.detail.dimension === "rgCol") {
                            // The header overlay tracks the scroll position
                            // imperatively — no React state per scroll tick
                            // (a setState per tick re-rendered the whole grid
                            // and made horizontal scrolling janky).
                            const body = headerBodyRef.current;
                            if (body) {
                                body.style.transform = `translateX(${-event.detail.coordinate}px)`;
                            }
                            const scrollbar = horizontalScrollRef.current;
                            if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                                scrollbar.scrollLeft = event.detail.coordinate;
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
            {/* The custom horizontal strip (the native one is hidden: Chrome
                renders it too thick). Its spacer carries the full content
                width, so the thumb is proportional to the whole plan. It stops
                before the vertical scrollbar's lane: the bars must not overlap
                in the bottom-right corner (the vertical strip itself ends above
                the strip's row — see the height rule in globals.css). */}
            <div
                ref={horizontalScrollRef}
                className="revo-grid-external-scrollbar"
                style={{
                    display: hasHorizontalOverflow ? undefined : "none",
                    right: verticalScrollbarLane > 0 ? verticalScrollbarLane : undefined,
                }}
                aria-label="Горизонтальная прокрутка таблицы"
                onScroll={(event) => {
                    void gridRef.current?.scrollToCoordinate({
                        x: event.currentTarget.scrollLeft,
                    });
                }}
            >
                <div style={{ width: horizontalContentWidth }} />
            </div>
        </div>
    );
}

export const RenovationGrid = forwardRef(RenovationGridInner);
