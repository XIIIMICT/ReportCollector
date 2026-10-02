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
    deleteRenovationRow,
    exportRenovationPlan,
    getRenovationDiff,
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
    submitRenovationPlan,
    undoCells,
    updateRenovationCells,
    type RenovationColumnFilter,
    type RenovationDiff,
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
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";
import { SectionTabs } from "@/shared/grid/section-tabs";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { isFromTextEditor } from "@/shared/keyboard";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import { ColumnFilterPopover } from "@/shared/ui/column-filter-popover";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { RowRangeFilter } from "@/shared/ui/row-range-filter";
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
// ── Sliding-window lazy loading of the working plan ──────────────────────────
// The plan holds ~94k rows — too much for one request or for memory, so the
// grid renders a contiguous WINDOW of rows: chunks arrive as the user scrolls
// towards the edges and the far side is evicted, with the grid's scroll
// position compensated by the evicted height. Row numbering is the stored
// position («№»), so windowing is invisible in the UI. Archived versions are
// read-only comparisons and stay full reads.
// 100 строк на чанк. Первым запросом читается ровно один чанк — его пользователь
// видит сразу; следующим — BUFFER_CHUNKS над ним (см. useEffect ниже). Дальше
// окно растит по краям: пока сверху/снизу от видимой области есть BUFFER_CHUNKS
// буферных чанков, догрузки нет; окно никогда не держит больше WINDOW_MAX_ROWS.
const WINDOW_CHUNK = 100;
/** Chunk-буферов держится над и под видимой областью до начала догрузки. */
const BUFFER_CHUNKS = 2;
const WINDOW_MAX_ROWS = WINDOW_CHUNK * 5;

