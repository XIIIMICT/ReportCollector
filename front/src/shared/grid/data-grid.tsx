import { Tooltip } from "@heroui/react";
import {
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
import type { GridPlugin } from "@revolist/revogrid";
import type { MergeData } from "@revolist/rv-pro-trial";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    forwardRef,
    useImperativeHandle,
    type CSSProperties,
    type MouseEvent as ReactMouseEvent,
    type Ref,
} from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    columnMenuItems,
    GridContextMenu,
    GridUnhideButton,
    type GridMenuItem,
    type GridMenuRequest,
} from "@/shared/grid/grid-header-menu";
import { keepsGridSelection } from "@/shared/grid/grid-selection";
import {
    hiddenColumnBlocks,
    useGridHiddenColumns,
    type HiddenColumnAnchors,
} from "@/shared/grid/hidden-columns";
import { type HiddenColumnEntry } from "@/shared/grid/hidden-columns-list";
import { isColumnPinned, useGridPinnedColumns } from "@/shared/grid/pinned-columns";
import { reactEditor } from "@/shared/grid/react-editor";
import { createGridEpoch, createRevoGridAdapter } from "@/shared/grid/revo-grid-adapter";
import { readStoredSorting, writeStoredSorting } from "@/shared/grid/use-grid-column-filters";
import { isFromTextEditor } from "@/shared/keyboard";
import {
    computeRowFacets,
    facetWindow,
    precedingColumnFilters,
    rowMatchesFilters,
    searchRowFacets,
    withFilterMarkers,
    type ColumnFilter,
} from "@/shared/ui/column-filter-facets";
import { ColumnFilterPopover, type FacetSource } from "@/shared/ui/column-filter-popover";
import { hasOpenKeyboardOverlay } from "@/shared/ui/modal-presence";
import { RowGoToFilter } from "@/shared/ui/row-go-to-filter";
import { hasOpenSmartSelect } from "@/shared/ui/smart-select";

