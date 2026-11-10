"use client";

import { toast } from "@heroui/react";
import { CheckCircle, Send, Upload } from "iconoir-react";
import { useTheme } from "next-themes";
import {
    createElement,
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    startTransition,
} from "react";

import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    AddRowDialog,
    ApprovePlanDialog,
    SubmitBlockedDialog,
    SubmitPlanDialog,
} from "@/features/renovation/renovation-plan-dialogs";
import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    addRenovationRow,
    approveRenovationPlan,
    createRenovationExportJob,
    deleteRenovationRow,
    getRenovationDiff,
    getRenovationMeta,
    getRenovationPlanRows,
    getRenovationPlanSummary,
    inspectRenovationPlanImport,
    getRenovationPlans,
    getUndoState,
    redoCells,
    RENOVATION_ASSETS_SECTION,
    RENOVATION_SECTION,
    RENOVATION_USERS_SECTION,
    renovationCatalogSection,
    renovationExportJobFileUrl,
    submitRenovationPlan,
    undoCells,
    updateRenovationCells,
    type RenovationDiff,
    type RenovationDiffRow,
    type RenovationMeta,
    type RenovationPlanRow,
    type RenovationPlanVersion,
    type RenovationSortColumn,
    type RenovationUndoResult,
} from "@/shared/api/renovation";
import {
    isProjectAdmin,
    ReportCollectorApiError,
    type CurrentUser,
} from "@/shared/api/report-collector";
import { canonicalCellKey, useCollabRoom } from "@/shared/collab/use-collab-room";
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import type { GridViewStats } from "@/shared/grid/data-grid";
import { fieldFacetOrder } from "@/shared/grid/grid-config";
import { collaborationAvailableFor, undoAvailableFor } from "@/shared/grid/section-policy";
import { SectionTabs } from "@/shared/grid/section-tabs";
import {
    clearFilterDrafts,
    isConstrainingFilter,
    readStoredFilters,
    readStoredSorting,
    writeStoredSorting,
    type StoredSortColumn,
    writeStoredFilters,
} from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useRestoredState } from "@/shared/hooks/use-restored-state";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import {
    NO_ROW_SELECTED_REASON,
    buildAddRowAction,
    buildClipboardActions,
    buildDeleteAction,
    buildExportAction,
    buildReplaceAction,
    buildHistoryActions,
    buildPrimaryAction,
    DeleteRecordDialog,
} from "@/shared/ui/action-items";
import { AppHeader } from "@/shared/ui/app-header";
import { precedingColumnFilters, type ColumnFilter } from "@/shared/ui/column-filter-facets";
import { FooterProgress } from "@/shared/ui/footer-progress";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import { applyHistoryResult, runHistoryAction } from "@/shared/ui/history-action";
import { readWorkspaceQuery } from "@/shared/ui/query-state";
import { RowGoToFilter } from "@/shared/ui/row-go-to-filter";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
import {
    errorMessage,
    updateClipboardActions,
    type ClipboardActions,
} from "@/shared/ui/text-helpers";
import { ToolbarFilters } from "@/shared/ui/toolbar-filters";
import { refreshUndoCounters } from "@/shared/ui/undo-counters";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import {
    readToolbarFilters,
    restoredFilterVerdict,
    useToolbarFilters,
} from "@/shared/ui/use-toolbar-filters";
import { useViewState } from "@/shared/ui/use-view-state";
import { ViewStrip } from "@/shared/ui/view-strip";
import { WorkspaceLoadError } from "@/shared/ui/workspace-feedback";
import { WorkspaceContent, WorkspaceFullscreenPanels } from "@/shared/ui/workspace-layout";
import { useDeleteShortcut, useUndoRedoShortcuts } from "@/shared/ui/workspace-shortcuts";
import type { ArchiveItem, ArchiveState, UndoState } from "@/shared/ui/workspace-state";

import { PlanExcelImportModal } from "./plan-excel-import-modal";
import { RenovationColumnFilterPopover } from "./renovation-column-filter";
import {
    RenovationDictionarySection,
    renovationSectionTable,
} from "./renovation-dictionary-section";
import {
    canonicalRenovationKey,
    RenovationGrid,
    type RenovationGridHandle,
    type RenovationGridRow,
    type RenovationRowPatch,
} from "./renovation-grid";
import { ASSET_COLUMN_PROP, ASSET_FIELD_DB_NAME, AUDIT_COLUMNS } from "./renovation-grid-constants";
import { usePlanImportJob } from "./use-plan-import-job";

/** Standard grid toasts: see `gridWarningHandlers`. */
const gridWarnings = gridWarningHandlers({});

export type RenovationWorkspaceProps = {
    currentUser: CurrentUser;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
    /** Form restored from `?section=` (plan / assets / a catalog id / users). */
    initialSection?: string | null;
    /** Mirrors the active form into the URL so a reload keeps it. */
    onSectionChange?: (section: string | null) => void;
    /** `SUPPORT_MAIL` - the mailbox of the header support control. */
    supportMail: string;
};

const WORKING_VERSION_ID = "__working";

/**
 * Identity of the remote row scope the grid loads: the plan version plus every
 * server-side filter. Changing any of them must restart loading from the first
 * chunk, so the grid keys its Infinity Scroll config off this string.
 */
function renovationGridScopeKey(params: {
    planId: string | null;
    asset: string | null;
    filters: ColumnFilter[];
    sorting: RenovationSortColumn[];
}): string {
    return JSON.stringify([
        params.planId ?? "working",
        params.asset ?? "",
        params.filters,
        params.sorting,
    ]);
}

/**
 * The column a plan-query validation error names, if any: the backend reports a
 * stale sorting/filter as «Неизвестный столбец «<db name>»» (or «… столбца
 * «<db name>»» for a bad range bound). Matching the RUSSIAN message is the only
 * signal there is — the 422 body carries no structured column field.
 */
function validationColumn(message: string): string | null {
    return message.match(/столб(?:ец|ца|цу|цом|це)\s+«([^»]+)»/i)?.[1] ?? null;
}

/**
 * A server plan row as the grid renders it: the cell values plus the service
 * fields the columns, the row header and the live patches read. It is applied
 * as a chunk loads, so the diff state (an archived plan compared to its
 * baseline) is stamped immediately instead of waiting for the diff request.
 *
 * Deleted rows live only in the diff — the grid's source is server-ordered and
 * index-addressed (grid index == server offset), so a ghost row cannot be
 * spliced in; the deletion is reported through the diff cell highlighting.
 */
function toGridRow(
    row: RenovationPlanRow,
    diffByRowId: Map<string, RenovationDiffRow>,
): RenovationGridRow {
    const diffRow = diffByRowId.get(row.id);
    return {
        ...row.values,
        // The read-only «№ ремонта» column shows the computed value.
        col_003: row.renovationId,
        // Service columns (corner menu «Показать служебные столбцы»).
        id: row.id,
        position: row.position,
        revision: row.revision,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        updatedBy: row.updatedBy,
        __id: row.id,
        __position: row.position,
        __assetId: row.assetId,
        __assetName: row.assetName ?? "",
        __renovationId: row.renovationId,
        __rowState: diffRow ? (diffRow.isNew ? "new" : "changed") : "unchanged",
        __changedFields: diffRow?.cells.map((cell) => cell.field) ?? [],
        __missingComponents: row.missingComponents,
        __invalidFields: row.invalidFields ?? [],
    };
}

/**
 * Hand the file URL to the browser as a normal download.
 *
 * The browser streams it to disk and shows its OWN progress UI — unlike the
 * File System Access / blob flows, which report the byte progress in a site
 * toast and (for a blob) buffer the whole file in memory.
 */