type PlanWindow = {
    id: string;
    rows: RenovationPlanRow[];
    /** Offset of rows[0] in the filtered plan (0 for archive full reads). */
    start: number;
    total: number;
    /** Whole-plan row count (ignoring the asset filter) — «всего» counter. */
    totalAll: number | null;
    baselineBoundary: number | null;
    /** Archive full read — no lazy loading. */
    complete: boolean;
};

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
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
    // Row-number («№») range filter: `null` on a bound means «from the first» /
    // «to the last». Applied server-side so the window and the counter agree.
    const [positionFilter, setPositionFilter] = useState<{
        from: number | null;
        to: number | null;
    } | null>(null);
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
    // The loaded window of the working plan (see PlanWindow above). `refresh()`
    // reloads it in place (cell edits, undo/redo, remote row changes keep the
    // anchor); switching the version or the asset filter resets it to the top.
    const [planWindow, setPlanWindow] = useState<PlanWindow | null>(null);
    const [planError, setPlanError] = useState<string | null>(null);
    // Loading state for showing skeleton rows during chunk fetch
    const [loadingEdge, setLoadingEdge] = useState<"top" | "bottom" | null>(null);
    // Numeric totals of the filtered plan — the pinned summary row.
    const [summaryValues, setSummaryValues] = useState<Record<string, number> | null>(null);
    const windowGenerationRef = useRef(0);
    const planWindowRef = useRef<PlanWindow | null>(null);
    const loadingBottomRef = useRef(false);
    const loadingTopRef = useRef(false);
    // After a structural load the grid jumps to this edge of the loaded data:
    // the working plan anchors at its TAIL (rows are appended at the end), the
    // archive reads top-down.
    const [scrollToEdge, setScrollToEdge] = useState<{
        edge: "top" | "bottom";
        token: number;
    } | null>(null);
    const scrollToEdgeTokenRef = useRef(0);

    // Structural reset: a new plan version or a new asset filter restarts the
    // window. The working plan anchors at its tail — the first request reads
    // the total (one row), the second fetches the last WINDOW_CHUNK rows, so
    // the user already sees the tail while the buffer loads; a third request
    // then fetches BUFFER_CHUNKS more chunks ABOVE the tail in one go. Bumping
    // the generation discards in-flight chunks.
    useEffect(() => {
        if (!meta) return;
        // The window belongs to the plan section only; on a dictionary there is
        // nothing to load and the working-plan read would 404 («Нет плана в
        // состоянии working») into an error card over the dictionary.
        if (!isPlanSection) {
            setPlanError(null);
            planWindowRef.current = null;
            startTransition(() => {
                setPlanWindow(null);
            });
            return;
        }
        const generation = ++windowGenerationRef.current;
        loadingBottomRef.current = false;
        loadingTopRef.current = false;
        let cancelled = false;
        setPlanError(null);
        void (async () => {
            try {
                // The planId/undefined pair must match in all three reads —
                // they describe the same plan view.
                const planArg = viewPlanId ?? undefined;
                if (viewPlanId) {
                    // Archive/pending view: lazy-load a tail window too — a full
                    // read of a ~94k-row plan is slow and fills memory. Reuse the
                    // same getRenovationPlanRows(offset, limit, planId, asset)
                    // path as the working window.
                    const head = await getRenovationPlanRows(
                        0,
                        1,
                        planArg,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (cancelled || generation !== windowGenerationRef.current) return;
                    const total = head.total;
                    const start = Math.max(0, total - WINDOW_CHUNK);
                    const page = await getRenovationPlanRows(
                        start,
                        WINDOW_CHUNK,
                        planArg,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (cancelled || generation !== windowGenerationRef.current) return;
                    const next: PlanWindow = {
                        id: page.id,
                        rows: page.rows,
                        start,
                        total: page.total,
                        totalAll: page.totalAll ?? null,
                        baselineBoundary: page.baselineBoundary ?? null,
                        complete: false,
                    };
                    planWindowRef.current = next;
                    startTransition(() => {
                        setPlanWindow(next);
                    });
                    scrollToEdgeTokenRef.current += 1;
                    setScrollToEdge({ edge: "bottom", token: scrollToEdgeTokenRef.current });
                } else if (meta?.workingPlanId == null && isPlanSection) {
                    // No working plan — right after submit, or a cold load with
                    // only pending/archived versions. Only on the plan section:
                    // auto-select the pending plan (the most relevant) and let the
                    // effect re-run its archive branch. Guarded by isPlanSection so
                    // leaving into a dictionary (which resets viewPlanId to null)
                    // doesn't re-select the pending plan and fight the reset — that
                    // toggle (null ↔ pending) would loop past the update-depth guard.
                    const nextPlanId = meta?.pendingPlanId ?? null;
                    if (nextPlanId && nextPlanId !== viewPlanId) {
                        setViewPlanId(nextPlanId);
                    }
                } else {
                    const head = await getRenovationPlanRows(
                        0,
                        1,
                        planArg,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (cancelled || generation !== windowGenerationRef.current) return;
                    const total = head.total;
                    const start = Math.max(0, total - WINDOW_CHUNK);
                    const page = await getRenovationPlanRows(
                        start,
                        WINDOW_CHUNK,
                        planArg,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (cancelled || generation !== windowGenerationRef.current) return;
                    const next: PlanWindow = {
                        id: page.id,
                        rows: page.rows,
                        start,
                        total: page.total,
                        totalAll: page.totalAll ?? null,
                        baselineBoundary: page.baselineBoundary ?? null,
                        complete: false,
                    };
                    planWindowRef.current = next;
                    startTransition(() => {
                        setPlanWindow(next);
                    });
                    scrollToEdgeTokenRef.current += 1;
                    setScrollToEdge({ edge: "bottom", token: scrollToEdgeTokenRef.current });
                }
                // The tail chunk is on screen — fill the buffer above it in ONE
                // request while the user reads. Without the generation re-check
                // a fast filter switch could merge stale rows into a new window.
                const base = planWindowRef.current;
                if (!base || base.start <= 0) return;
                if (generation !== windowGenerationRef.current || cancelled) return;
                const bufferStart = Math.max(0, base.start - BUFFER_CHUNKS * WINDOW_CHUNK);
                const buffered = await getRenovationPlanRows(
                    bufferStart,
                    base.start - bufferStart,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                    positionFilter?.from ?? null,
                    positionFilter?.to ?? null,
                );
                if (cancelled || generation !== windowGenerationRef.current) return;
                applyWindowChunk(
                    buffered.rows,
                    bufferStart,
                    "top",
                    buffered.total,
                    buffered.baselineBoundary ?? null,
                    buffered.id,
                );
                // The buffer prepend replaces the source, so the tail jump issued
                // for the single tail chunk may have settled on the shorter window.
                // Re-issue it so the grid lands on the last row of the full window.
                scrollToEdgeTokenRef.current += 1;
                setScrollToEdge({ edge: "bottom", token: scrollToEdgeTokenRef.current });
            } catch (reason) {
                if (!cancelled && generation === windowGenerationRef.current) {
                    setPlanError(errorMessage(reason));
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [meta, viewPlanId, assetFilter, activeColumnFilters, positionFilter, isPlanSection]);

    // Pinned summary row: an aggregating query over the same scope as the row
    // window (asset + header + row-number filters), refreshed with them.
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
            positionFrom: positionFilter?.from ?? null,
            positionTo: positionFilter?.to ?? null,
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
    }, [meta, viewPlanId, assetFilter, activeColumnFilters, positionFilter, isPlanSection]);

    // In-place reload (refresh()): re-read the current window, keep the anchor.
    // The working and the pending («на согласовании») views re-read their own
    // window (so recalced cells like the renovation ID appear in place); only a
    // read-only archive skips the re-read.
    useEffect(() => {
        if (reloadKey === 0 || isArchive) return;
        const base = planWindowRef.current;
        if (!base) return;
        const generation = windowGenerationRef.current;
        let cancelled = false;
        void (async () => {
            try {
                const limit = Math.max(base.rows.length, WINDOW_CHUNK);
                const page = await getRenovationPlanRows(
                    base.start,
                    limit,
                    viewPlanId ?? undefined,
                    assetFilter,
                    activeColumnFilters,
                    positionFilter?.from ?? null,
                    positionFilter?.to ?? null,
                );
                if (!cancelled && generation === windowGenerationRef.current) {
                    const prev = planWindowRef.current;
                    if (!prev) return;
                    const next: PlanWindow = {
                        ...prev,
                        id: page.id,
                        rows: page.rows,
                        total: page.total,
                        baselineBoundary: page.baselineBoundary ?? null,
                    };
                    planWindowRef.current = next;
                    startTransition(() => {
                        setPlanWindow(next);
                    });
                }
            } catch {
                // Keep the current rows; the next refresh retries.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [reloadKey, assetFilter, viewPlanId, activeColumnFilters, positionFilter]);

    /** Merge a fetched chunk into the window; evict the far side when it overflows. */
    const applyWindowChunk = useCallback(
        (
            chunk: RenovationPlanRow[],
            chunkStart: number,
            direction: "top" | "bottom",
            total: number,
            baselineBoundary: number | null,
            id: string,
        ) => {
            // The ref is the synchronous source of truth: edge events can fire
            // before React commits the previous chunk, and reading state would
            // fetch the same chunk twice.
            const prev = planWindowRef.current;
            if (!prev) return;
            const baseEnd = prev.start + prev.rows.length;
            const chunkEnd = chunkStart + chunk.length;
            const start = Math.min(prev.start, chunkStart);
            const end = Math.max(baseEnd, chunkEnd);
            // Offset-indexed fill; fresh chunk rows win over stale base rows.
            const merged: Array<RenovationPlanRow | undefined> = Array.from({
                length: end - start,
            });
            for (let i = 0; i < prev.rows.length; i += 1) {
                merged[prev.start + i - start] = prev.rows[i];
            }
            for (let i = 0; i < chunk.length; i += 1) {
                merged[chunkStart + i - start] = chunk[i];
            }
            // Trim possible holes from racing fetches: keep the contiguous
            // run around the chunk.
            let first = 0;
            while (first < merged.length && merged[first] === undefined) first += 1;
            let last = merged.length - 1;
            while (last >= 0 && merged[last] === undefined) last -= 1;
            let rows = (
                first <= last
                    ? merged.slice(first, last + 1).filter((row) => row !== undefined)
                    : []
            ) as RenovationPlanRow[];
            let newStart = start + first;
            if (rows.length > WINDOW_MAX_ROWS) {
                if (direction === "top") {
                    // Prepend: trim the overflow from the far (bottom) side.
                    rows = rows.slice(0, WINDOW_MAX_ROWS);
                } else {
                    // Append: trim the overflow from the top.
                    const evict = rows.length - WINDOW_MAX_ROWS;
                    rows = rows.slice(evict);
                    newStart += evict;
                }
            }
            const next: PlanWindow = {
                id,
                rows,
                start: newStart,
                total,
                totalAll: prev.totalAll,
                baselineBoundary,
                complete: false,
            };
            planWindowRef.current = next;
            startTransition(() => {
                setPlanWindow(next);
            });
        },
        [],
    );

    const loadChunk = useCallback(
        async (direction: "top" | "bottom") => {
            const base = planWindowRef.current;
            if (!base || base.complete) return;
            const generation = windowGenerationRef.current;

            // Show loading indicator
            setLoadingEdge(direction);

            try {
                if (direction === "bottom") {
                    const chunkStart = base.start + base.rows.length;
                    if (chunkStart >= base.total) return;
                    const page = await getRenovationPlanRows(
                        chunkStart,
                        WINDOW_CHUNK,
                        viewPlanId ?? undefined,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (generation !== windowGenerationRef.current) return;
                    applyWindowChunk(
                        page.rows,
                        chunkStart,
                        "bottom",
                        page.total,
                        page.baselineBoundary ?? null,
                        page.id,
                    );
                } else {
                    const chunkStart = Math.max(0, base.start - WINDOW_CHUNK);
                    if (chunkStart >= base.start) return;
                    const page = await getRenovationPlanRows(
                        chunkStart,
                        base.start - chunkStart,
                        viewPlanId ?? undefined,
                        assetFilter,
                        activeColumnFilters,
                        positionFilter?.from ?? null,
                        positionFilter?.to ?? null,
                    );
                    if (generation !== windowGenerationRef.current) return;
                    applyWindowChunk(
                        page.rows,
                        chunkStart,
                        "top",
                        page.total,
                        page.baselineBoundary ?? null,
                        page.id,
                    );
                }
            } catch {
                // Chunk fetch failed — the next edge approach retries.
            } finally {
                // Hide loading indicator
                setLoadingEdge(null);
            }
        },
        [applyWindowChunk, assetFilter, viewPlanId, activeColumnFilters, positionFilter],
    );

    const handleNearBottom = useCallback(() => {
        if (loadingBottomRef.current) return;
        const base = planWindowRef.current;
        if (!base || base.complete) return;
        if (base.start + base.rows.length >= base.total) return;
        loadingBottomRef.current = true;
        void loadChunk("bottom").finally(() => {
            loadingBottomRef.current = false;
        });
    }, [loadChunk]);

    const handleNearTop = useCallback(() => {
        if (loadingTopRef.current) return;
        const base = planWindowRef.current;
        if (!base || base.complete) return;
        if (base.start <= 0) return;
        loadingTopRef.current = true;
        void loadChunk("top").finally(() => {
            loadingTopRef.current = false;
        });
    }, [loadChunk]);
    const { data: diff } = useAsyncData<RenovationDiff | null>(
        () => (viewPlanId ? getRenovationDiff(viewPlanId) : Promise.resolve(null)),
        [metaReloadKey, reloadKey, viewPlanId],
    );

    const hasMoreTop = !!planWindow && !planWindow.complete && planWindow.start > 0;
    const hasMoreBottom =
        !!planWindow &&
        !planWindow.complete &&
        planWindow.start + planWindow.rows.length < planWindow.total;

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

    // The grid rows: plan rows with diff states plus deleted-row ghosts from
    // the comparison plan (the version being compared to).
    // Stable identity cache to prevent unnecessary re-renders
    const gridRowsCache = useRef(new Map<string, RenovationGridRow>());

    const gridRows = useMemo<RenovationGridRow[]>(() => {
        if (!planWindow) return [];

        const cache = gridRowsCache.current;
        const nextCache = new Map<string, RenovationGridRow>();
        const result: RenovationGridRow[] = [];
        const diffByRow = new Map((diff?.rows ?? []).map((row) => [row.rowId, row]));

        for (const row of planWindow.rows) {
            const diffRow = diffByRow.get(row.id);
            const rowState: RenovationGridRow["__rowState"] = diffRow
                ? diffRow.isNew
                    ? "new"
                    : "changed"
                : "unchanged";

            // Build cache key from row data that affects rendering
            const changedFields = diffRow?.cells.map((cell) => cell.field) ?? [];
            const cacheKey = `${row.id}|${row.revision}|${rowState}|${changedFields.join(",")}`;

            const cached = cache.get(cacheKey);
            if (cached) {
                // Reuse cached row object
                nextCache.set(cacheKey, cached);
                result.push(cached);
                continue;
            }

            // Create new row object
            const gridRow: RenovationGridRow = {
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
                __rowState: rowState,
                __changedFields: changedFields,
                __missingComponents: row.missingComponents,
            };

            nextCache.set(cacheKey, gridRow);
            result.push(gridRow);
        }

        if (diff) {
            // Deleted ghosts come from the diff itself (the previous version's
            // rows), so no full plan read is needed to render them.
            for (const row of diff.deletedRows) {
                const cacheKey = `${row.id}|${row.revision}|deleted`;
                const cached = cache.get(cacheKey);

                if (cached) {
                    nextCache.set(cacheKey, cached);
                    result.push(cached);
                    continue;
                }

                const gridRow: RenovationGridRow = {
                    ...row.values,
                    col_003: row.renovationId,
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
                    __rowState: "deleted",
                    __changedFields: [],
                    __missingComponents: row.missingComponents,
                };

                nextCache.set(cacheKey, gridRow);
                result.push(gridRow);
            }
        }

        result.sort((a, b) => a.__position - b.__position);
        gridRowsCache.current = nextCache;
        return result;
    }, [diff, planWindow?.id, planWindow?.rows, planWindow?.start]);

    // Remote cell values (live edits of other users) override the local ones
    // for display — the working plan only.
    // Stable row identity map to prevent unnecessary re-renders
    const rowIdentityCache = useRef(new Map<string, RenovationGridRow>());

    const displayRows = useMemo(() => {
        if (isArchive || remoteCellValues.size === 0) {
            // Clear cache when no remote edits
            rowIdentityCache.current.clear();
            return gridRows;
        }

        const cache = rowIdentityCache.current;
        const nextCache = new Map<string, RenovationGridRow>();

        const result = gridRows.map((row) => {
            // Build cache key from row id + remote values for this row
            const remoteKeys: string[] = [];
            for (const field of editableFieldKeys) {
                const value = remoteCellValues.get(canonicalRenovationKey(row.__id, field));
                if (value !== undefined) {
                    remoteKeys.push(`${field}:${JSON.stringify(value)}`);
                }
            }

            if (remoteKeys.length === 0) {
                // No remote edits for this row
                nextCache.set(row.__id, row);
                return row;
            }

            // Cache key: row id + sorted remote field values
            const cacheKey = `${row.__id}|${remoteKeys.sort().join("|")}`;
            const cached = cache.get(cacheKey);

            if (cached) {
                // Reuse cached merged row
                nextCache.set(cacheKey, cached);
                return cached;
            }

            // Create new merged row
            const merged = { ...row };
            for (const field of editableFieldKeys) {
                const value = remoteCellValues.get(canonicalRenovationKey(row.__id, field));
                if (value !== undefined) {
                    merged[field] = value;
                }
            }

            nextCache.set(cacheKey, merged);
            return merged;
        });

        rowIdentityCache.current = nextCache;
        return result;
    }, [editableFieldKeys, gridRows, isArchive, remoteCellValues]);

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
            try {
                // «Видимая» — the whole plan filtered server-side by the active
                // asset and block, not just the window currently rendered.
                const visible = scope === "visible";
                const { blob, fileName } = await exportRenovationPlan({
                    planId: viewPlanId ?? undefined,
                    asset: visible ? assetFilter : null,
                    block: visible ? blockFilter : null,
                });
                downloadBlob(blob, fileName);
                toast.success("Файл выгружен", { description: fileName });
            } catch (reason) {
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
                    setPositionFilter(null);
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

    const selectedRow = useMemo(
        () => displayRows.find((row) => row.__id === selection?.rowId) ?? null,
        [displayRows, selection?.rowId],
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
        (planWindow?.total ?? 0) > 0;
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
                                    planWindow ? (
                                        <>
                                            <GridErrorBoundary>
                                                <RenovationGrid
                                                    ref={gridRef}
                                                    fields={meta.fields}
                                                    headerCells={meta.headerCells}
                                                    catalogs={meta.catalogs}
                                                    rows={displayRows}
                                                    assetFilter={assetFilterSet}
                                                    blockFilter={blockFilter}
                                                    baselineBoundary={planWindow.baselineBoundary}
                                                    readOnly={readOnly}
                                                    canEdit={canEditRows}
                                                    editableFieldKeys={editableFieldKeys}
                                                    userAssetIds={userAssetIds}
                                                    remoteLocks={remoteLocks}
                                                    remotePresence={remotePresence}
                                                    remoteCellInfo={remoteCellInfo}
                                                    loadingEdge={loadingEdge}
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
                                                    headerPinned={planHeaderPinned}
                                                    showServiceColumns={planServiceColumns}
                                                    hasMoreTop={hasMoreTop}
                                                    hasMoreBottom={hasMoreBottom}
                                                    onNearTop={handleNearTop}
                                                    onNearBottom={handleNearBottom}
                                                    scrollToEdge={scrollToEdge}
                                                    totalRowsOverride={planWindow.total}
                                                    totalAllRowsOverride={planWindow.totalAll}
                                                    summaryValues={summaryValues}
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
                                                    rowFilterActive={positionFilter !== null}
                                                />
                                            </GridErrorBoundary>
                                            {displayRows.length === 0 ? (
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
                <RowRangeFilter
                    anchor={rowFilterTarget}
                    initial={positionFilter}
                    onApply={setPositionFilter}
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
