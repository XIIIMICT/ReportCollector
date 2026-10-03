"use client";

import { AlertDialog, Button, Card, Skeleton, Spinner, toast, Typography } from "@heroui/react";
import {
    CheckCircle,
    Copy,
    Download,
    PasteClipboard,
    Plus,
    Redo,
    Send,
    Trash,
    Undo,
    WarningTriangle,
} from "iconoir-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState, startTransition } from "react";

import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    AppHeader,
    DeleteRecordDialog,
    downloadBlob,
    type ArchiveItem,
    type ArchiveState,
    type ClipboardActions,
    type UndoState,
} from "@/features/raw-materials/raw-materials-workspace";
import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    addRenovationRow,
    approveRenovationPlan,
    createRenovationExportJob,
    deleteRenovationRow,
    downloadRenovationExportJob,
    getRenovationDiff,
    getRenovationExportJob,
    getRenovationMeta,
    getRenovationPlanRows,
    getRenovationPlanSummary,
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
    type RenovationColumnFilter,
    type RenovationDiff,
    type RenovationDiffRow,
    type RenovationExportJob,
    type RenovationMeta,
    type RenovationPlanRow,
    type RenovationPlanVersion,
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
import {
    hasSaveFilePicker,
    pickSaveFile,
    streamUrlToFileHandle,
    type SaveFileHandle,
} from "@/shared/file-save";
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";
import { SectionTabs } from "@/shared/grid/section-tabs";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { isFromTextEditor } from "@/shared/keyboard";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import { ColumnFilterPopover } from "@/shared/ui/column-filter-popover";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { RowGoToFilter } from "@/shared/ui/row-go-to-filter";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { ViewStrip } from "@/shared/ui/view-strip";

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

export type RenovationWorkspaceProps = {
    currentUser: CurrentUser;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
    /** Form restored from `?section=` (plan / assets / a catalog id / users). */
    initialSection?: string | null;
    /** Mirrors the active form into the URL so a reload keeps it. */
    onSectionChange?: (section: string | null) => void;
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
    filters: RenovationColumnFilter[];
}): string {
    return JSON.stringify([params.planId ?? "working", params.asset ?? "", params.filters]);
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
    };
}

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
}

/** Human-readable progress of a background plan export (toast description). */
function exportProgressLabel(job: RenovationExportJob): string {
    if (job.total > 0 && job.progress > 0) {
        const percent = Math.min(100, Math.round((job.progress / job.total) * 100));
        return `${percent}% (${job.progress} из ${job.total} строк)`;
    }
    return "Подготовка данных…";
}