function triggerNativeDownload(url: string, fileName: string): void {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function formatVersionStamp(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}

const VERSION_STATE_LABELS: Record<string, string> = {
    working: "рабочая",
    pending_approval: "на согласовании",
    archived: "утверждённая",
};

/**
 * Canonical display names of the responsibility blocks — the same strings in
 * the plan's column-header overlay and in the «Все блоки» filter dropdown.
 * The values are the backend RBlock keys (tech, ksir, mto).
 */
const BLOCK_LABELS: Record<string, string> = {
    tech: "1) Техническая дирекция",
    ksir: "2) Дирекция КСиР",
    mto: "3) Дирекция МТО",
};
/** Preferred dropdown order (after «Все блоки»). */
const BLOCK_ORDER = ["tech", "ksir", "mto"];

export function RenovationWorkspace({
    currentUser,
    activeProjectKey,
    onProjectChange,
    initialSection = null,
    onSectionChange,
    supportMail,
}: RenovationWorkspaceProps) {
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const [reloadKey, setReloadKey] = useState(0);
    const [assetFilter, setAssetFilter] = useRestoredState<string | null>(null, () =>
        readWorkspaceQuery("asset"),
    );
    // The plan tab's panel filter (block), restored from the URL under the tab id.
    const [storedBlockFilter, setBlockFilter] = useRestoredState<string | null>(null, () => {
        const stored = readToolbarFilters("plan")["block"];
        return typeof stored === "string" ? stored : null;
    });
    /**
     * Active HEADER filters, keyed by column prop (field dbName or the synthetic
     * asset column). A column keeps display values + the «Пустые» bucket; all
     * columns combine with AND (server-side).
     *
     * Deliberately NOT in the URL. The address carries the PANEL filters — few,
     * and they describe which slice of the data is open — while a header filter
     * carries the values it keeps, and the plan's ID column alone can hold
     * hundreds of thousands. Putting that in `?filters=` grew the address past
     * what browsers and proxies accept and pushed a table's filters onto the
     * plan (a «Предприятия» filter once arrived there as a server error). The
     * They ARE remembered between visits, in `localStorage` under
     * `rc:filters:renovation:plan` — the same scheme every client table uses.
     * The state itself stays in memory while the workspace is mounted; the hook
     * helpers only read it once on mount and write on every change.
     */
    const [columnFilters, setColumnFilters] = useState<Record<string, ColumnFilter>>(() =>
        readStoredFilters("renovation", "plan"),
    );
    // Flat filter list for the rows API; a column with nothing kept selects
    // nothing, which the server treats as "no rows" (never "all").
    const activeColumnFilters = useMemo(
        () =>
            Object.entries(columnFilters).map(([column, filter]) => ({
                column,
                values: filter.values,
                includeNulls: filter.includeNulls,
                ...(filter.valueMode ? { valueMode: filter.valueMode } : {}),
                // A `timestamptz` column filters by a window, not by values.
                ...(filter.from != null || filter.to != null
                    ? { from: filter.from ?? null, to: filter.to ?? null }
                    : {}),
            })),
        [columnFilters],
    );
    // The column whose header-filter popover is open (its label + anchor rect).
    const [columnFilterTarget, setColumnFilterTarget] = useState<{
        column: string;
        label: string;
        order: "text" | "number" | "date";
        /** A `date` column holds a calendar day, so its filter asks for days. */
        dateOnly: boolean;
        /** A `timestamptz` column: the popover asks for a window, not values. */
        instant?: boolean;
        /** A numeric field with a fixed display precision. */
        decimals?: number;
        anchor: DOMRect;
    } | null>(null);
    // The row-number («№») range filter popover anchor (the corner's rect).
    const [rowFilterTarget, setRowFilterTarget] = useState<DOMRect | null>(null);
    /** Where the filter popovers and «Перейти к» remember their drafts. */
    const filterDraftScope = { project: "renovation", section: "plan" };
    const activeFilterColumns = useMemo(() => new Set(Object.keys(columnFilters)), [columnFilters]);
    const applyColumnFilter = (column: string) => (filter: ColumnFilter | null) =>
        setColumnFilters((current) => {
            const next = { ...current };
            // A filter with NO kept values and NO window constrains NOTHING — and
            // the server reads a column filter with nothing kept as «select no
            // rows». Storing one made the whole plan empty after a reload (the
            // funnel and the status looked clean, the rows were gone); it is
            // dropped, exactly like «Отменить».
            if (filter === null || !isConstrainingFilter(filter)) delete next[column];
            else next[column] = filter;
            writeStoredFilters("renovation", "plan", next);
            return next;
        });
    // Active section (plan vs dictionary) — declared early because the
    // plan-window reset effect keys off it. A section restored from the URL is
    // validated against the available sections by the guard effect below.
    const [activeSection, setActiveSection] = useState<string>(initialSection ?? "plan");
    // Panel filters of the OPEN tab live in the URL under its id. The plan tab's
    // panel is the block filter; dictionary tabs have no panel.
    const currentTabId = activeSection;
    const { setFilter: setPanelFilter } = useToolbarFilters(currentTabId);
    const isPlanSection = activeSection === "plan";
    const { data: meta, error } = useAsyncData<RenovationMeta | null>(
        () => getRenovationMeta().catch(() => null),
        [metaReloadKey],
    );
    const { data: versions, error: versionsError } = useAsyncData<RenovationPlanVersion[] | null>(
        () => getRenovationPlans().catch(() => null),
        [metaReloadKey],
    );
    const [columnSorting, setColumnSorting] = useState<StoredSortColumn[]>(() =>
        readStoredSorting("renovation", "plan"),
    );
    const sortablePlanColumns = useMemo(() => {
        const columns = new Set<string>([ASSET_COLUMN_PROP, ...Object.keys(AUDIT_COLUMNS)]);
        for (const field of meta?.fields ?? []) {
            if (field.dbName !== ASSET_FIELD_DB_NAME) columns.add(field.dbName);
        }
        return columns;
    }, [meta]);
    const activeSorting = useMemo<RenovationSortColumn[]>(
        () =>
            columnSorting
                .filter(({ prop }) => sortablePlanColumns.has(prop))
                .map(({ prop, order }) => ({ column: prop, order })),
        [columnSorting, sortablePlanColumns],
    );
    useEffect(() => {
        if (!meta) return;
        const valid = columnSorting.filter(({ prop }) => sortablePlanColumns.has(prop));
        if (valid.length === columnSorting.length) return;
        setColumnSorting(valid);
        writeStoredSorting("renovation", "plan", valid);
    }, [columnSorting, meta, sortablePlanColumns]);
    useEffect(() => {
        if (!meta) return;
        const valid = Object.fromEntries(
            Object.entries(columnFilters).filter(([column]) => sortablePlanColumns.has(column)),
        );
        if (Object.keys(valid).length === Object.keys(columnFilters).length) return;
        setColumnFilters(valid);
        writeStoredFilters("renovation", "plan", valid);
    }, [columnFilters, meta, sortablePlanColumns]);
    /**
     * Drop a column the server refused from the sorting AND the filters, in the
     * state and in `localStorage` — the stale entry would otherwise come back on
     * the next request (and on every reload). Returns whether it was there: an
     * error that removes nothing must not be swallowed, or the caller would sit
     * on a scope the server keeps rejecting.
     */
    const discardInvalidPlanColumn = useCallback(
        (column: string | null): boolean => {
            if (!column) return false;
            const hadSorting = columnSorting.some((entry) => entry.prop === column);
            const hadFilter = column in columnFilters;
            if (!hadSorting && !hadFilter) return false;
            if (hadSorting) {
                setColumnSorting((current) => {
                    const next = current.filter((entry) => entry.prop !== column);
                    writeStoredSorting("renovation", "plan", next);
                    return next;
                });
            }
            if (hadFilter) {
                setColumnFilters((current) => {
                    if (!(column in current)) return current;
                    const next = { ...current };
                    delete next[column];
                    writeStoredFilters("renovation", "plan", next);
                    return next;
                });
            }
            return true;
        },
        [columnFilters, columnSorting],
    );
    /**
     * A 422 on a plan query means a saved or sent column is no longer valid
     * (renamed, revoked). Drop it and let the caller retry the remaining scope;
     * `false` — nothing to drop, or not a 422 — keeps the error visible.
     */
    const discardInvalidPlanQuery = useCallback(
        (reason: unknown, fallbackColumn: string | null = null): boolean => {
            if (!(reason instanceof ReportCollectorApiError) || reason.status !== 422) return false;
            if (!discardInvalidPlanColumn(validationColumn(reason.message) ?? fallbackColumn)) {
                return false;
            }
            setColumnFilterTarget(null);
            return true;
        },
        [discardInvalidPlanColumn],
    );
    const toggleColumnSorting = useCallback((column: string, additive: boolean) => {
        setColumnSorting((current) => {
            const existing = current.find((item) => item.prop === column);
            if (existing?.order === "desc") {
                const next = current.filter((item) => item.prop !== column);
                writeStoredSorting("renovation", "plan", next);
                return next;
            }
            const nextEntry: StoredSortColumn = {
                prop: column,
                order: existing?.order === "asc" ? "desc" : "asc",
            };
            const next = additive
                ? [...current.filter((item) => item.prop !== column), nextEntry]
                : [nextEntry];
            writeStoredSorting("renovation", "plan", next);
            return next;
        });
    }, []);
    const [viewPlanId, setViewPlanId] = useState<string | null>(null);
    // After an approve, immediately show the resulting versions from the response
    // (the async meta/plans refetch may lag a moment); cleared once the server
    // list arrives so it never goes stale.
    const [approveVersions, setApproveVersions] = useState<RenovationPlanVersion[] | null>(null);
    useEffect(() => {
        setApproveVersions(null);
    }, [versions]);
    // ── Remote plan metadata (the grid loads its own chunks) ────────────────
    // The workspace no longer owns a sliding window of rows: the grid's
    // InfinityScrollPlugin does. What is still needed here is the scope the
    // rows belong to — the plan version, its filtered size (the plugin's
    // `total`), the baseline boundary and the summary row.
    const [planError, setPlanError] = useState<string | null>(null);
    // Numeric totals of the filtered plan — the pinned summary row.
    const [summaryValues, setSummaryValues] = useState<Record<string, number> | null>(null);
    // Filtered plan size (the plugin's `total`) and the whole-plan counter.
    const [planTotals, setPlanTotals] = useState<{ total: number; totalAll: number | null } | null>(
        null,
    );
    // Highest column number in the form («Предприятие» is 1). The strip numbers
    // are FIXED (the workbook ordinal shifted by one), unlike the sequential row
    // numbers, so this is the bound for the «go to column» field.
    const totalColumns = useMemo(() => {
        let max = 1;
        for (const field of meta?.fields ?? []) {
            max = Math.max(max, field.ordinal - 1);
        }
        return max;
    }, [meta]);
    const [baselineBoundary, setBaselineBoundary] = useState<number | null>(null);
    // Structural scroll: the working plan opens at its TAIL (rows are appended
    // at the end), the archive reads top-down. Expressed as the sequential
    // number in the filtered scope (1-based).
    const [scrollToPosition, setScrollToPosition] = useState<{
        position: number;
        token: number;
        centered?: boolean;
    } | null>(null);
    const scrollToPositionTokenRef = useRef(0);

    // The stored position («__position») of the row the user keeps in the
    // middle of the viewport — persisted per plan version, so a reload (or a
    // filter change) shows the same content; when the filters hide that row,
    // the server resolves the nearest remaining one (see the jump below).
    const centerPositionRef = useRef<number | null>(null);
    const centerStorageKey = useCallback(
        (planId: string | null) => `renovation:centerPosition:${planId ?? "working"}`,
        [],
    );
    useEffect(() => {
        if (typeof window === "undefined") return;
        const stored = window.localStorage.getItem(centerStorageKey(viewPlanId));
        const parsed = stored === null ? Number.NaN : Number(stored);
        centerPositionRef.current = Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    }, [centerStorageKey, viewPlanId]);
    const handleCenterPositionChange = useCallback(
        (position: number | null) => {
            if (position === null || position <= 0) return;
            centerPositionRef.current = position;
            window.localStorage.setItem(centerStorageKey(viewPlanId), String(position));
        },
        [centerStorageKey, viewPlanId],
    );

    // Structural reset: a new plan version or a new filter restarts the row
    // scope. The grid's Infinity Scroll reloads from the first chunk when the
    // scope key changes; here we only re-read the scope's metadata (its size,
    // the baseline boundary) and re-issue the structural jump.
    useEffect(() => {
        if (!meta) return;
        // The plan belongs to the plan section only; on a dictionary there is
        // nothing to read and the working-plan request would 404 («Нет плана в
        // состоянии working») into an error card over the dictionary.
        if (!isPlanSection) {
            setPlanError(null);
            setLoadedRows([]);
            setScrollToPosition(null);
            startTransition(() => {
                setPlanTotals(null);
            });
            return;
        }
        let cancelled = false;
        setPlanError(null);
        setLoadedRows([]);
        // The remembered plan position is a stored ordinal. Once rows are
        // ordered by arbitrary columns it no longer identifies a viewport
        // location, so sorted scopes intentionally reopen at their first page.
        const anchor = activeSorting.length === 0 ? centerPositionRef.current : null;
        void (async () => {
            try {
                // The head read doubles as the scope probe: it returns the
                // filtered total (the plugin's `total`) and the baseline
                // boundary without transferring any rows. With an anchor it
                // also resolves that row's index in the current scope.
                const head = await getRenovationPlanRows(
                    0,
                    1,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                    activeSorting,
                    null,
                    null,
                    anchor,
                );
                if (cancelled) return;
                setPlanTotals({ total: head.total, totalAll: head.totalAll ?? null });
                setBaselineBoundary(head.baselineBoundary ?? null);
                const nextToken = () => {
                    scrollToPositionTokenRef.current += 1;
                    return scrollToPositionTokenRef.current;
                };
                if (anchor !== null && head.anchorOffset !== null && head.total > 0) {
                    // Keep the same content in the middle: the anchor row, or —
                    // when the filters hid it — the nearest remaining one (the
                    // offset clamps to the first/last row).
                    setScrollToPosition({
                        position: Math.min(head.anchorOffset + 1, head.total),
                        token: nextToken(),
                        centered: true,
                    });
                } else if (!viewPlanId && head.total > 0 && activeSorting.length === 0) {
                    // No remembered row yet: the working plan anchors at its
                    // tail — the user lands on the rows they append most; an
                    // archive reads top-down. The jump must be cleared for the
                    // archive, otherwise the previous working-plan position
                    // leaks into it (the grid effect keys on totalRows, which
                    // changes between versions).
                    setScrollToPosition({ position: head.total, token: nextToken() });
                } else {
                    setScrollToPosition(null);
                }
            } catch (reason) {
                if (
                    !cancelled &&
                    reason instanceof ReportCollectorApiError &&
                    reason.status === 422
                ) {
                    if (discardInvalidPlanQuery(reason)) return;
                }
                if (!cancelled) setPlanError(errorMessage(reason));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [
        meta,
        viewPlanId,
        assetFilter,
        activeColumnFilters,
        activeSorting,
        discardInvalidPlanColumn,
        discardInvalidPlanQuery,
        isPlanSection,
    ]);

    // No working plan — right after submit, or a cold load with only
    // pending/archived versions. Only on the plan section: auto-select the
    // pending plan (the most relevant) and let the effect above re-run its
    // archive branch. Guarded by isPlanSection so leaving into a dictionary
    // (which resets viewPlanId to null) doesn't re-select the pending plan and
    // fight the reset — that toggle (null ↔ pending) would loop past the
    // update-depth guard.
    useEffect(() => {
        if (!meta || !isPlanSection || viewPlanId) return;
        if (meta.workingPlanId != null) return;
        const nextPlanId = meta.pendingPlanId ?? null;
        if (nextPlanId) setViewPlanId(nextPlanId);
    }, [meta, isPlanSection, viewPlanId]);

    // Pinned summary row: an aggregating query over the same scope as the remote
    // rows (asset + header filters), refreshed with them.
    // Note: getRenovationPlanSummary only needs planId/asset/filters, not meta.
    // Removing meta from deps lets the summary compute in parallel with data loading,
    // so rows appear immediately and the summary updates when ready.
    useEffect(() => {
        if (!isPlanSection) {
            setSummaryValues(null);
            return;
        }
        let cancelled = false;
        void getRenovationPlanSummary({
            planId: viewPlanId ?? undefined,
            asset: assetFilter,
            filters: activeColumnFilters,
        })
            .then((values) => {
                if (!cancelled) setSummaryValues(values);
            })
            .catch((reason) => {
                if (cancelled) return;
                if (discardInvalidPlanQuery(reason)) return;
                setSummaryValues(null);
            });
        return () => {
            cancelled = true;
        };
    }, [viewPlanId, assetFilter, activeColumnFilters, discardInvalidPlanQuery, isPlanSection]);

    // ── Row-scope loading (InfinityScrollPlugin's loadData) ────────────────
    // One chunk of the filtered plan in «№» order. The offset/limit are rows
    // of the FILTERED scope, which is exactly what the server pages by, so a
    // row's grid index equals its offset — that is what lets the grid jump to
    // a «№» and patch rows by index. Every request carries the scope it was
    // made for; a stale one (the user switched version/filter mid-flight) is
    // dropped instead of leaking rows into the new scope.
    const scopeKey = useMemo(
        () =>
            renovationGridScopeKey({
                planId: viewPlanId,
                asset: assetFilter,
                filters: activeColumnFilters,
                sorting: activeSorting,
            }),
        [activeColumnFilters, activeSorting, assetFilter, viewPlanId],
    );
    const scopeKeyRef = useRef(scopeKey);
    scopeKeyRef.current = scopeKey;
    // The diff of the version being viewed, by row id. Assigned below with the
    // diff itself and read by `loadRows`, so a chunk that arrives before (or
    // after) the diff still carries the right `__rowState`/`__changedFields`.
    const diffByRowIdRef = useRef<Map<string, RenovationDiffRow>>(new Map());

    const loadRows = useCallback(
        async (
            offset: number,
            limit: number,
        ): Promise<{ rows: RenovationGridRow[]; total: number }> => {
            const requestedScope = scopeKeyRef.current;
            let page;
            try {
                page = await getRenovationPlanRows(
                    offset,
                    limit,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                    activeSorting,
                );
            } catch (reason) {
                if (discardInvalidPlanQuery(reason)) return { rows: [], total: 0 };
                throw reason;
            }
            if (scopeKeyRef.current !== requestedScope) return { rows: [], total: 0 };
            const diffByRowId = diffByRowIdRef.current;
            return {
                rows: page.rows.map((row) => toGridRow(row, diffByRowId)),
                total: page.total,
            };
        },
        [
            activeColumnFilters,
            activeSorting,
            assetFilter,
            discardInvalidPlanColumn,
            discardInvalidPlanQuery,
            viewPlanId,
        ],
    );

    const { data: diff } = useAsyncData<RenovationDiff | null>(
        () => (viewPlanId ? getRenovationDiff(viewPlanId) : Promise.resolve(null)),
        [metaReloadKey, reloadKey, viewPlanId],
    );
    const diffByRowId = useMemo(
        () => new Map((diff?.rows ?? []).map((row) => [row.rowId, row] as const)),
        [diff],
    );
    diffByRowIdRef.current = diffByRowId;

    const [user, setUser] = useState(currentUser);
    const {
        isFullscreen,
        isFullscreenPending,
        setFullscreenMode,
        setToolbarControlOpen,
        revealClass,
    } = useFullscreenPanels();
    const [gridStats, setGridStats] = useState<GridViewStats | null>(null);
    // The dictionaries report their own stats: the plan's chunk-loaded numbers
    // must not stick in the footer after switching tabs.
    const [dictGridStats, setDictGridStats] = useState<GridViewStats | null>(null);
    const [selection, setSelection] = useState<{
        label: string | null;
        detail: string | null;
        cellKeys: string[];
        rowId: string | null;
    } | null>(null);
    const [clipboardState, setClipboardState] = useState<ClipboardActions>({
        canPaste: false,
        onCopy: () => {},
        onPaste: () => {},
    });
    const [undoState, setUndoState] = useState<UndoState>({
        enabled: false,
        offline: false,
        canUndo: false,
        canRedo: false,
        onUndo: () => {},
        onRedo: () => {},
    });
    const [profileOpen, setProfileOpen] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [submitOpen, setSubmitOpen] = useState(false);
    const [approveOpen, setApproveOpen] = useState(false);
    // The server refused the submit because the plan's work IDs are not all
    // correct and unique — the message is shown in an AlertDialog.
    const [submitBlocked, setSubmitBlocked] = useState<string | null>(null);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const [exportPending, setExportPending] = useState(false);
    const [planExcelImportOpen, setPlanExcelImportOpen] = useState(false);
    const [planExcelImportResult, setPlanExcelImportResult] = useState<
        import("./plan-excel-import-modal").PlanExcelImportResult | null
    >(null);
    // Closing the dialog discards the previous file and its check result, so
    // the next open starts from an empty field.
    const closePlanExcelImport = (open: boolean) => {
        setPlanExcelImportOpen(open);
        if (!open) setPlanExcelImportResult(null);
    };
    const [undoCount, setUndoCount] = useState(0);
    const [redoCount, setRedoCount] = useState(0);
    // The «Добавить строку» dialog (asset picker → an empty row at the end).
    const [addOpen, setAddOpen] = useState(false);
    const [addAssetKey, setAddAssetKey] = useState<string | null>(null);
    // Dictionary tab actions are delegated by the active dictionary section.
    const [dictDeleteState, setDictDeleteState] = useState<{
        canDelete: boolean;
        blockedReason: string | null;
        onDelete: () => void;
    }>({ canDelete: false, blockedReason: null, onDelete: () => {} });
    const [dictReplaceState, setDictReplaceState] = useState<{
        canReplace: boolean;
        blockedReason: string | null;
        onReplace: () => void;
    }>({ canReplace: false, blockedReason: null, onReplace: () => {} });
    const [dictExportState, setDictExportState] = useState<{
        pending: boolean;
        onExport: (scope: "all" | "visible") => void;
    }>({ pending: false, onExport: () => {} });
    const [dictResetState, setDictResetState] = useState<{
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }>({ resetColumnWidths: () => {}, resetFiltersSorting: () => {} });
    // The users dictionary creates records through a modal (role-dependent
    // fields) — the section delegates the action up like delete/export.
    const [dictUserCreateState, setDictUserCreateState] = useState<{
        canCreate: boolean;
        onCreate: () => void;
    }>({ canCreate: false, onCreate: () => {} });
    // Display toggles of the project's grids (rendered in the view strip): one
    // pair for the plan and the dictionaries, remembered between sessions —
    // the same mechanism as every other project (`useViewState`).
    const {
        headerPinned,
        showServiceColumns,
        rowHeight,
        setHeaderPinned,
        setShowServiceColumns,
        setRowHeight,
        resetRowHeight,
    } = useViewState(activeProjectKey);
    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const gridRef = useRef<RenovationGridHandle>(null);
    // The dictionaries report their completed selection here; the plan keeps
    // its own richer `selection` (column numbers + header chain).
    const [dictSelectionLabel, setDictSelectionLabel] = useState<string | null>(null);
    // When another tab of the same account takes over the selection, the grid
    // is cleared programmatically; the resulting empty presence update must be
    // swallowed (the server entry belongs to the other tab).
    const suppressSelectionSendRef = useRef(false);
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, unknown>>(new Map());
    const { setTheme } = useTheme();

    // Apply the saved user theme once on mount (see the raw-materials
    // workspace for why the effect must not depend on setTheme).
    useEffect(() => {
        setTheme(user.theme);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const activeProject = user.access?.find((project) => project.key === activeProjectKey);
    const roles = activeProject?.roles ?? [];
    // The renovation roles carried in meta are the source of truth for plan
    // edit rights — the auth ProjectRole only knows admin/asset_worker, so the
    // renovation approver/block_owner roles arrive here. Fall back to the auth
    // roles for robustness.
    const renovationRoles = meta?.userRoles ?? [];
    const isAdmin =
        isProjectAdmin(activeProject) ||
        roles.includes("admin") ||
        renovationRoles.includes("admin");
    const isApprover = renovationRoles.includes("approver") || roles.includes("approver");
    const isBlockOwner = renovationRoles.includes("block_owner") || roles.includes("block_owner");
    const isAssetWorker =
        renovationRoles.includes("asset_worker") || roles.includes("asset_worker");
    // The admin role is stripped of both the submit and the approve actions —
    // they belong to the block_owner / approver roles, so an admin sees neither
    // button (they manage access, not the plan workflow).
    const canSubmit = isBlockOwner;
    const canApprove = isApprover;

    // The backend derives the responsibility block from the user-role link
    // and rejects edits outside it. The meta now carries userBlock so the
    // frontend can restrict editing to the user's block on the client side.
    const userBlock = meta?.userBlock ?? null;
    const editableFieldKeys = useMemo(() => {
        const keys = new Set<string>();
        if (!meta) return keys;
        if (isApprover) return keys;
        for (const field of meta.fields) {
            if (field.isCalculated || field.isReadOnly) continue;
            // Block owners can only edit fields in their block (+ central
            // fields with no block assignment).
            if (userBlock && field.responsibilityBlock && field.responsibilityBlock !== userBlock) {
                continue;
            }
            keys.add(field.dbName);
        }
        return keys;
    }, [isApprover, meta, userBlock]);
    // Calculated fields (formula results + the renovation ID) are never edited
    // directly, but they DO update live from other users' edits — the backend
    // publishes them as cell:changed, and displayRows merges them on top.
    const calculatedFieldKeys = useMemo(() => {
        const keys = new Set<string>();
        if (!meta) return keys;
        for (const field of meta.fields) {
            if (field.isCalculated) keys.add(field.dbName);
        }
        return keys;
    }, [meta]);
    // The union of editable + calculated fields: the fields displayRows merges
    // from remote cell values.
    const mergeableFieldKeys = useMemo(() => {
        const keys = new Set(editableFieldKeys);
        for (const key of calculatedFieldKeys) keys.add(key);
        return keys;
    }, [editableFieldKeys, calculatedFieldKeys]);

    const userAssetIds = useMemo(() => {
        if (!meta) return null;
        if (isAdmin || isApprover) return null;
        const names = new Set((activeProject?.assets ?? []).map((asset) => asset.name));
        const ids = meta.assets.filter((asset) => names.has(asset.name)).map((asset) => asset.id);
        return ids.length > 0 ? new Set(ids) : null;
    }, [activeProject?.assets, isAdmin, isApprover, meta]);

    const blocks = useMemo(() => {
        // The read_only block groups the hidden/always-visible columns
        // (the synthetic asset column, the renovation id, the note) — filtering
        // by it makes no sense, so it's not offered in the dropdown. The
        // dropdown follows the canonical 1/2/3 block order.
        const present = new Set<string>();
        for (const field of meta?.fields ?? []) {
            if (field.responsibilityBlock && field.responsibilityBlock !== "read_only") {
                present.add(field.responsibilityBlock);
            }
        }
        return BLOCK_ORDER.filter((block) => present.has(block));
    }, [meta]);
    // A block that no longer exists in this plan (a renamed/dropped directorate)
    // must not survive a reload: it would filter every row away. The stored
    // value is validated only once the block list is KNOWN — `blocks` is empty
    // while `meta` loads, and validating against that empty list deleted a
    // perfectly valid «МТО» from the URL on every reload.
    const blocksKnown = meta !== null;
    const blockVerdict = restoredFilterVerdict(storedBlockFilter, blocksKnown ? blocks : null);
    const blockFilter = blockVerdict === "keep" ? storedBlockFilter : null;
    useEffect(() => {
        if (blockVerdict !== "clear") return;
        setBlockFilter(null);
        setPanelFilter("plan", "block", null);
    }, [blockVerdict, setPanelFilter, setBlockFilter]);

    // Section tabs: the plan grid first, then the reference dictionaries
    // (enterprises, the five catalogs and project users), mirroring the
    // «Сырьё» workspace. The dictionaries are admin-only.
    const sections = useMemo(() => {
        if (!meta) return [];
        if (!isAdmin) return [{ id: "plan", label: "Пообъектный план" }];
        return [
            { id: "plan", label: "Пообъектный план" },
            { id: "assets", label: "Предприятия" },
            ...meta.catalogs.map((catalog) => ({ id: catalog.id, label: catalog.label })),
            { id: "users", label: "Пользователи" },
        ];
    }, [isAdmin, meta]);

    useEffect(() => {
        if (sections.length > 0 && !sections.some((section) => section.id === activeSection)) {
            setActiveSection("plan");
            onSectionChange?.("plan");
        }
    }, [activeSection, sections, onSectionChange]);

    // Browser back/forward changes `?section=` without remounting this
    // component (the `key` only tracks the project), so follow the prop.
    useEffect(() => {
        if (initialSection && sections.some((section) => section.id === initialSection)) {
            setActiveSection(initialSection);
        }
    }, [initialSection, sections]);

    // The same strip as «Сырьё»: the plan first, then the dictionaries after
    // a separator (assets, catalogs, users).
    const sectionTabItems = useMemo(
        () =>
            sections.map((section, index) => ({
                id: section.id,
                label: section.label,
                separatorBefore: index === 1,
            })),
        [sections],
    );

    // The collab room joins the active section: the plan (renovationPlan) or
    // the dictionary tab (renovationAssets / renovationCatalog:{id} /
    // renovationUsers). Undo/redo and the remote cell values follow it.
    const dictSection = isPlanSection
        ? null
        : activeSection === "assets"
          ? RENOVATION_ASSETS_SECTION
          : activeSection === "users"
            ? RENOVATION_USERS_SECTION
            : renovationCatalogSection(activeSection);
    const collabSection = dictSection ?? RENOVATION_SECTION;

    const visibleAssetNames = useMemo(
        () => (meta?.assets ?? []).map((asset) => asset.name),
        [meta],
    );

    const assetFilterSet = useMemo(
        () => (assetFilter ? new Set([assetFilter]) : null),
        [assetFilter],
    );

    // A selected version is read-only («archive») only when it is the approved
    // one. The pending («на согласовании») version is still edited by the block
    // owners/admins, so it keeps the collab room, editing and undo/redo.
    const isArchivedView =
        viewPlanId !== null &&
        (versions?.find((version) => version.id === viewPlanId)?.state ?? "archived") ===
            "archived";
    const isArchive = isArchivedView;
    const collabEnabled = !isArchivedView && collaborationAvailableFor(collabSection, isAdmin);

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);
    const refreshMeta = useCallback(() => {
        setMetaReloadKey((key) => key + 1);
        setViewPlanId(null);
    }, []);
    // The Excel load runs detached from its dialog: the footer bar owns it
    // while the project is open and re-attaches after a project round-trip
    // (the job id is kept per project in sessionStorage).
    const planImport = usePlanImportJob({
        projectKey: activeProjectKey,
        onApplied: refresh,
    });

    const handleCellChanged = useCallback((event: CollabCellChanged) => {
        const key = canonicalCellKey(
            renovationSectionTable(event.section),
            event.rowId,
            event.field,
        );
        setRemoteCellValues((current) => {
            const next = new Map(current);
            next.set(key, event.value);
            return next;
        });
    }, []);

    const room = useCollabRoom({
        projectKey: activeProjectKey,
        section: collabSection,
        reportMonth: null,
        enabled: collabEnabled,
        // Only the plan has an asset scope: its rows belong to assets, so a
        // member is shown to colleagues whose assets overlap. The dictionaries
        // have no asset dimension — there everyone sees everyone, like the
        // «Сырьё» dictionaries and «КППК».
        visibleAssets: isPlanSection ? visibleAssetNames : null,
        onCellChanged: handleCellChanged,
        onRowsChanged: refresh,
        onReconnect: refresh,
        onSelectionPreempted: () => {
            suppressSelectionSendRef.current = true;
            gridRef.current?.clearSelection();
            setSelection(null);
        },
    });

    // Leased / selected cells and the combined highlight marker, stabilized by
    // content so a presence heartbeat with unchanged state does not rebuild the
    // 216-column grid (see the hook). Shared with the «Сырьё» workspace.
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);

    const readOnly = isMutationPending || isArchive || (collabEnabled && room.status === "offline");

    useEffect(() => {
        setRemoteCellValues(new Map());
    }, [activeSection, viewPlanId]);

    // The version archive applies to the plan only: leaving the plan tab
    // (e.g. into a dictionary) drops the archive view so the dictionaries
    // stay live and collab-enabled.
    useEffect(() => {
        if (!isPlanSection && viewPlanId !== null) {
            setViewPlanId(null);
            setSelection(null);
        }
    }, [isPlanSection, viewPlanId]);

    // Clipboard actions are bound to the active grid; reset on tab switch so
    // a stale handler from the previous section is never invoked.
    useEffect(() => {
        setClipboardState({ canPaste: false, onCopy: () => {}, onPaste: () => {} });
    }, [activeSection]);

    // ── Row mirror + live patches ───────────────────────────────────────────
    // The grid owns the loaded window (the plugin fills and evicts chunks);
    // the workspace mirrors the SAME row objects so selection, delete checks
    // and remote-cell overlays see them. Updates are applied by mutating a row
    // object and pushing its index to the grid — never by replacing the source
    // (which is what used to make the viewport flicker on every chunk).
    const loadedRowsRef = useRef(new Map<string, RenovationGridRow>());
    const [loadedRows, setLoadedRows] = useState<RenovationGridRow[]>([]);
    const [rowPatches, setRowPatches] = useState<RenovationRowPatch[]>([]);

    /** Mirror of the rows the plugin holds; the same objects the grid renders. */
    const handleLoadedRowsChange = useCallback((rows: RenovationGridRow[]) => {
        const byId = new Map<string, RenovationGridRow>();
        for (const row of rows) byId.set(row.__id, row);
        loadedRowsRef.current = byId;
        setLoadedRows(rows);
    }, []);

    /**
     * Push fresh values into the rows the grid holds — the row object is
     * mutated in place (the grid renders that very reference) and handed back
     * as an indexed patch, so no source reset and no viewport flicker.
     */
    const patchLoadedRows = useCallback(
        (changes: Array<{ rowId: string; fields: Record<string, unknown> }>) => {
            const patches: RenovationRowPatch[] = [];
            for (const change of changes) {
                const row = loadedRowsRef.current.get(change.rowId);
                if (!row || row.__index === undefined) continue;
                Object.assign(row, change.fields);
                patches.push({ index: row.__index, fields: change.fields });
            }
            if (patches.length > 0) setRowPatches(patches);
        },
        [],
    );

    /** Fresh server rows (refresh()): patch the loaded window in place. */
    const applyServerRows = useCallback(
        (rows: RenovationPlanRow[]) => {
            patchLoadedRows(
                rows.map((row) => ({
                    rowId: row.id,
                    fields: {
                        ...row.values,
                        col_003: row.renovationId,
                        revision: row.revision,
                        updatedAt: row.updatedAt,
                        updatedBy: row.updatedBy,
                        __renovationId: row.renovationId,
                        __missingComponents: row.missingComponents,
                        // The rule violations are recomputed server-side on
                        // every read: a recalculated cell (e.g. a negative
                        // «Продолжительность, дни» after the dates were swapped)
                        // must tint red immediately, not only after a reload.
                        __invalidFields: row.invalidFields ?? [],
                    },
                })),
            );
        },
        [patchLoadedRows],
    );

    // Remote cell values (live edits of other users) override the local ones.
    // Only the rows the grid currently holds can be patched; the rest pick the
    // value up when their chunk loads.
    useEffect(() => {
        if (isArchive || remoteCellValues.size === 0) return;
        const changes: Array<{ rowId: string; fields: Record<string, unknown> }> = [];
        for (const row of loadedRowsRef.current.values()) {
            const fields: Record<string, unknown> = {};
            for (const field of mergeableFieldKeys) {
                const value = remoteCellValues.get(canonicalRenovationKey(row.__id, field));
                if (value === undefined) continue;
                fields[field] = value;
                if (field === "col_003") {
                    // The renovation ID drives the red «incomplete» tint: derive
                    // the missing-components flag from the value itself (<NULL>
                    // placeholder) — the cell:changed payload carries no metadata.
                    fields.__renovationId = value == null ? null : String(value);
                    fields.__missingComponents = String(value).includes("<NULL>")
                        ? ["missing"]
                        : [];
                }
            }
            if (Object.keys(fields).length > 0) changes.push({ rowId: row.__id, fields });
        }
        patchLoadedRows(changes);
    }, [isArchive, mergeableFieldKeys, patchLoadedRows, remoteCellValues]);

    // The diff marks rows as new/changed; it arrives after the rows themselves,
    // so the states are patched into the loaded window instead of being baked
    // into the source (which would evict and re-fetch it).
    useEffect(() => {
        if (!diff) return;
        patchLoadedRows(
            diff.rows.map((row) => ({
                rowId: row.rowId,
                fields: {
                    __rowState: row.isNew ? "new" : "changed",
                    __changedFields: row.cells.map((cell) => cell.field),
                },
            })),
        );
    }, [diff, patchLoadedRows]);

    // Undo/redo is per session, form and collab section: the counters are
    // re-read from the server on every version or tab switch.
    useEffect(() => {
        if (!collabEnabled) return;
        void refreshUndoCounters(() => getUndoState(collabSection), setUndoCount, setRedoCount);
    }, [collabEnabled, collabSection, reloadKey, viewPlanId]);

    const applyUndoResult = useCallback(
        (result: RenovationUndoResult, direction: "undo" | "redo") => {
            applyHistoryResult({
                result,
                direction,
                setPendingSkip,
                refresh,
                refreshState: () =>
                    void refreshUndoCounters(
                        () => getUndoState(collabSection),
                        setUndoCount,
                        setRedoCount,
                    ),
            });
        },
        [collabSection, refresh],
    );

    const runUndo = useCallback(async () => {
        await runHistoryAction({
            direction: "undo",
            pendingWrite: pendingCommitRef.current,
            pendingSkip,
            request: (skip) => undoCells(skip, collabSection),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, collabSection, pendingSkip]);

    const runRedo = useCallback(async () => {
        await runHistoryAction({
            direction: "redo",
            pendingWrite: pendingCommitRef.current,
            pendingSkip,
            request: (skip) => redoCells(skip, collabSection),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, collabSection, pendingSkip]);

    // An approver cannot edit the plan — no undo/redo shortcuts either.
    useUndoRedoShortcuts({
        enabled: collabEnabled && !isApprover && undoAvailableFor(collabSection, isAdmin),
        onUndo: () => void runUndo(),
        onRedo: () => void runRedo(),
    });

    useEffect(() => {
        setUndoState({
            enabled:
                collabEnabled &&
                !isArchive &&
                !isApprover &&
                undoAvailableFor(collabSection, isAdmin),
            offline: collabEnabled && room.status !== "online",
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [
        collabEnabled,
        collabSection,
        isAdmin,
        isApprover,
        isArchive,
        redoCount,
        room.status,
        runRedo,
        runUndo,
        undoCount,
    ]);

    // Stable identity: `commit` feeds the grid's `columns` memo, which the
    // selection effect depends on — an inline function would re-trigger the
    // effect on every render (setSelection → re-render → new commit → columns
    // → effect again) and overflow the React update-depth guard.
    const doCommit = useCallback(
        async (updates: CellUpdate[]): Promise<boolean> => {
            setIsMutationPending(true);
            try {
                await updateRenovationCells(updates);
                setUndoCount((count) => count + 1);
                setRedoCount(0);
                refresh();
                return true;
            } catch (reason) {
                toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
                return false;
            } finally {
                setIsMutationPending(false);
            }
        },
        [refresh],
    );

    const commit = useCallback(
        async (updates: CellUpdate[]): Promise<boolean> => {
            if (readOnly || !updates.length) return true;
            // Serialize commits so consecutive edits apply in order and a stale
            // refresh() from an earlier PATCH can't clobber a later one.
            await pendingCommitRef.current;
            const task = doCommit(updates);
            pendingCommitRef.current = task;
            try {
                return await task;
            } finally {
                if (pendingCommitRef.current === task) pendingCommitRef.current = null;
            }
        },
        [doCommit, readOnly],
    );

    const exportPlan = useCallback(
        async (scope: "all" | "visible") => {
            if (exportPending) return;
            setExportPending(true);
            // «Видимая» — the whole plan filtered server-side by the active
            // asset, block and header filters, not just the window currently
            // rendered.
            const visible = scope === "visible";
            try {
                // Start the background export (fast) so the file begins generating.
                const job = await createRenovationExportJob({
                    planId: viewPlanId ?? undefined,
                    asset: visible ? assetFilter : null,
                    block: visible ? blockFilter : null,
                    filters: visible ? activeColumnFilters : null,
                    sorting: visible ? activeSorting : null,
                });
                if (job.status === "failed") {
                    throw new Error(job.error ?? "Не удалось сформировать файл.");
                }
                // Hand the URL to the browser as a native download IMMEDIATELY.
                // The file endpoint streams the growing workbook, so the browser's
                // own save dialog and download UI open at once and advance with the
                // generation. Closing the tab or reloading neither cancels the
                // generation (it runs in the background worker) nor the
                // browser-owned download.
                triggerNativeDownload(renovationExportJobFileUrl(job.id), job.fileName);
            } catch (reason) {
                if (discardInvalidPlanQuery(reason)) {
                    // The stale column is gone, but THIS click produced no file:
                    // say so, otherwise the export looks like a dead button.
                    toast.warning("Сортировка или фильтр столбца удалены", {
                        description: "Столбец больше не поддерживается — повторите выгрузку.",
                    });
                    return;
                }
                toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
            } finally {
                setExportPending(false);
            }
        },
        [
            activeColumnFilters,
            activeSorting,
            assetFilter,
            blockFilter,
            discardInvalidPlanQuery,
            exportPending,
            viewPlanId,
        ],
    );

    const handleStripExport = useCallback(
        (scope: "all" | "visible") => {
            if (isPlanSection) void exportPlan(scope);
            else dictExportState.onExport(scope);
        },
        [dictExportState, exportPlan, isPlanSection],
    );

    // The plan grid resets its own widths and the workspace asset/block
    // filters; dictionary sections register their grid's reset handlers.
    const handleStripReset = useCallback(
        (scope: "columnWidths" | "filtersSorting") => {
            if (isPlanSection) {
                if (scope === "columnWidths") gridRef.current?.resetColumnWidths();
                else {
                    setAssetFilter(null);
                    setBlockFilter(null);
                    // Header filters live in the query, so clear them too —
                    // otherwise the reset looks like it did nothing. Their DRAFTS
                    // (search strings, MIN–MAX pairs, instant windows) are
                    // remembered per column, so they go as well: keeping them left
                    // a reset filter behind that came back on the next open.
                    clearFilterDrafts("renovation", "plan");
                    writeStoredFilters("renovation", "plan", {});
                    setColumnFilters({});
                    setColumnSorting([]);
                    writeStoredSorting("renovation", "plan", []);
                }
                return;
            }
            if (scope === "columnWidths") dictResetState.resetColumnWidths();
            else void dictResetState.resetFiltersSorting();
        },
        [dictResetState, isPlanSection, setAssetFilter, setBlockFilter, setColumnFilters],
    );

    // In-place reload (refresh()): re-read the rows the grid currently holds so
    // recalced cells (the renovation ID, repair counts, derived values) appear
    // in place. A full scope restart would throw the user back to the first
    // chunk, so the reload patches the loaded window instead.
    useEffect(() => {
        if (reloadKey === 0 || isArchive) return;
        let cancelled = false;
        void (async () => {
            try {
                const rows = await gridRef.current?.getLoadedRows();
                if (cancelled || !rows || rows.length === 0) return;
                const first = rows[0];
                const last = rows[rows.length - 1];
                if (!first || !last) return;
                // One contiguous read of the loaded span (the plugin evicts
                // outside its buffer, so the span stays bounded). The offsets
                // are the grid indexes (`__index` — the filtered offset), not
                // the stored plan positions: after filtering they differ.
                const start = first.__index ?? first.__position - 1;
                const end = last.__index ?? last.__position - 1;
                const page = await getRenovationPlanRows(
                    start,
                    end - start + 1,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                    activeSorting,
                );
                if (cancelled) return;
                setBaselineBoundary(page.baselineBoundary ?? null);
                setPlanTotals({ total: page.total, totalAll: page.totalAll ?? null });
                applyServerRows(page.rows);
            } catch (reason) {
                discardInvalidPlanQuery(reason);
                // Keep the current rows; the next refresh retries.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [
        reloadKey,
        assetFilter,
        viewPlanId,
        activeColumnFilters,
        activeSorting,
        discardInvalidPlanQuery,
        isArchive,
        applyServerRows,
    ]);

    const selectedRow = useMemo(
        () => loadedRows.find((row) => row.__id === selection?.rowId) ?? null,
        [loadedRows, selection?.rowId],
    );
    // A row may be deleted by anyone except the approver, and only while it
    // holds no data — just the mandatory asset plus the technical columns.
    // (The asset lives in __assetId; col_003 is the computed renovation ID.)
    const rowIsEmpty = useMemo(() => {
        if (!selectedRow) return true;
        const row = selectedRow as unknown as Record<string, unknown>;
        return [...editableFieldKeys].every((field) => {
            const value = row[field];
            return value === undefined || value === null || String(value).trim() === "";
        });
    }, [editableFieldKeys, selectedRow]);
    const canDeleteRow =
        !readOnly &&
        !isArchive &&
        !isApprover &&
        selection?.rowId != null &&
        selectedRow?.__rowState !== "deleted" &&
        rowIsEmpty;

    // Ctrl+Delete — the shortcut advertised on the action-bar delete button:
    // opens the same confirmation dialog as the button press.
    // Ctrl+Delete opens the same dialog as the action-bar button.
    const openDeleteDialog = useCallback(() => setDeleteOpen(true), []);
    useDeleteShortcut(canDeleteRow, openDeleteDialog);

    async function deleteSelectedRow() {
        const rowId = selection?.rowId;
        if (!rowId || isMutationPending) return;
        setIsMutationPending(true);
        try {
            await deleteRenovationRow(rowId);
            setDeleteOpen(false);
            setSelection(null);
            toast.success("Строка удалена");
            refresh();
        } catch (reason) {
            toast.danger("Не удалось удалить строку", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    async function addRow(assetId: string) {
        if (isMutationPending) return;
        setIsMutationPending(true);
        try {
            await addRenovationRow(assetId);
            setAddOpen(false);
            setAddAssetKey(null);
            toast.success("Строка добавлена");
            refresh();
        } catch (reason) {
            toast.danger("Не удалось добавить строку", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    const runSubmit = useCallback(async () => {
        if (isMutationPending) return;
        setIsMutationPending(true);
        try {
            const submitted = await submitRenovationPlan();
            setSubmitOpen(false);
            setUndoCount(0);
            setRedoCount(0);
            toast.success("План отправлен на согласование");
            // The working plan is gone; show the pending one read-only instead
            // of a 404 «Не удалось загрузить план».
            setViewPlanId(submitted.id);
            setMetaReloadKey((key) => key + 1);
        } catch (reason) {
            if (reason instanceof ReportCollectorApiError && reason.status === 409) {
                // The server refuses to freeze a plan whose work IDs are not all
                // correct and unique; surface the reason in a dialog.
                setSubmitOpen(false);
                setSubmitBlocked(reason.message);
            } else {
                toast.danger("Не удалось отправить план", { description: errorMessage(reason) });
            }
        } finally {
            setIsMutationPending(false);
        }
    }, [isMutationPending]);

    const runApprove = useCallback(async () => {
        if (isMutationPending) return;
        setIsMutationPending(true);
        try {
            const result = await approveRenovationPlan();
            setApproveOpen(false);
            setUndoCount(0);
            setRedoCount(0);
            toast.success("План согласован и заархивирован");
            // Show the archived + new working version in the picker right away.
            setApproveVersions([result.archived, result.working]);
            refreshMeta();
        } catch (reason) {
            toast.danger("Не удалось согласовать план", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }, [isMutationPending, refreshMeta]);

    const canSubmitPlan =
        canSubmit &&
        !isArchive &&
        !readOnly &&
        meta?.workingPlanId != null &&
        (planTotals?.total ?? 0) > 0;
    const canApprovePlan = canApprove && !isArchive && !readOnly && meta?.pendingPlanId != null;

    // Assets the user may add rows for: everyone sees the full dictionary
    // except asset workers, who are restricted to their own assets (the
    // server enforces the same rule).
    const addRowAssets = useMemo(() => {
        const all = meta?.assets ?? [];
        if (userAssetIds === null) return all.map((asset) => ({ id: asset.id, name: asset.name }));
        return all
            .filter((asset) => userAssetIds.has(asset.id))
            .map((asset) => ({ id: asset.id, name: asset.name }));
    }, [meta, userAssetIds]);

    // Version footer: the working version plus every non-working version
    // (pending approval and archived) viewed read-only with a diff.
    const archiveItems = useMemo<ArchiveItem[]>(() => {
        const effective = approveVersions ?? versions;
        if (!effective || effective.length === 0) return [];
        return effective
            .filter((version) => version.state !== "working")
            .map((version) => ({
                id: version.id,
                // Main label is the state only ("Версия на согласовании" etc.);
                // the description carries the date+time, like the «Сырьё» picker.
                // The archived state reads as a noun phrase («Утверждённая версия»).
                name:
                    version.state === "archived"
                        ? "Утверждённая версия"
                        : `Версия ${VERSION_STATE_LABELS[version.state]}`,
                description: formatVersionStamp(version.createdAt),
            }));
    }, [approveVersions, versions]);

    const archiveState = useMemo<ArchiveState>(
        () => ({
            items: archiveItems,
            selectedKey: isArchive ? viewPlanId : null,
            onSelect: (planId) => {
                if (planId === null) {
                    setViewPlanId(null);
                    setSelection(null);
                    refresh();
                } else {
                    setViewPlanId(planId);
                    setSelection(null);
                }
            },
        }),
        [archiveItems, isArchive, refresh, viewPlanId],
    );

    // The dictionary grid binds the copy/paste actions to its own handle.
    const handleDictClipboardState = useCallback(
        (state: { canPaste: boolean; onCopy: () => void; onPaste: () => void }) => {
            updateClipboardActions(setClipboardState, state);
        },
        [],
    );
    const handleClipboardState = useGridClipboardActions(gridRef, handleDictClipboardState);

    // Stable identity: the grid's selection effect runs on every prop change
    // and calls onClipboardStateChange — an inline handler would re-trigger it
    // on every render (setClipboardState → re-render → new handler → effect
    // again) and overflow the React update-depth guard.
    //
    // Echo-loop breaker: every presence send comes back as a members update,
    // which rebuilds `room`, which re-runs the grid's selection effect, which
    // calls this handler with a fresh (content-equal) object — setSelection
    // then re-renders the whole workspace and the loop closes at WebSocket
    // RTT speed (~20 renders/s measured: 61 → 20 fps with one cell selected,
    // button animations janking too). Content-equal payloads are dropped;
    // the suppress flag is reset before the check so a preemption clear
    // still swallows its echo.
    const lastSelectionContentRef = useRef("");
    const handleSelectionChange = useCallback(
        (
            next: {
                label: string | null;
                detail: string | null;
                cellKeys: string[];
                rowId: string | null;
            } | null,
        ) => {
            let suppressed = false;
            if (suppressSelectionSendRef.current) {
                // The clear following a preemption from another tab of the same
                // account is not sent — the server entry belongs to the other tab.
                suppressSelectionSendRef.current = false;
                suppressed = true;
            }
            const content = next
                ? `${next.label ?? ""}|${next.cellKeys.join(",")}|${next.rowId ?? ""}`
                : "";
            if (content === lastSelectionContentRef.current) {
                if (suppressed && !next?.label) return;
                return;
            }
            lastSelectionContentRef.current = content;
            setSelection(next);
            if (suppressed && !next?.label) return;
            if (next) {
                room.setSelection({
                    label: next.label ?? "",
                    cellKeys: next.cellKeys,
                });
            } else {
                room.setSelection(null);
            }
        },
        [room],
    );

    if (error || versionsError) {
        return <WorkspaceLoadError error={error ?? versionsError ?? "Неизвестная ошибка."} />;
    }

    if (isPlanSection && planError) {
        return (
            <WorkspaceLoadError
                error={planError}
                title="Не удалось загрузить план"
                header={
                    <AppHeader
                        user={user}
                        projects={user.access ?? []}
                        activeProjectKey={activeProjectKey}
                        onProjectChange={onProjectChange}
                        onOpenProfile={() => setProfileOpen(true)}
                        supportMail={supportMail}
                    />
                }
            />
        );
    }

    // meta is foundational for every section; the plan window belongs to the
    // plan section only — a dictionary renders its own grid, so a null plan
    // window must not show the plan loading screen over it.
    if (!meta) {
        return (
            <div className="app-shell">
                <AppHeader
                    user={user}
                    projects={user.access ?? []}
                    activeProjectKey={activeProjectKey}
                    onProjectChange={onProjectChange}
                    onOpenProfile={() => setProfileOpen(true)}
                    supportMail={supportMail}
                />
                <div className="app-body">
                    <GridLoadingSkeleton />
                </div>
            </div>
        );
    }

    // «Рабочая версия» / version picker — moved into the toolbar. It stays
    // mounted across section switches so its collapse/expand can animate
    // (width + opacity transition); the `is-hidden` class hides and drops it
    // out of the tab order when the section has no versions to pick from.
    const hasWorkingPlan = meta?.workingPlanId != null;
    const versionItems = archiveItems.length > 0 || hasWorkingPlan ? archiveItems : [];
    const showVersionPicker = isPlanSection && versionItems.length > 0;
    const versionPicker = (
        <div
            className={
                showVersionPicker
                    ? "archive-autocomplete archive-autocomplete--visible"
                    : "archive-autocomplete archive-autocomplete--hidden"
            }
            aria-hidden={showVersionPicker ? undefined : true}
        >
            <SearchAutocomplete
                placeholder="Выберите версию"
                compactLabel="Версия"
                isDisabled={!showVersionPicker}
                onOpenChange={setToolbarControlOpen}
                items={[
                    ...(hasWorkingPlan
                        ? [
                              {
                                  id: WORKING_VERSION_ID,
                                  name: "Рабочая версия",
                                  description: "Доступна для редактирования",
                              },
                          ]
                        : []),
                    ...archiveItems,
                ]}
                selectedKey={viewPlanId ?? WORKING_VERSION_ID}
                onSelectionChange={(key) => {
                    if (key === null) return;
                    archiveState.onSelect(key === WORKING_VERSION_ID ? null : String(key));
                }}
            />
        </div>
    );

    // Block filter (asset first, then block) — blocks are rare, so the compact
    // popover wins over a tab row in the toolbar. It is a SearchAutocomplete like
    // the asset/version/form filters: a raw HeroUI `Select` here double-opened.
    // The wrapper stays mounted and collapses like the version picker, so the
    // toolbar settles (instead of popping) when the section changes.
    const showBlockFilter = isPlanSection && blocks.length > 0;
    const blockFilterSelect = (
        <ToolbarFilters placement="inline" visible={showBlockFilter}>
            <div className="plan-filters__block">
                <SearchAutocomplete
                    placeholder="Все блоки"
                    compactLabel="Блок"
                    isDisabled={!showBlockFilter}
                    onOpenChange={setToolbarControlOpen}
                    allOption={{ label: "Все блоки", present: true }}
                    allValue="__all"
                    items={blocks.map((block) => ({
                        id: block,
                        name: BLOCK_LABELS[block] ?? block,
                    }))}
                    selectedKey={blockFilter ?? "__all"}
                    onSelectionChange={(key) => {
                        const value = key && key !== "__all" ? String(key) : null;
                        setBlockFilter(value);
                        setPanelFilter(currentTabId, "block", value);
                    }}
                />
            </div>
        </ToolbarFilters>
    );

    // Form tabs — the same strip as «Сырьё», right after the version and block
    // selectors. Only a multi-section user (an admin) gets a strip; the empty
    // wrapper still absorbs the free width so the view strip stays on the right.
    const sectionTabsStrip = (
        <div className="table-toolbar__controls">
            {sectionTabItems.length > 1 ? (
                <SectionTabs
                    tabs={sectionTabItems}
                    value={activeSection}
                    onChange={(tab) => {
                        if (tab.id === activeSection) return;
                        setActiveSection(tab.id);
                        onSectionChange?.(tab.id);
                    }}
                />
            ) : null}
        </div>
    );

    const toolbar = (
        <div className="table-toolbar">
            {versionPicker}
            {blockFilterSelect}
            {sectionTabsStrip}
            <ViewStrip
                headerPinned={headerPinned}
                onHeaderPinnedChange={setHeaderPinned}
                showServiceColumns={showServiceColumns}
                onShowServiceColumnsChange={setShowServiceColumns}
                rowHeight={rowHeight}
                onRowHeightChange={setRowHeight}
                onResetRowHeight={resetRowHeight}
                // The plan's reset also drops the header level heights.
                {...(isPlanSection && {
                    columnResetLabel: "Сбросить ширину столбцов и высоту строк",
                })}
                onReset={handleStripReset}
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
                onDropdownOpenChange={setToolbarControlOpen}
            />
        </div>
    );

    const undoRedoActions: ActionItem[] = buildHistoryActions({
        show: undoState.enabled,
        offline: undoState.offline,
        canUndo: undoState.canUndo,
        canRedo: undoState.canRedo,
        onUndo: undoState.onUndo,
        onRedo: undoState.onRedo,
    });

    const clipboardActions: ActionItem[] = buildClipboardActions({
        show: true,
        disabled: isFullscreenPending,
        canPaste: clipboardState.canPaste,
        onCopy: clipboardState.onCopy,
        onPaste: clipboardState.onPaste,
    });

    // An approver cannot edit the table at all, so the row-editing and history
    // actions are removed (not merely disabled) for that role: no add/delete,
    // no undo/redo, no paste. Copy stays — reading the selection is harmless.
    const canEditRows = !isApprover;
    // Only an admin or an asset worker of the Техническая дирекция (tech) block
    // may add rows — other blocks' workers and block owners cannot (server-side
    // the same rule); the add button is hidden for everyone else.
    const canAddRows = isAdmin || (isAssetWorker && userBlock === "tech");
    const planClipboardActions = isApprover
        ? clipboardActions.filter((action) => action.id !== "paste")
        : clipboardActions;

    const actions: ActionItem[] = [
        ...planClipboardActions,
        ...(canEditRows ? undoRedoActions : []),
        buildExportAction({
            pending: exportPending,
            disabled: exportPending || isFullscreenPending,
            rowCount: gridStats?.filteredRows ?? 0,
            onExport: (mode) => void handleStripExport(mode),
        }),
        ...(!isArchive && isAdmin
            ? [
                  {
                      id: "plan-excel-import",
                      label: "Загрузить из Excel",
                      icon: createElement(Upload),
                      onPress: () => setPlanExcelImportOpen(true),
                      group: "download" as const,
                  },
              ]
            : []),
        ...(!isArchive && canAddRows
            ? buildAddRowAction({
                  show: true,
                  label: "Добавить строку",
                  disabled: readOnly || isFullscreenPending,
                  onAdd: () => {
                      setAddAssetKey(null);
                      setAddOpen(true);
                  },
              })
            : []),
        ...(!isArchive && canEditRows
            ? buildDeleteAction({
                  disabled: !canDeleteRow || isFullscreenPending,
                  blockedReason:
                      canDeleteRow && !isFullscreenPending ? null : NO_ROW_SELECTED_REASON,
                  onDelete: () => setDeleteOpen(true),
              })
            : []),
        ...buildPrimaryAction({
            show: canSubmit && !isArchive,
            id: "submit",
            label: "Отправить на согласование",
            icon: createElement(Send),
            disabled: isMutationPending || !canSubmitPlan || isFullscreenPending,
            onPress: () => setSubmitOpen(true),
        }),
        ...buildPrimaryAction({
            show: canApprovePlan && !isArchive,
            id: "approve",
            label: "Согласовать",
            icon: createElement(CheckCircle),
            disabled: isMutationPending || isFullscreenPending,
            onPress: () => setApproveOpen(true),
        }),
    ];

    // Dictionary tabs: export/undo/redo/copy/paste act on the dictionary grid;
    // delete is delegated by the active dictionary section (hidden for users).
    const dictActions: ActionItem[] = [
        ...clipboardActions,
        ...undoRedoActions,
        buildExportAction({
            pending: dictExportState.pending,
            disabled: dictExportState.pending || isFullscreenPending,
            rowCount: dictGridStats?.filteredRows ?? 0,
            onExport: dictExportState.onExport,
        }),
        ...(dictUserCreateState.canCreate
            ? buildAddRowAction({
                  show: true,
                  label: "Добавить пользователя",
                  disabled: isFullscreenPending,
                  onAdd: dictUserCreateState.onCreate,
              })
            : []),
        ...buildReplaceAction({
            show: dictReplaceState.canReplace || dictReplaceState.blockedReason !== null,
            disabled: !dictReplaceState.canReplace || isFullscreenPending,
            blockedReason: isFullscreenPending ? null : dictReplaceState.blockedReason,
            onReplace: dictReplaceState.onReplace,
        }),
        ...buildDeleteAction({
            disabled: !dictDeleteState.canDelete || isFullscreenPending,
            blockedReason: dictDeleteState.blockedReason,
            onDelete: dictDeleteState.onDelete,
        }),
    ];

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                {collabEnabled ? (
                    <FormStatus
                        room={room}
                        selectionLabel={
                            isPlanSection ? (selection?.label ?? null) : dictSelectionLabel
                        }
                        selectionDetail={isPlanSection ? (selection?.detail ?? null) : null}
                        gridStats={isPlanSection ? gridStats : dictGridStats}
                    />
                ) : null}
            </div>
            {planImport.progress ? <FooterProgress {...planImport.progress} /> : null}
            <ActionBar
                items={isPlanSection ? actions : dictActions}
                onDropdownOpenChange={setToolbarControlOpen}
            />
            <PlanExcelImportModal
                isOpen={planExcelImportOpen}
                result={planExcelImportResult}
                onOpenChange={closePlanExcelImport}
                onFileSelected={async (file, progress) => {
                    setPlanExcelImportResult(null);
                    const result = await inspectRenovationPlanImport(file, progress);
                    setPlanExcelImportResult({
                        status:
                            result.status === "invalid_format"
                                ? "invalid-format"
                                : result.status === "has_errors"
                                  ? "has-errors"
                                  : "ready",
                        duplicateCodes: result.duplicate_codes,
                        invalidCalculatedCodes: result.invalid_calculated_codes,
                        invalidDataTypes: result.invalid_data_types,
                        missingDictionaryValues: result.missing_dictionary_values,
                        unknownAssets: result.unknown_assets,
                        skippedRows: result.skipped_rows,
                        allRowsSkipped: result.all_rows_skipped,
                        assets: result.assets,
                        newRows: result.new_rows,
                        rowsToUpdate: result.rows_to_update,
                    });
                }}
                onUpload={(file) => {
                    const expectedRows =
                        planExcelImportResult?.status === "ready"
                            ? planExcelImportResult.newRows + planExcelImportResult.rowsToUpdate
                            : 0;
                    void planImport.start(file, expectedRows);
                }}
            />
        </div>
    );

    const header = (
        <AppHeader
            user={user}
            projects={user.access ?? []}
            activeProjectKey={activeProjectKey}
            onProjectChange={onProjectChange}
            onOpenProfile={() => setProfileOpen(true)}
            supportMail={supportMail}
        />
    );

    return (
        <main className={isFullscreen ? "app-shell app-shell--fullscreen" : "app-shell"}>
            {header}
            <WorkspaceContent toolbar={toolbar} footer={footer}>
                <div className="grid-viewport" aria-busy={isMutationPending}>
                    {isPlanSection ? (
                        planTotals ? (
                            <>
                                <GridErrorBoundary>
                                    <RenovationGrid
                                        ref={gridRef}
                                        fields={meta.fields}
                                        headerCells={meta.headerCells}
                                        catalogs={meta.catalogs}
                                        scopeKey={scopeKey}
                                        totalRows={planTotals.total}
                                        totalRowsAll={planTotals.totalAll ?? planTotals.total}
                                        loadRows={loadRows}
                                        loadedRows={loadedRows}
                                        assetFilter={assetFilterSet}
                                        blockFilter={blockFilter}
                                        baselineBoundary={baselineBoundary}
                                        readOnly={readOnly}
                                        canEdit={canEditRows}
                                        editableFieldKeys={editableFieldKeys}
                                        userAssetIds={userAssetIds}
                                        remoteLocks={remoteLocks}
                                        remotePresence={remotePresence}
                                        remoteCellInfo={remoteCellInfo}
                                        onAcquireLease={room.acquireLeases}
                                        onReleaseLease={room.releaseLeases}
                                        onLockedCellEdit={gridWarnings.onLockedCellEdit}
                                        onCommit={commit}
                                        onCommitFailed={refresh}
                                        onValidationError={gridWarnings.onValidationError}
                                        onPasteSkipped={gridWarnings.onPasteSkipped}
                                        onSelectionChange={handleSelectionChange}
                                        onViewStatsChange={setGridStats}
                                        onClipboardStateChange={handleClipboardState}
                                        onLoadedRowsChange={handleLoadedRowsChange}
                                        onCenterPositionChange={handleCenterPositionChange}
                                        headerPinned={headerPinned}
                                        showServiceColumns={showServiceColumns}
                                        rowHeight={rowHeight}
                                        summaryValues={summaryValues}
                                        rowPatches={rowPatches}
                                        scrollToPosition={scrollToPosition}
                                        activeFilterColumns={activeFilterColumns}
                                        activeSorting={activeSorting}
                                        onSortColumn={toggleColumnSorting}
                                        onOpenColumnFilter={(column, anchor) => {
                                            const field = meta.fields.find(
                                                (candidate) => candidate.dbName === column,
                                            );
                                            // The audit columns are not form
                                            // fields, so they have no `field`:
                                            // their shape comes from the same
                                            // rule the cells use.
                                            const audit = AUDIT_COLUMNS[column];
                                            // The FIELD's type decides the filter (see
                                            // `fieldFacetOrder`), so a text cell never
                                            // gets a number range and vice versa.
                                            setColumnFilterTarget({
                                                column,
                                                label: field?.label ?? audit?.label ?? column,
                                                // A `timestamptz` audit column takes a
                                                // MIN–MAX window applied to the rows;
                                                // the popover shows no value list for it.
                                                instant: audit?.kind === "date",
                                                order: field
                                                    ? fieldFacetOrder(field)
                                                    : audit
                                                      ? audit.kind
                                                      : "text",
                                                // A `date` cell holds a calendar
                                                // day («Дата начала/окончания
                                                // работ»), not an instant: its
                                                // filter asks for days only. The
                                                // audit instants keep their time.
                                                dateOnly: field?.cellType === "date",
                                                // The option list reads like the cells:
                                                // a field with a fixed precision shows it.
                                                ...(typeof field?.decimals === "number" && {
                                                    decimals: field.decimals,
                                                }),
                                                anchor,
                                            });
                                        }}
                                        onOpenRowFilter={setRowFilterTarget}
                                    />
                                </GridErrorBoundary>
                                {planTotals.total === 0 ? (
                                    <div className="grid-empty">В этом плане пока нет строк.</div>
                                ) : null}
                            </>
                        ) : (
                            <GridLoadingSkeleton />
                        )
                    ) : (
                        <RenovationDictionarySection
                            currentUserEmail={user.email}
                            section={dictSection ?? RENOVATION_ASSETS_SECTION}
                            room={room}
                            assets={meta.assets}
                            catalogs={meta.catalogs}
                            collabEnabled={collabEnabled}
                            refreshKey={reloadKey}
                            remoteCellValues={remoteCellValues}
                            onDeleteState={setDictDeleteState}
                            onReplaceState={setDictReplaceState}
                            onExportState={setDictExportState}
                            onResetState={setDictResetState}
                            onUserCreateState={setDictUserCreateState}
                            headerPinned={headerPinned}
                            showServiceColumns={showServiceColumns}
                            rowHeight={rowHeight}
                            onClipboardStateChange={handleDictClipboardState}
                            onUndoHistoryChanged={() => {
                                setUndoCount((count) => count + 1);
                                setRedoCount(0);
                            }}
                            onAssetsChanged={refreshMeta}
                            onSelectionLabelChange={setDictSelectionLabel}
                            onGridStatsChange={setDictGridStats}
                        />
                    )}
                </div>
            </WorkspaceContent>
            <WorkspaceFullscreenPanels
                visible={isFullscreen && !isFullscreenPending}
                revealClass={revealClass}
                header={header}
                toolbar={toolbar}
                footer={footer}
            />

            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={selectedRow?.__assetName ?? null}
                count={1}
                onOpenChange={setDeleteOpen}
                onConfirm={() => void deleteSelectedRow()}
            />
            <AddRowDialog
                isOpen={addOpen}
                isPending={isMutationPending}
                assets={addRowAssets}
                selectedKey={addAssetKey}
                onSelectionChange={setAddAssetKey}
                onOpenChange={setAddOpen}
                onConfirm={() => {
                    if (addAssetKey) void addRow(addAssetKey);
                }}
            />
            <SubmitPlanDialog
                isOpen={submitOpen}
                isPending={isMutationPending}
                onOpenChange={setSubmitOpen}
                onConfirm={() => void runSubmit()}
            />
            <SubmitBlockedDialog
                isOpen={submitBlocked !== null}
                message={submitBlocked ?? ""}
                onOpenChange={(open) => {
                    if (!open) setSubmitBlocked(null);
                }}
            />
            <ApprovePlanDialog
                isOpen={approveOpen}
                isPending={isMutationPending}
                pendingCreatedBy={
                    versions?.find((version) => version.state === "pending_approval")?.createdBy ??
                    null
                }
                onOpenChange={setApproveOpen}
                onConfirm={() => void runApprove()}
            />
            <ProfileModal
                isOpen={profileOpen}
                user={user}
                onOpenChange={setProfileOpen}
                onProfileSaved={setUser}
            />
            {columnFilterTarget ? (
                <RenovationColumnFilterPopover
                    column={columnFilterTarget.column}
                    columnLabel={columnFilterTarget.label}
                    anchor={columnFilterTarget.anchor}
                    order={columnFilterTarget.order}
                    dateOnly={columnFilterTarget.dateOnly}
                    decimals={columnFilterTarget.decimals}
                    instant={columnFilterTarget.instant === true}
                    {...(viewPlanId && { planId: viewPlanId })}
                    asset={assetFilter}
                    filters={precedingColumnFilters(activeColumnFilters, columnFilterTarget.column)}
                    initial={columnFilters[columnFilterTarget.column] ?? null}
                    storageScope={filterDraftScope}
                    // «По умолчанию»: the same state «Сбросить все фильтры и
                    // сортировки» restores — no constraint on this column.
                    defaultFilter={null}
                    onApply={applyColumnFilter(columnFilterTarget.column)}
                    onInvalidQuery={(reason) =>
                        discardInvalidPlanQuery(reason, columnFilterTarget.column)
                    }
                    onClose={() => setColumnFilterTarget(null)}
                />
            ) : null}
            {rowFilterTarget ? (
                <RowGoToFilter
                    anchor={rowFilterTarget}
                    total={planTotals?.total ?? 0}
                    maxColumn={totalColumns}
                    storageScope={filterDraftScope}
                    onGoTo={(position) => void gridRef.current?.scrollToPosition(position)}
                    onGoToColumn={(number) => void gridRef.current?.scrollToColumn(number)}
                    onClose={() => setRowFilterTarget(null)}
                />
            ) : null}
        </main>
    );
}
