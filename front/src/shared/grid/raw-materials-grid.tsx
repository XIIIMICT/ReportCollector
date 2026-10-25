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
    type Ref,
} from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";
import { reactEditor } from "@/shared/grid/react-editor";
import { isFromTextEditor } from "@/shared/keyboard";
import {
    computeRowFacets,
    facetWindow,
    rowMatchesFilters,
    searchRowFacets,
    withFilterMarkers,
    type ColumnFilter,
} from "@/shared/ui/column-filter-facets";
import { ColumnFilterPopover, type FacetSource } from "@/shared/ui/column-filter-popover";
import { hasOpenModal } from "@/shared/ui/modal-presence";
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
    type ColumnDescriptor,
    type ExportColumn,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "./grid-config";
import { extractEdits, parseNumericText } from "./grid-edit";
import { MultiAssetEditor, ReferenceEditor, SelectEditor } from "./grid-editors";
import { parsePastedText, serializeCellValue } from "./index";
import { readScrollPosition, scrollScopeKey, writeScrollPosition } from "./scroll-position";
import { isChunkedSection } from "./section-policy";
import { describeSelection } from "./selection-label";

/** The pinned summary row's id (revo-grid `pinnedTopSource`) — never a real record. */
export const SUMMARY_ROW_ID = "__summary__";

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
    forwardedRef: Ref<RawMaterialsGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    // CellMergePlugin binds its config to the grid's `cellMerge` DOM property
    // through the same property watcher as `hideColumns` (the plugin's setter
    // interceptor fires on any later assignment too), but the React wrapper's
    // typings don't know the prop — so it's assigned directly on the element.
    useEffect(() => {
        const grid = gridRef.current;
        if (grid && cellMerge !== undefined) {
            grid.cellMerge = cellMerge;
        }
    }, [cellMerge]);
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
    const userSortedSectionsRef = useRef(new Set<SectionId>());
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
                    grid.getVisibleSource("rgRow"),
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
    }, [rowHeight, scrollFormKey, scrollScope]);

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

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || scrollFormKey === null) return;
        const onScroll = () => scheduleScrollRemember();
        grid.addEventListener("scrollchange", onScroll);
        return () => {
            grid.removeEventListener("scrollchange", onScroll);
        };
    }, [scheduleScrollRemember, scrollFormKey]);

    const sorting =
        sortingBySectionRef.current.get(section) ??
        (["users", "renovationUsers", "kppkUsers"].includes(section)
            ? {
                  columns: [
                      {
                          prop: "fullname",
                          order: "asc" as const,
                          cellCompare: placeholderLastCellCompare,
                      },
                  ],
              }
            : undefined);

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
    facetTextRef.current = facetText;

    /**
     * A `FacetSource` over the rows visible under the OTHER filters: a column's
     * option list must not collapse to what its own filter already kept, or the
     * user could never bring a value back. Same rule the plan applies
     * server-side (its facets are counted over the panel filters, never over the
     * header filters themselves).
     */
    /** Real records only: the «＋» row and deleted ghosts are not data. */
    const realRows = useMemo(
        () => rows.filter((row) => !row.isPlaceholder && !row.isDeleted),
        [rows],
    );

    const facetSourceFor = useCallback(
        (column: string): FacetSource => {
            const others = activeFilters.filter((filter) => filter.column !== column);
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
            );
            // The search narrows the WHOLE computed set before the window is cut
            // — the same rule `rowFacetSource` applies, kept here because this
            // source is already narrowed by the other columns' filters.
            return async (_column, { offset, limit, search }) =>
                facetWindow(searchRowFacets(facets, search), offset, limit);
        },
        [activeFilters, facetOrder, facetText, realRows],
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
            const current = (await grid.getColumns()) as (ColumnRegular | ColumnGrouping)[];
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
            ...(columnDescriptors && { descriptors: columnDescriptors }),
            onBooleanChange: (row, field, value) => {
                if (row.isPlaceholder) void onPlaceholderCommit({ [field]: value });
                else void onCommit([{ rowId: row.id, field, value }]);
            },
        });
        const summaryRow = pinnedTopSource?.[0];
        // Rendering-only columns (no header sort): the «Реализация» view keeps
        // its server order (product number → market), so a click on a header
        // must not reshuffle it. RevoGrid reads `sortable` per leaf column.
        if (!sortable) {
            for (const column of next) {
                const leaves = (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular];
                for (const leaf of leaves) leaf.sortable = false;
            }
        }
        // Tag the pinned summary row's cells so CSS can draw them like the
        // plan's summary (one vertical + one bottom border, white empty cells,
        // a green numeric cell) and the row header can hide its number.
        for (const column of next) {
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
                    }) as ColumnRegular["columnProperties"];
                    if (mergedHeaderProperties !== undefined) {
                        leaf.columnProperties = mergedHeaderProperties;
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
                            "raw-materials-grid__summary-cell": true,
                            "raw-materials-grid__summary-cell--filled": typeof value === "number",
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
        markFilterableColumns(next, descriptors);
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
        pinnedTopSource,
        readOnly,
        remoteCellInfo,
        remoteLocks,
        selectedRowIdSet,
        section,
        showServiceColumns,
        sortable,
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

    const headerRowCount = descriptors.some((descriptor) => descriptor.path?.length) ? 2 : 1;
    const headerHeight = headerRowCount * rowHeight;
    const contentHeight = headerHeight + rows.length * rowHeight + 18;

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
                return {
                    className: {
                        "report-row-header": true,
                        "report-row-header--selected": selectedRowIds.includes(row.id),
                        "report-row-header--summary": row.id === SUMMARY_ROW_ID,
                    },
                };
            },
            cellTemplate: (h: HyperFunc<VNode>, { rowIndex, model }) => {
                const row = model as GridRow;
                // The pinned summary row shows no row number.
                if (row.id === SUMMARY_ROW_ID) {
                    return h("span", { class: "report-row-header__summary" });
                }
                // The go-to popover clamps to the running row number.
                if (!row.isPlaceholder) visibleRowCountRef.current = rowIndex + 1;
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
            const current = (await grid.getColumns()) as (ColumnRegular | ColumnGrouping)[];
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
        // `canEditDescriptor` is a MODULE-level function (pure, no captured state),
        // so it never needs to be a dependency; the linter cannot tell a module
        // function from a local one.
        // oxlint-disable-next-line react-hooks/exhaustive-deps
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
        const size = await grid.getContentSize();
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
        let drag: {
            prop: string;
            startX: number;
            startWidth: number;
            minWidth: number;
            maxWidth: number;
        } | null = null;
        // getColumns() returns the grouping shape too (a group has children), so
        // narrow it the way the flatColumns memo above does.
        const leavesOf = (columns: unknown[]) =>
            (columns as (ColumnRegular | ColumnGrouping)[]).flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            );
        const resolveColumn = async (handle: Element) => {
            const grid = gridRef.current;
            const cell = handle.closest(".rgHeaderCell") as HTMLElement | null;
            if (!grid || !cell) return null;
            const match = /translateX\((-?[\d.]+)px\)/.exec(cell.style.transform);
            const cellLeft = match ? Number(match[1]) : 0;
            const width = cell.offsetWidth;
            const leaves = leavesOf(await grid.getColumns());
            let offset = 0;
            for (const leaf of leaves) {
                const leafWidth = typeof leaf.size === "number" ? leaf.size : 150;
                if (
                    Math.abs(offset + leafWidth - (cellLeft + width)) < 2 ||
                    Math.abs(offset - cellLeft) < 2
                ) {
                    return {
                        prop: String(leaf.prop),
                        startWidth: leafWidth,
                        minWidth: typeof leaf.minSize === "number" ? leaf.minSize : 40,
                        maxWidth:
                            typeof leaf.maxSize === "number"
                                ? leaf.maxSize
                                : Number.POSITIVE_INFINITY,
                    };
                }
                offset += leafWidth;
            }
            return null;
        };
        const onPointerDown = (event: PointerEvent) => {
            const target = event.target as Element | null;
            if (!target || !target.classList.contains("resizable-r")) return;
            if (!gridRef.current) return;
            event.preventDefault();
            event.stopPropagation();
            void resolveColumn(target).then((resolved) => {
                if (!resolved) return;
                drag = { ...resolved, startX: event.clientX };
                document.body.style.cursor = "ew-resize";
            });
        };
        const onPointerMove = (event: MouseEvent) => {
            if (!drag) return;
            const next = Math.round(
                Math.min(
                    drag.maxWidth,
                    Math.max(drag.minWidth, drag.startWidth + (event.clientX - drag.startX)),
                ),
            );
            resizedWidthsRef.current.set(columnWidthKey(widthKeySection, drag.prop), next);
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
    }, [rows, section, showAssetColumn, showServiceColumns, columnDescriptors, keycloakEnabled]);

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
        setFunnels(next);
    }, [descriptorByKey, filteringEnabled]);

    /** Re-measure on the frame after the grid has settled. */
    const scheduleFunnelMeasure = useCallback(() => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => measureFunnels());
        });
    }, [measureFunnels]);

    // First placement, and again whenever the columns change (the header is laid
    // out asynchronously, so a single early frame is not enough).
    useEffect(() => {
        if (!filteringEnabled) return;
        let cancelled = false;
        let attempts = 0;
        let lastCount = -1;
        const tick = () => {
            if (cancelled) return;
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
            cancelled = true;
        };
    }, [columns, filteringEnabled, measureFunnels]);

    // The column whose header CELL is under the pointer. The overlay mirrors it
    // onto its funnel: a collapsed button is zero-width, so it can never receive
    // the hover that would open it.
    const [hoveredHeaderProp, setHoveredHeaderProp] = useState<string | null>(null);
    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || !filteringEnabled) return;
        const onPointerMove = (event: MouseEvent) => {
            const target = event.target as Element | null;
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
        // delivers `mousemove`. The handler reads only `target`.
        const onPointerLeave = (event: MouseEvent) => {
            const target = event.target as Element | null;
            if (!target?.closest?.(".revo-grid-shell")) return;
            setHoveredHeaderProp(null);
        };
        document.addEventListener("pointermove", onPointerMove, true);
        document.addEventListener("mousemove", onPointerMove, true);
        document.addEventListener("pointerleave", onPointerLeave, true);
        return () => {
            document.removeEventListener("pointermove", onPointerMove, true);
            document.removeEventListener("mousemove", onPointerMove, true);
            document.removeEventListener("pointerleave", onPointerLeave, true);
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
        onSortingChange?.(false);
        userSortedSectionsRef.current.delete(section);
        gridRef.current?.classList.remove("raw-materials-grid--user-sorted");
    }, [onSortingChange, section]);

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

            // One cell — paste the whole buffer starting from it. Two or more —
            // paste only into the selection: a bigger buffer is trimmed by it
            // (Toast), a smaller one is pasted as-is, other cells untouched.
            // Models come from the current visible source: under sorting/filtering
            // the displayed row index doesn't match the original rows index.
            const visibleRows = await getVisibleRows();
            const visible = visibleRows.filter((row): row is GridRow => row !== undefined);
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
                const row = visible[y];
                if (!row) continue;
                models[y] = row;
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
            if (isFromTextEditor(event)) return;
            const grid = gridRef.current;
            if (!grid) return;
            // The paste belongs to this grid when it happens inside it, or
            // when the grid has an active selection: selecting a cell can
            // leave the focus on body (focuscell → re-render → blur), and a
            // plain isGridTarget check on the event target would lose it.
            if (!isGridTarget(event, grid) && selectionRangeRef.current === null) return;
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
        window.addEventListener("copy", handleCopy, true);
        window.addEventListener("paste", handlePaste, true);
        grid.addEventListener("setrange", handleSetRange);
        grid.addEventListener("focuscell", handleFocusCell);
        grid.addEventListener("afterfilterapply", handleAfterFilterApply);
        grid.addEventListener("canceledit", handleCancelEdit);
        grid.addEventListener("beforeheaderclick", handleBeforeHeaderClickCapture, true);
        document.addEventListener("click", handleFilterClickCapture, true);
        return () => {
            window.removeEventListener("copy", handleCopy, true);
            window.removeEventListener("paste", handlePaste, true);
            grid.removeEventListener("setrange", handleSetRange);
            grid.removeEventListener("focuscell", handleFocusCell);
            grid.removeEventListener("afterfilterapply", handleAfterFilterApply);
            grid.removeEventListener("canceledit", handleCancelEdit);
            grid.removeEventListener("beforeheaderclick", handleBeforeHeaderClickCapture, true);
            document.removeEventListener("click", handleFilterClickCapture, true);
        };
    }, [
        copySelection,
        descriptorByKey,
        filteringEnabled,
        onReleaseLease,
        pasteIntoSelection,
        updateSelectionRange,
        measureViewport,
    ]);
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
                if (
                    hasOpenSmartSelect() ||
                    hasOpenModal() ||
                    (event.target instanceof Element &&
                        event.target.closest("[data-grid-keyboard-overlay]")) ||
                    isFromTextEditor(event) ||
                    isEditableTarget(document.activeElement)
                )
                    return;
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
            if (!isGridTarget(event, grid) && selectionRangeRef.current === null) return;
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
    }, [clearGridSelection, copySelection, cutSelection, selectAllCells, section]);

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
                    </button>
                ))}
            </div>
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                <RevoGrid
                    ref={gridRef}
                    className={[
                        "raw-materials-grid",
                        headerRowCount > 1 && "raw-materials-grid--merged-header",
                        Boolean(pinnedTopSource?.length) && "raw-materials-grid--summary",
                        userSortedSectionsRef.current.has(section) &&
                            "raw-materials-grid--user-sorted",
                    ]
                        .filter(Boolean)
                        .join(" ")}
                    columns={columns}
                    source={visibleRows}
                    pinnedTopSource={pinnedTopSource ?? EMPTY_PINNED_SOURCE}
                    plugins={plugins ?? EMPTY_PLUGIN_LIST}
                    theme="compact"
                    range
                    useClipboard={false}
                    resize
                    rowHeaders={rowHeaders}
                    editors={editors}
                    {...(sorting ? { sorting } : {})}
                    hideAttribution
                    readonly={readOnly}
                    rowSize={rowHeight}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
                        void measureViewport();
                        scheduleFunnelMeasure();
                    }}
                    onBeforesorting={() => {
                        // Remove index-based merges before RevoGrid reorders
                        // rows. RevoGrid starts sorting in a debounced job after
                        // this event; queue the plugin update so it cannot reset
                        // the sort while the lifecycle event is still running.
                        queueMicrotask(() => {
                            if (onSortingChange && gridRef.current) gridRef.current.cellMerge = [];
                        });
                        userSortedSectionsRef.current.add(section);
                        gridRef.current?.classList.add("raw-materials-grid--user-sorted");
                    }}
                    onAftersortingapply={(event) => {
                        const sorting = sortingConfigFromEvent(event.detail);
                        sortingBySectionRef.current.set(section, sorting);
                        onSortingChange?.(sorting.columns.length > 0);
                        scheduleFunnelMeasure();
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
                    onContentsizechanged={() => {
                        void refreshHorizontalScrollbar();
                        scheduleFunnelMeasure();
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
                    // «По умолчанию» is the same state «Сбросить все фильтры
                    // и сортировки» restores: no constraint on this column.
                    defaultFilter={null}
                    onApply={(filter) => applyColumnFilter(filterTarget.column, filter)}
                    onClose={() => setFilterTarget(null)}
                />
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