/** Bytes as KB/MB for the saving-progress toast. */
function formatBytes(bytes: number): string {
    if (bytes < 1024 * 1024) {
        return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
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
}: RenovationWorkspaceProps) {
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const [reloadKey, setReloadKey] = useState(0);
    const [assetFilter, setAssetFilter] = useState<string | null>(null);
    const [blockFilter, setBlockFilter] = useState<string | null>(null);
    // Active header filters, keyed by column prop (field dbName or the synthetic
    // asset column). A column keeps display values + the «Пустые» bucket; all
    // columns combine with AND (server-side).
    const [columnFilters, setColumnFilters] = useState<Record<string, RenovationColumnFilter>>({});
    // Flat filter list for the rows API; a column with nothing kept selects
    // nothing, which the server treats as "no rows" (never "all").
    const activeColumnFilters = useMemo(
        () =>
            Object.entries(columnFilters).map(([column, filter]) => ({
                column,
                values: filter.values,
                includeNulls: filter.includeNulls,
            })),
        [columnFilters],
    );
    // The column whose header-filter popover is open (its label + anchor rect).
    const [columnFilterTarget, setColumnFilterTarget] = useState<{
        column: string;
        label: string;
        anchor: DOMRect;
    } | null>(null);
    // The row-number («№») range filter popover anchor (the corner's rect).
    const [rowFilterTarget, setRowFilterTarget] = useState<DOMRect | null>(null);
    const activeFilterColumns = useMemo(() => new Set(Object.keys(columnFilters)), [columnFilters]);
    const applyColumnFilter = (column: string) => (filter: RenovationColumnFilter | null) =>
        setColumnFilters((current) => {
            const next = { ...current };
            if (filter === null) delete next[column];
            else next[column] = filter;
            return next;
        });
    // Active section (plan vs dictionary) — declared early because the
    // plan-window reset effect keys off it. A section restored from the URL is
    // validated against the available sections by the guard effect below.
    const [activeSection, setActiveSection] = useState<string>(initialSection ?? "plan");
    const isPlanSection = activeSection === "plan";
    const { data: meta, error } = useAsyncData<RenovationMeta | null>(
        () => getRenovationMeta().catch(() => null),
        [metaReloadKey],
    );
    const { data: versions, error: versionsError } = useAsyncData<RenovationPlanVersion[] | null>(
        () => getRenovationPlans().catch(() => null),
        [metaReloadKey],
    );
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
    const [baselineBoundary, setBaselineBoundary] = useState<number | null>(null);
    // Structural scroll: the working plan opens at its TAIL (rows are appended
    // at the end), the archive reads top-down. Expressed as the sequential
    // number in the filtered scope (1-based).
    const [scrollToPosition, setScrollToPosition] = useState<{
        position: number;
        token: number;
    } | null>(null);
    const scrollToPositionTokenRef = useRef(0);

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
        void (async () => {
            try {
                // The head read doubles as the scope probe: it returns the
                // filtered total (the plugin's `total`) and the baseline
                // boundary without transferring any rows.
                const head = await getRenovationPlanRows(
                    0,
                    1,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                );
                if (cancelled) return;
                setPlanTotals({ total: head.total, totalAll: head.totalAll ?? null });
                setBaselineBoundary(head.baselineBoundary ?? null);
                // The working plan anchors at its tail — the user lands on the
                // rows they append most; an archive reads top-down. The jump
                // must be cleared for the archive, otherwise the previous
                // working-plan position leaks into it (the grid effect keys on
                // totalRows, which changes between versions).
                if (!viewPlanId && head.total > 0) {
                    scrollToPositionTokenRef.current += 1;
                    setScrollToPosition({
                        position: head.total,
                        token: scrollToPositionTokenRef.current,
                    });
                } else {
                    setScrollToPosition(null);
                }
            } catch (reason) {
                if (!cancelled) setPlanError(errorMessage(reason));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [meta, viewPlanId, assetFilter, activeColumnFilters, isPlanSection]);

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
    useEffect(() => {
        if (!meta || !isPlanSection) {
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
            .catch(() => {
                if (!cancelled) setSummaryValues(null);
            });
        return () => {
            cancelled = true;
        };
    }, [meta, viewPlanId, assetFilter, activeColumnFilters, isPlanSection]);

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
            }),
        [activeColumnFilters, assetFilter, viewPlanId],
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
            const page = await getRenovationPlanRows(
                offset,
                limit,
                viewPlanId ?? undefined,
                assetFilter,
                activeColumnFilters,
            );
            if (scopeKeyRef.current !== requestedScope) return { rows: [], total: 0 };
            const diffByRowId = diffByRowIdRef.current;
            return {
                rows: page.rows.map((row) => toGridRow(row, diffByRowId)),
                total: page.total,
            };
        },
        [activeColumnFilters, assetFilter, viewPlanId],
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
    const [selection, setSelection] = useState<{
        label: string | null;
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
    const [undoCount, setUndoCount] = useState(0);
    const [redoCount, setRedoCount] = useState(0);
    // The «Добавить строку» dialog (asset picker → an empty row at the end).
    const [addOpen, setAddOpen] = useState(false);
    const [addAssetKey, setAddAssetKey] = useState<string | null>(null);
    // Dictionary tab actions are delegated by the active dictionary section.
    const [dictDeleteState, setDictDeleteState] = useState<{
        canDelete: boolean;
        onDelete: () => void;
    }>({ canDelete: false, onDelete: () => {} });
    const [dictExportState, setDictExportState] = useState<{
        onExport: (scope: "all" | "visible") => void;
    }>({ onExport: () => {} });
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
    // Display toggles for the active grid (rendered in the view strip). The
    // Header pinning defaults on everywhere (plan and dictionaries).
    const [planHeaderPinned, setPlanHeaderPinned] = useState(true);
    const [planServiceColumns, setPlanServiceColumns] = useState(false);
    const [dictHeaderPinned, setDictHeaderPinned] = useState(true);
    const [dictServiceColumns, setDictServiceColumns] = useState(false);
    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const gridRef = useRef<RenovationGridHandle>(null);
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
    const collabEnabled = !isArchivedView;

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);
    const refreshMeta = useCallback(() => {
        setMetaReloadKey((key) => key + 1);
        setViewPlanId(null);
    }, []);

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
        visibleAssets: visibleAssetNames,
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
        void getUndoState(collabSection)
            .then((state) => {
                setUndoCount(state.canUndo ? 1 : 0);
                setRedoCount(state.canRedo ? 1 : 0);
            })
            .catch(() => {});
    }, [collabEnabled, collabSection, reloadKey, viewPlanId]);

    const applyUndoResult = useCallback(
        (result: RenovationUndoResult, direction: "undo" | "redo") => {
            if (result.applied) {
                setPendingSkip(null);
                // Re-read the stack state from the server instead of local ±1:
                // the counters only track presence (canUndo/canRedo), and a
                // locally decremented count died after a single undo even when
                // the server stack still had actions.
                void getUndoState(collabSection)
                    .then((state) => {
                        setUndoCount(state.canUndo ? 1 : 0);
                        setRedoCount(state.canRedo ? 1 : 0);
                    })
                    .catch(() => {});
                refresh();
                toast.success(direction === "undo" ? "Действие отменено" : "Действие возвращено");
                return;
            }
            if (result.reason === "empty") {
                setPendingSkip(null);
                // The counters drifted from the actual stack — re-sync.
                void getUndoState(collabSection)
                    .then((state) => {
                        setUndoCount(state.canUndo ? 1 : 0);
                        setRedoCount(state.canRedo ? 1 : 0);
                    })
                    .catch(() => {});
                return;
            }
            const conflict = result.conflicts[0];
            if (conflict) {
                setPendingSkip({ actionId: conflict.actionId, direction });
                const owner = conflict.user || "другой пользователь";
                const description =
                    conflict.reason === "locked"
                        ? `Ячейку сейчас редактирует ${owner}. Нажмите ещё раз, чтобы пропустить действие.`
                        : `Данные изменил ${owner}. Нажмите ещё раз, чтобы пропустить это действие.`;
                toast.warning("Действие не выполнено", { description });
                return;
            }
            setPendingSkip(null);
        },
        [collabSection, refresh],
    );

    const runUndo = useCallback(async () => {
        await pendingCommitRef.current;
        try {
            const result = await undoCells(
                pendingSkip?.direction === "undo" ? pendingSkip.actionId : null,
                collabSection,
            );
            applyUndoResult(result, "undo");
        } catch (reason) {
            toast.danger("Не удалось отменить действие", { description: errorMessage(reason) });
        }
    }, [applyUndoResult, collabSection, pendingSkip]);

    const runRedo = useCallback(async () => {
        await pendingCommitRef.current;
        try {
            const result = await redoCells(
                pendingSkip?.direction === "redo" ? pendingSkip.actionId : null,
                collabSection,
            );
            applyUndoResult(result, "redo");
        } catch (reason) {
            toast.danger("Не удалось вернуть действие", { description: errorMessage(reason) });
        }
    }, [applyUndoResult, collabSection, pendingSkip]);

    useEffect(() => {
        // An approver cannot edit the plan — no undo/redo shortcuts either.
        if (!collabEnabled || isApprover) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (!(event.ctrlKey || event.metaKey)) return;
            // event.code — the physical key: works in any layout
            // (in Russian event.key gives «я»/«н»).
            if (event.code !== "KeyZ" && event.code !== "KeyY") return;
            const target = event.target as HTMLElement | null;
            if (
                target &&
                (target.tagName === "INPUT" ||
                    target.tagName === "TEXTAREA" ||
                    target.isContentEditable)
            )
                return;
            event.preventDefault();
            if (event.code === "KeyZ") {
                if (event.shiftKey) void runRedo();
                else void runUndo();
            } else {
                void runRedo();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [collabEnabled, isApprover, runRedo, runUndo]);

    useEffect(() => {
        setUndoState({
            enabled: collabEnabled && !isArchive && !isApprover,
            offline: collabEnabled && room.status !== "online",
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [collabEnabled, isApprover, isArchive, redoCount, room.status, runRedo, runUndo, undoCount]);

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
            // asset and block, not just the window currently rendered.
            const visible = scope === "visible";
            let toastId: string | null = null;
            try {
                const job = await createRenovationExportJob({
                    planId: viewPlanId ?? undefined,
                    asset: visible ? assetFilter : null,
                    block: visible ? blockFilter : null,
                });
                // Chromium: ask for the destination immediately (like a normal
                // big download), then stream the ready file into it. The user
                // sees the save dialog at once and a real percentage: first the
                // generation progress, then the byte progress.
                let handle: SaveFileHandle | null = null;
                if (hasSaveFilePicker()) {
                    try {
                        handle = await pickSaveFile(job.fileName);
                    } catch (reason) {
                        if (reason instanceof DOMException && reason.name === "AbortError") {
                            // The user closed the save dialog — stop silently.
                            return;
                        }
                        handle = null; // picker unavailable/expired — blob fallback
                    }
                }
                let current = job;
                if (current.status !== "done") {
                    toastId = toast.info("Формируется файл выгрузки…", {
                        timeout: 0,
                        description: exportProgressLabel(current),
                    });
                }
                // The full plan is minutes of server CPU: poll the background
                // job until it is ready instead of holding the request open.
                while (current.status === "pending" || current.status === "running") {
                    await new Promise((resolve) => setTimeout(resolve, 1500));
                    current = await getRenovationExportJob(current.id);
                    if (toastId) {
                        toast.update(toastId, "Формируется файл выгрузки…", {
                            description: exportProgressLabel(current),
                        });
                    }
                }
                if (current.status === "failed") {
                    throw new Error(current.error ?? "Не удалось сформировать файл.");
                }
                if (handle) {
                    if (toastId) {
                        toast.update(toastId, "Сохранение файла…", { description: "0%" });
                    }
                    await streamUrlToFileHandle(
                        handle,
                        renovationExportJobFileUrl(current.id),
                        (received, total) => {
                            if (!toastId) return;
                            const percent =
                                total > 0 ? Math.min(100, Math.round((received / total) * 100)) : 0;
                            const description =
                                total > 0
                                    ? `${percent}% (${formatBytes(received)} из ${formatBytes(total)})`
                                    : formatBytes(received);
                            toast.update(toastId, "Сохранение файла…", { description });
                        },
                    );
                    if (toastId) toast.close(toastId);
                    toast.success("Файл сохранён", { description: current.fileName });
                } else {
                    const { blob, fileName } = await downloadRenovationExportJob(current.id);
                    downloadBlob(blob, fileName);
                    if (toastId) toast.close(toastId);
                    toast.success("Файл выгружен", { description: fileName });
                }
            } catch (reason) {
                if (toastId) toast.close(toastId);
                toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
            } finally {
                setExportPending(false);
            }
        },
        [exportPending, viewPlanId, assetFilter, blockFilter],
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
                    // otherwise the reset looks like it did nothing.
                    setColumnFilters({});
                }
                return;
            }
            if (scope === "columnWidths") dictResetState.resetColumnWidths();
            else void dictResetState.resetFiltersSorting();
        },
        [dictResetState, isPlanSection],
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
                );
                if (cancelled) return;
                setBaselineBoundary(page.baselineBoundary ?? null);
                setPlanTotals({ total: page.total, totalAll: page.totalAll ?? null });
                applyServerRows(page.rows);
            } catch {
                // Keep the current rows; the next refresh retries.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [reloadKey, assetFilter, viewPlanId, activeColumnFilters, isArchive, applyServerRows]);

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
    useEffect(() => {
        if (!canDeleteRow) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.code !== "Delete" || !(event.ctrlKey || event.metaKey)) return;
            if (isFromTextEditor(event)) return;
            event.preventDefault();
            setDeleteOpen(true);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [canDeleteRow]);

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

    const handleClipboardState = useCallback((state: { canPaste: boolean }) => {
        setClipboardState({
            canPaste: state.canPaste,
            onCopy: () => void gridRef.current?.copySelection(),
            onPaste: () => void gridRef.current?.pasteIntoSelection(),
        });
    }, []);

    // The dictionary grid binds the copy/paste actions to its own handle.
    const handleDictClipboardState = useCallback(
        (state: { canPaste: boolean; onCopy: () => void; onPaste: () => void }) => {
            setClipboardState(state);
        },
        [],
    );

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
        return (
            <div className="app-shell">
                <div className="app-body">
                    <Card className="startup-error-card">
                        <Card.Content className="p-8">
                            <h2 className="mb-2 text-lg font-semibold">
                                Не удалось открыть рабочее пространство
                            </h2>
                            <p className="text-danger text-sm">{error ?? versionsError}</p>
                        </Card.Content>
                    </Card>
                </div>
            </div>
        );
    }

    if (isPlanSection && planError) {
        return (
            <div className="app-shell">
                <AppHeader
                    user={user}
                    projects={user.access ?? []}
                    activeProjectKey={activeProjectKey}
                    onProjectChange={onProjectChange}
                    onOpenProfile={() => setProfileOpen(true)}
                />
                <div className="app-body">
                    <Card className="startup-error-card">
                        <Card.Content className="p-8">
                            <h2 className="mb-2 text-lg font-semibold">
                                Не удалось загрузить план
                            </h2>
                            <p className="text-danger text-sm">{planError}</p>
                        </Card.Content>
                    </Card>
                </div>
            </div>
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
                />
                <div className="app-body">
                    <div className="flex items-center gap-3 p-8">
                        <Spinner size="sm" />
                        <Typography>Загрузка...</Typography>
                    </div>
                    <Skeleton className="h-48 rounded-xl" />
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
        <div
            className={
                showBlockFilter
                    ? "plan-filters__block toolbar-collapse"
                    : "plan-filters__block toolbar-collapse toolbar-collapse--hidden"
            }
            aria-hidden={showBlockFilter ? undefined : true}
        >
            <SearchAutocomplete
                placeholder="Все блоки"
                compactLabel="Блок"
                isDisabled={!showBlockFilter}
                onOpenChange={setToolbarControlOpen}
                items={[
                    { id: "__all", name: "Все блоки" },
                    ...blocks.map((block) => ({
                        id: block,
                        name: BLOCK_LABELS[block] ?? block,
                    })),
                ]}
                selectedKey={blockFilter ?? "__all"}
                onSelectionChange={(key) =>
                    setBlockFilter(key && key !== "__all" ? String(key) : null)
                }
            />
        </div>
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
                headerPinned={isPlanSection ? planHeaderPinned : dictHeaderPinned}
                onHeaderPinnedChange={isPlanSection ? setPlanHeaderPinned : setDictHeaderPinned}
                showServiceColumns={isPlanSection ? planServiceColumns : dictServiceColumns}
                onShowServiceColumnsChange={
                    isPlanSection ? setPlanServiceColumns : setDictServiceColumns
                }
                exportPending={exportPending}
                onExport={handleStripExport}
                onReset={handleStripReset}
                hideExport
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
                onDropdownOpenChange={setToolbarControlOpen}
            />
        </div>
    );

    const undoRedoActions: ActionItem[] = undoState.enabled
        ? [
              {
                  id: "undo",
                  label: "Отменить действие",
                  icon: <Undo />,
                  onPress: undoState.onUndo,
                  disabled: undoState.offline || !undoState.canUndo,
                  shortcut: { key: "Z", aria: "Control+Z" },
                  group: "history",
              } satisfies ActionItem,
              {
                  id: "redo",
                  label: "Вернуть действие",
                  icon: <Redo />,
                  onPress: undoState.onRedo,
                  disabled: undoState.offline || !undoState.canRedo,
                  shortcut: { key: "Y", aria: "Control+Shift+Z" },
                  group: "history",
              } satisfies ActionItem,
          ]
        : [];

    const clipboardActions: ActionItem[] = [
        {
            id: "copy",
            label: "Копировать",
            icon: <Copy />,
            onPress: clipboardState.onCopy,
            disabled: isFullscreenPending,
            shortcut: { key: "C", aria: "Control+C" },
            group: "clipboard",
        } satisfies ActionItem,
        {
            id: "paste",
            label: "Вставить",
            icon: <PasteClipboard />,
            onPress: clipboardState.onPaste,
            disabled: isFullscreenPending || !clipboardState.canPaste,
            shortcut: { key: "V", aria: "Control+V" },
            group: "clipboard",
        } satisfies ActionItem,
    ];

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
        {
            id: "export",
            label: "Скачать в Excel",
            icon: <Download />,
            onPress: () => void handleStripExport("all"),
            disabled: exportPending || isFullscreenPending,
            pending: exportPending,
            group: "download",
            menu: [
                {
                    id: "all",
                    label: "Скачать всю таблицу",
                    icon: <Download aria-hidden="true" />,
                    onPress: () => void handleStripExport("all"),
                },
                {
                    id: "visible",
                    label: "Скачать видимую таблицу",
                    icon: <Download aria-hidden="true" />,
                    onPress: () => void handleStripExport("visible"),
                },
            ],
        } satisfies ActionItem,
        ...(!isArchive && canAddRows
            ? [
                  {
                      id: "add",
                      label: "Добавить строку",
                      icon: <Plus />,
                      onPress: () => {
                          setAddAssetKey(null);
                          setAddOpen(true);
                      },
                      disabled: readOnly || isFullscreenPending,
                      group: "row" as const,
                  } satisfies ActionItem,
              ]
            : []),
        ...(!isArchive && canEditRows
            ? [
                  {
                      id: "delete",
                      label: "Удалить строку",
                      icon: <Trash />,
                      onPress: () => setDeleteOpen(true),
                      variant: "danger" as const,
                      disabled: !canDeleteRow || isFullscreenPending,
                      group: "row" as const,
                  } satisfies ActionItem,
              ]
            : []),
        ...(canSubmit && !isArchive
            ? [
                  {
                      id: "submit",
                      label: "Отправить на согласование",
                      icon: <Send />,
                      onPress: () => setSubmitOpen(true),
                      variant: "primary" as const,
                      disabled: isMutationPending || !canSubmitPlan || isFullscreenPending,
                  } satisfies ActionItem,
              ]
            : []),
        ...(canApprovePlan && !isArchive
            ? [
                  {
                      id: "approve",
                      label: "Согласовать",
                      icon: <CheckCircle />,
                      onPress: () => setApproveOpen(true),
                      variant: "primary" as const,
                      disabled: isMutationPending || isFullscreenPending,
                  } satisfies ActionItem,
              ]
            : []),
    ];

    // Dictionary tabs: export/undo/redo/copy/paste act on the dictionary grid;
    // delete is delegated by the active dictionary section (hidden for users).
    const dictActions: ActionItem[] = [
        ...clipboardActions,
        ...undoRedoActions,
        {
            id: "export",
            label: "Скачать в Excel",
            icon: <Download />,
            onPress: () => dictExportState.onExport("all"),
            disabled: exportPending || isFullscreenPending,
            pending: exportPending,
            group: "download",
            menu: [
                {
                    id: "all",
                    label: "Скачать всю таблицу",
                    onPress: () => dictExportState.onExport("all"),
                },
                {
                    id: "visible",
                    label: "Скачать видимую таблицу",
                    onPress: () => dictExportState.onExport("visible"),
                },
            ],
        } satisfies ActionItem,
        ...(dictUserCreateState.canCreate
            ? [
                  {
                      id: "create-user",
                      label: "Добавить пользователя",
                      icon: <Plus />,
                      onPress: dictUserCreateState.onCreate,
                      disabled: isFullscreenPending,
                  } satisfies ActionItem,
              ]
            : []),
        ...(dictDeleteState.canDelete
            ? [
                  {
                      id: "delete",
                      label: "Удалить строку",
                      icon: <Trash />,
                      onPress: dictDeleteState.onDelete,
                      variant: "danger" as const,
                      disabled: isFullscreenPending,
                  } satisfies ActionItem,
              ]
            : []),
    ];

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                {collabEnabled ? (
                    <FormStatus
                        room={room}
                        selectionLabel={isPlanSection ? (selection?.label ?? null) : null}
                        gridStats={isPlanSection ? gridStats : null}
                    />
                ) : null}
            </div>
            <ActionBar
                items={isPlanSection ? actions : dictActions}
                onDropdownOpenChange={setToolbarControlOpen}
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
        />
    );

    return (
        <main className={isFullscreen ? "app-shell app-shell--fullscreen" : "app-shell"}>
            {header}
            <div className="app-body">
                <section className="workspace">
                    <Card className="workspace-card">
                        <Card.Content className="workspace-card__content">
                            {toolbar}
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
                                                    onLockedCellEdit={(_, ownerName) =>
                                                        toast.warning("Ячейка занята", {
                                                            description: `Сейчас её занимает ${ownerName}.`,
                                                        })
                                                    }
                                                    onCommit={commit}
                                                    onCommitFailed={refresh}
                                                    onValidationError={(message) =>
                                                        toast.warning("Некорректное значение", {
                                                            description: message,
                                                        })
                                                    }
                                                    onPasteSkipped={(description) =>
                                                        toast.warning(
                                                            "Вставка выполнена не полностью",
                                                            {
                                                                description,
                                                            },
                                                        )
                                                    }
                                                    onSelectionChange={handleSelectionChange}
                                                    onViewStatsChange={setGridStats}
                                                    onClipboardStateChange={handleClipboardState}
                                                    onLoadedRowsChange={handleLoadedRowsChange}
                                                    headerPinned={planHeaderPinned}
                                                    showServiceColumns={planServiceColumns}
                                                    summaryValues={summaryValues}
                                                    rowPatches={rowPatches}
                                                    scrollToPosition={scrollToPosition}
                                                    activeFilterColumns={activeFilterColumns}
                                                    onOpenColumnFilter={(column, anchor) =>
                                                        setColumnFilterTarget({
                                                            column,
                                                            label:
                                                                meta.fields.find(
                                                                    (field) =>
                                                                        field.dbName === column,
                                                                )?.label ?? column,
                                                            anchor,
                                                        })
                                                    }
                                                    onOpenRowFilter={setRowFilterTarget}
                                                />
                                            </GridErrorBoundary>
                                            {planTotals.total === 0 ? (
                                                <div className="grid-empty">
                                                    В этом плане пока нет строк.
                                                </div>
                                            ) : null}
                                        </>
                                    ) : (
                                        <div className="grid-empty">
                                            <Spinner size="sm" />
                                            <Typography>Загрузка плана ремонта...</Typography>
                                        </div>
                                    )
                                ) : (
                                    <RenovationDictionarySection
                                        section={dictSection ?? RENOVATION_ASSETS_SECTION}
                                        room={room}
                                        assets={meta.assets}
                                        catalogs={meta.catalogs}
                                        collabEnabled={collabEnabled}
                                        refreshKey={reloadKey}
                                        remoteCellValues={remoteCellValues}
                                        onDeleteState={setDictDeleteState}
                                        onExportState={setDictExportState}
                                        onResetState={setDictResetState}
                                        onUserCreateState={setDictUserCreateState}
                                        headerPinned={dictHeaderPinned}
                                        showServiceColumns={dictServiceColumns}
                                        onClipboardStateChange={handleDictClipboardState}
                                        onUndoHistoryChanged={() => {
                                            setUndoCount((count) => count + 1);
                                            setRedoCount(0);
                                        }}
                                        onAssetsChanged={refreshMeta}
                                    />
                                )}
                            </div>
                            {footer}
                        </Card.Content>
                    </Card>
                </section>
            </div>

            {isFullscreen && !isFullscreenPending ? (
                <>
                    <div className={revealClass("top")}>
                        <div className="fullscreen-panel fullscreen-panel--top">
                            {header}
                            {toolbar}
                        </div>
                    </div>
                    <div className={revealClass("bottom")}>
                        <div className="fullscreen-panel fullscreen-panel--bottom">{footer}</div>
                    </div>
                </>
            ) : null}

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
                <ColumnFilterPopover
                    column={columnFilterTarget.column}
                    columnLabel={columnFilterTarget.label}
                    anchor={columnFilterTarget.anchor}
                    {...(viewPlanId && { planId: viewPlanId })}
                    asset={assetFilter}
                    initial={columnFilters[columnFilterTarget.column] ?? null}
                    onApply={applyColumnFilter(columnFilterTarget.column)}
                    onClose={() => setColumnFilterTarget(null)}
                />
            ) : null}
            {rowFilterTarget ? (
                <RowGoToFilter
                    anchor={rowFilterTarget}
                    total={planTotals?.total ?? 0}
                    onGoTo={(position) => void gridRef.current?.scrollToPosition(position)}
                    onClose={() => setRowFilterTarget(null)}
                />
            ) : null}
        </main>
    );
}

