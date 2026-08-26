import { Tooltip } from "@heroui/react";
import {
    Editor,
    RevoGrid,
    type BeforeRangeSaveDataDetails,
    type BeforeSaveDataDetails,
    type ColumnGrouping,
    type ColumnRegular,
    type Editors,
    type HyperFunc,
    type RangeArea,
    type RowHeaders,
    type VNode,
} from "@revolist/react-datagrid";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    forwardRef,
    useImperativeHandle,
    type CSSProperties,
    type Ref,
} from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";

import {
    buildGridColumns,
    exportColumnsFromGrid,
    getEditableKeys,
    getVisibleDescriptors,
    placeholderLastCellCompare,
    RUSSIAN_FILTER_CONFIG,
    type ColumnDescriptor,
    type ExportColumn,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "./grid-config";
import { extractEdits } from "./grid-edit";
import { MultiAssetEditor, ReferenceEditor, SelectEditor } from "./grid-editors";

export type RawMaterialsGridState = {
    columns: ExportColumn[];
    rowIds: string[];
};

export type GridViewStats = {
    totalRows: number;
    /** Rows matching the current filters (non-placeholder, non-deleted). */
    filteredRows: number;
    totalCols: number;
    /** Whether any column filter is currently applied. */
    filtersApplied: boolean;
    /** Fixed (non-calculated) columns — not editable. */
    immutableCols: number;
    /** Calculated columns. */
    calculatedCols: number;
    /** Editable columns. */
    editableCols: number;
};

export type RawMaterialsGridHandle = {
    getExportState: () => Promise<RawMaterialsGridState>;
    copySelection: () => Promise<void>;
    pasteIntoSelection: () => Promise<void>;
    /** Reset the cell range selection (same path as Escape). */
    clearSelection: () => void;
    /** Drop the persisted per-column widths — columns return to defaults. */
    resetColumnWidths: () => void;
    /** Clear every column filter and the user sorting. */
    resetFiltersSorting: () => Promise<void>;
};

type Props = {
    section: SectionId;
    rows: GridRow[];
    isAdmin: boolean;
    keycloakEnabled?: boolean;
    showAssetColumn: boolean;
    readOnly: boolean;
    hasBaseline: boolean;
    /** Controlled «Закрепить заголовок» toggle (rendered in the view strip). */
    headerPinned: boolean;
    /** Controlled «Показать служебные столбцы» toggle (lives in the view strip). */
    showServiceColumns: boolean;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    /** Custom column set — overrides the built-in descriptors for the section. */
    columnDescriptors?: ColumnDescriptor[];
    /** Distinguishes width/sorting persistence when several grids share a section id. */
    namespace?: string;
    onCommit: (updates: CellUpdate[]) => Promise<boolean>;
    onPlaceholderCommit: (patch: Record<string, unknown>) => Promise<boolean>;
    /** The server rejected the edits: the source must be restored (optimistic rollback). */
    onCommitFailed?: () => void;
    onSelectedRowsChange: (selection: { ids: string[]; wholeRows: boolean }) => void;
    onValidationError: (message: string) => void;
    onPasteSkipped?: (description: string) => void;
    /** The pasted range is bigger than the selection — values were trimmed by it. */
    onPasteTruncated?: () => void;
    /** Cells leased by others (active editing). */
    remoteLocks?: ReadonlyMap<string, { fullname: string; email: string }>;
    /** Cells selected by other members (presence) — also protected. */
    remotePresence?: ReadonlyMap<string, { fullname: string; email: string }>;
    onLockedCellEdit?: (key: string, ownerName: string) => void;
    onAcquireLease?: (keys: string[]) => void;
    onReleaseLease?: (keys: string[]) => void;
    onViewStatsChange?: (stats: GridViewStats) => void;
    onClipboardStateChange?: (state: { canPaste: boolean }) => void;
    /** Completed selection (for presence): label and cell keys of the range. */
    onSelectionChange?: (selection: { label: string | null; cellKeys: string[] }) => void;
    /** Keys of cells where other members are (highlight + tooltip). */
    remoteCellInfo?: ReadonlyMap<string, { name: string; label: string | null }>;
};

/** Debounce for sending a completed selection to presence (after release). */
const PRESENCE_DEBOUNCE_MS = 400;

type GridSortingOrder = "asc" | "desc";

type GridSortingConfig = {
    columns: Array<{
        prop: string;
        order: GridSortingOrder;
        cellCompare: typeof placeholderLastCellCompare;
    }>;
};

type GridSortingEvent = {
    sorting?: Record<string, GridSortingOrder | undefined>;
    sortingOrder?: Array<string | number>;
};

function isGridTarget(event: Event, grid: HTMLElement): boolean {
    return grid.contains(event.target as Node | null) || event.composedPath().includes(grid);
}

/**
 * Canonical keys of the cells in the selected range — the same for all
 * members regardless of the visible column set (coordinates diverge when
 * users have different column sets).
 */
function selectionCellKeys(
    range: RangeArea | null,
    rows: GridRow[],
    columns: ColumnRegular[],
): string[] {
    if (!range || range.x1 < range.x || range.y1 < range.y) return [];
    const keys: string[] = [];
    for (let y = range.y; y <= Math.min(range.y1, rows.length - 1); y++) {
        const row = rows[y];
        if (!row?.canonicalCells) continue;
        for (let x = range.x; x <= Math.min(range.x1, columns.length - 1); x++) {
            const prop = columns[x]?.prop;
            if (prop == null) continue;
            const key = row.canonicalCells[String(prop)];
            if (key) keys.push(key);
        }
    }
    return keys;
}

/**
 * Column label with the nested header: «Потребность / План, т».
 */
function columnLabel(
    column: ColumnRegular | undefined,
    descriptorByKey: Map<string, ColumnDescriptor>,
): string | null {
    if (!column) return null;
    const descriptor = descriptorByKey.get(String(column.prop));
    if (descriptor) {
        return `«${[...(descriptor.path ?? []), descriptor.label].join(" / ")}»`;
    }
    const fallback = String(column.name ?? column.prop ?? "");
    return fallback ? `«${fallback}»` : null;
}

/**
 * Human-readable description of the selected range, e.g.
 * «Бизнес-план, т» – «Факт, т», строки 3–5 or «Потребность / План, т», строка 2.
 * Column names are quoted; ranges are written with a dash.
 * null — no selection.
 */
export function selectionLabelForRange(
    range: RangeArea | null,
    columns: ColumnRegular[],
    descriptorByKey: Map<string, ColumnDescriptor>,
    rowCount: number,
): string | null {
    if (!range || range.x1 < range.x || range.y1 < range.y) return null;
    const firstLabel = columnLabel(columns[range.x], descriptorByKey);
    const lastLabel = columnLabel(columns[range.x1], descriptorByKey);
    if (!firstLabel || !lastLabel) return null;
    const columnText = firstLabel === lastLabel ? firstLabel : `${firstLabel} – ${lastLabel}`;
    const startRow = range.y + 1;
    const endRow = Math.min(range.y1 + 1, rowCount);
    const rowText = startRow === endRow ? `строка ${startRow}` : `строки ${startRow}–${endRow}`;
    return `${columnText}, ${rowText}`;
}

/**
 * Canonical keys of all cells in the selected rows (across all columns).
 */
function selectionRowCellKeys(
    rows: GridRow[],
    startRow: number,
    endRow: number,
    columns: ColumnRegular[],
): string[] {
    const keys: string[] = [];
    for (let y = startRow; y <= endRow; y++) {
        const row = rows[y];
        if (!row?.canonicalCells) continue;
        for (const column of columns) {
            const prop = column.prop;
            if (prop == null) continue;
            const key = row.canonicalCells[String(prop)];
            if (key) keys.push(key);
        }
    }
    return keys;
}

export function sortingConfigFromEvent(detail: GridSortingEvent): GridSortingConfig {
    const sorting = detail.sorting ?? {};
    const order = detail.sortingOrder?.map(String) ?? Object.keys(sorting);
    return {
        columns: order.flatMap((prop) => {
            const direction = sorting[prop];
            return direction
                ? [{ prop, order: direction, cellCompare: placeholderLastCellCompare }]
                : [];
        }),
    };
}

function RawMaterialsGridInner(
    {
        section,
        rows,
        isAdmin,
        keycloakEnabled = false,
        showAssetColumn,
        readOnly,
        hasBaseline,
        headerPinned,
        showServiceColumns,
        assets,
        deliveryBases,
        columnDescriptors,
        namespace,
        onCommit,
        onPlaceholderCommit,
        onCommitFailed,
        onSelectedRowsChange,
        onValidationError,
        onPasteSkipped,
        onPasteTruncated,
        remoteLocks = new Map(),
        remotePresence = new Map(),
        onLockedCellEdit,
        onAcquireLease,
        onReleaseLease,
        onViewStatsChange,
        onClipboardStateChange,
        onSelectionChange,
        remoteCellInfo = new Map(),
    }: Props,
    forwardedRef: Ref<RawMaterialsGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const shellRef = useRef<HTMLDivElement>(null);
    const verticalScrollRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    const filtersAppliedRef = useRef(false);
    // Unpinned mode: the external horizontal scrollbar is shown only when the
    // grid content actually overflows horizontally (native behavior elsewhere).
    const [hasHorizontalOverflow, setHasHorizontalOverflow] = useState(false);
    const resizedWidthsRef = useRef(new Map<string, number>());
    // Bumped after a widths reset: the columns memo reads the ref, so it needs
    // a dependency change to rebuild with default sizes.
    const [widthsVersion, setWidthsVersion] = useState(0);
    const widthKeySection = namespace ?? section;
    const sortingBySectionRef = useRef(new Map<SectionId, GridSortingConfig>());
    const userSortedSectionsRef = useRef(new Set<SectionId>());
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);
    const rowValuesRef = useRef<Map<string, Readonly<Record<string, unknown>>>>(new Map());
    const activeLeaseRef = useRef<string | null>(null);
    const [selectionRange, setSelectionRange] = useState<RangeArea | null>(null);
    const [canPaste, setCanPaste] = useState(false);
    // Floating HeroUI Tooltip over cells occupied by another participant.
    const [remoteTooltip, setRemoteTooltip] = useState<{
        text: string;
        left: number;
        top: number;
    } | null>(null);
    const remoteTooltipTargetRef = useRef<Element | null>(null);

    useEffect(() => {
        rowValuesRef.current = new Map(rows.map((row) => [row.id, { ...row }]));
    }, [rows]);

    const sorting = sortingBySectionRef.current.get(section);

    const descriptors = useMemo(
        () =>
            getVisibleDescriptors(section, showAssetColumn, showServiceColumns, {
                keycloakEnabled,
                descriptors: columnDescriptors,
            }),
        [section, showAssetColumn, showServiceColumns, keycloakEnabled, columnDescriptors],
    );
    const descriptorByKey = useMemo(
        () => new Map(descriptors.map((descriptor) => [descriptor.key, descriptor])),
        [descriptors],
    );

    const editableKeys = useMemo(
        () =>
            getEditableKeys(section, isAdmin, showAssetColumn, keycloakEnabled, columnDescriptors),
        [isAdmin, section, showAssetColumn, keycloakEnabled, columnDescriptors],
    );

    const editors = useMemo<Editors>(
        () => ({
            multiAssets: Editor(MultiAssetEditor),
            reference: Editor(ReferenceEditor),
            select: Editor(SelectEditor),
        }),
        [],
    );

    const columns = useMemo(() => {
        const next = buildGridColumns(section, {
            isAdmin,
            showAssetColumn,
            keycloakEnabled,
            readOnly,
            hasBaseline,
            assets,
            deliveryBases,
            selectedRowIds: selectedRowIdSet,
            showServiceColumns,
            remoteLocks,
            remoteCellInfo,
            descriptors: columnDescriptors,
            onBooleanChange: (row, field, value) => {
                if (row.isPlaceholder) void onPlaceholderCommit({ [field]: value });
                else void onCommit([{ rowId: row.id, field, value }]);
            },
        });
        restoreColumnWidths(next, resizedWidthsRef.current, widthKeySection);
        return next;
    }, [
        assets,
        columnDescriptors,
        deliveryBases,
        hasBaseline,
        isAdmin,
        keycloakEnabled,
        showAssetColumn,
        onCommit,
        onPlaceholderCommit,
        readOnly,
        remoteCellInfo,
        remoteLocks,
        selectedRowIdSet,
        section,
        showServiceColumns,
        widthKeySection,
        widthsVersion,
    ]);

    const flatColumns: ColumnRegular[] = useMemo(
        () =>
            columns.flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            ),
        [columns],
    );

    const headerHeight = descriptors.some((descriptor) => descriptor.path?.length) ? 88 : 44;
    const contentHeight = headerHeight + rows.length * 36 + 18;

    const selectionRangeRef = useRef<RangeArea | null>(null);
    const updateSelectionRange = useCallback((range: RangeArea | null) => {
        selectionRangeRef.current = range;
        setSelectionRange(range);
    }, []);

    /**
     * Current visible rows (sorting and filters already applied).
     * Selection/paste/presence indexes are coordinates of visible rows,
     * so any index-based lookup must go here, not into the original rows.
     */
    const getVisibleRows = useCallback(async (): Promise<GridRow[]> => {
        const grid = gridRef.current;
        if (!grid) return rows;
        try {
            return (await grid.getVisibleSource("rgRow")) as GridRow[];
        } catch {
            return rows;
        }
    }, [rows]);

    // Selected rows (row-number buttons) — we reflect them in presence too.
    const selectedRowsRange = useMemo(() => {
        if (selectedRowIds.length === 0) return null;
        const indexes = rows
            .map((row, index) => (selectedRowIdSet.has(row.id) ? index : -1))
            .filter((index) => index >= 0);
        if (indexes.length === 0) return null;
        return { min: indexes[0], max: indexes[indexes.length - 1] };
    }, [rows, selectedRowIdSet, selectedRowIds.length]);

    // Presence: report only the completed selection (not every drag event),
    // so live streaming doesn't break dragging and doesn't spam the room.
    // Debounce guarantees the send after release. The callback and computed
    // data stay in a ref, so the presence:update echo (members rebuilt on our
    // own client) doesn't cause repeated sends.
    const presenceInputRef = useRef({
        descriptorByKey,
        flatColumns,
        getVisibleRows,
        selectedRowIds: selectedRowIdSet,
        onSelectionChange,
    });
    presenceInputRef.current = {
        descriptorByKey,
        flatColumns,
        getVisibleRows,
        selectedRowIds: selectedRowIdSet,
        onSelectionChange,
    };
    useEffect(() => {
        const timer = setTimeout(() => {
            const {
                descriptorByKey,
                flatColumns,
                getVisibleRows,
                selectedRowIds,
                onSelectionChange,
            } = presenceInputRef.current;
            // Selection coordinates are displayed rows (sorting/filtering already
            // applied), so cell keys are computed from the visible source.
            void (async () => {
                const visible = await getVisibleRows();
                const rowIndexes: number[] = [];
                for (let index = 0; index < visible.length; index++) {
                    if (selectedRowIds.has(visible[index].id)) rowIndexes.push(index);
                }
                const label = rowIndexes.length
                    ? rowIndexes[0] === rowIndexes[rowIndexes.length - 1]
                        ? `строка ${rowIndexes[0] + 1}`
                        : `строки ${rowIndexes[0] + 1}–${rowIndexes[rowIndexes.length - 1] + 1}`
                    : selectionLabelForRange(
                          selectionRange,
                          flatColumns,
                          descriptorByKey,
                          visible.length,
                      );
                const keys = rowIndexes.length
                    ? rowIndexes.flatMap((index) =>
                          selectionRowCellKeys(visible, index, index, flatColumns),
                      )
                    : selectionCellKeys(selectionRange, visible, flatColumns);
                onSelectionChange?.({ label, cellKeys: keys });
            })();
        }, PRESENCE_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [selectedRowsRange, selectionRange]);

    /**
     * Programmatic range selection through the grid's public API.
     * setCellsFocus updates the selection store (focus + range), and refresh
     * forces the overlay redraw: without it the state updates but the frame
     * doesn't appear (Stencil doesn't redraw the overlay on external store
     * changes). Writing directly to overlay-selectionStore doesn't work —
     * there's a raw observable without setRange/setFocus.
     */
    const applyGridRange = useCallback(
        async (start: { x: number; y: number }, end: { x: number; y: number }) => {
            const grid = gridRef.current;
            if (!grid) return;
            await grid.setCellsFocus(start, end);
            await grid.refresh();
        },
        [],
    );

    const clearGridSelection = useCallback(() => {
        const grid = gridRef.current;
        if (!grid) return;
        // clearFocus() empties the selection store, but without refresh the
        // selection overlay isn't repainted (the same Stencil quirk as with
        // setCellsFocus) — the range rectangle would stay visible.
        void grid.clearFocus().then(() => void grid.refresh());
        // revo-grid caches the last focused cell (pendingColumnFocusRestore)
        // whenever columns change while a range is active, then re-applies it
        // in componentDidRender. After clearFocus() the store is empty, so the
        // "column didn't move" guard can't match and the stale restore would
        // resurrect a single-cell range. Drop it so the clearing survives the
        // next render (this is what kept the border visible after row clicks).
        (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore =
            undefined;
        updateSelectionRange(null);
    }, [updateSelectionRange]);

    // Ctrl+A: one contiguous range over all data cells. Headers aren't
    // selected (merged headers' border doesn't match), the «добавить запись»
    // row is outside the range too, row selection is reset — only the cell
    // range remains.
    const selectAllCells = useCallback(async () => {
        if (rows.length === 0 || flatColumns.length === 0) return;
        let lastDataIndex = rows.length - 1;
        while (lastDataIndex >= 0 && rows[lastDataIndex]?.isPlaceholder) {
            lastDataIndex -= 1;
        }
        if (lastDataIndex < 0) return;
        const start = { x: 0, y: 0 };
        const end = { x: flatColumns.length - 1, y: lastDataIndex };
        await applyGridRange(start, end);
        updateSelectionRange({ x: 0, y: 0, x1: end.x, y1: end.y });
        setSelectedRowIds([]);
        rowSelectionAnchorRef.current = null;
    }, [applyGridRange, flatColumns.length, rows, setSelectedRowIds, updateSelectionRange]);

    const handleRowNumberClick = useCallback(
        (event: MouseEvent, rowIndex: number, id: string) => {
            event.preventDefault();
            event.stopPropagation();
            const modifiers = event.ctrlKey || event.metaKey;
            // Clear the cell range BEFORE the row selection state updates:
            // selectedRowIds is a columns dependency, so the row click rebuilds
            // the columns and revo-grid captures the still-live focus into
            // pendingColumnFocusRestore — which would then resurrect a
            // single-cell range on the next render, keeping the border visible.
            if (event.shiftKey && !modifiers) {
                const anchor = rowSelectionAnchorRef.current ?? rowIndex;
                const start = Math.min(anchor, rowIndex);
                const end = Math.max(anchor, rowIndex);
                clearGridSelection();
                setSelectedRowIds(
                    rows
                        .slice(start, end + 1)
                        .map((row) => row.id)
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
        [clearGridSelection, rows, selectedRowIdSet],
    );

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: 52,
            columnProperties: () => ({
                className: {
                    "report-row-header": true,
                    "report-row-header--heading": true,
                },
            }),
            cellProperties: ({ model }) => {
                const row = model as GridRow;
                return {
                    className: {
                        "report-row-header": true,
                        "report-row-header--selected": selectedRowIds.includes(row.id),
                    },
                };
            },
            cellTemplate: (h: HyperFunc<VNode>, { rowIndex, model }) => {
                const row = model as GridRow;
                const selected = selectedRowIds.includes(row.id);
                // The trailing «добавить запись» row shows «+» instead of a
                // number — same as the renovation plan grid.
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
                              ? `Снять выделение строки ${rowIndex + 1}`
                              : `Выделить строку ${rowIndex + 1}`,
                        onMouseDown: (event: MouseEvent) => {
                            event.stopPropagation();
                        },
                        onClick: (event: MouseEvent) => {
                            event.stopPropagation();
                            handleRowNumberClick(event, rowIndex, row.id);
                        },
                    },
                    row.isPlaceholder ? "+" : String(rowIndex + 1),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [selectedRowIds, handleRowNumberClick],
    );

    useEffect(() => {
        onSelectedRowsChange({
            ids: selectedRowIds,
            wholeRows: true,
        });
    }, [onSelectedRowsChange, selectedRowIds]);

    function canEditDescriptor(descriptor: ColumnDescriptor, row: GridRow): boolean {
        return !(
            readOnly ||
            row.isDeleted ||
            descriptor.fixed ||
            descriptor.kind === "calculated" ||
            (descriptor.placeholderOnly && !row.isPlaceholder) ||
            (descriptor.placeholderReadOnly && row.isPlaceholder) ||
            (keycloakEnabled &&
                section === "users" &&
                !row.isPlaceholder &&
                (descriptor.key === "email" || descriptor.key === "fullname"))
        );
    }

    const canPasteRange = useCallback(
        (range: RangeArea | null, source: GridRow[]): boolean => {
            if (!range || readOnly) return false;
            for (let y = range.y; y <= Math.min(range.y1, source.length - 1); y++) {
                const row = source[y];
                if (!row) continue;
                for (let x = range.x; x <= range.x1; x++) {
                    const column = flatColumns[x] as ColumnRegular | undefined;
                    if (!column) continue;
                    const descriptor = descriptorByKey.get(String(column.prop));
                    if (!descriptor || !editableKeys.has(descriptor.key)) continue;
                    if (!canEditDescriptor(descriptor, row)) continue;
                    const canonicalKey = row.canonicalCells?.[descriptor.key];
                    if (
                        canonicalKey &&
                        (remoteLocks.has(canonicalKey) || remotePresence.has(canonicalKey))
                    )
                        continue;
                    return true;
                }
            }

            return false;
        },
        [descriptorByKey, editableKeys, flatColumns, readOnly, remoteLocks, remotePresence],
    );

    useEffect(() => {
        // Selection is in displayed-row coordinates — check against the visible
        // source (sorting/filtering already applied).
        let cancelled = false;
        void (async () => {
            const source = await getVisibleRows();
            if (cancelled) return;
            setCanPaste(canPasteRange(selectionRange, source));
        })();
        return () => {
            cancelled = true;
        };
    }, [canPasteRange, getVisibleRows, selectionRange]);

    useEffect(() => {
        onClipboardStateChange?.({ canPaste });
    }, [canPaste, onClipboardStateChange]);

    function normalizeValue(
        value: unknown,
        kind: ColumnDescriptor["kind"],
    ): { ok: true; value: unknown } | { ok: false; message: string } {
        if (kind === "number") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const parsed =
                typeof value === "number" ? value : Number(String(value).trim().replace(",", "."));
            if (!Number.isFinite(parsed) || parsed < 0) {
                return { ok: false, message: "введите конечное неотрицательное число." };
            }
            return { ok: true, value: parsed };
        }
        if (kind === "boolean") {
            if (typeof value === "boolean") return { ok: true, value };
            const normalized = String(value).trim().toLocaleLowerCase("ru-RU");
            if (["РґР°", "true", "1"].includes(normalized)) return { ok: true, value: true };
            if (["нет", "false", "0"].includes(normalized)) return { ok: true, value: false };
            return { ok: false, message: "выберите «Да» или «Нет»." };
        }
        if (kind === "assetsMulti") {
            // Dictionary values contain no commas, so a comma is the separator.
            const requested = Array.isArray(value) ? value : String(value ?? "").split(",");
            const canonical = canonicalOptions(requested, assets);
            if (!canonical.ok) return canonical;
            return { ok: true, value: canonical.value };
        }
        if (kind === "asset" || kind === "basis") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const canonical = canonicalOptions(
                [String(value)],
                kind === "asset" ? assets : deliveryBases,
            );
            if (!canonical.ok) return canonical;
            return { ok: true, value: canonical.value[0] ?? null };
        }
        if (value === null || value === undefined) return { ok: true, value: null };
        return { ok: true, value: String(value).trim() };
    }

    function serializeCellValue(value: unknown): string {
        if (value === null || value === undefined) return "";
        if (Array.isArray(value)) return value.map(serializeCellValue).join(", ");
        if (typeof value === "object") {
            const named = value as { name?: unknown };
            return named.name !== undefined ? String(named.name) : String(value);
        }
        return String(value);
    }

    function beforeEditStart(event: CustomEvent<BeforeSaveDataDetails>) {
        const descriptor = descriptorByKey.get(String(event.detail.prop));
        const row = event.detail.model as GridRow;
        if (
            !descriptor ||
            !editableKeys.has(descriptor.key) ||
            !canEditDescriptor(descriptor, row)
        ) {
            event.preventDefault();
            return;
        }
        const canonicalKey = row.canonicalCells?.[descriptor.key];
        if (canonicalKey) {
            // Lease or foreign selection — the cell is protected: editing can't start.
            const owner = remoteLocks.get(canonicalKey) ?? remotePresence.get(canonicalKey);
            if (owner) {
                event.preventDefault();
                onLockedCellEdit?.(
                    canonicalKey,
                    owner.fullname || owner.email || "другой пользователь",
                );
                return;
            }
        }
        if (canonicalKey && !row.isPlaceholder) {
            activeLeaseRef.current = canonicalKey;
            onAcquireLease?.([canonicalKey]);
        } else {
            activeLeaseRef.current = null;
        }
    }

    function beforeEdit(event: CustomEvent<BeforeSaveDataDetails>) {
        const descriptor = descriptorByKey.get(String(event.detail.prop));
        const row = event.detail.model as GridRow;
        if (
            !descriptor ||
            !editableKeys.has(descriptor.key) ||
            !canEditDescriptor(descriptor, row)
        ) {
            event.preventDefault();
            return;
        }
        const canonicalKey = row.canonicalCells?.[descriptor.key];
        if (canonicalKey) {
            const byLease = remoteLocks.get(canonicalKey);
            // A foreign lease always blocks; a foreign selection — except when
            // I'm editing the cell myself (my lease beats a foreign selection).
            const byPresence =
                remotePresence.get(canonicalKey) && activeLeaseRef.current !== canonicalKey
                    ? remotePresence.get(canonicalKey)
                    : undefined;
            const owner = byLease ?? byPresence;
            if (owner) {
                event.preventDefault();
                onLockedCellEdit?.(
                    canonicalKey,
                    owner.fullname || owner.email || "другой пользователь",
                );
                return;
            }
        }
        const normalized = normalizeValue(event.detail.val, descriptor.kind);
        if (!normalized.ok) {
            event.preventDefault();
            onValidationError(normalized.message);
            return;
        }
        event.detail.val = normalized.value;
    }

    function beforeRangeEdit(event: CustomEvent<BeforeRangeSaveDataDetails>) {
        const patches = event.detail.data as Record<string, GridRow>;
        const models = event.detail.models as Record<string, GridRow | undefined>;
        const skippedColumns = new Map<string, string>();
        for (const [rowIndex, patch] of Object.entries(patches)) {
            const currentRow = models[Number(rowIndex)] ?? rows[Number(rowIndex)];
            for (const prop of Object.keys(patch)) {
                const descriptor = descriptorByKey.get(String(prop));
                if (!descriptor) {
                    delete patch[prop];
                    continue;
                }
                if (!editableKeys.has(descriptor.key)) {
                    delete patch[prop];
                    const reason =
                        descriptor.kind === "calculated"
                            ? "расчётное поле"
                            : descriptor.fixed
                              ? "фиксированное поле"
                              : "поле недоступно для редактирования";
                    skippedColumns.set(descriptor.label, reason);
                    continue;
                }
                if (!currentRow || !canEditDescriptor(descriptor, currentRow)) {
                    delete patch[prop];
                    const reason =
                        descriptor.kind === "calculated"
                            ? "расчётное поле"
                            : descriptor.fixed
                              ? "фиксированное поле"
                              : "поле недоступно для редактирования";
                    skippedColumns.set(descriptor.label, reason);
                    continue;
                }
                const canonicalKey = currentRow.canonicalCells?.[descriptor.key];
                if (
                    canonicalKey &&
                    (remoteLocks.has(canonicalKey) || remotePresence.has(canonicalKey))
                ) {
                    delete patch[prop];
                    skippedColumns.set(descriptor.label, "ячейка занята другим пользователем");
                    continue;
                }
                const normalized = normalizeValue(patch[prop], descriptor.kind);
                if (!normalized.ok) {
                    event.preventDefault();
                    onValidationError(normalized.message);
                    return;
                }
                patch[prop] = normalized.value;
            }
        }
        if (skippedColumns.size > 0) {
            const reasons = new Map<string, string[]>();
            for (const [title, reason] of skippedColumns) {
                const titles = reasons.get(reason) ?? [];
                titles.push(title);
                reasons.set(reason, titles);
            }
            const description = [...reasons.entries()]
                .map(
                    ([reason, titles]) =>
                        `${titles.map((title) => `В«${title}В»`).join(", ")}: ${reason}`,
                )
                .join("; ");
            onPasteSkipped?.(description);
        }
    }

    function afterEdit(event: CustomEvent<BeforeSaveDataDetails | Record<string, unknown>>) {
        const edits = extractEdits(
            event.detail,
            editableKeys,
            descriptorByKey,
            rowValuesRef.current,
        );
        const releaseKeys: string[] = [];
        if ("data" in event.detail) {
            const models = (event.detail as { models?: GridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                event.detail.data as Record<string, Record<string, unknown>>,
            )) {
                const previous = models[Number(rowIndex)] as GridRow | undefined;
                const row = (previous ?? patch) as GridRow;
                if (!row?.id || row.isDeleted) continue;
                for (const field of Object.keys(patch)) {
                    if (!editableKeys.has(field)) continue;
                    const key = row.canonicalCells?.[field];
                    if (key) releaseKeys.push(key);
                }
            }
        } else {
            const row = event.detail.model as GridRow;
            const key = row.canonicalCells?.[String(event.detail.prop)];
            if (key) releaseKeys.push(key);
        }
        void (async () => {
            try {
                let ok = true;
                if (edits.updates.length) {
                    ok = await onCommit(translateUpdates(section, edits.updates, assets));
                }
                if (ok && Object.keys(edits.placeholderPatch).length) {
                    ok = await onPlaceholderCommit(
                        translatePlaceholderPatch(section, edits.placeholderPatch, assets),
                    );
                }
                if (!ok) onCommitFailed?.();
            } finally {
                activeLeaseRef.current = null;
                onReleaseLease?.(releaseKeys);
            }
        })();
    }

    const refreshHorizontalScrollbar = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const size = await grid.getContentSize();
        const verticalScroller = verticalScrollRef.current;
        const scrollbarGutter = verticalScroller
            ? verticalScroller.offsetWidth - verticalScroller.clientWidth
            : 0;
        const width = size.x + scrollbarGutter + 52;
        setHorizontalContentWidth(width);
        const shell = shellRef.current;
        setHasHorizontalOverflow(width > (shell?.clientWidth ?? 0));
    }, []);

    const getExportState = useCallback(async (): Promise<RawMaterialsGridState> => {
        const grid = gridRef.current;
        if (!grid) {
            return {
                columns: exportColumnsFromGrid(
                    section,
                    showAssetColumn,
                    showServiceColumns,
                    [],
                    keycloakEnabled,
                    columnDescriptors,
                ),
                rowIds: rows
                    .filter((row) => !row.isPlaceholder && !row.isDeleted)
                    .map((row) => row.id),
            };
        }
        const [currentColumns, currentRows] = await Promise.all([
            grid.getColumns(),
            grid.getVisibleSource("rgRow"),
        ]);
        return {
            columns: exportColumnsFromGrid(
                section,
                showAssetColumn,
                showServiceColumns,
                currentColumns as (ColumnRegular | ColumnGrouping)[],
                keycloakEnabled,
                columnDescriptors,
            ),
            rowIds: (currentRows as GridRow[])
                .filter((row) => !row.isPlaceholder && !row.isDeleted)
                .map((row) => row.id),
        };
    }, [rows, section, showAssetColumn, showServiceColumns, columnDescriptors]);

    const lastStatsRef = useRef<GridViewStats | null>(null);

    const measureViewport = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid || !onViewStatsChange) return;
        const [columns, visibleSource] = await Promise.all([
            grid.getColumns(),
            grid.getVisibleSource("rgRow"),
        ]);
        const visible = (visibleSource as GridRow[]).filter(
            (row) => !row.isPlaceholder && !row.isDeleted,
        );
        const counted = descriptors.reduce(
            (acc, descriptor) => {
                if (descriptor.kind === "calculated") acc.calculated += 1;
                else if (descriptor.fixed) acc.immutable += 1;
                else acc.editable += 1;
                return acc;
            },
            { immutable: 0, calculated: 0, editable: 0 },
        );
        const next: GridViewStats = {
            totalRows: rows.filter((row) => !row.isPlaceholder && !row.isDeleted).length,
            filteredRows: visible.length,
            totalCols: columns.length,
            filtersApplied: filtersAppliedRef.current,
            immutableCols: counted.immutable,
            calculatedCols: counted.calculated,
            editableCols: counted.editable,
        };
        const previous = lastStatsRef.current;
        if (
            previous !== null &&
            previous.totalRows === next.totalRows &&
            previous.filteredRows === next.filteredRows &&
            previous.totalCols === next.totalCols &&
            previous.filtersApplied === next.filtersApplied &&
            previous.immutableCols === next.immutableCols &&
            previous.calculatedCols === next.calculatedCols &&
            previous.editableCols === next.editableCols
        ) {
            return;
        }
        lastStatsRef.current = next;
        onViewStatsChange(next);
    }, [descriptors, onViewStatsChange, rows]);

    const resetGridView = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const plugins = await grid.getPlugins();
        const filterPlugin = plugins.find((plugin) => "clearFiltering" in plugin) as
            | { clearFiltering: () => Promise<void> }
            | undefined;
        await filterPlugin?.clearFiltering();
        await grid.clearSorting();
        userSortedSectionsRef.current.delete(section);
        gridRef.current?.classList.remove("raw-materials-grid--user-sorted");
    }, [section]);

    const resetColumnWidths = useCallback(() => {
        resizedWidthsRef.current.clear();
        setWidthsVersion((version) => version + 1);
    }, []);

    const buildCopyText = useCallback(
        async (range: RangeArea | null): Promise<string | null> => {
            if (columns.length === 0) return null;
            const allProps = flatColumns.map((column) => column.prop);
            const lines: string[] = [];
            if (selectedRowIds.length > 0) {
                for (const id of selectedRowIds) {
                    const row = rows.find((candidate) => candidate.id === id);
                    if (!row) continue;
                    lines.push(allProps.map((prop) => serializeCellValue(row[prop])).join("\t"));
                }
            } else {
                const visible = await getVisibleRows();
                const target = range ?? {
                    x: 0,
                    y: 0,
                    x1: flatColumns.length - 1,
                    y1: visible.length - 1,
                };
                if (target.x1 < target.x || target.y1 < target.y) return null;
                const props = flatColumns
                    .slice(target.x, target.x1 + 1)
                    .map((column) => column.prop);
                for (let y = target.y; y <= Math.min(target.y1, visible.length - 1); y++) {
                    const row = visible[y];
                    if (!row) continue;
                    lines.push(props.map((prop) => serializeCellValue(row[prop])).join("\t"));
                }
            }
            const text = lines.join("\n");
            return text || null;
        },
        [columns, flatColumns, getVisibleRows, rows, selectedRowIds],
    );

    const copySelection = useCallback(async () => {
        const text = await buildCopyText(selectionRangeRef.current);
        if (text === null) return;
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
                // clipboard may be unavailable, ignore
            }
        }
    }, [buildCopyText]);

    const cutSelection = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const range = selectionRangeRef.current;
        if (!range) return;
        const text = await buildCopyText(range);
        if (text === null) return;
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
                // buffer unavailable — don't clear the source
                return;
            }
        }
        // Models come from the current visible source: under sorting/filtering
        // the displayed row index doesn't match the original rows index.
        const visible = await getVisibleRows();
        const data: Record<string, Record<string, unknown>> = {};
        const models: GridRow[] = [];
        for (let y = range.y; y <= Math.min(range.y1, visible.length - 1); y++) {
            models[y] = visible[y];
            const patch: Record<string, unknown> = {};
            for (let x = range.x; x <= range.x1; x++) {
                const column = flatColumns[x] as ColumnRegular | undefined;
                if (!column) continue;
                const descriptor = descriptorByKey.get(String(column.prop));
                if (!descriptor || !editableKeys.has(descriptor.key)) continue;
                // Boolean fields have no "empty" state — cutting skips them.
                if (descriptor.kind === "boolean") continue;
                patch[String(column.prop)] = descriptor.kind === "assetsMulti" ? [] : "";
            }
            if (Object.keys(patch).length > 0) data[String(y)] = patch;
        }
        if (Object.keys(data).length === 0) return;
        // Go through rangeeditapply: beforeRangeEdit itself skips busy and
        // locked cells (the values are already in the clipboard by then).
        grid.dispatchEvent(
            new CustomEvent("rangeeditapply", {
                detail: {
                    data,
                    models,
                    type: "rgRow",
                    oldRange: range,
                    newRange: range,
                },
                bubbles: true,
                composed: true,
            }),
        );
        // Select the cut range (rangeeditapply doesn't move the selection itself).
        void applyGridRange({ x: range.x, y: range.y }, { x: range.x1, y: range.y1 });
    }, [applyGridRange, buildCopyText, descriptorByKey, editableKeys, flatColumns, getVisibleRows]);

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
            // Excel and other apps put a trailing line break (and trailing tabs)
            // in the buffer — without trimming, an extra empty cell below gets
            // captured and overwrites a neighbor nobody asked for.
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

            // One cell — paste the whole buffer starting from it. Two or more —
            // paste only into the selection: a bigger buffer is trimmed by it
            // (Toast), a smaller one is pasted as-is, other cells untouched.
            // Models come from the current visible source: under sorting/filtering
            // the displayed row index doesn't match the original rows index.
            const visible = await getVisibleRows();
            let toY = range.y;
            let toY1 = range.y1;
            let toX = range.x;
            let toX1 = range.x1;
            if (singleCell) {
                toY1 = Math.min(visible.length - 1, range.y + pasteHeight - 1);
                toX1 = Math.min(flatColumns.length - 1, range.x + pasteWidth - 1);
            } else if (pasteHeight > selectedHeight || pasteWidth > selectedWidth) {
                onPasteTruncated?.();
            }

            const data: Record<string, Record<string, unknown>> = {};
            const models: GridRow[] = [];
            for (let y = toY; y <= toY1; y++) {
                models[y] = visible[y];
                const patch: Record<string, unknown> = {};
                for (let x = toX; x <= toX1; x++) {
                    const column = flatColumns[x] as ColumnRegular | undefined;
                    if (!column) continue;
                    const sourceCol = parsed[y - range.y]?.[x - range.x];
                    if (sourceCol === undefined) continue;
                    patch[String(column.prop)] = sourceCol;
                }
                if (Object.keys(patch).length > 0) data[String(y)] = patch;
            }
            // newRange defines both the apply area and the resulting selection —
            // after the paste, exactly the pasted cells are selected.
            const newRange: RangeArea = { y: toY, y1: toY1, x: toX, x1: toX1 };
            grid.dispatchEvent(
                new CustomEvent("rangeeditapply", {
                    detail: {
                        data,
                        models,
                        type: "rgRow",
                        oldRange: range,
                        newRange,
                    },
                    bubbles: true,
                    composed: true,
                }),
            );
            // After the paste, select exactly the pasted cells — and presence
            // picks up the new range via updateSelectionRange
            // (the setrange event doesn't emit this path).
            void applyGridRange({ x: toX, y: toY }, { x: toX1, y: toY1 });
        },
        [applyGridRange, flatColumns, getVisibleRows, onPasteTruncated],
    );

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
                const selection = window.getSelection()?.toString() ?? "";
                event.clipboardData?.setData("text/plain", selection);
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            // Range copying goes through the same async path as Ctrl+C (the
            // current visible source is needed: sorting/filtering change the
            // index mapping).
            event.preventDefault();
            event.stopPropagation();
            void copySelection();
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
            setSelectedRowIds([]);
            rowSelectionAnchorRef.current = null;
        };
        const handlePaste = (event: ClipboardEvent) => {
            const grid = gridRef.current;
            if (!grid) return;
            if (!isGridTarget(event, grid)) return;
            const isEditable = (element: Element | null) =>
                !!element &&
                (element.tagName === "INPUT" ||
                    element.tagName === "TEXTAREA" ||
                    (element as HTMLElement).isContentEditable);
            if (
                isEditable(document.activeElement) ||
                isEditable(grid.shadowRoot?.activeElement ?? null)
            ) {
                return;
            }
            const text = event.clipboardData?.getData("text/plain");
            if (text == null || text === "") return;
            event.preventDefault();
            event.stopPropagation();
            void pasteIntoSelection(text);
        };
        const handleAfterFilterApply = () => {
            void measureViewport();
        };
        // A header click on RevoGrid's native filter funnel also falls through to
        // the column sort (`beforeheaderclick`), so opening a filter silently
        // sorts the column as well. That extra sort is what makes the funnel
        // (and the newly revealed sort indicator) jump from the left edge of the
        // header cell to the right. Suppress the sort for funnel clicks in the
        // capture phase — before the sorting plugin's bubble handler — while
        // still letting the click reach the filter plugin (preventDefault does
        // not stop propagation). `rv-filter` is RevoGrid's filter-button class.
        const handleBeforeHeaderClickCapture = (event: Event) => {
            const originalEvent = (event as CustomEvent<{ originalEvent?: MouseEvent | null }>)
                .detail?.originalEvent;
            const target = originalEvent?.target as Element | null;
            if (!target) return;
            const hit = target.classList?.contains("rv-filter") || target.closest?.(".rv-filter");
            if (hit) event.preventDefault();
        };
        const handleCancelEdit = () => {
            if (!activeLeaseRef.current) return;
            onReleaseLease?.([activeLeaseRef.current]);
            activeLeaseRef.current = null;
        };
        window.addEventListener("copy", handleCopy, true);
        window.addEventListener("paste", handlePaste, true);
        grid.addEventListener("setrange", handleSetRange);
        grid.addEventListener("focuscell", handleFocusCell);
        grid.addEventListener("afterfilterapply", handleAfterFilterApply);
        grid.addEventListener("canceledit", handleCancelEdit);
        grid.addEventListener("beforeheaderclick", handleBeforeHeaderClickCapture, true);
        return () => {
            window.removeEventListener("copy", handleCopy, true);
            window.removeEventListener("paste", handlePaste, true);
            grid.removeEventListener("setrange", handleSetRange);
            grid.removeEventListener("focuscell", handleFocusCell);
            grid.removeEventListener("afterfilterapply", handleAfterFilterApply);
            grid.removeEventListener("canceledit", handleCancelEdit);
            grid.removeEventListener("beforeheaderclick", handleBeforeHeaderClickCapture, true);
        };
    }, [copySelection, pasteIntoSelection, updateSelectionRange, measureViewport]);
    useImperativeHandle(
        forwardedRef,
        () => ({
            getExportState,
            copySelection,
            pasteIntoSelection,
            clearSelection: clearGridSelection,
            resetColumnWidths,
            resetFiltersSorting: resetGridView,
        }),
        [
            clearGridSelection,
            copySelection,
            getExportState,
            pasteIntoSelection,
            resetColumnWidths,
            resetGridView,
        ],
    );

    useEffect(() => {
        const isEditableTarget = (active: Element | null) =>
            active instanceof HTMLElement &&
            (active.tagName === "INPUT" ||
                active.tagName === "TEXTAREA" ||
                active.isContentEditable);

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !document.fullscreenElement) {
                if (isEditableTarget(document.activeElement)) return;
                // Same path as the row-selection reset: clears the range,
                // repaints the overlay and drops revo-grid's stale focus restore.
                clearGridSelection();
                setSelectedRowIds([]);
                rowSelectionAnchorRef.current = null;
                return;
            }
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
            // event.code — the physical key: works in any layout
            // (in Russian event.key gives «с»/«м»/«ч»/«ф»).
            if (
                event.code !== "KeyC" &&
                event.code !== "KeyV" &&
                event.code !== "KeyX" &&
                event.code !== "KeyA"
            )
                return;
            if (isEditableTarget(document.activeElement)) return;
            const grid = gridRef.current;
            if (!grid || !isGridTarget(event, grid)) return;
            event.preventDefault();
            if (event.code === "KeyA") {
                // Listen in the capture phase to intercept Ctrl+A before revo-grid's
                // built-in select-all (it selects headers and tears the frame).
                // stopImmediatePropagation — reliably silences other
                // document listeners (incl. revo-grid itself), in any order.
                event.stopImmediatePropagation();
                void selectAllCells();
            } else if (event.code === "KeyC") {
                void copySelection();
            } else if (event.code === "KeyX") {
                void cutSelection();
            } else {
                void pasteIntoSelection();
            }
        };
        // capture: intercept Ctrl+A before revo-grid.
        document.addEventListener("keydown", handleKeyDown, true);
        return () => document.removeEventListener("keydown", handleKeyDown, true);
    }, [clearGridSelection, copySelection, cutSelection, pasteIntoSelection, selectAllCells]);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
        });
        const observer = new ResizeObserver(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
        });
        if (gridRef.current) observer.observe(gridRef.current);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [columns, headerPinned, measureViewport, refreshHorizontalScrollbar, rows]);

    // Wheel over the horizontal scrollbar strip scrolls it horizontally
    // (native Chrome behavior scrolls the vertical axis instead). The strips
    // are real DOM elements: revo-grid's virtual horizontal scrollbar in
    // pinned mode and our external one in unpinned mode. Native listener —
    // React's onWheel is passive and can't preventDefault.
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
            // No horizontal overflow — let the wheel fall through so the
            // vertical axis keeps working.
            if (strip.scrollWidth <= strip.clientWidth) return;
            event.preventDefault();
            strip.scrollLeft += event.deltaY + event.deltaX;
        };
        shell.addEventListener("wheel", handleWheel, { passive: false });
        return () => shell.removeEventListener("wheel", handleWheel);
    }, []);

    return (
        <div
            ref={shellRef}
            className={[
                "revo-grid-shell",
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
            onPointerMove={(event) => {
                const target = (event.target as Element).closest?.("[data-remote-lock]") ?? null;
                if (target === remoteTooltipTargetRef.current) return;
                remoteTooltipTargetRef.current = target;
                if (!target) {
                    setRemoteTooltip(null);
                    return;
                }
                const rect = target.getBoundingClientRect();
                setRemoteTooltip({
                    text:
                        target.getAttribute("data-remote-lock") ??
                        "Ячейка занята другим пользователем",
                    left: rect.left + rect.width / 2,
                    top: rect.top,
                });
            }}
            onPointerLeave={() => {
                remoteTooltipTargetRef.current = null;
                setRemoteTooltip(null);
            }}
        >
            <Tooltip isOpen={remoteTooltip !== null}>
                <Tooltip.Trigger
                    className="remote-cell-tooltip-trigger"
                    style={{
                        left: remoteTooltip?.left ?? -1000,
                        top: remoteTooltip?.top ?? -1000,
                    }}
                >
                    <span aria-hidden="true" />
                </Tooltip.Trigger>
                <Tooltip.Content placement="top">{remoteTooltip?.text}</Tooltip.Content>
            </Tooltip>
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                <RevoGrid
                    ref={gridRef}
                    className={[
                        "raw-materials-grid",
                        headerHeight > 44 && "raw-materials-grid--merged-header",
                        userSortedSectionsRef.current.has(section) &&
                            "raw-materials-grid--user-sorted",
                    ]
                        .filter(Boolean)
                        .join(" ")}
                    columns={columns}
                    source={rows}
                    theme="compact"
                    range
                    useClipboard={false}
                    resize
                    rowHeaders={rowHeaders}
                    editors={editors}
                    filter={RUSSIAN_FILTER_CONFIG}
                    sorting={sorting}
                    hideAttribution
                    readonly={readOnly}
                    rowSize={36}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
                        void measureViewport();
                    }}
                    onBeforesorting={() => {
                        userSortedSectionsRef.current.add(section);
                        gridRef.current?.classList.add("raw-materials-grid--user-sorted");
                    }}
                    onAftersortingapply={(event) => {
                        sortingBySectionRef.current.set(
                            section,
                            sortingConfigFromEvent(event.detail),
                        );
                    }}
                    onBeforefilterapply={(event) => {
                        // Filtering trims the data source synchronously, and there
                        // is no public event after it — re-measure the filtered
                        // row count on the next tick. The event carries the new
                        // filter collection (empty when filters were cleared).
                        const collection = (
                            event as CustomEvent<{ collection?: Record<string, unknown> }>
                        ).detail?.collection;
                        filtersAppliedRef.current =
                            !!collection && Object.keys(collection).length > 0;
                        setTimeout(() => void measureViewport(), 0);
                    }}
                    onContentsizechanged={() => void refreshHorizontalScrollbar()}
                    onViewportscroll={(event) => {
                        if (event.detail.dimension === "rgCol") {
                            if (!headerPinned) {
                                const scrollbar = horizontalScrollRef.current;
                                if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                                    scrollbar.scrollLeft = event.detail.coordinate;
                                }
                            }
                        }
                        void measureViewport();
                    }}
                    onAftercolumnresize={(event) => {
                        for (const column of Object.values(event.detail)) {
                            if (column?.prop !== undefined && typeof column.size === "number") {
                                resizedWidthsRef.current.set(
                                    columnWidthKey(widthKeySection, String(column.prop)),
                                    column.size,
                                );
                            }
                        }
                        void refreshHorizontalScrollbar();
                        void measureViewport();
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

export const RawMaterialsGrid = forwardRef(RawMaterialsGridInner);

function canonicalOptions(
    values: unknown[],
    options: ReferenceOption[],
): { ok: true; value: string[] } | { ok: false; message: string } {
    const byName = new Map(
        options.map(({ name }) => [name.trim().toLocaleLowerCase("ru-RU"), name.trim()]),
    );
    const result: string[] = [];
    const seen = new Set<string>();
    for (const value of values) {
        const requested = String(value ?? "").trim();
        if (!requested) continue;
        const canonical = byName.get(requested.toLocaleLowerCase("ru-RU"));
        if (!canonical) {
            return { ok: false, message: `значение «${requested}» отсутствует в справочнике.` };
        }
        const key = canonical.toLocaleLowerCase("ru-RU");
        if (!seen.has(key)) {
            seen.add(key);
            result.push(canonical);
        }
    }
    return { ok: true, value: result };
}

function translateUpdates(
    section: SectionId,
    updates: CellUpdate[],
    assets: ReferenceOption[],
): CellUpdate[] {
    if (section === "rawMaterials") {
        return updates.map((update) =>
            update.field === "assetNames"
                ? { ...update, field: "assetIds", value: namesToIds(update.value, assets) }
                : update,
        );
    }
    return updates;
}

function translatePlaceholderPatch(
    section: SectionId,
    patch: Record<string, unknown>,
    assets: ReferenceOption[],
): Record<string, unknown> {
    if (section === "rawMaterials" && "assetNames" in patch) {
        return {
            ...patch,
            assetIds: namesToIds(patch.assetNames as CellUpdate["value"], assets),
        };
    }
    return patch;
}

function namesToIds(value: CellUpdate["value"], assets: ReferenceOption[]): CellUpdate["value"] {
    const names = Array.isArray(value) ? value : [];
    const idsByName = new Map(
        assets.map(({ id, name }) => [name.trim().toLocaleLowerCase("ru-RU"), id]),
    );
    return names.map((name) => idsByName.get(name.toLocaleLowerCase("ru-RU")) ?? name);
}

function columnWidthKey(section: string, prop: string): string {
    return `${section}\u0000${prop}`;
}

export function restoreColumnWidths(
    columns: Array<{ prop?: unknown; size?: number; children?: unknown[] }>,
    widths: Map<string, number>,
    section: string,
) {
    for (const column of columns) {
        if (column.prop !== undefined) {
            const width = widths.get(columnWidthKey(section, String(column.prop)));
            if (width !== undefined) column.size = width;
        }
        if (Array.isArray(column.children)) {
            restoreColumnWidths(
                column.children as Array<{ prop?: unknown; size?: number; children?: unknown[] }>,
                widths,
                section,
            );
        }
    }
}