import {
    buildGridColumns,
    exportColumnsFromGrid,
    facetOrderOf,
    facetTextOf,
    getEditableKeys,
    getVisibleDescriptors,
    markFilterableColumns,
    placeholderLastCellCompare,
    SERVICE_DESCRIPTORS,
    type ColumnDescriptor,
    type ExportColumn,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "./grid-config";
import { extractEdits, parseNumericText } from "./grid-edit";
import { MultiAssetEditor, ReferenceEditor, SelectEditor } from "./grid-editors";
import { parsePastedText, pasteTargetRange, serializeCellValue } from "./index";
import { readScrollPosition, scrollScopeKey, writeScrollPosition } from "./scroll-position";
import { isChunkedSection } from "./section-policy";
import { describeSelection } from "./selection-label";

/** The pinned summary row's id (revo-grid `pinnedTopSource`) — never a real record. */
export const SUMMARY_ROW_ID = "__summary__";

/**
 * The shared editable grid of the application.
 *
 * It is the table behind THREE projects — «Сырьё», «КППК» and the renovation
 * reference dictionaries — which is why it is named after what it IS and not
 * after one of its callers. It used to be `RawMaterialsGrid` in
 * `raw-materials-grid.tsx`, with a `.raw-materials-grid` root class styling all
 * of them: a name that told every reader of КППК and the renovation dictionaries
 * that they were looking at «Сырьё».
 *
 * The object-level plan is NOT a consumer: it renders its own RevoGrid
 * (`features/renovation/renovation-grid.tsx`), so a change made here does not
 * reach it.
 */
/** Which form a remembered position belongs to, and in which scope. */
export type ScrollPositionKey = {
    /** Stable form key: project + section + variant (e.g. `renovation:plan`). */
    form: string;
    /** Filters/sorting/version the position was taken in. */
    scope?: unknown;
};

/**
 * Stable empty pinned strip. The `pinnedTopSource` prop must ALWAYS be assigned
 * (even with no summary row): the React wrapper only writes props it receives,
 * so omitting the prop when a section has no summary left the previous
 * section's pinned row on the element — a blank strip appeared in every
 * dictionary after visiting «Предприятия». A fresh `[]` per render is not an
 * option either: Stencil's `@Watch` fires on every reference change.
 */
const EMPTY_PINNED_SOURCE: Record<string, unknown>[] = [];

/** Stable empty plugin/merge lists: a fresh array would reset the plugin setup on every render. */
const EMPTY_PLUGIN_LIST: GridPlugin[] = [];
/**
 * Shared empty lease map. `new Map()` as a default prop value is re-created on
 * every render for every caller that omits the prop, which breaks referential
 * equality and re-runs the column memo and the lease lookups with it.
 */
const EMPTY_LEASE_MAP: ReadonlyMap<string, { fullname: string; email: string }> = new Map();
/** The same for the remote-cell previews (tooltips over other users' cells). */
const EMPTY_CELL_INFO: ReadonlyMap<string, { name: string; label: string | null }> = new Map();

export type DataGridState = {
    columns: ExportColumn[];
    rowIds: string[];
    mergeRows: boolean;
};

export type GridViewStats = {
    totalRows: number;
    /** Rows matching the current filters (non-placeholder, non-deleted). */
    filteredRows: number;
    totalCols: number;
    /** Whether any column filter is currently applied. */
    filtersApplied: boolean;
    /**
     * The columns an applied filter narrows, each as its full header CHAIN —
     * group first, leaf last (a grouped column reads «Иерархия → Лист»), which
     * is what the status tooltip lists. Empty when nothing is filtered.
     */
    filteredColumns: string[][];
    /**
     * The columns the table is SORTED by, in the order RevoGrid applies them —
     * each as its header chain (like `filteredColumns`) plus the direction.
     * Empty when the table is unsorted.
     */
    sortedColumns: Array<{ path: string[]; order: "asc" | "desc" }>;
    /** Fixed (non-calculated) columns — not editable. */
    immutableCols: number;
    /** Calculated columns. */
    calculatedCols: number;
    /** Editable columns. */
    editableCols: number;
    /**
     * Columns hidden by the user, in table order — the same numbered list the
     * expand button's tooltip shows. Service columns appear too while their
     * project-wide toggle is off.
     */
    hiddenColumns?: HiddenColumnEntry[];
};

export type DataGridHandle = {
    getExportState: () => Promise<DataGridState>;
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
    /** Whether the project-wide service columns are shown. */
    showServiceColumns: boolean;
    /** Show/hide the service columns for the whole project (the + button). */
    onServiceColumnsChange?: (visible: boolean) => void;
    /** Controlled RevoGrid data row height. */
    rowHeight: number;
    /** Pinned summary row(s) rendered above the data (revo-grid `pinnedTopSource`). */
    pinnedTopSource?: Record<string, unknown>[] | undefined;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    /** Custom column set — overrides the built-in descriptors for the section. */
    columnDescriptors?: ColumnDescriptor[];
    /**
     * Whether the header sorts on click (default true). The КППК realization
     * view keeps sorting enabled and temporarily disables product-cell merges.
     */
    sortable?: boolean;
    /** Distinguishes width/sorting persistence when several grids share a section id. */
    namespace?: string;
    /**
     * Remember the row the user left and reopen the form there. The KEY names
     * the form (project + section), the value is the scope the position belongs
     * to — filters, sorting, version — so a position is only reused inside the
     * scope it was taken in. Omit it and the form keeps no position (it opens
     * at its own default, e.g. the renovation plan at the tail).
     */
    scrollPosition?: ScrollPositionKey | undefined;
    /**
     * RevoGrid pro plugins (trial build) — the «Реализация» form passes
     * CellMergePlugin for its merged product cells.
     */
    plugins?: GridPlugin[] | undefined;
    /**
     * Merged-cell ranges (rowSpan) of the visible data — the «Реализация»
     * product column folds each product's region rows into one cell. Always
     * assigned (empty by default) so the prop never lingers after a section
     * switch — same trick as `pinnedTopSource`.
     */
    cellMerge?: MergeData[];
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
    /** Reports whether a user sorting is currently active. */
    onSortingChange?: (active: boolean) => void;
    onClipboardStateChange?: (state: { canPaste: boolean }) => void;
    /** Completed selection (for presence): label and cell keys of the range. */
    onSelectionChange?: (selection: {
        label: string | null;
        cellKeys: string[];
        /** The selection spelled out for the status tooltip (header chain). */
        detail?: string | null;
    }) => void;
    /** Keys of cells where other members are (highlight + tooltip). */
    remoteCellInfo?: ReadonlyMap<string, { name: string; label: string | null }>;
    /**
     * The ??? corner's funnel was pressed (anchor = its rect). The owner opens
     * `RowGoToFilter` ? the shared component ? with its own totals.
     */
    onOpenGoTo?: (anchor: DOMRect) => void;
} & ClientColumnFilterOptions;

/**
 * What the grid needs to own its header filters client-side.
 *
 * Every column filter in the app is the same popover (see
 * `shared/ui/column-filter-popover.tsx`); what differs is where the options
 * come from. A client grid holds its rows in memory, so the WORKSPACE hands in
 * the visible rows and the grid computes the facets itself
 * (`computeRowFacets`) — no round trip, no server-side filter plumbing.
 *
 * `empty` when omitted: the grid then draws no funnels at all (an archived,
 * read-only view can pass nothing).
 */
type ClientColumnFilterOptions =
    | {
          /** Active filters keyed by column prop; the grid shows the active funnel. */
          filters: Record<string, ColumnFilter>;
          /** Commit the whole filter map (an empty map clears every column). */
          onFiltersChange: (filters: Record<string, ColumnFilter>) => void;
      }
    | {
          filters?: undefined;
          onFiltersChange?: undefined;
      }
    | undefined;

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

/** Marks a header-properties function that already adds the summary-merge class. */
function markSummaryMerge<T>(properties: T): T {
    Object.defineProperty(properties as object, "__summaryMerge", {
        value: true,
        enumerable: false,
    });
    return properties;
}

/** Does this header-properties function already add the summary-merge class? */
function hasSummaryMergeMarker(properties: ColumnRegular["columnProperties"] | undefined): boolean {
    const probe = properties as unknown as { __summaryMerge?: boolean } | undefined;
    return probe?.__summaryMerge === true;
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

/**
 * The shared editable grid of the application.
 *
 * It is the table behind THREE projects — «Сырьё», «КППК» and the renovation
 * reference dictionaries — which is why it is named after what it IS and not
 * after one of its callers. It used to be `RawMaterialsGrid` in
 * `raw-materials-grid.tsx`, with a `.raw-materials-grid` root class styling all
 * of them: a name that told every reader of КППК and the renovation dictionaries
 * that they were looking at «Сырьё».
 *
 * The object-level plan is NOT a consumer: it renders its own RevoGrid
 * (`features/renovation/renovation-grid.tsx`), so a change made here does not
 * reach it.
 */
function DataGridInner(
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
        onServiceColumnsChange,
        rowHeight,
        pinnedTopSource,
        assets,
        deliveryBases,
        columnDescriptors,
        sortable = true,
        namespace,
        plugins,
        cellMerge,
        onCommit,
        onPlaceholderCommit,
        onCommitFailed,
        onSelectedRowsChange,
        onValidationError,
        onPasteSkipped,
        onPasteTruncated,
        remoteLocks = EMPTY_LEASE_MAP,
        remotePresence = EMPTY_LEASE_MAP,
        onLockedCellEdit,
        onAcquireLease,
        onReleaseLease,
        onViewStatsChange,
        onSortingChange,
        onClipboardStateChange,
        onSelectionChange,
        remoteCellInfo = EMPTY_CELL_INFO,
        filters: columnFilters,
        onFiltersChange,
        scrollPosition,
    }: Props,
    forwardedRef: Ref<DataGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const gridAdapter = useMemo(() => createRevoGridAdapter<GridRow>(gridRef), []);
    // CellMergePlugin binds its config to the grid's `cellMerge` DOM property
    // through the same property watcher as `hideColumns` (the plugin's setter
    // interceptor fires on any later assignment too), but the React wrapper's
    // typings don't know the prop — so it's assigned directly on the element.
    //
    // The merges are INDEX-based, so they only hold for the rows they were
    // computed on: once the view is FILTERED or SORTED, a merge can join two
    // unrelated records. Both triggers therefore turn them off, and this effect
    // must not put them back — it used to, because filtering does not change the
    // prop, but the state below survives only while nothing re-applies it.
    /** The merges the owner configured — restored when the view is reset. */
    const configuredCellMergeRef = useRef(cellMerge);
    configuredCellMergeRef.current = cellMerge;
    /**
     * Merges must be OFF while any header filter is active.
     *
     * `onBeforefilterapply` only covers the moment a filter is APPLIED — a filter
     * restored from `localStorage` on mount, or a source set while one is
     * active, never fires it, and the merges came back over the filtered rows
     * (a product block spanning two unrelated records). This is derived state
     * instead of an event, so every path into a filtered view agrees.
     */
    const filtersActive = columnFilters !== undefined && Object.keys(columnFilters).length > 0;
    const shellRef = useRef<HTMLDivElement>(null);
    const verticalScrollRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    const filtersAppliedRef = useRef(false);
    // Unpinned mode: the external horizontal scrollbar is shown only when the
    // grid content actually overflows horizontally (native behavior elsewhere).
    const [hasHorizontalOverflow, setHasHorizontalOverflow] = useState(false);
    // The vertical scrollbar's lane: the width it takes away from the data area at
    // the grid's right edge. The custom horizontal strip must END before it, so the
    // two bars never overlap in the bottom-right corner (the renovation plan does
    // the same). revo-grid writes the reserved space into the strip's inline
    // min-width; overlay-scrollbar platforms report 0 (the bar paints over the grid
    // instead of taking a lane).
    const [verticalScrollbarLane, setVerticalScrollbarLane] = useState(0);
    const verticalScrollbarLaneRef = useRef(0);
    const measureVerticalScrollbarLane = useCallback(() => {
        const strip = gridRef.current?.querySelector(
            "revogr-scroll-virtual.vertical",
        ) as HTMLElement | null;
        const reserved = strip ? Number.parseFloat(strip.style.minWidth) : 0;
        const lane = Number.isFinite(reserved) && reserved > 0 ? Math.round(reserved) : 0;
        if (lane === verticalScrollbarLaneRef.current) return;
        verticalScrollbarLaneRef.current = lane;
        setVerticalScrollbarLane(lane);
    }, []);
    const resizedWidthsRef = useRef(new Map<string, number>());
    // Bumped after a widths reset: the columns memo reads the ref, so it needs
    // a dependency change to rebuild with default sizes. Read as a `void`
    // statement inside the memo so the dependency is genuinely used — the linter
    // cannot see through `restoreColumnWidths` into the ref it touches.
    const [widthsVersion, setWidthsVersion] = useState(0);
    const widthKeySection = namespace ?? section;
    // The form whose position is remembered; both parts are per scope.
    const scrollFormKey = scrollPosition?.form ?? null;
    const scrollScope = scrollScopeKey(scrollPosition?.scope);
    const sortingBySectionRef = useRef(new Map<SectionId, GridSortingConfig>());
    /** The pointer state of the current header click (its Ctrl modifier). */
    const lastMouseEventRef = useRef<MouseEvent | null>(null);
    /**
     * Ctrl was held on the header click being handled. The plugin neither passes
     * the modifier through nor learns it, so it is captured from the click itself
     * (`onHeaderclick`) and read by `onBeforesorting` for that same click.
     */
    const additiveSortRef = useRef(false);
    /**
     * Sections whose remembered sorting has been restored. `localStorage` is
     * read ONCE per section, on mount: re-reading it on every render would let a
     * stale entry fight the user's own clicks.
     */
    const restoredSortingRef = useRef(new Set<SectionId>());
    /**
     * The live sorting of this section. `sortingBySectionRef` is the imperative
     * copy (event handlers read it without a re-render); this state is what lets
     * `columns` be rebuilt with the current `order` on every leaf.
     */
    const [sortingVersion, setSortingVersion] = useState(0);
    const bumpSorting = useCallback(() => setSortingVersion((version) => version + 1), []);
    /**
     * The storage scope of this grid's sorting. `namespace` is meant to name the
     * PROJECT (several grids sharing a section id), but some call sites pass
     * `project:section` — so the scope is the part before the first colon when
     * there is one, which is the project in both shapes.
     */
    const sortingScope = (namespace ?? section).split(":")[0] ?? section;
    /** Where the filter popovers remember their search drafts (same scheme). */
    const filterDraftScope = { project: sortingScope, section };
    const userSortedSectionsRef = useRef(new Set<SectionId>());
    /** Settles the burst of sort events into ONE header re-measure. */
    const sortMeasureTimer = useRef<number>(0);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);
    const rowValuesRef = useRef<Map<string, Readonly<Record<string, unknown>>>>(new Map());
    /** Set once the remembered row of this scope has been restored (or given up on). */
    const scrollRestoredRef = useRef(false);
    /** The scope the flags above belong to, so a form switch re-arms them. */
    const lastScrollScopeRef = useRef<string | null>(null);
    /** A restore is travelling: its transient scroll positions must not be saved. */
    const scrollRestoringRef = useRef(false);
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

    // ── Remembered position ────────────────────────────────────────────────
    // A form opens where the user left it. The stored value is the row ID, so
    // the restore looks it up in the list that is on screen NOW — a row that was
    // deleted (or hidden by a filter) simply leaves the form at its default
    // instead of silently landing on somebody else's record.
    useEffect(() => {
        // A different form or scope is a different position: forget the old one.
        scrollRestoredRef.current = false;
        scrollRestoringRef.current = false;
    }, [scrollFormKey, scrollScope]);

    useEffect(() => {
        if (scrollFormKey === null) return;
        if (typeof window === "undefined") return;
        // A form switch is a different position: re-arm for the new scope.
        if (lastScrollScopeRef.current !== `${scrollFormKey}\u0000${scrollScope}`) {
            lastScrollScopeRef.current = `${scrollFormKey}\u0000${scrollScope}`;
            scrollRestoredRef.current = false;
            scrollRestoringRef.current = false;
        }
        if (scrollRestoredRef.current) return;
        const grid = gridRef.current;
        if (!grid) return;
        const realRows = rows.filter((row) => !row.isPlaceholder && !row.isDeleted);
        if (realRows.length === 0) return;
        const stored = readScrollPosition(scrollFormKey, scrollScope);
        if (!stored) {
            // Nothing remembered: the form keeps its own default (the top).
            scrollRestoredRef.current = true;
            return;
        }
        const index = realRows.findIndex((row) => row.id === stored.rowId);
        // Give up after a bounded wait: the row may be filtered out for good, and
        // a form must never sit waiting for a position that cannot arrive.
        scrollRestoredRef.current = true;
        if (index <= 0) return;
        scrollRestoringRef.current = true;
        let attempts = 0;
        const attempt = () => {
            const current = gridRef.current;
            if (!current || attempts >= 20) {
                scrollRestoringRef.current = false;
                return;
            }
            attempts += 1;
            // A grid that has not laid itself out cannot scroll: wait for height.
            if (current.clientHeight < rowHeight * 3) {
                window.setTimeout(attempt, 80);
                return;
            }
            void (async () => {
                try {
                    await current.scrollToRow(index);
                } catch {
                    // Not upgraded yet — retried below.
                }
                window.setTimeout(() => {
                    const settled = gridRef.current;
                    const scroller = settled?.querySelector(
                        "revogr-scroll-virtual.vertical",
                    ) as HTMLElement | null;
                    if (!settled || attempts >= 20) {
                        scrollRestoringRef.current = false;
                        return;
                    }
                    if (Math.abs((scroller?.scrollTop ?? 0) - index * rowHeight) <= rowHeight * 2) {
                        scrollRestoringRef.current = false;
                        return;
                    }
                    attempt();
                }, 120);
            })();
        };
        attempt();
    }, [rows, rowHeight, scrollFormKey, scrollScope]);

    const rememberScrollPosition = useCallback(() => {
        if (scrollFormKey === null) return;
        const grid = gridRef.current;
        if (!grid) return;
        if (!scrollRestoredRef.current || scrollRestoringRef.current) return;
        void (async () => {
            try {
                const [visible, scroller] = await Promise.all([
                    gridAdapter.getVisibleRows(rows),
                    Promise.resolve(
                        grid.querySelector("revogr-scroll-virtual.vertical") as HTMLElement | null,
                    ),
                ]);
                const top = scroller?.scrollTop ?? 0;
                const index = Math.max(0, Math.round(top / rowHeight));
                const row = (visible as GridRow[])[index];
                // A placeholder slot (the «＋» add-row) is not a record: keep the
                // last real position instead of forgetting the form.
                if (!row || row.isPlaceholder || row.isDeleted) return;
                writeScrollPosition(scrollFormKey, { rowId: row.id, scope: scrollScope });
            } catch {
                // Grid disposed mid-read — the next scroll saves instead.
            }
        })();
    }, [gridAdapter, rowHeight, rows, scrollFormKey, scrollScope]);

    /** Debounced save: a scroll fires dozens of events, one write is enough. */
    const scrollRememberTimerRef = useRef<number | undefined>(undefined);
    const scheduleScrollRemember = useCallback(() => {
        if (scrollFormKey === null) return;
        if (scrollRememberTimerRef.current !== undefined) {
            window.clearTimeout(scrollRememberTimerRef.current);
        }
        scrollRememberTimerRef.current = window.setTimeout(() => {
            scrollRememberTimerRef.current = undefined;
            rememberScrollPosition();
        }, 250);
    }, [rememberScrollPosition, scrollFormKey]);

    useEffect(
        () => () => {
            if (scrollRememberTimerRef.current !== undefined) {
                window.clearTimeout(scrollRememberTimerRef.current);
            }
        },
        [],
    );

    const pushSortingToPlugin = useCallback((columns: GridSortingConfig["columns"] | undefined) => {
        const grid = gridRef.current;
        if (!grid) return;
        // The column list IS the priority: the plugin derives its
        // `sortingOrder` from it, and that order decides which column wins.
        grid.sorting = {
            columns: (columns ?? []).map((column) => ({
                prop: column.prop,
                order: column.order,
                cellCompare: placeholderLastCellCompare,
            })),
        };
    }, []);

    // A remembered sorting exists before the grid mounts, so nothing would ever
    // hand it over: pushed once, right after mount, in the stored order.
    useEffect(() => {
        pushSortingToPlugin(sortingBySectionRef.current.get(section)?.columns);
        // oxlint-disable-next-line react-hooks/exhaustive-deps
    }, [section, pushSortingToPlugin]);

    // The Ctrl modifier of a header click never reaches `beforesorting` (the
    // plugin reports neither it nor an `additive` flag), so the pointer event
    // that started the click is kept here: `onHeaderclick` reads it back.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const remember = (event: Event) => {
            lastMouseEventRef.current = event as MouseEvent;
        };
        grid.addEventListener("mousedown", remember, true);
        return () => {
            grid.removeEventListener("mousedown", remember, true);
        };
    }, []);

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || scrollFormKey === null) return;
        const onScroll = () => scheduleScrollRemember();
        grid.addEventListener("scrollchange", onScroll);
        return () => {
            grid.removeEventListener("scrollchange", onScroll);
        };
    }, [scheduleScrollRemember, scrollFormKey]);

    // A remembered sorting is rebuilt into a full config (with the comparator the
    // grid needs) once per section. Storage keeps only `prop` + `order`, so a
    // column that no longer exists simply drops out.
    //
    // The users dictionaries are a special case only in what happens with NO
    // stored sorting: nothing. They arrive ordered by «ФИО» FROM THE SERVER, which
    // is a default the backend owns — the grid must not invent a `fullname:asc`
    // of its own, or a click on another column would come back rewritten to it.
    if (!restoredSortingRef.current.has(section) && typeof window !== "undefined") {
        restoredSortingRef.current.add(section);
        const stored = readStoredSorting(sortingScope, section);
        if (stored.length > 0) {
            // The class the CSS keys the arrows on. A restored sorting is still a
            // USER sorting: without it the indicator stays `display: none` and
            // the header shows no arrow even though the rows are ordered.
            // The `sorting` prop below is what pushes it into the plugin.
            userSortedSectionsRef.current.add(section);
            sortingBySectionRef.current.set(section, {
                columns: stored.map((entry) => ({
                    prop: entry.prop,
                    order: entry.order,
                    cellCompare: placeholderLastCellCompare,
                })),
            });
        }
    }

    const sortingActive = (sortingBySectionRef.current.get(section)?.columns.length ?? 0) > 0;
    const syncSortPriorityMarkers = useCallback(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const columns = sortingBySectionRef.current.get(section)?.columns ?? [];
        const priorities = new Map(
            columns.map((column, index) => [String(column.prop), index + 1]),
        );
        for (const cell of grid.querySelectorAll<HTMLElement>(".rgHeaderCell")) {
            const prop = cell.querySelector("[data-prop]")?.getAttribute("data-prop");
            const indicator = cell.querySelector<HTMLElement>(".sort-indicator");
            if (!indicator) continue;
            const priority = prop === null || prop === undefined ? undefined : priorities.get(prop);
            if (priority === undefined) {
                if (indicator.hasAttribute("data-sort-priority")) {
                    indicator.removeAttribute("data-sort-priority");
                }
            } else {
                const value = String(priority);
                if (indicator.getAttribute("data-sort-priority") !== value) {
                    indicator.setAttribute("data-sort-priority", value);
                }
            }
        }
    }, [section]);
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        syncSortPriorityMarkers();
        // RevoGrid rebuilds the header cells on every sorting/columns change and
        // renders NO rank for a single sort, so the markers are re-applied
        // whenever a header node lands. The observer sits on the whole grid (the
        // header mount is asynchronous), but the callback ignores the batches that
        // only touch DATA rows — a 94k-row form scrolls constantly and must not
        // pay for a header scan on every virtual re-render.
        const touchesHeader = (records: MutationRecord[]) =>
            records.some((record) => {
                const target = record.target as Element | null;
                if (target?.closest?.("revogr-header")) return true;
                // The header element itself being mounted (it is created lazily).
                return [...record.addedNodes].some(
                    (node) => node.nodeType === 1 && (node as Element).tagName === "REVOGR-HEADER",
                );
            });
        const observer = new MutationObserver((records) => {
            if (touchesHeader(records)) syncSortPriorityMarkers();
        });
        observer.observe(grid, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, [sortingVersion, syncSortPriorityMarkers]);
    useEffect(() => {
        onSortingChange?.(sortingActive);
    }, [onSortingChange, sortingActive]);

    // Always OUR sorting — the user's clicks, a restored one, or nothing. It is
    // pushed to the plugin IMPERATIVELY (see `pushSortingToPlugin`), not through a
    // `sorting` prop: the plugin reads the column list order as the sort PRIORITY,
    // and a prop property arrives asynchronously, in an order React does not
    // promise — which came back as swapped sort levels after a reload.
    // Always OUR sorting — the user's clicks, a restored one, or nothing. It is
    // pushed to the plugin IMPERATIVELY (see `syncSortingToPlugin`), not through a
    // `sorting` prop: the plugin reads the column list order as the sort PRIORITY,
    // and a prop property arrives asynchronously and in an order React does not
    // promise — which came back as swapped sort levels after a reload.

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

    // ── Hidden data columns ────────────────────────────────────────────────
    // Persisted per table (project scope + section) exactly like the filters
    // and sorting. Fixed columns and the project-wide service columns are not
    // part of this set: they have their own rules.
    const { hidden, hide, show } = useGridHiddenColumns(sortingScope, section);
    const { overrides: pinnedOverrides, setMany: setPinnedMany } = useGridPinnedColumns(
        sortingScope,
        section,
    );
    const dataDescriptors = useMemo(
        () =>
            getVisibleDescriptors(section, showAssetColumn, false, {
                keycloakEnabled,
                descriptors: columnDescriptors,
            }),
        [section, showAssetColumn, keycloakEnabled, columnDescriptors],
    );
    /** Columns pinning to the left for the CURRENT table. */
    const pinnedKeys = useMemo(
        () =>
            new Set(
                dataDescriptors
                    .filter((descriptor) =>
                        isColumnPinned(descriptor.key, pinnedOverrides, descriptor.pinned),
                    )
                    .map((descriptor) => descriptor.key),
            ),
        [dataDescriptors, pinnedOverrides],
    );
    const hideableKeys = useMemo(
        () =>
            new Set(
                dataDescriptors
                    .filter((descriptor) => !pinnedKeys.has(descriptor.key))
                    .map((descriptor) => descriptor.key),
            ),
        [dataDescriptors, pinnedKeys],
    );
    const effectiveHidden = useMemo<HiddenColumnAnchors>(() => {
        const result: HiddenColumnAnchors = {};
        for (const [prop, anchor] of Object.entries(hidden)) {
            if (hideableKeys.has(prop)) result[prop] = anchor;
        }
        return result;
    }, [hidden, hideableKeys]);
    const hiddenKeys = useMemo(() => new Set(Object.keys(effectiveHidden)), [effectiveHidden]);
    const hiddenBlocks = useMemo(
        () =>
            hiddenColumnBlocks(
                dataDescriptors.map((descriptor) => descriptor.key),
                effectiveHidden,
            ),
        [dataDescriptors, effectiveHidden],
    );
    /**
     * The numbered hidden-column list of this table: data columns in their
     * full order, then the service trio while its project flag is off. The
     * number is the column's place in the TABLE, never the list index.
     */
    const hiddenColumnEntries = useMemo<HiddenColumnEntry[]>(() => {
        const entries: HiddenColumnEntry[] = [];
        dataDescriptors.forEach((descriptor, index) => {
            if (effectiveHidden[descriptor.key] === undefined) return;
            entries.push({
                number: index + 1,
                path: [...(descriptor.path ?? []), descriptor.label],
            });
        });
        if (!showServiceColumns) {
            SERVICE_DESCRIPTORS.forEach((descriptor, index) => {
                entries.push({
                    number: dataDescriptors.length + index + 1,
                    path: [descriptor.label],
                });
            });
        }
        return entries;
    }, [dataDescriptors, effectiveHidden, showServiceColumns]);
    const serviceKeys = useMemo(
        () => new Set(SERVICE_DESCRIPTORS.map((descriptor) => descriptor.key)),
        [],
    );
    /** The shared column/row context menu (opened by a header or a row number). */
    const [gridMenu, setGridMenu] = useState<GridMenuRequest | null>(null);
    const [gridMenuOpen, setGridMenuOpen] = useState(false);
    /**
     * Rows the user pinned. Session-only (unlike the persisted column pins):
     * a pinned row stays in the main source and gets a READ-ONLY copy right
     * under the column-number strip, so nothing is re-indexed and the original
     * remains where the user found it.
     */
    const [pinnedRowIds, setPinnedRowIds] = useState<string[]>([]);
    const pinnedRowIdSet = useMemo(() => new Set(pinnedRowIds), [pinnedRowIds]);
    /**
     * Snapshots of the pinned rows. The copy must survive a filter that hides
     * the original (filtering/sorting are VIEW state and must not evict a pin),
     * so the row is kept here and refreshed from `rows` while it still exists.
     */
    const [pinnedRowSnapshots, setPinnedRowSnapshots] = useState<Record<string, GridRow>>({});
    /**
     * The numbers the pinned copies show. Captured from the RENDERED row order
     * (RevoGrid sorts its own source, so `visibleRows` is not the display
     * order) and refreshed after every source pass — a pinned copy must keep
     * the same number as the row it mirrors.
     */
    const [pinnedRowNumbers, setPinnedRowNumbers] = useState<Record<string, number>>({});
    const pinnedRowIdsRef = useRef(pinnedRowIds);
    pinnedRowIdsRef.current = pinnedRowIds;
    // Row pins are session-only AND table-scoped: an id from another section
    // must not come back when the user returns to it. Sorting and filtering do
    // NOT reset them.
    useEffect(() => {
        setPinnedRowIds([]);
        setPinnedRowSnapshots({});
        setPinnedRowNumbers({});
    }, [section, sortingScope]);
    const [unhideButtons, setUnhideButtons] = useState<
        Array<{
            key: string;
            props: string[];
            entries: HiddenColumnEntry[];
            left: number;
            top: number;
            label: string;
            service: boolean;
            priority: number;
        }>
    >([]);
    const unhideLayerRef = useRef<HTMLDivElement>(null);

    const mergesSuppressed = filtersActive || sortingActive || hiddenKeys.size > 0;
    // The event handlers below are registered once and must not restore
    // index-based merges after the column set changed.
    const hiddenActiveRef = useRef(false);
    hiddenActiveRef.current = hiddenKeys.size > 0;
    useEffect(() => {
        const grid = gridRef.current;
        if (grid && cellMerge !== undefined) grid.cellMerge = mergesSuppressed ? [] : cellMerge;
    }, [cellMerge, mergesSuppressed, section, sortingVersion]);

    /**
     * The applied filters spelled out as header chains, leaf LAST: a grouped
     * column reads «Иерархия → Лист», exactly the order its headers stack on
     * screen. `path` holds the group labels of a descriptor; a column without a
     * group is just its own label.
     */
    /**
     * The active sorting spelled out as header chains, in RevoGrid's own order
     * (its `sortingOrder` decides which column wins). A sorted column whose
     * descriptor is missing — a synthetic one — is skipped: its label would be
     * meaningless in a tooltip.
     */
    const sortedColumnPaths = useCallback(() => {
        const sorting = sortingBySectionRef.current.get(section);
        if (!sorting) return [];
        return sorting.columns
            .map((column) => {
                const descriptor = descriptorByKey.get(column.prop);
                return descriptor
                    ? { path: [...(descriptor.path ?? []), descriptor.label], order: column.order }
                    : null;
            })
            .filter((entry): entry is { path: string[]; order: "asc" | "desc" } => entry !== null);
    }, [descriptorByKey, section]);

    const filteredColumnPaths = useCallback(() => {
        return Object.keys(columnFilters ?? {}).flatMap((column) => {
            const descriptor = descriptorByKey.get(column);
            return descriptor ? [[...(descriptor.path ?? []), descriptor.label]] : [];
        });
    }, [columnFilters, descriptorByKey]);

    // ── Header column filters (client-side) ────────────────────────────────
    // Every table uses the SAME popover (`ColumnFilterPopover`); here the
    // facets are computed from the rows already in memory instead of being
    // fetched. The grid only owns the plumbing: which column is open, where
    // its funnel is, and the filtered source the grid renders.
    // The filters are the workspace's state, so they are also what enables the
    // funnels: a form that passes the map gets them, a read-only/archive view
    // that passes nothing renders a plain header.
    const filteringEnabled = columnFilters !== undefined && onFiltersChange !== undefined;
    /**
     * The header funnels, drawn as an OVERLAY over RevoGrid's header.
     *
     * They cannot live in the header template: RevoGrid mounts that template
     * inside `.header-content`, and its sorted state rewrites that box
     * (`position: absolute` + `margin-left: 24px` for the sort arrow), dragging
     * anything inside it sideways. Positioned from the header CELLS' rects — the
     * one geometry RevoGrid keeps between states — a funnel is immune to sorting,
     * resizing and re-rendering alike.
     */
    const funnelLayerRef = useRef<HTMLDivElement>(null);
    const [funnels, setFunnels] = useState<
        Array<{ prop: string; label: string; left: number; top: number; height: number }>
    >([]);
    // The «№» corner's go-to popover (shared component, own numbering). The
    // row-header template reaches the opener through a ref: the row headers are
    // built once, and a setter in their dependency list would rebuild them on
    // every render.
    const [goToTarget, setGoToTarget] = useState<DOMRect | null>(null);
    const openGoToRef = useRef<((anchor: DOMRect) => void) | null>(null);
    openGoToRef.current = setGoToTarget;
    const visibleRowCountRef = useRef(0);
    const [filterTarget, setFilterTarget] = useState<{
        column: string;
        label: string;
        anchor: DOMRect;
    } | null>(null);

    const activeFilters = useMemo(
        () => (filteringEnabled ? Object.values(columnFilters) : []),
        [columnFilters, filteringEnabled],
    );
    // A ref mirror for the filter plugins/columns built ONCE: they must read the
    // live filters without being rebuilt on every keystroke of the popover.
    const activeFiltersRef = useRef<ColumnFilter[]>(activeFilters);
    activeFiltersRef.current = activeFilters;
    const facetTextRef = useRef<(column: string, row: Record<string, unknown>) => unknown>(
        () => undefined,
    );

    /** The text a column is filtered by — exactly what its cell renders. */
    const facetText = useCallback(
        (column: string, row: Record<string, unknown>) =>
            facetTextOf(descriptorByKey.get(column), row),
        [descriptorByKey],
    );
    /** Text sorts alphabetically; numbers and dates sort numerically. */
    const facetOrder = useCallback(
        (column: string) => facetOrderOf(descriptorByKey.get(column)),
        [descriptorByKey],
    );
    /**
     * The NUMERIC value behind a numeric column's display text. A range search
     * compares numbers, and the display string is formatted («3 361») — parsing
     * it back is a guess; the row already holds the value.
     */
    const facetNumber = useCallback(
        (column: string, row: Record<string, unknown>) => {
            const descriptor = descriptorByKey.get(column);
            if (!descriptor) return undefined;
            if (descriptor.kind !== "number" && descriptor.kind !== "calculated") return undefined;
            const raw = row[descriptor.displayKey ?? descriptor.key];
            if (raw === null || raw === undefined || raw === "") return undefined;
            const parsed = typeof raw === "number" ? raw : Number(raw);
            return Number.isFinite(parsed) ? parsed : undefined;
        },
        [descriptorByKey],
    );
    facetTextRef.current = facetText;

    // Options use only preceding filters, preserving every choice when editing
    // an earlier step. Final rows still apply all constraints with AND.
    /** Real records only: the «＋» row and deleted ghosts are not data. */
    const realRows = useMemo(
        () => rows.filter((row) => !row.isPlaceholder && !row.isDeleted),
        [rows],
    );

    const facetSourceFor = useCallback(
        (column: string): FacetSource => {
            const others = precedingColumnFilters(activeFilters, column);
            // Virtual rows are not records: the «＋» placeholder has no values at
            // all, so counting it put a phantom entry in the «Пустые» bucket and
            // inflated every count by one.
            const source =
                others.length === 0
                    ? realRows
                    : realRows.filter((row) => rowMatchesFilters(row, others, facetText));
            const facets = computeRowFacets(
                source,
                (row) => facetText(column, row),
                facetOrder(column),
                (row) => facetNumber(column, row),
            );
            // The search narrows the WHOLE computed set before the window is cut
            // — the same rule `rowFacetSource` applies, kept here because this
            // source is already narrowed by the other columns' filters.
            return async (_column, { offset, limit, search }) =>
                facetWindow(searchRowFacets(facets, search), offset, limit, facets.values.length);
        },
        [activeFilters, facetNumber, facetOrder, facetText, realRows],
    );

    /**
     * The rows RevoGrid renders: the workspace source narrowed by the active
     * filters. `isPlaceholder` (the «＋» row) always survives — it is the way to
     * add a record and must not vanish because of a filter.
     */
    const visibleRows = useMemo(() => {
        if (!filteringEnabled || activeFilters.length === 0) return rows;
        return rows.filter(
            (row) => row.isPlaceholder || rowMatchesFilters(row, activeFilters, facetText),
        );
    }, [activeFilters, facetText, filteringEnabled, rows]);

    /**
     * Keep a pinned snapshot in step with its source row and drop it when the
     * row is really gone from `rows`. `visibleRows` is the FILTERED view: using
     * it here would evict a pin as soon as a filter hides the row.
     */
    useEffect(() => {
        setPinnedRowSnapshots((previous) => {
            const ids = Object.keys(previous);
            if (ids.length === 0) return previous;
            const byId = new Map(rows.map((row) => [row.id, row]));
            let changed = false;
            const next: Record<string, GridRow> = {};
            for (const id of ids) {
                const live = byId.get(id);
                if (!live) {
                    changed = true;
                    continue;
                }
                next[id] = live;
                if (live !== previous[id]) changed = true;
            }
            return changed ? next : previous;
        });
    }, [rows]);

    /**
     * The read-only snapshots shown in the pinned strip. Filtering and sorting
     * are view state: a pinned copy survives both, even when its original row
     * is filtered out. The copy carries the row's current number and a unique
     * id so RevoGrid treats it as a separate record.
     */
    const pinnedCopyRows = useMemo<GridRow[]>(() => {
        if (pinnedRowIds.length === 0) return [];
        const copies: GridRow[] = [];
        for (const id of pinnedRowIds) {
            const row = pinnedRowSnapshots[id];
            if (!row || row.isPlaceholder) continue;
            const fallbackNumber = rows.findIndex((candidate) => candidate.id === id) + 1;
            copies.push({
                ...row,
                id: `${row.id}::pinned`,
                __pinnedCopy: true,
                __pinSourceId: row.id,
                __pinNumber: pinnedRowNumbers[id] ?? (fallbackNumber > 0 ? fallbackNumber : null),
            });
        }
        return copies;
    }, [pinnedRowIds, pinnedRowNumbers, pinnedRowSnapshots, rows]);

    /** The summary row (if any) stays FIRST; the pinned copies follow it. */
    const gridPinnedTopSource = useMemo<Record<string, unknown>[]>(() => {
        const summary = pinnedTopSource ?? EMPTY_PINNED_SOURCE;
        if (pinnedCopyRows.length === 0) return summary;
        return [...summary, ...pinnedCopyRows];
    }, [pinnedCopyRows, pinnedTopSource]);

    /** Scroll to a 1-based position in the rows the grid currently shows. */
    const scrollToRowPosition = useCallback(async (position: number) => {
        const grid = gridRef.current;
        if (!grid) return;
        await grid.scrollToRow(position - 1);
    }, []);

    useEffect(() => {
        visibleRowCountRef.current = 0;
    }, [visibleRows]);

    const applyColumnFilter = useCallback(
        (column: string, filter: ColumnFilter | null) => {
            if (!filteringEnabled) return;
            const next = { ...columnFilters };
            if (filter === null) delete next[column];
            else next[column] = filter;
            onFiltersChange(next);
        },
        [columnFilters, filteringEnabled, onFiltersChange],
    );

    // The active state of a column funnel is a RevoGrid column flag, and
    // `updateColumns` is the public way to change a column after render.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        void (async () => {
            const current = (await gridAdapter.getColumns()) as (ColumnRegular | ColumnGrouping)[];
            const leaves = current.flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            );
            const changed = withFilterMarkers(leaves, columnFilters ?? {});
            if (changed.length > 0) await grid.updateColumns(changed);
        })();
    }, [columnFilters]);

    const editableKeys = useMemo(
        () =>
            getEditableKeys(section, isAdmin, showAssetColumn, keycloakEnabled, columnDescriptors),
        [isAdmin, section, showAssetColumn, keycloakEnabled, columnDescriptors],
    );

    const editors = useMemo<Editors>(
        () => ({
            multiAssets: reactEditor(MultiAssetEditor),
            reference: reactEditor(ReferenceEditor),
            select: reactEditor(SelectEditor),
        }),
        [],
    );

    const columns = useMemo(() => {
        // Referenced on purpose: `restoreColumnWidths` below reads the widths
        // through a ref (not reactive), so this counter is the only signal that
        // a reset happened and the columns must be rebuilt.
        void widthsVersion;
        // Same reason: the leaves carry the sorting `order`, so a new sorting
        // must rebuild them — see the `activeOrders` block below.
        void sortingVersion;
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
            hiddenKeys,
            pinnedKeys,
            ...(columnDescriptors && { descriptors: columnDescriptors }),
            onBooleanChange: (row, field, value) => {
                if (row.isPlaceholder) void onPlaceholderCommit({ [field]: value });
                else void onCommit([{ rowId: row.id, field, value }]);
            },
        });
        // The SUMMARY row, never a pinned copy: a grid without a summary must
        // not merge its header over the first pinned copy.
        const summaryRow = pinnedTopSource?.[0];
        // Sorting is carried BOTH ways: by the `sorting` prop (the sorting job
        // itself) and by the columns' own `order` (`columnChanged` collects it
        // into `aftercolumnsset`). RevoGrid's sorting plugin tracks its current
        // order ONLY through `aftercolumnsset`, so a config applied without the
        // column orders is invisible to it: `getNextOrder(undefined)` then starts
        // every click from `asc` again, and the header reads a stale direction.
        // One source of truth — `sortingBySectionRef` — feeds both.
        const activeOrders = new Map<string, GridSortingOrder>();
        for (const entry of sortingBySectionRef.current.get(section)?.columns ?? []) {
            activeOrders.set(entry.prop, entry.order);
        }
        // A column set is REBUILT, never patched: RevoGrid diffs columns by
        // identity, so a leaf mutated in place never reaches `columnChanged` and
        // the plugin would keep seeing no sorting at all.
        const withSorting = next.map((column) => {
            const grouping = column as ColumnGrouping;
            if (grouping.children?.length) {
                return {
                    ...grouping,
                    children: (grouping.children as ColumnRegular[]).map((leaf) => {
                        const patched = { ...leaf } as ColumnRegular & {
                            order?: GridSortingOrder;
                        };
                        const order =
                            leaf.prop === null ? undefined : activeOrders.get(String(leaf.prop));
                        if (order === undefined) delete patched.order;
                        else patched.order = order;
                        if (!sortable) patched.sortable = false;
                        return patched;
                    }),
                } as ColumnGrouping;
            }
            const leaf = column as ColumnRegular;
            const patched = { ...leaf } as ColumnRegular & { order?: GridSortingOrder };
            const order = leaf.prop === null ? undefined : activeOrders.get(String(leaf.prop));
            if (order === undefined) delete patched.order;
            else patched.order = order;
            if (!sortable) patched.sortable = false;
            return patched;
        });
        // Tag the pinned summary row's cells so CSS can draw them like the
        // plan's summary (one vertical + one bottom border, white empty cells,
        // a green numeric cell) and the row header can hide its number.
        for (const column of withSorting) {
            const leaves = (column as ColumnGrouping).children?.length
                ? ((column as ColumnGrouping).children as ColumnRegular[])
                : [column as ColumnRegular];
            for (const leaf of leaves) {
                // A column whose summary cell stays empty merges with it: the
                // header cell stretches over the summary row, so the column
                // reads as one tall header cell (the totals columns — the only
                // ones carrying a value — keep a separate header cell).
                const summaryValue = summaryRow?.[String(leaf.prop)];
                const mergeHeader =
                    summaryRow !== undefined &&
                    (summaryValue === undefined || summaryValue === null || summaryValue === "");
                if (mergeHeader) {
                    const baseHeader = leaf.columnProperties;
                    // A NEW function per leaf, never one already carrying the
                    // marker: the header re-renders repeatedly (a sorting bump
                    // rebuilds the columns), and reading its own marker from the
                    // base would accumulate one more wrap per render.
                    if (!hasSummaryMergeMarker(baseHeader)) {
                        const mergedHeaderProperties = ((props: unknown) => {
                            const result = (baseHeader ? baseHeader(props as never) : undefined) as
                                | { className?: Record<string, boolean> }
                                | undefined;
                            return {
                                ...result,
                                className: {
                                    ...result?.className,
                                    "report-header-cell--summary-merge": true,
                                },
                            };
                        }) as NonNullable<ColumnRegular["columnProperties"]>;
                        leaf.columnProperties = markSummaryMerge(mergedHeaderProperties);
                    }
                }
                const base = leaf.cellProperties;
                const newCellProperties = ((props: { model: unknown; prop: string }) => {
                    const result = (base ? base(props as never) : undefined) as
                        | { className?: Record<string, boolean> }
                        | undefined;
                    if ((props.model as { id?: string } | undefined)?.id !== SUMMARY_ROW_ID) {
                        return result;
                    }
                    const value = (props.model as Record<string, unknown>)[props.prop];
                    return {
                        ...result,
                        className: {
                            ...result?.className,
                            "data-grid__summary-cell": true,
                            "data-grid__summary-cell--filled": typeof value === "number",
                        },
                    };
                }) as ColumnRegular["cellProperties"];
                if (newCellProperties !== undefined) {
                    leaf.cellProperties = newCellProperties;
                }
            }
        }
        // Every leaf gets RevoGrid's own `.rv-filter` funnel so the header
        // affordance is the one users already know; the filter itself is OURS
        // (the popover reads the grid, not RevoGrid's panel).
        // RevoGrid draws its own header (the plan replaces it with an overlay),
        // so the funnels come through its API: `columnTemplate` makes the grid
        // render a `.rv-filter` button. Ours is what the click opens — the click
        // is intercepted in the capture phase below.
        markFilterableColumns(withSorting, descriptors);
        restoreColumnWidths(withSorting, resizedWidthsRef.current, widthKeySection);
        return withSorting;
    }, [
        assets,
        columnDescriptors,
        deliveryBases,
        hasBaseline,
        hiddenKeys,
        pinnedKeys,
        isAdmin,
        keycloakEnabled,
        showAssetColumn,
        onCommit,
        onPlaceholderCommit,
        pinnedTopSource,
        readOnly,
        remoteCellInfo,
        remoteLocks,
        selectedRowIdSet,
        section,
        showServiceColumns,
        sortable,
        sortingVersion,
        widthKeySection,
        widthsVersion,
    ]);

    // A ref mirror for the header-funnel click handler, which is registered once
    // and must still read the columns of the CURRENT form tab.
    const flatColumnsRef = useRef<ColumnRegular[]>([]);

    const flatColumns: ColumnRegular[] = useMemo(
        () =>
            columns.flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            ),
        [columns],
    );
    flatColumnsRef.current = flatColumns;

    const headerRowCount = columns.some(
        (column) => ((column as ColumnGrouping).children?.length ?? 0) > 0,
    )
        ? 2
        : 1;
    const headerHeight = headerRowCount * rowHeight;
    const contentHeight = headerHeight + rows.length * rowHeight + 18;

    const selectionRangeRef = useRef<RangeArea | null>(null);
    /**
     * The current focus/range belongs to the pinned-row strip. Its coordinates
     * are indices of `pinnedTopSource`, NOT of the main `rgRow` source — any
     * clipboard or edit action that maps `y` to `visibleRows` must be refused
     * rather than silently touching the wrong row.
     */
    const selectionInPinnedRowRef = useRef(false);
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
        return gridAdapter.getVisibleRows(rows);
    }, [gridAdapter, rows]);

    /** Keep the pinned copies' numbers in step with the rendered row order. */
    const syncPinnedRowNumbers = useCallback(async () => {
        const ids = pinnedRowIdsRef.current;
        const visible = await getVisibleRows();
        const idSet = new Set(ids);
        const positions: Record<string, number> = {};
        visible.forEach((row, index) => {
            if (idSet.has(row.id)) positions[row.id] = index + 1;
        });
        setPinnedRowNumbers((previous) => {
            const next: Record<string, number> = {};
            // A row hidden by the filter keeps its last known number: the pin
            // itself must survive filtering, so do not drop its entry.
            for (const [id, value] of Object.entries(previous)) {
                if (idSet.has(id)) next[id] = positions[id] ?? value;
            }
            for (const [id, value] of Object.entries(positions)) next[id] = value;
            const previousKeys = Object.keys(previous);
            const nextKeys = Object.keys(next);
            if (
                previousKeys.length === nextKeys.length &&
                nextKeys.every((key) => previous[key] === next[key])
            ) {
                return previous;
            }
            return next;
        });
    }, [getVisibleRows]);

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
                    const row = visible[index];
                    if (row && selectedRowIds.has(row.id)) rowIndexes.push(index);
                }
                const coveredIndexes = rowIndexes.length
                    ? rowIndexes
                    : selectionRange
                      ? Array.from(
                            {
                                length:
                                    Math.min(selectionRange.y1, visible.length - 1) -
                                    selectionRange.y +
                                    1,
                            },
                            (_, offset) => selectionRange.y + offset,
                        ).filter((index) => index >= 0 && index < visible.length)
                      : [];
                // A focus on the trailing «добавить запись» placeholder row is not
                // a real selection: it carries no persistence keys and never blocks
                // others, so it must not appear in the footer status or in presence.
                const anyDataRow = coveredIndexes.some((index) => !visible[index]?.isPlaceholder);
                if (coveredIndexes.length > 0 && !anyDataRow) {
                    onSelectionChange?.({ label: null, cellKeys: [], detail: null });
                    return;
                }
                const firstIndex = rowIndexes[0];
                const lastIndex = rowIndexes[rowIndexes.length - 1];
                const described = selectionRange
                    ? describeSelection(
                          selectionRange,
                          flatColumns,
                          descriptorByKey,
                          visible.length,
                      )
                    : null;
                const label =
                    rowIndexes.length && firstIndex !== undefined && lastIndex !== undefined
                        ? firstIndex === lastIndex
                            ? `строка ${firstIndex + 1}`
                            : `строки ${firstIndex + 1}–${lastIndex + 1}`
                        : (described?.label ?? null);
                const keys = rowIndexes.length
                    ? rowIndexes.flatMap((index) =>
                          selectionRowCellKeys(visible, index, index, flatColumns),
                      )
                    : selectionCellKeys(selectionRange, visible, flatColumns);
                // Row selection names rows only; the column details belong to a
                // cell range (the renovation plan behaves the same way).
                onSelectionChange?.({
                    label,
                    cellKeys: keys,
                    detail: rowIndexes.length ? null : (described?.detail ?? null),
                });
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
            await gridAdapter.setRange(start, end);
        },
        [gridAdapter],
    );

    const clearGridSelection = useCallback(() => {
        // The adapter also clears RevoGrid's private focus-restoration cache.
        void gridAdapter.clearRange();
        updateSelectionRange(null);
    }, [gridAdapter, updateSelectionRange]);

    // Ctrl+A: one contiguous range over all data cells. Headers aren't
    // selected (merged headers' border doesn't match), the «добавить запись»
    // row is outside the range too, row selection is reset — only the cell
    // range remains.
    const selectAllCells = useCallback(async () => {
        if (rows.length === 0 || flatColumns.length === 0) return;
        // Selection coordinates are screen indexes: derive the range from the
        // visible source (sorting/filtering applied), not the original rows.
        const visible = await getVisibleRows();
        let lastDataIndex = visible.length - 1;
        while (lastDataIndex >= 0 && visible[lastDataIndex]?.isPlaceholder) {
            lastDataIndex -= 1;
        }
        if (lastDataIndex < 0) return;
        const start = { x: 0, y: 0 };
        const end = { x: flatColumns.length - 1, y: lastDataIndex };
        await applyGridRange(start, end);
        updateSelectionRange({ x: 0, y: 0, x1: end.x, y1: end.y });
        setSelectedRowIds([]);
        rowSelectionAnchorRef.current = null;
    }, [
        applyGridRange,
        flatColumns.length,
        getVisibleRows,
        rows,
        setSelectedRowIds,
        updateSelectionRange,
    ]);

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
                // Row indexes are screen coordinates: under sorting/filtering
                // they map through the visible source, not the original rows
                // (same landmine as paste/copy).
                void (async () => {
                    const visible = await getVisibleRows();
                    clearGridSelection();
                    setSelectedRowIds(
                        visible
                            .slice(start, end + 1)
                            .map((row) => row.id)
                            .filter(Boolean),
                    );
                })();
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
        [clearGridSelection, getVisibleRows, selectedRowIdSet],
    );

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: 52,
            columnTemplate: (h: HyperFunc<VNode>) =>
                // The corner draws two nodes (the «№» and its funnel); the
                // row-header template type only declares single-node overloads.
                (h as unknown as (...args: unknown[]) => VNode)(
                    "span",
                    { class: "report-row-header__heading-wrap" },
                    h("span", { class: "report-row-header__heading" }, "№"),
                    // The «№» corner opens the shared «Перейти к строке/столбцу»
                    // popover — the same component the renovation plan uses, so
                    // every table navigates the same way. The funnel itself is
                    // painted by CSS (`--filter-icon`): one glyph for every
                    // filter affordance in the app.
                    h("button", {
                        type: "button",
                        class: "report-row-header__filter-button",
                        "aria-label": "Перейти к строке",
                        onMouseDown: (event: MouseEvent) => event.stopPropagation(),
                        onClick: (event: MouseEvent) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const host = (event.currentTarget as HTMLElement).parentElement;
                            if (!host) return;
                            openGoToRef.current?.(host.getBoundingClientRect());
                        },
                    }),
                ),
            columnProperties: () => ({
                className: {
                    "report-row-header": true,
                    "report-row-header--heading": true,
                },
            }),
            cellProperties: ({ model }) => {
                const row = model as GridRow;
                const sourceId = row.__pinnedCopy ? row.__pinSourceId : row.id;
                return {
                    className: {
                        "report-row-header": true,
                        "report-row-header--selected":
                            !row.__pinnedCopy && selectedRowIds.includes(row.id),
                        "report-row-header--summary": row.id === SUMMARY_ROW_ID,
                        "report-row-header--pinned-copy": row.__pinnedCopy === true,
                    },
                    // The row menu needs the row behind the cell. Real rows are
                    // addressed by their own id; a pinned copy points at the
                    // source row it mirrors. The «＋» placeholder is not a row.
                    "data-row-id": row.isPlaceholder ? undefined : sourceId,
                    "data-pinned-copy": row.__pinnedCopy === true ? "true" : undefined,
                };
            },
            cellTemplate: (h: HyperFunc<VNode>, { rowIndex, model }) => {
                const row = model as GridRow;
                // The pinned summary row shows no row number.
                if (row.id === SUMMARY_ROW_ID) {
                    return h("span", { class: "report-row-header__summary" });
                }
                const isCopy = row.__pinnedCopy === true;
                // The go-to popover clamps to the running row number. A pinned
                // copy is not part of the main source and must not move it.
                if (!row.isPlaceholder && !isCopy) visibleRowCountRef.current = rowIndex + 1;
                const selected = !isCopy && selectedRowIds.includes(row.id);
                const number = isCopy ? (row.__pinNumber ?? rowIndex + 1) : rowIndex + 1;
                // The trailing «добавить запись» row shows «+» instead of a
                // number — same as the renovation plan grid.
                return h(
                    "button",
                    {
                        type: "button",
                        class: {
                            "report-row-header__button": true,
                            "report-row-header__button--selected": selected,
                            "report-row-header__button--pinned-copy": isCopy,
                        },
                        title: row.isPlaceholder
                            ? "Добавить строку"
                            : isCopy
                              ? `Закреплённая строка ${number}`
                              : selected
                                ? `Снять выделение строки ${number}`
                                : `Выделить строку ${number}`,
                        onMouseDown: (event: MouseEvent) => {
                            event.stopPropagation();
                        },
                        onClick: (event: MouseEvent) => {
                            event.stopPropagation();
                            if (isCopy) return;
                            handleRowNumberClick(event, rowIndex, row.id);
                        },
                    },
                    row.isPlaceholder ? "+" : String(number),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [selectedRowIds, handleRowNumberClick],
    );

    // RevoGrid draws its own header (the plan replaces it with an overlay), so
    // the filter funnels have to come through the grid's API: `columnTemplate`
    // is what makes RevoGrid render a `.rv-filter` button, and the grid's
    // `.hasFilter` prop paints it as active. Both are re-applied whenever the
    // active filter set changes — `updateColumns` is the public way to change a
    // column after render.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        void (async () => {
            const current = (await gridAdapter.getColumns()) as (ColumnRegular | ColumnGrouping)[];
            const changed: ColumnRegular[] = [];
            const walk = (column: ColumnRegular | ColumnGrouping) => {
                const leaves = (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular];
                for (const leaf of leaves) {
                    if (!descriptorByKey.has(String(leaf.prop))) continue;
                    if (descriptorByKey.get(String(leaf.prop))?.kind === "calculated") continue;
                    const active = columnFilters?.[String(leaf.prop)] !== undefined;
                    if (Boolean(leaf.hasFilter) === active) continue;
                    leaf.hasFilter = active;
                    changed.push(leaf);
                }
            };
            for (const column of current) walk(column);
            if (changed.length > 0) await grid.updateColumns(changed);
        })();
    }, [columnFilters, descriptorByKey]);

    useEffect(() => {
        onSelectedRowsChange({
            ids: selectedRowIds,
            wholeRows: true,
        });
    }, [onSelectedRowsChange, selectedRowIds]);

    function canEditDescriptor(descriptor: ColumnDescriptor, row: GridRow): boolean {
        return !(
            readOnly ||
            row.__pinnedCopy === true ||
            row.readOnlyRow === true ||
            row.lockedCells?.includes(descriptor.key) ||
            row.isDeleted ||
            descriptor.fixed ||
            descriptor.kind === "calculated" ||
            (keycloakEnabled &&
                (section === "users" || section === "kppkUsers") &&
                !row.isPlaceholder &&
                (descriptor.key === "email" || descriptor.key === "fullname"))
        );
    }

    const canPasteRange = useCallback(
        (range: RangeArea | null, source: GridRow[]): boolean => {
            if (!range || readOnly || selectionInPinnedRowRef.current) return false;
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
        // `canEditDescriptor` is a MODULE-level function (pure, no captured state),
        // so it never needs to be a dependency; the linter cannot tell a module
        // function from a local one.
        // oxlint-disable-next-line react-hooks/exhaustive-deps
        [descriptorByKey, editableKeys, flatColumns, readOnly, remoteLocks, remotePresence],
    );

    useEffect(() => {
        // Selection is in displayed-row coordinates — check against the visible
        // source (sorting/filtering already applied).
        //
        // The result is a BOOLEAN, so it is compared before it is stored: the
        // selection range object is rebuilt by the grid on every focus pass, and
        // an unconditional `setCanPaste` re-rendered on each of those passes until
        // React stopped it («Maximum update depth exceeded»).
        const epoch = createGridEpoch();
        const token = epoch.next();
        void (async () => {
            const source = await getVisibleRows();
            if (!epoch.isCurrent(token)) return;
            const next = canPasteRange(selectionRange, source);
            setCanPaste((previous) => (previous === next ? previous : next));
        })();
        return () => {
            epoch.next();
        };
    }, [canPasteRange, getVisibleRows, selectionRange]);

    // One stable object per STATE change, not per render: the owner stores what it
    // receives, and a fresh object on every render is the loop React reports.
    // The callbacks are read through a ref, because they are declared below.
    const clipboardRef = useRef<{ copy: () => void; paste: () => void }>({
        copy: () => {},
        paste: () => {},
    });
    const clipboardActions = useMemo(
        () => ({
            canPaste,
            onCopy: () => clipboardRef.current.copy(),
            onPaste: () => clipboardRef.current.paste(),
        }),
        [canPaste],
    );
    useEffect(() => {
        onClipboardStateChange?.(clipboardActions);
    }, [clipboardActions, onClipboardStateChange]);

    function normalizeValue(
        value: unknown,
        kind: ColumnDescriptor["kind"],
    ): { ok: true; value: unknown } | { ok: false; message: string } {
        if (kind === "number") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            // parseNumericText accepts any Unicode whitespace as a thousands
            // separator and ',' as the decimal mark (Excel ru-RU buffers).
            const parsed = typeof value === "number" ? value : parseNumericText(value);
            if (parsed === null || !Number.isFinite(parsed) || parsed < 0) {
                return { ok: false, message: "введите конечное неотрицательное число." };
            }
            return { ok: true, value: parsed };
        }
        if (kind === "boolean") {
            if (typeof value === "boolean") return { ok: true, value };
            const normalized = String(value).trim().toLocaleLowerCase("ru-RU");
            if (["да", "true", "1"].includes(normalized)) return { ok: true, value: true };
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
        // Pinned copies are read-only and their coordinates are not rgRow ones.
        if (event.detail.type === "rowPinStart") {
            event.preventDefault();
            return;
        }
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
                        `${titles.map((title) => `«${title}»`).join(", ")}: ${reason}`,
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
        const isRangeSave =
            "data" in event.detail &&
            !Array.isArray((event.detail as { data?: unknown }).data) &&
            (event.detail as { prop?: unknown }).prop === undefined;
        if (isRangeSave) {
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
        measureVerticalScrollbarLane();
        const size = await gridAdapter.getContentSize();
        const verticalScroller = verticalScrollRef.current;
        const scrollbarGutter = verticalScroller
            ? verticalScroller.offsetWidth - verticalScroller.clientWidth
            : 0;
        // `size.x` already includes the row-header column («№») — adding its
        // width again produced a phantom horizontal scrollbar on tables that
        // fit the viewport, and that bar overlapped the last visible row.
        const width = size.x + scrollbarGutter;
        setHorizontalContentWidth(width);
        const shell = shellRef.current;
        setHasHorizontalOverflow(width > (shell?.clientWidth ?? 0));
    }, [measureVerticalScrollbarLane]);

    // Live column resize. revo-grid's own handle commits the width only on
    // pointer-up (its ResizeDirective forwards `onResize` for the `end` event
    // alone), so the boundary jumped only after release. Here we drive the drag
    // ourselves and route it through the SAME state the columns memo reads: the
    // new width lands in `resizedWidthsRef` and `widthsVersion` is bumped on every
    // pointermove, so the `columns` memo rebuilds and revo-grid re-renders the
    // header (group headers included) live — exactly like the renovation plan's
    // overlay. Calling `grid.updateColumns` instead left nested group headers stale,
    // so the boundary did not move.
    useEffect(() => {
        const shell = shellRef.current;
        if (!shell) return;
        type ResizeTarget = {
            prop: string;
            startWidth: number;
            minWidth: number;
            maxWidth: number;
        };
        let drag: { targets: ResizeTarget[]; startX: number } | null = null;
        // `grid.columns` carries the grouping shape too (a group has children),
        // so narrow it the way the flatColumns memo above does.
        const leavesOf = (columns: (ColumnRegular | ColumnGrouping)[]) =>
            columns.flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            );
        const toTarget = (leaf: ColumnRegular, startWidth?: number): ResizeTarget => ({
            prop: String(leaf.prop),
            startWidth:
                typeof startWidth === "number" && startWidth > 0
                    ? startWidth
                    : typeof leaf.size === "number"
                      ? leaf.size
                      : 150,
            minWidth: typeof leaf.minSize === "number" ? leaf.minSize : 40,
            maxWidth: typeof leaf.maxSize === "number" ? leaf.maxSize : Number.POSITIVE_INFINITY,
        });
        /**
         * Resolve the dragged boundary to the columns it resizes.
         *
         * Geometry is not a reliable lookup here: header cells are positioned
         * per viewport (`translateX` is relative to the pinned or main strip),
         * while a flat column list starts at the first grid column. The first
         * column of the main viewport therefore translates to 0 — the same
         * offset as the first pinned column — so a cumulative-width match sent
         * every main-viewport boundary to the leftmost pinned column (dragging
         * «Потребление»'s edge resized «Сырьевая позиция»). The DOM already
         * names the column (`data-prop` on the title span) and the group
         * (`data-group` on a grouping cell); resolve from those instead.
         *
         * A leaf resizes itself. A grouping cell distributes the delta over
         * its children, matching RevoGrid's own group-boundary behaviour.
         */
        const resolveTargets = (handle: Element): ResizeTarget[] | null => {
            const grid = gridRef.current;
            const cell = handle.closest(".rgHeaderCell") as HTMLElement | null;
            if (!grid || !cell) return null;
            const columnTree = (grid.columns ?? []) as (ColumnRegular | ColumnGrouping)[];
            const prop = cell.querySelector("[data-prop]")?.getAttribute("data-prop") ?? null;
            if (prop !== null) {
                const leaf = leavesOf(columnTree).find((column) => String(column.prop) === prop);
                // The rendered cell width is the live width: `leaf.size` can be
                // a render behind while the previous drag is settling.
                return leaf ? [toTarget(leaf, cell.offsetWidth)] : null;
            }
            const group = cell.getAttribute("data-group");
            if (!group) return null;
            const grouping = columnTree.find(
                (column): column is ColumnGrouping =>
                    ((column as ColumnGrouping).children?.length ?? 0) > 0 &&
                    (column as ColumnGrouping).name === group,
            );
            const targets = (grouping?.children ?? []).flatMap((child) =>
                ((child as ColumnGrouping).children?.length ?? 0) > 0
                    ? ((child as ColumnGrouping).children as ColumnRegular[]).map((leaf) =>
                          toTarget(leaf),
                      )
                    : [toTarget(child as ColumnRegular)],
            );
            return targets.length > 0 ? targets : null;
        };
        const onPointerDown = (event: PointerEvent) => {
            const target = event.target as Element | null;
            if (!target || !target.classList.contains("resizable-r")) return;
            const targets = resolveTargets(target);
            if (!targets) return;
            event.preventDefault();
            event.stopPropagation();
            drag = { targets, startX: event.clientX };
            document.body.style.cursor = "ew-resize";
        };
        const onPointerMove = (event: MouseEvent) => {
            if (!drag) return;
            const delta = event.clientX - drag.startX;
            const count = drag.targets.length;
            for (const [index, target] of drag.targets.entries()) {
                // A group moves its right edge by the pointer distance: the
                // per-column shares must add up to `delta` exactly, or the
                // boundary drifts away from the cursor (rounding each share
                // independently loses up to half a pixel per column).
                const share =
                    count === 1
                        ? delta
                        : Math.round(((index + 1) * delta) / count) -
                          Math.round((index * delta) / count);
                const next = Math.round(
                    Math.min(target.maxWidth, Math.max(target.minWidth, target.startWidth + share)),
                );
                resizedWidthsRef.current.set(columnWidthKey(widthKeySection, target.prop), next);
            }
            setWidthsVersion((version) => version + 1);
        };
        const onPointerUp = () => {
            if (!drag) return;
            document.body.style.cursor = "";
            drag = null;
            void refreshHorizontalScrollbar();
        };
        shell.addEventListener("pointerdown", onPointerDown, true);
        window.addEventListener("pointermove", onPointerMove);
        window.addEventListener("pointerup", onPointerUp);
        window.addEventListener("pointercancel", onPointerUp);
        return () => {
            shell.removeEventListener("pointerdown", onPointerDown, true);
            window.removeEventListener("pointermove", onPointerMove);
            window.removeEventListener("pointerup", onPointerUp);
            window.removeEventListener("pointercancel", onPointerUp);
        };
    }, [refreshHorizontalScrollbar, widthKeySection]);
    const getExportState = useCallback(async (): Promise<DataGridState> => {
        const grid = gridRef.current;
        if (!grid) {
            return {
                mergeRows: !mergesSuppressed,
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
            gridAdapter.getColumns(),
            gridAdapter.getVisibleRows(rows),
        ]);
        return {
            mergeRows: !mergesSuppressed,
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
    }, [
        columnDescriptors,
        gridAdapter,
        keycloakEnabled,
        mergesSuppressed,
        rows,
        section,
        showAssetColumn,
        showServiceColumns,
    ]);

    const lastStatsRef = useRef<GridViewStats | null>(null);

    /**
     * Keep each funnel's `is-active` state in step with the stored filters.
     *
     * The header template is built from the columns (which do not change when a
     * filter is applied), so the marker is written onto the rendered buttons —
     * the same class the plan's overlay buttons carry, which keeps a filtered
     * column's funnel permanently visible.
     */
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || !filteringEnabled) return;
        for (const button of grid.querySelectorAll<HTMLElement>(".header-filter-button")) {
            const prop = button.getAttribute("data-prop");
            const active = prop !== null && columnFilters?.[prop] !== undefined;
            button.classList.toggle("is-active", active);
        }
    }, [columnFilters, filteringEnabled, rows]);

    /**
     * Place the funnel overlays from the header cells' real rects.
     *
     * Called on the FRAME AFTER a scroll/resize: RevoGrid emits `viewportscroll`
     * before it re-lays the header out, so measuring inside the handler would
     * copy the previous position (that is what broke the first attempt at this
     * overlay).
     */
    const measureFunnels = useCallback(() => {
        const grid = gridRef.current;
        const layer = funnelLayerRef.current;
        if (!grid || !layer || !filteringEnabled) return;
        const gridRect = grid.getBoundingClientRect();
        const cells = Array.from(grid.querySelectorAll(".rgHeaderCell")).filter(
            (cell): cell is HTMLElement => cell instanceof HTMLElement,
        );
        let top = Number.POSITIVE_INFINITY;
        let bottom = Number.NEGATIVE_INFINITY;
        const next: Array<{
            prop: string;
            label: string;
            left: number;
            top: number;
            height: number;
        }> = [];
        const seen = new Set<string>();
        for (const cell of cells) {
            // The row-number corner has no `data-prop`: it is not a filterable
            // column and must not borrow the first column's funnel.
            const prop = cell.querySelector("[data-prop]")?.getAttribute("data-prop") ?? null;
            if (!prop || !descriptorByKey.has(prop) || seen.has(prop)) continue;
            seen.add(prop);
            const rect = cell.getBoundingClientRect();
            top = Math.min(top, rect.top - gridRect.top);
            bottom = Math.max(bottom, rect.bottom - gridRect.top);
            next.push({
                prop,
                label: descriptorByKey.get(prop)?.label ?? prop,
                left: rect.right - gridRect.left,
                top: rect.top - gridRect.top,
                height: rect.height,
            });
        }
        if (Number.isFinite(top)) {
            layer.style.top = `${Math.round(top)}px`;
            layer.style.height = `${Math.round(bottom - top)}px`;
        }
        // The overlay is positioned from raw rects, which wobble by less than a
        // pixel between frames: writing an identical list every time re-rendered
        // the grid, and the re-render measured again — an endless update loop.
        setFunnels((previous) =>
            previous.length === next.length &&
            previous.every((entry, index) => {
                const candidate = next[index];
                if (!candidate) return false;
                return (
                    entry.prop === candidate.prop &&
                    entry.label === candidate.label &&
                    Math.abs(entry.left - candidate.left) < 1 &&
                    Math.abs(entry.top - candidate.top) < 1 &&
                    Math.abs(entry.height - candidate.height) < 1
                );
            })
                ? previous
                : next,
        );
    }, [descriptorByKey, filteringEnabled]);

    /** Re-measure on the frame after the grid has settled. */
    const scheduleFunnelMeasure = useCallback(() => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => measureFunnels());
        });
    }, [measureFunnels]);

    /**
     * Place the hidden-column expand buttons from the live column widths.
     *
     * The button stands at the LEFT edge of a maximal hidden block: the sum of
     * the visible columns before it (hidden ones take no width), with the row
     * header offset. `anchor` is the header level the block was hidden from —
     * a leaf starts under the group row, a whole group at the very top. The
     * horizontal scrollbar is subtracted because the layer is outside the
     * scrolled content.
     */
    const measureUnhide = useCallback(() => {
        const grid = gridRef.current;
        const layer = unhideLayerRef.current;
        const shell = shellRef.current;
        if (!grid || !layer || !shell) return;
        const shellRect = shell.getBoundingClientRect();
        let headerTop = Number.POSITIVE_INFINITY;
        for (const cell of grid.querySelectorAll(".rgHeaderCell")) {
            const rect = cell.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) headerTop = Math.min(headerTop, rect.top);
        }
        if (!Number.isFinite(headerTop)) {
            setUnhideButtons((previous) => (previous.length > 0 ? [] : previous));
            return;
        }
        const layerTop = Math.round(headerTop - shellRect.top);
        layer.style.top = `${layerTop}px`;
        layer.style.height = `${headerRowCount * rowHeight}px`;
        const scrollLeft = horizontalScrollRef.current?.scrollLeft ?? 0;
        const widthByKey = new Map(
            dataDescriptors.map((descriptor) => [
                descriptor.key,
                resizedWidthsRef.current.get(columnWidthKey(widthKeySection, descriptor.key)) ??
                    descriptor.width,
            ]),
        );
        const orderedKeys = dataDescriptors.map((descriptor) => descriptor.key);
        const indexByKey = new Map(orderedKeys.map((key, index) => [key, index]));
        const entryFor = (prop: string): HiddenColumnEntry | null => {
            const index = indexByKey.get(prop);
            const descriptor = index === undefined ? undefined : dataDescriptors[index];
            return descriptor
                ? {
                      number: (index ?? 0) + 1,
                      path: [...(descriptor.path ?? []), descriptor.label],
                  }
                : null;
        };
        const next: typeof unhideButtons = [];
        for (const block of hiddenBlocks) {
            const first = block.props[0];
            if (!first) continue;
            const firstIndex = indexByKey.get(first) ?? 0;
            let left = 52;
            for (let index = 0; index < firstIndex; index += 1) {
                const key = orderedKeys[index];
                if (key !== undefined && effectiveHidden[key] === undefined) {
                    left += widthByKey.get(key) ?? 0;
                }
            }
            // Prefer the live header-cell edge: it already includes the row
            // header and the exact horizontal scroll, without the scrollbar's
            // width rounding.
            const previous = orderedKeys
                .slice(0, firstIndex)
                .reverse()
                .find((key) => effectiveHidden[key] === undefined);
            const previousCell =
                previous === undefined
                    ? null
                    : grid
                          .querySelector(`.rgHeaderCell [data-prop="${previous}"]`)
                          ?.closest(".rgHeaderCell");
            if (previousCell instanceof HTMLElement) {
                left = previousCell.getBoundingClientRect().right - shellRect.left;
            } else {
                left -= scrollLeft;
            }
            const anchor = Math.max(0, Math.min(block.anchor, headerRowCount - 1));
            next.push({
                key: `${block.priority}:${block.props.join("|")}`,
                props: block.props,
                entries: block.props
                    .map(entryFor)
                    .filter((entry): entry is HiddenColumnEntry => entry !== null),
                left: Math.round(left),
                top: anchor * rowHeight,
                label: `Показать скрытые столбцы (${block.props.length})`,
                service: false,
                // A column hidden LATER wins the pointer over a neighbouring
                // button at the same boundary; the service «+» stays lowest.
                priority: block.priority + 1,
            });
        }
        if (!showServiceColumns && onServiceColumnsChange) {
            let left = 52;
            for (const key of orderedKeys) {
                if (effectiveHidden[key] === undefined) left += widthByKey.get(key) ?? 0;
            }
            const last = [...orderedKeys]
                .reverse()
                .find((key) => effectiveHidden[key] === undefined);
            const lastCell =
                last === undefined
                    ? null
                    : grid
                          .querySelector(`.rgHeaderCell [data-prop="${last}"]`)
                          ?.closest(".rgHeaderCell");
            if (lastCell instanceof HTMLElement) {
                left = lastCell.getBoundingClientRect().right - shellRect.left;
            } else {
                left -= scrollLeft;
            }
            // Centre the service reveal button ON the TABLE's right boundary —
            // the right edge of the last column — like every hidden-block
            // button is centred on its left edge. It is deliberately NOT
            // attached to the viewport/scrollbar: the anchor scrolls with the
            // table and can move off-screen when the last column does.
            // The anchor itself applies translateX(-50%).
            next.push({
                key: "__service__",
                props: [],
                entries: SERVICE_DESCRIPTORS.map((descriptor, index) => ({
                    number: dataDescriptors.length + index + 1,
                    path: [descriptor.label],
                })),
                // Centred ON the table's right boundary by CSS
                // (`translateX(-50%)`): the button's midpoint sits exactly on
                // the last column's right edge, matching the hidden-block
                // buttons that centre on a block's left edge.
                left: Math.round(left),
                top: 0,
                label: "Показать служебные столбцы",
                service: true,
                priority: 0,
            });
        }
        setUnhideButtons((previous) =>
            previous.length === next.length &&
            previous.every((entry, index) => {
                const candidate = next[index];
                return (
                    candidate !== undefined &&
                    entry.key === candidate.key &&
                    entry.left === candidate.left &&
                    entry.top === candidate.top &&
                    entry.label === candidate.label &&
                    entry.service === candidate.service &&
                    entry.priority === candidate.priority
                );
            })
                ? previous
                : next,
        );
    }, [
        dataDescriptors,
        effectiveHidden,
        headerRowCount,
        hiddenBlocks,
        onServiceColumnsChange,
        rowHeight,
        showServiceColumns,
        widthKeySection,
    ]);

    const scheduleUnhideMeasure = useCallback(() => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => measureUnhide());
        });
    }, [measureUnhide]);

    useEffect(() => {
        const epoch = createGridEpoch();
        const token = epoch.next();
        let attempts = 0;
        let lastCount = -1;
        const tick = () => {
            if (!epoch.isCurrent(token)) return;
            measureUnhide();
            const count = gridRef.current?.querySelectorAll(".rgHeaderCell").length ?? 0;
            attempts += 1;
            const settled = count === lastCount && count > 0;
            lastCount = count;
            if (!settled && attempts < 40) window.setTimeout(tick, 100);
        };
        tick();
        return () => {
            epoch.next();
        };
    }, [columns, measureUnhide, rowHeight, showServiceColumns]);

    /**
     * Right-click on a header: a leaf hides one column, a grouping cell hides
     * every (non-fixed) leaf under it. Fixed columns have no menu — they are the
     * table's identity and stay.
     */
    const openHeaderMenu = useCallback(
        (event: ReactMouseEvent<HTMLDivElement>) => {
            const path = event.nativeEvent.composedPath?.() ?? [];
            const cell = path.find(
                (node): node is HTMLElement =>
                    node instanceof HTMLElement && node.classList.contains("rgHeaderCell"),
            );
            if (!cell) return;
            const leaf = cell.querySelector("[data-prop]")?.getAttribute("data-prop") ?? null;
            let targets: string[] = [];
            let level = 0;
            let service = false;
            if (leaf !== null) {
                if (serviceKeys.has(leaf)) {
                    if (!onServiceColumnsChange) return;
                    targets = [leaf];
                    service = true;
                } else {
                    // The leaf starts at the BOTTOM of its group path, but a
                    // standalone column (no groups) takes the whole header
                    // height and starts at level 0. Hard-coding 1 kept a
                    // standalone neighbour from lifting a hidden block's
                    // button to its real header level.
                    targets = [leaf];
                    level = descriptorByKey.get(leaf)?.path?.length ?? 0;
                }
            } else {
                const groupNames = [
                    ...new Set(
                        descriptors
                            .map((descriptor) => descriptor.path?.[0])
                            .filter((name): name is string => Boolean(name)),
                    ),
                ];
                const text = cell.textContent?.trim() ?? "";
                const group =
                    cell.getAttribute("data-group") ??
                    groupNames.find((name) => text.includes(name)) ??
                    null;
                if (!group) return;
                targets = dataDescriptors
                    .filter(
                        (descriptor) =>
                            descriptor.path?.[0] === group &&
                            effectiveHidden[descriptor.key] === undefined &&
                            !serviceKeys.has(descriptor.key),
                    )
                    .map((descriptor) => descriptor.key);
                if (targets.length === 0) return;
                level = 0;
            }
            event.preventDefault();
            event.stopPropagation();
            const items = columnMenuItems({
                targets,
                level,
                pinnedKeys,
                service,
                onHide: (props, targetLevel) => {
                    hide(props, targetLevel);
                    clearGridSelection();
                },
                onPin: (props) => {
                    setPinnedMany(props, true);
                    show(props);
                    clearGridSelection();
                },
                onUnpin: (props) => {
                    setPinnedMany(props, false);
                    clearGridSelection();
                },
                onHideService: () => onServiceColumnsChange?.(false),
            });
            if (items.length === 0) return;
            setGridMenuOpen(true);
            setGridMenu({ x: event.clientX, y: event.clientY, items });
        },
        [
            clearGridSelection,
            dataDescriptors,
            descriptors,
            effectiveHidden,
            hide,
            onServiceColumnsChange,
            pinnedKeys,
            serviceKeys,
            setPinnedMany,
            show,
        ],
    );

    const setRowPinned = useCallback(
        (rowId: string, pinned: boolean, number?: number) => {
            setPinnedRowIds((previous) => {
                if (pinned) return previous.includes(rowId) ? previous : [...previous, rowId];
                return previous.filter((id) => id !== rowId);
            });
            setPinnedRowSnapshots((previous) => {
                if (!pinned) {
                    if (previous[rowId] === undefined) return previous;
                    const next = { ...previous };
                    delete next[rowId];
                    return next;
                }
                const row = rows.find((candidate) => candidate.id === rowId);
                if (!row) return previous;
                return previous[rowId] === row ? previous : { ...previous, [rowId]: row };
            });
            setPinnedRowNumbers((previous) => {
                if (pinned) {
                    const next = number ?? previous[rowId];
                    if (next === undefined) return previous;
                    return previous[rowId] === next ? previous : { ...previous, [rowId]: next };
                }
                if (previous[rowId] === undefined) return previous;
                const next = { ...previous };
                delete next[rowId];
                return next;
            });
        },
        [rows],
    );

    /**
     * Right-click on a row number or anywhere on a pinned-row copy.
     *
     * The row's real place in the source is untouched: the copy in the pinned
     * strip is the only thing that appears or disappears. Returns `true` when
     * the event belonged to a row so the header menu does not also open.
     */
    const openRowMenu = useCallback(
        (event: ReactMouseEvent<HTMLDivElement>): boolean => {
            const path = event.nativeEvent.composedPath?.() ?? [];
            const cell = path.find(
                (node): node is HTMLElement =>
                    node instanceof HTMLElement &&
                    node.classList.contains("rgCell") &&
                    node.hasAttribute("data-row-id"),
            );
            if (!cell) return false;
            const rowId = cell.getAttribute("data-row-id");
            if (!rowId || rowId === SUMMARY_ROW_ID) return false;
            const isCopy = cell.getAttribute("data-pinned-copy") === "true";
            // The row header's `data-rgrow` is the RENDERED index (sorting
            // included), which is exactly the number the copy must show.
            const renderedIndex = Number(cell.getAttribute("data-rgrow"));
            event.preventDefault();
            event.stopPropagation();
            const pinned = isCopy || pinnedRowIdSet.has(rowId);
            const items: GridMenuItem[] = [
                pinned
                    ? {
                          id: "unpin-row",
                          label: "Открепить",
                          icon: "unpin" as const,
                          onSelect: () => setRowPinned(rowId, false),
                      }
                    : {
                          id: "pin-row",
                          label: "Закрепить",
                          icon: "pin" as const,
                          onSelect: () =>
                              setRowPinned(
                                  rowId,
                                  true,
                                  Number.isFinite(renderedIndex) ? renderedIndex + 1 : undefined,
                              ),
                      },
            ];
            setGridMenuOpen(true);
            setGridMenu({ x: event.clientX, y: event.clientY, items });
            return true;
        },
        [pinnedRowIdSet, setRowPinned],
    );

    /** The grid shell's right-click: a row menu wins, then a header menu. */
    const openGridContextMenu = useCallback(
        (event: ReactMouseEvent<HTMLDivElement>) => {
            if (openRowMenu(event)) return;
            openHeaderMenu(event);
        },
        [openHeaderMenu, openRowMenu],
    );

    const handleUnhide = useCallback(
        (button: (typeof unhideButtons)[number]) => {
            if (button.service) {
                onServiceColumnsChange?.(true);
                return;
            }
            show(button.props);
            clearGridSelection();
        },
        [clearGridSelection, onServiceColumnsChange, show],
    );

    // First placement, and again whenever the columns change (the header is laid
    // out asynchronously, so a single early frame is not enough).
    useEffect(() => {
        if (!filteringEnabled) return;
        const epoch = createGridEpoch();
        const token = epoch.next();
        let attempts = 0;
        let lastCount = -1;
        const tick = () => {
            if (!epoch.isCurrent(token)) return;
            measureFunnels();
            const grid = gridRef.current;
            const count = grid ? grid.querySelectorAll(".rgHeaderCell [data-prop]").length : 0;
            attempts += 1;
            const settled = count === lastCount && count > 0;
            lastCount = count;
            if (!settled && attempts < 40) window.setTimeout(tick, 100);
        };
        tick();
        return () => {
            epoch.next();
        };
    }, [columns, filteringEnabled, measureFunnels]);

    // The column whose header CELL is under the pointer. The overlay mirrors it
    // onto its funnel: a collapsed button is zero-width, so it can never receive
    // the hover that would open it.
    const [hoveredHeaderProp, setHoveredHeaderProp] = useState<string | null>(null);
    // The columns whose funnel is VISIBLE right now (hovered, or pinned by an
    // applied filter). Written onto each matching label as
    // `data-funnel-open="true"`: the button lives in the overlay, so a header
    // label cannot see it, and CSS cannot correlate the two otherwise.
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const open = new Set(Object.keys(columnFilters ?? {}));
        if (hoveredHeaderProp !== null) open.add(hoveredHeaderProp);
        for (const label of grid.querySelectorAll<HTMLElement>(".report-header__title")) {
            const prop = label.getAttribute("data-prop");
            // The label's own marker: the funnel is an overlay sibling, so this
            // is the only thing CSS can read from the header side.
            if (prop !== null && open.has(prop)) label.setAttribute("data-funnel-open", "true");
            else label.removeAttribute("data-funnel-open");
        }
    }, [columnFilters, hoveredHeaderProp, funnels]);
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || !filteringEnabled) return;
        const onPointerMove = (event: MouseEvent) => {
            const target = event.target as Element | null;
            // The column boundary is RevoGrid's resize handle: the funnel must
            // not open there. Even offset 7px away it sits next to the strip
            // the pointer is aiming for, and a moving control beside a 6px
            // target is exactly what made resizing feel impossible.
            if (target?.closest?.(".resizable")) {
                setHoveredHeaderProp(null);
                return;
            }
            // Moving onto OUR overlay button must not close it: the button is
            // the thing the user is aiming at, so its own column stays hovered.
            const funnel = target?.closest?.(".report-header-funnels [data-prop]");
            if (funnel) {
                setHoveredHeaderProp(funnel.getAttribute("data-prop"));
                return;
            }
            const cell = target?.closest?.(".rgHeaderCell");
            const prop = cell?.querySelector?.("[data-prop]")?.getAttribute("data-prop") ?? null;
            setHoveredHeaderProp((previous) => (previous === prop ? previous : prop));
        };

        // On the DOCUMENT, not the grid: the funnel layer is a SIBLING of
        // `revo-grid` (it has to be — it must sit in the shell's stacking
        // context, above the header), so a listener on the grid never sees the
        // pointer entering the button. That is why the funnel closed exactly as
        // the pointer reached it.
        //
        // BOTH events: a real mouse fires `pointermove`, while synthetic input
        // delivers `mousemove`. The handler reads only `target`. Do not add a
        // document-level `pointerleave` here: it fires while the pointer crosses
        // from a header cell to the overlay sibling, clearing the marker in the
        // small gap before the next pointermove reopens it (visible as a rapid
        // reveal/hide animation at the cell edge).
        document.addEventListener("pointermove", onPointerMove, true);
        document.addEventListener("mousemove", onPointerMove, true);
        return () => {
            document.removeEventListener("pointermove", onPointerMove, true);
            document.removeEventListener("mousemove", onPointerMove, true);
        };
    }, [filteringEnabled]);

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        for (const label of grid.querySelectorAll<HTMLElement>(".report-header__title")) {
            if (label.getAttribute("data-prop") === hoveredHeaderProp) {
                label.setAttribute("data-hovered", "true");
            } else {
                label.removeAttribute("data-hovered");
            }
        }
    }, [hoveredHeaderProp]);

    const measureViewport = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid || !onViewStatsChange) return;
        const [columns, visibleSource] = await Promise.all([
            gridAdapter.getColumns(),
            gridAdapter.getVisibleRows(rows),
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
            filteredColumns: filteredColumnPaths(),
            sortedColumns: sortedColumnPaths(),
            immutableCols: counted.immutable,
            calculatedCols: counted.calculated,
            editableCols: counted.editable,
            hiddenColumns: hiddenColumnEntries,
        };
        const previous = lastStatsRef.current;
        if (
            previous !== null &&
            previous.totalRows === next.totalRows &&
            previous.filteredRows === next.filteredRows &&
            previous.totalCols === next.totalCols &&
            previous.filtersApplied === next.filtersApplied &&
            previous.filteredColumns.length === next.filteredColumns.length &&
            previous.filteredColumns.every(
                (path, index) => path.join("→") === next.filteredColumns[index]?.join("→"),
            ) &&
            previous.sortedColumns.length === next.sortedColumns.length &&
            previous.sortedColumns.every(
                (entry, index) =>
                    entry.order === next.sortedColumns[index]?.order &&
                    entry.path.join("→") === next.sortedColumns[index]?.path.join("→"),
            ) &&
            previous.immutableCols === next.immutableCols &&
            previous.calculatedCols === next.calculatedCols &&
            previous.editableCols === next.editableCols &&
            (previous.hiddenColumns?.length ?? 0) === (next.hiddenColumns?.length ?? 0) &&
            (previous.hiddenColumns ?? []).every(
                (entry, index) =>
                    entry.number === next.hiddenColumns?.[index]?.number &&
                    entry.path.join("→") === next.hiddenColumns?.[index]?.path.join("→"),
            )
        ) {
            return;
        }
        lastStatsRef.current = next;
        onViewStatsChange(next);
    }, [
        descriptors,
        filteredColumnPaths,
        gridAdapter,
        hiddenColumnEntries,
        onViewStatsChange,
        rows,
        sortedColumnPaths,
    ]);

    /**
     * Drop this section's sorting: the grids' plugin, our state, the stored order,
     * the arrow class and the merges that a sorting suppresses. One place, because
     * the third click on a header, a reset through the menu and an empty
     * `aftersortingapply` must all leave exactly the same state behind.
     */
    /**
     * Drop THIS column's sorting — the third state of a header click.
     *
     * The plugin MERGES every config it receives into what it already holds
     * (`applySortingConfig` builds on its current state), so a prop assignment
     * can only ADD a column: the removal has to go through its own
     * `clearColumnSorting`, which is not exposed on the element. What IS exposed
     * is `clearSorting()` — so the whole sorting is cleared and immediately
     * re-applied from our state, which is the only place the answer lives.
     */
    const clearColumnSorting = useCallback(
        (prop: string) => {
            const columns = (sortingBySectionRef.current.get(section)?.columns ?? []).filter(
                (column) => column.prop !== prop,
            );
            if (columns.length === 0) sortingBySectionRef.current.delete(section);
            else sortingBySectionRef.current.set(section, { columns });
            writeStoredSorting(
                sortingScope,
                section,
                columns.map((column) => ({ prop: column.prop, order: column.order })),
            );
            if (columns.length === 0) {
                userSortedSectionsRef.current.delete(section);
                gridRef.current?.classList.remove("data-grid--user-sorted");
                const configured = configuredCellMergeRef.current;
                if (configured !== undefined && gridRef.current) {
                    gridRef.current.cellMerge = filtersActive ? [] : configured;
                }
                onSortingChange?.(false);
            }
            bumpSorting();
            const grid = gridRef.current;
            if (!grid) return;
            void (async () => {
                // Clear first: the plugin has no «drop one column» entry point.
                await grid.clearSorting();
                // Even with nothing left, the empty state is re-asserted: the
                // plugin's own bookkeeping must agree with the arrow, or the next
                // click starts its cycle from the wrong point (the dead presses).
                pushSortingToPlugin(columns);
            })();
        },
        [bumpSorting, filtersActive, onSortingChange, pushSortingToPlugin, section, sortingScope],
    );

    /** Drop this section's whole sorting (the «reset view» menu). */
    const resetSorting = useCallback(() => {
        const grid = gridRef.current;
        sortingBySectionRef.current.delete(section);
        writeStoredSorting(sortingScope, section, []);
        userSortedSectionsRef.current.delete(section);
        grid?.classList.remove("data-grid--user-sorted");
        const configured = configuredCellMergeRef.current;
        if (configured !== undefined && grid) grid.cellMerge = filtersActive ? [] : configured;
        if (grid) pushSortingToPlugin([]);
        onSortingChange?.(false);
        bumpSorting();
    }, [bumpSorting, filtersActive, onSortingChange, section, sortingScope, pushSortingToPlugin]);

    const resetGridView = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const plugins = await grid.getPlugins();
        const filterPlugin = plugins.find((plugin) => "clearFiltering" in plugin) as
            | { clearFiltering: () => Promise<void> }
            | undefined;
        await filterPlugin?.clearFiltering();
        // The same reset the third header click performs, so the menu and the
        // click leave an identical view — including the stored entry, which used
        // to survive here and come back on the next reload.
        if (sortMeasureTimer.current) window.clearTimeout(sortMeasureTimer.current);
        resetSorting();
        void measureViewport();
    }, [measureViewport, resetSorting]);

    const resetColumnWidths = useCallback(() => {
        resizedWidthsRef.current.clear();
        setWidthsVersion((version) => version + 1);
    }, []);

    const buildCopyText = useCallback(
        async (range: RangeArea | null): Promise<string | null> => {
            if (columns.length === 0) return null;
            // A range in the pinned strip is not a range of the main source.
            if (selectionInPinnedRowRef.current && selectedRowIds.length === 0) return null;
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
            const row = visible[y];
            if (!row) continue;
            models[y] = row;
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
        gridAdapter.dispatchRangeEdit({
            data,
            models,
            type: "rgRow",
            oldRange: range,
            newRange: range,
        });
        // Select the cut range (rangeeditapply doesn't move the selection itself).
        void applyGridRange({ x: range.x, y: range.y }, { x: range.x1, y: range.y1 });
    }, [
        applyGridRange,
        buildCopyText,
        descriptorByKey,
        editableKeys,
        flatColumns,
        getVisibleRows,
        gridAdapter,
    ]);

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
            // Coordinates in the pinned strip index `pinnedTopSource` — pasting
            // there would land on unrelated main rows.
            if (selectionInPinnedRowRef.current) return;

            const pasteHeight = parsed.length;
            const pasteWidth = Math.max(0, ...parsed.map((row) => row.length));
            // Models come from the current visible source: under sorting/filtering
            // the displayed row index doesn't match the original rows index.
            const visibleRows = await getVisibleRows();
            const visible = visibleRows.filter((row): row is GridRow => row !== undefined);
            // One cell — paste the whole buffer starting from it. Two or more —
            // paste only into the selection: a bigger buffer is trimmed by it
            // (Toast), a smaller one is pasted as-is, other cells untouched.
            const { range: target, truncated } = pasteTargetRange(
                range,
                { height: pasteHeight, width: pasteWidth },
                { rows: visible.length, columns: flatColumns.length },
            );
            if (truncated) onPasteTruncated?.();

            const data: Record<string, Record<string, unknown>> = {};
            const models: GridRow[] = [];
            for (let y = target.y; y <= target.y1; y++) {
                const row = visible[y];
                if (!row) continue;
                models[y] = row;
                const patch: Record<string, unknown> = {};
                for (let x = target.x; x <= target.x1; x++) {
                    const column = flatColumns[x] as ColumnRegular | undefined;
                    if (!column) continue;
                    const sourceCol = parsed[y - target.y]?.[x - target.x];
                    if (sourceCol === undefined) continue;
                    patch[String(column.prop)] = sourceCol;
                }
                if (Object.keys(patch).length > 0) data[String(y)] = patch;
            }
            gridAdapter.dispatchRangeEdit({
                data,
                models,
                type: "rgRow",
                oldRange: range,
                newRange: target,
            });
            // After the paste, select exactly the pasted cells. The grid's own
            // `setrange` event does not fire on the programmatic path, so the
            // published range (footer status + presence) is updated here.
            await applyGridRange({ x: target.x, y: target.y }, { x: target.x1, y: target.y1 });
            updateSelectionRange(target);
        },
        [
            applyGridRange,
            flatColumns,
            getVisibleRows,
            gridAdapter,
            onPasteTruncated,
            updateSelectionRange,
        ],
    );

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const handleCopy = (event: ClipboardEvent) => {
            if (!gridAdapter.isTarget(event)) return;
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
            if (isFromTextEditor(event)) return;
            const grid = gridRef.current;
            if (!grid) return;
            // The paste belongs to this grid when it happens inside it, or
            // when the grid has an active selection: selecting a cell can
            // leave the focus on body (focuscell → re-render → blur), and a
            // plain isGridTarget check on the event target would lose it.
            if (!gridAdapter.isTarget(event) && selectionRangeRef.current === null) return;
            const text = event.clipboardData?.getData("text/plain");
            if (text == null || text === "") return;
            event.preventDefault();
            event.stopPropagation();
            void pasteIntoSelection(text);
        };
        const handleAfterFilterApply = () => {
            void measureViewport();
        };
        /**
         * The header funnel opens OUR popover.
         *
         * RevoGrid's `.rv-filter` button is the click target (it is what renders
         * something the user can see), but its own handler opens RevoGrid's
         * condition panel — and a header click also starts a sort. Both run
         * after a `click` that bubbles through the grid, so the interception is
         * registered on `document` in the CAPTURE phase: the right target is
         * still known, yet RevoGrid never sees the event it would react to.
         *
         * `document`, not the grid element: React's own listeners live on the
         * container above it, so a capture listener on the grid would still run
         * after the click had already reached RevoGrid's handlers.
         */
        const handleFilterClickCapture = (event: Event) => {
            const target = event.target as Element | null;
            // The funnel is ours (drawn by the column template); RevoGrid's own
            // `.rv-filter` is hidden inside the shadow root.
            const funnel = target?.closest?.(".header-filter-button") ?? null;
            if (!funnel || !filteringEnabled) return;
            if (!gridRef.current?.contains(funnel)) return;
            const cell = funnel.closest(".rgHeaderCell") as HTMLElement | null;
            if (!cell) return;
            // RevoGrid's header cell carries only `data-col`, and that is the
            // VIEWPORT's position (the leaf's index among the rendered
            // columns), not the column's own order — reading `flatColumns`
            // with it is right only while the grid is not grouped. Prefer the
            // prop the column template stamps into `.header-content`, and fall
            // back to the index.
            const propFromHeader =
                funnel.getAttribute("data-prop") ??
                cell.querySelector("[data-prop]")?.getAttribute("data-prop");
            const index = Number(cell.getAttribute("data-col"));
            const indexed = Number.isFinite(index) ? flatColumnsRef.current[index] : undefined;
            const prop = propFromHeader ?? (indexed ? String(indexed.prop) : null);
            if (!prop || !descriptorByKey.has(prop)) return;
            event.preventDefault();
            event.stopPropagation();
            setFilterTarget({
                column: prop,
                label: descriptorByKey.get(prop)?.label ?? prop,
                anchor: cell.getBoundingClientRect(),
            });
        };
        // A header click on RevoGrid's native filter funnel also falls through to
        // the column sort (`beforeheaderclick`), so opening a filter silently
        // sorts the column as well. Suppress the sort for funnel clicks.
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
        /**
         * A click anywhere OUTSIDE the grid drops the selection.
         *
         * revo-grid clears its own focus rectangle when the pointer leaves for
         * the page (that is why `Del` stopped working), but it emits no event
         * for it — so without this the footer status and the collaboration
         * presence kept advertising a selection that no longer existed.
         *
         * Capture phase, so it runs before the click's own handlers. A click
         * INSIDE the grid is ignored, and so is a click on one of the grid's
         * PORTALS — the cell editor, a header-filter popover, the smart select —
         * which React renders into `document.body`, outside the shell; treating
         * those as outside would clear the very range being edited. The table
         * action bar is exempt for the same reason (`keepsGridSelection`): its
         * row buttons read the selection they would otherwise erase mid-press.
         */
        const handleOutsidePointerDown = (event: PointerEvent) => {
            const target = event.target;
            if (!(target instanceof Element)) return;
            if (keepsGridSelection(target)) return;
            const shell = grid.closest(".revo-grid-shell") ?? grid;
            if (shell.contains(target)) return;
            clearGridSelection();
            setSelectedRowIds([]);
            rowSelectionAnchorRef.current = null;
        };
        window.addEventListener("copy", handleCopy, true);
        window.addEventListener("paste", handlePaste, true);
        document.addEventListener("pointerdown", handleOutsidePointerDown, true);
        const removeSetRange = gridAdapter.on("setRange", handleSetRange);
        const removeFocusCell = gridAdapter.on("focusCell", handleFocusCell);
        const removeAfterFilterApply = gridAdapter.on("afterFilterApply", handleAfterFilterApply);
        const removeCancelEdit = gridAdapter.on("cancelEdit", handleCancelEdit);
        const removeBeforeHeaderClick = gridAdapter.on(
            "beforeHeaderClick",
            handleBeforeHeaderClickCapture,
            true,
        );
        document.addEventListener("click", handleFilterClickCapture, true);
        return () => {
            window.removeEventListener("copy", handleCopy, true);
            window.removeEventListener("paste", handlePaste, true);
            removeSetRange();
            removeFocusCell();
            removeAfterFilterApply();
            removeCancelEdit();
            removeBeforeHeaderClick();
            document.removeEventListener("click", handleFilterClickCapture, true);
            document.removeEventListener("pointerdown", handleOutsidePointerDown, true);
        };
    }, [
        clearGridSelection,
        copySelection,
        descriptorByKey,
        filteringEnabled,
        gridAdapter,
        onReleaseLease,
        pasteIntoSelection,
        updateSelectionRange,
        measureViewport,
    ]);
    // The handle reads the CURRENT callbacks through a ref instead of being
    // rebuilt: React detaches and re-attaches a handle whose identity changed, and
    // on a Stencil host that re-attachment rewrites the element's props, which
    // schedules one more render — a closed loop.
    const handleRef = useRef({
        getExportState,
        copySelection,
        pasteIntoSelection,
        clearSelection: clearGridSelection,
        resetColumnWidths,
        resetFiltersSorting: resetGridView,
    });
    handleRef.current = {
        getExportState,
        copySelection,
        pasteIntoSelection,
        clearSelection: clearGridSelection,
        resetColumnWidths,
        resetFiltersSorting: resetGridView,
    };
    clipboardRef.current = { copy: copySelection, paste: pasteIntoSelection };

    useImperativeHandle(
        forwardedRef,
        () => ({
            getExportState: (...args) => handleRef.current.getExportState(...args),
            copySelection: (...args) => handleRef.current.copySelection(...args),
            pasteIntoSelection: (...args) => handleRef.current.pasteIntoSelection(...args),
            clearSelection: (...args) => handleRef.current.clearSelection(...args),
            resetColumnWidths: (...args) => handleRef.current.resetColumnWidths(...args),
            resetFiltersSorting: (...args) => handleRef.current.resetFiltersSorting(...args),
        }),
        [],
    );

    useEffect(() => {
        const isEditableTarget = (active: Element | null) =>
            active instanceof HTMLElement &&
            (active.tagName === "INPUT" ||
                active.tagName === "TEXTAREA" ||
                active.isContentEditable);

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                if (
                    event.defaultPrevented ||
                    hasOpenSmartSelect() ||
                    hasOpenKeyboardOverlay() ||
                    (event.target instanceof Element &&
                        event.target.closest("[data-grid-keyboard-overlay]")) ||
                    isFromTextEditor(event) ||
                    isEditableTarget(document.activeElement)
                )
                    return;
                if (selectionRangeRef.current === null && selectedRowIds.length === 0) return;
                event.preventDefault();
                event.stopImmediatePropagation();
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
            // Ctrl+V is intentionally NOT intercepted: the native paste event
            // (the window "paste" listener below) carries the text without the
            // clipboard-read confirmation prompt Chrome shows for
            // navigator.clipboard.readText().
            if (event.code !== "KeyC" && event.code !== "KeyX" && event.code !== "KeyA") return;
            if (isFromTextEditor(event)) return;
            const grid = gridRef.current;
            if (!grid) return;
            // A cell click can leave the focus on body (focuscell →
            // re-render → blur), so a live selection routes the shortcut to
            // the grid even without grid focus.
            if (!gridAdapter.isTarget(event) && selectionRangeRef.current === null) return;
            event.preventDefault();
            if (event.code === "KeyA") {
                // Listen in the capture phase to intercept Ctrl+A before revo-grid's
                // built-in select-all (it selects headers and tears the frame).
                // stopImmediatePropagation — reliably silences other
                // document listeners (incl. revo-grid itself), in any order.
                event.stopImmediatePropagation();
                if (!isChunkedSection(section)) void selectAllCells();
            } else if (event.code === "KeyC") {
                void copySelection();
            } else {
                void cutSelection();
            }
        };
        // capture: intercept Ctrl+A before revo-grid.
        document.addEventListener("keydown", handleKeyDown, true);
        return () => document.removeEventListener("keydown", handleKeyDown, true);
    }, [clearGridSelection, copySelection, cutSelection, selectAllCells, section, selectedRowIds]);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
        });
        const observer = new ResizeObserver(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
            scheduleUnhideMeasure();
        });
        if (gridRef.current) observer.observe(gridRef.current);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [
        columns,
        headerPinned,
        measureViewport,
        refreshHorizontalScrollbar,
        rows,
        scheduleUnhideMeasure,
    ]);

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
                    "--grid-header-row-height": `${rowHeight}px`,
                    "--rm-summary-row-height": `${rowHeight}px`,
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
            onContextMenu={openGridContextMenu}
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
            {/* The funnels: OUR buttons, positioned over the header cells. See
                `measureFunnels` for why they cannot live in the header template. */}
            <div ref={funnelLayerRef} className="report-header-funnels">
                {funnels.map((funnel) => (
                    <button
                        key={funnel.prop}
                        type="button"
                        className={
                            columnFilters?.[funnel.prop] !== undefined
                                ? "header-filter-button is-active"
                                : "header-filter-button"
                        }
                        aria-label={`Фильтр: ${funnel.label}`}
                        aria-pressed={columnFilters?.[funnel.prop] !== undefined}
                        data-filter-order={
                            columnFilters?.[funnel.prop]
                                ? Object.keys(columnFilters).indexOf(funnel.prop) + 1
                                : undefined
                        }
                        data-prop={funnel.prop}
                        data-cell-hover={hoveredHeaderProp === funnel.prop ? "true" : undefined}
                        style={{
                            left: funnel.left,
                            top: funnel.top,
                            height: funnel.height,
                        }}
                        onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const cell = gridRef.current
                                ?.querySelector(`.rgHeaderCell [data-prop="${funnel.prop}"]`)
                                ?.closest(".rgHeaderCell");
                            setFilterTarget({
                                column: funnel.prop,
                                label: funnel.label,
                                anchor: (cell ?? event.currentTarget).getBoundingClientRect(),
                            });
                        }}
                    >
                        <span className="header-filter-button__icon" aria-hidden="true" />
                        {columnFilters?.[funnel.prop] ? (
                            <span className="header-filter-button__order">
                                {Object.keys(columnFilters).indexOf(funnel.prop) + 1}
                            </span>
                        ) : null}
                    </button>
                ))}
            </div>
            <div ref={unhideLayerRef} className="report-header-unhide">
                {unhideButtons.map((button) => (
                    <GridUnhideButton
                        key={button.key}
                        label={button.label}
                        entries={button.entries}
                        style={{ left: button.left, top: button.top }}
                        priority={button.priority}
                        onPress={() => handleUnhide(button)}
                    />
                ))}
            </div>
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                <RevoGrid
                    ref={gridRef}
                    className={[
                        "data-grid",
                        headerRowCount > 1 && "data-grid--merged-header",
                        Boolean(pinnedTopSource?.length) && "data-grid--summary",
                        pinnedCopyRows.length > 0 && "data-grid--pinned-rows",
                        userSortedSectionsRef.current.has(section) && "data-grid--user-sorted",
                    ]
                        .filter(Boolean)
                        .join(" ")}
                    columns={columns}
                    source={visibleRows}
                    pinnedTopSource={gridPinnedTopSource}
                    plugins={plugins ?? EMPTY_PLUGIN_LIST}
                    theme="compact"
                    range
                    useClipboard={false}
                    resize
                    rowHeaders={rowHeaders}
                    editors={editors}
                    readonly={readOnly}
                    rowSize={rowHeight}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
                        void measureViewport();
                        scheduleFunnelMeasure();
                        void syncPinnedRowNumbers();
                    }}
                    onHeaderclick={(event) => {
                        // Ctrl+click ADDS a sort level; a plain click REPLACES the
                        // whole sorting. The modifier must come from the click's own
                        // mouse event (`headerclick` carries it) — `beforesorting`
                        // reports `additive` taken from `originalEvent.shiftKey`,
                        // always false for a Ctrl+click, and does not carry the
                        // event at all. The value is consumed by the matching
                        // `beforesorting` below and reset right after.
                        const originalEvent = (
                            event.detail as { originalEvent?: MouseEvent } | undefined
                        )?.originalEvent;
                        additiveSortRef.current =
                            originalEvent?.ctrlKey === true || originalEvent?.metaKey === true;
                    }}
                    onBeforesorting={(event) => {
                        // The click's outcome is OURS, not the plugin's.
                        //
                        // `headerclick` derives the next order from its own
                        // `this.sorting`, which it learns only through
                        // `aftercolumnsset` — and that event is not emitted on every
                        // path (a filter re-applying the source, a chunk arriving).
                        // When it misses, the plugin starts from `undefined`, which it
                        // reads as «reset»: the click switched the sorting OFF instead
                        // of moving it — the dead presses and jumps in the reports.
                        //
                        // `sortingBySectionRef` always has the answer, so every click
                        // is resolved from there: asc → desc → off. The plugin's own
                        // guess is never used for a column we know.
                        const detail = event.detail as {
                            column?: { prop?: string };
                            order?: "asc" | "desc";
                        };
                        const prop = detail?.column?.prop;
                        if (prop !== undefined) {
                            const columns = sortingBySectionRef.current.get(section)?.columns ?? [];
                            const stored = columns.find(
                                (column) => column.prop === String(prop),
                            )?.order;

                            if (stored === "desc") {
                                // The third state. `beforesorting` only arrives after
                                // `getNextOrder()` already reset the plugin, and
                                // cancelling the event would leave the plugin's
                                // bookkeeping untouched — so the reset is applied here,
                                // in one place, for every way into it.
                                event.preventDefault();
                                clearColumnSorting(String(prop));
                                return;
                            }

                            const next: GridSortingOrder = stored === "asc" ? "desc" : "asc";
                            detail.order = next;
                            // A plain click REPLACES the sorting — «отсортировать по
                            // этому столбцу». Multi-column sorting is an explicit
                            // Ctrl+click, as every table does it; keeping the other
                            // columns on a plain click turned a second click into a
                            // second sort level (the numbered arrows), which nobody
                            // asked for.
                            //
                            // The modifier is read from the ORIGINAL mouse event: the
                            // plugin neither passes it through nor learns it from the
                            // header click, so `detail.additive` never arrives.
                            const additive = additiveSortRef.current;
                            // One click, one value: the next click sets its own.
                            queueMicrotask(() => {
                                additiveSortRef.current = false;
                            });
                            const remaining = additive
                                ? columns.filter((column) => column.prop !== String(prop))
                                : [];
                            const applied = [
                                ...remaining,
                                {
                                    prop: String(prop),
                                    order: next,
                                    cellCompare: placeholderLastCellCompare,
                                },
                            ];
                            sortingBySectionRef.current.set(section, { columns: applied });
                            // The plugin is told the SAME state: its column list
                            // order is the sort priority (`sortingOrder`), so the
                            // list must be handed over explicitly — see
                            // `pushSortingToPlugin`.
                            pushSortingToPlugin(applied);
                            writeStoredSorting(
                                sortingScope,
                                section,
                                applied.map((column) => ({
                                    prop: column.prop,
                                    order: column.order,
                                })),
                            );
                            userSortedSectionsRef.current.add(section);
                            gridRef.current?.classList.add("data-grid--user-sorted");
                            onSortingChange?.(true);
                            bumpSorting();
                        }
                        // Remove index-based merges before RevoGrid reorders
                        // rows. RevoGrid starts sorting in a debounced job after
                        // this event; queue the plugin update so it cannot reset
                        // the sort while the lifecycle event is still running.
                        queueMicrotask(() => {
                            if (!gridRef.current || configuredCellMergeRef.current === undefined)
                                return;
                            gridRef.current.cellMerge = [];
                        });
                        userSortedSectionsRef.current.add(section);
                        gridRef.current?.classList.add("data-grid--user-sorted");
                    }}
                    onAftersortingapply={(event) => {
                        // The PLUGIN applied a sorting — it does not say what the
                        // state SHOULD be. Every click is resolved in
                        // `onBeforesorting` before this event fires, so this
                        // handler only keeps the leaves in step with the plugin
                        // (they carry the `order`) and re-measures the header.
                        //
                        // Treating its payload as the truth is what produced the
                        // dead presses: after a reset the plugin can report an empty
                        // sorting while our state still holds one, and adopting
                        // that erased the state the next click reads.
                        const sorting = sortingConfigFromEvent(event.detail);
                        const ours = sortingBySectionRef.current.get(section)?.columns ?? [];
                        // The leaves carry the sorting `order`, so a CHANGED sorting
                        // has to rebuild the columns. An unchanged one must NOT: the
                        // rebuild re-emits the sorting events, and rebuilding on
                        // every emission is a closed loop — «Maximum update depth
                        // exceeded» on every grid that sorts (the dictionaries
                        // crashed the page).
                        const next = sorting.columns.map(
                            (column) => `${column.prop}:${column.order}`,
                        );
                        const current = ours.map((column) => `${column.prop}:${column.order}`);
                        const changed =
                            next.length !== current.length ||
                            next.some((entry, index) => entry !== current[index]);
                        // The plugin MERGES every config it receives and, for a
                        // Ctrl+click, first applies its OWN guess — a single column,
                        // because it learned `this.sorting` from a config it never
                        // saw. Our list is the truth, so it is re-asserted whenever
                        // the plugin ends up disagreeing with it.
                        if (changed) {
                            pushSortingToPlugin(ours);
                            bumpSorting();
                        }
                        // The event fires in BURSTS for one click (the grid
                        // re-emits while it applies), and each one lands before
                        // the new header layout exists. Re-measuring the funnels
                        // on every burst made the header twitch mid-click and
                        // swallowed the next click. One pass, on the frame AFTER
                        // the burst, is what a click actually changes.
                        window.clearTimeout(sortMeasureTimer.current);
                        sortMeasureTimer.current = window.setTimeout(() => {
                            scheduleFunnelMeasure();
                            void measureViewport();
                        }, 250);
                    }}
                    onBeforefilterapply={(event) => {
                        // Filtering trims the data source synchronously, and there
                        // row count on the next tick. The event carries the new
                        // filter collection (empty when filters were cleared).
                        const collection = (
                            event as CustomEvent<{ collection?: Record<string, unknown> }>
                        ).detail?.collection;
                        filtersAppliedRef.current =
                            !!collection && Object.keys(collection).length > 0;
                        // Merges are INDEX-based: they were computed for the rows
                        // that were visible before the filter. Once the source is
                        // trimmed, a merge can span two unrelated records — the
                        // same hazard sorting already guards against (see
                        // `onBeforesorting`). Dropped on the same microtask, so
                        // the plugin update cannot fight the lifecycle event.
                        queueMicrotask(() => {
                            if (!gridRef.current || configuredCellMergeRef.current === undefined)
                                return;
                            const sorted =
                                (sortingBySectionRef.current.get(section)?.columns.length ?? 0) > 0;
                            gridRef.current.cellMerge =
                                sorted || filtersAppliedRef.current || hiddenActiveRef.current
                                    ? []
                                    : configuredCellMergeRef.current;
                        });
                        setTimeout(() => void measureViewport(), 0);
                    }}
                    onContentsizechanged={() => {
                        void refreshHorizontalScrollbar();
                        scheduleFunnelMeasure();
                        scheduleUnhideMeasure();
                    }}
                    onViewportscroll={(event) => {
                        if (event.detail.dimension === "rgCol") {
                            if (!headerPinned) {
                                const scrollbar = horizontalScrollRef.current;
                                if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                                    scrollbar.scrollLeft = event.detail.coordinate;
                                }
                            }
                        } else {
                            scheduleScrollRemember();
                        }
                        void measureViewport();
                        scheduleFunnelMeasure();
                        scheduleUnhideMeasure();
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
                        scheduleFunnelMeasure();
                        scheduleUnhideMeasure();
                    }}
                    onBeforeeditstart={beforeEditStart}
                    onBeforeedit={beforeEdit}
                    onAfteredit={afterEdit}
                    onBeforerangeedit={beforeRangeEdit}
                    onBeforecellfocus={(event) => {
                        selectionInPinnedRowRef.current = event.detail.type === "rowPinStart";
                        updateSelectionRange({
                            x: event.detail.colIndex,
                            y: event.detail.rowIndex,
                            x1: event.detail.colIndex,
                            y1: event.detail.rowIndex,
                        });
                    }}
                    onBeforerange={(event) => {
                        selectionInPinnedRowRef.current = event.detail.type === "rowPinStart";
                        updateSelectionRange(event.detail.newRange);
                    }}
                />
            </div>
            <div
                ref={horizontalScrollRef}
                className="revo-grid-external-scrollbar"
                style={{
                    display: hasHorizontalOverflow ? undefined : "none",
                    // The bar stops before the vertical scrollbar's lane, so the
                    // two bars never overlap in the bottom-right corner (same as
                    // the renovation plan).
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
            {goToTarget ? (
                <RowGoToFilter
                    anchor={goToTarget}
                    total={Math.max(1, visibleRowCountRef.current)}
                    maxColumn={flatColumns.length}
                    storageScope={filterDraftScope}
                    onGoTo={(position) => void scrollToRowPosition(position)}
                    onGoToColumn={(number) => void gridRef.current?.scrollToColumnIndex(number - 1)}
                    onClose={() => setGoToTarget(null)}
                />
            ) : null}
            {filterTarget ? (
                <ColumnFilterPopover
                    column={filterTarget.column}
                    columnLabel={filterTarget.label}
                    anchor={filterTarget.anchor}
                    facets={facetSourceFor(filterTarget.column)}
                    order={facetOrder(filterTarget.column)}
                    initial={columnFilters?.[filterTarget.column] ?? null}
                    storageScope={filterDraftScope}
                    // «По умолчанию» is the same state «Сбросить все фильтры
                    // и сортировки» restores: no constraint on this column.
                    defaultFilter={null}
                    onApply={(filter) => applyColumnFilter(filterTarget.column, filter)}
                    onClose={() => setFilterTarget(null)}
                />
            ) : null}
            <GridContextMenu
                request={gridMenu}
                isOpen={gridMenuOpen}
                onOpenChange={setGridMenuOpen}
            />
        </div>
    );
}

export const DataGrid = forwardRef(DataGridInner);

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
    // An unmatched name is not a valid asset reference: passing the name string
    // through as an id would send garbage to the backend. Drop it instead — the
    // server would reject it anyway, and this keeps the payload honest.
    return names
        .map((name) => idsByName.get(name.toLocaleLowerCase("ru-RU")))
        .filter((id): id is string => typeof id === "string");
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