function AddRowDialog({
    isOpen,
    isPending,
    assets,
    selectedKey,
    onSelectionChange,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    assets: Array<{ id: string; name: string }>;
    selectedKey: string | null;
    onSelectionChange: (key: string | null) => void;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="success">
                                <Plus />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Добавить строку плана</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            <div className="add-row-dialog__field">
                                <SearchAutocomplete
                                    placeholder="Предприятие"
                                    compactLabel="Предприятие"
                                    items={assets}
                                    selectedKey={selectedKey}
                                    onSelectionChange={onSelectionChange}
                                />
                                <p className="add-row-dialog__hint">
                                    Пустая строка для выбранного предприятия будет добавлена в конец
                                    плана.
                                </p>
                            </div>
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button
                                variant="tertiary"
                                onPress={() => onOpenChange(false)}
                                isDisabled={isPending}
                            >
                                Отмена
                            </Button>
                            <Button
                                variant="primary"
                                onPress={onConfirm}
                                isDisabled={isPending || selectedKey === null}
                            >
                                {isPending ? <Spinner color="current" size="sm" /> : <Plus />}
                                Добавить
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}

function SubmitPlanDialog({
    isOpen,
    isPending,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="warning">
                                <Send />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                Отправить план на согласование?
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            Текущая версия плана будет зафиксирована и передана на согласование.
                            Правки в неё будут недоступны до завершения согласования.
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button
                                variant="tertiary"
                                onPress={() => onOpenChange(false)}
                                isDisabled={isPending}
                            >
                                Отмена
                            </Button>
                            <Button variant="primary" onPress={onConfirm} isDisabled={isPending}>
                                {isPending ? <Spinner color="current" size="sm" /> : <Send />}
                                Отправить
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}

function SubmitBlockedDialog({
    isOpen,
    message,
    onOpenChange,
}: {
    isOpen: boolean;
    message: string;
    onOpenChange: (open: boolean) => void;
}) {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="danger">
                                <WarningTriangle />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                План нельзя отправить на согласование
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>{message}</AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button variant="primary" onPress={() => onOpenChange(false)}>
                                Понятно
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}

function ApprovePlanDialog({
    isOpen,
    isPending,
    pendingCreatedBy,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    pendingCreatedBy: string | null;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable={!isPending}>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="success">
                                <CheckCircle />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Согласовать план?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            {pendingCreatedBy
                                ? `Версия, отправленная ${pendingCreatedBy}, будет утверждена и заархивирована, после чего откроется новый рабочий план.`
                                : "Версия будет утверждена и заархивирована, после чего откроется новый рабочий план."}
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button
                                variant="tertiary"
                                onPress={() => onOpenChange(false)}
                                isDisabled={isPending}
                            >
                                Отмена
                            </Button>
                            <Button variant="primary" onPress={onConfirm} isDisabled={isPending}>
                                {isPending ? (
                                    <Spinner color="current" size="sm" />
                                ) : (
                                    <CheckCircle />
                                )}
                                Согласовать
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}
