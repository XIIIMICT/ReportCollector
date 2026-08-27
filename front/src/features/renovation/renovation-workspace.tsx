"use client";

import {
    AlertDialog,
    Button,
    ButtonGroup,
    Card,
    Kbd,
    ListBox,
    Select,
    Skeleton,
    Spinner,
    Tabs,
    toast,
    Tooltip,
    Typography,
} from "@heroui/react";
import { CheckCircle, Copy, PasteClipboard, Plus, Redo, Send, Trash, Undo } from "iconoir-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    AppHeader,
    CompactActionMenu,
    DeleteRecordDialog,
    downloadBlob,
    groupActionItems,
    type ActionItem,
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
    getRenovationPlan,
    getRenovationPlanRows,
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
    type RenovationDiff,
    type RenovationMeta,
    type RenovationPlan,
    type RenovationPlanRow,
    type RenovationPlanVersion,
    type RenovationUndoResult,
} from "@/shared/api/renovation";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import { canonicalCellKey, useCollabRoom } from "@/shared/collab/use-collab-room";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { FormStatus } from "@/shared/ui/form-status";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
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
};

const WORKING_VERSION_ID = "__working";
// ── Sliding-window lazy loading of the working plan ──────────────────────────
// The plan holds ~94k rows — too much for one request or for memory, so the
// grid renders a contiguous WINDOW of rows: chunks arrive as the user scrolls
// towards the edges and the far side is evicted, with the grid's scroll
// position compensated by the evicted height. Row numbering is the stored
// position («№»), so windowing is invisible in the UI. Archived versions are
// read-only comparisons and stay full reads.
const WINDOW_CHUNK = 300;
const WINDOW_MAX_ROWS = 1200;

type PlanWindow = {
    id: string;
    rows: RenovationPlanRow[];
    /** Offset of rows[0] in the filtered plan (0 for archive full reads). */
    start: number;
    total: number;
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
 * The values are the backend RBlock keys (tashpaev, borodiichuk, babaeva).
 */
const BLOCK_LABELS: Record<string, string> = {
    tashpaev: "1) Блок Ташпаева",
    borodiichuk: "2) Блок Бородийчука",
    babaeva: "3) Блок Бабаевой",
};
/** Preferred dropdown order (after «Все блоки»). */
const BLOCK_ORDER = ["tashpaev", "borodiichuk", "babaeva"];

export function RenovationWorkspace({
    currentUser,
    activeProjectKey,
    onProjectChange,
}: RenovationWorkspaceProps) {
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const [reloadKey, setReloadKey] = useState(0);
    const [assetFilter, setAssetFilter] = useState<string | null>(null);
    const [blockFilter, setBlockFilter] = useState<string | null>(null);
    const { data: meta, error } = useAsyncData<RenovationMeta | null>(
        () => getRenovationMeta().catch(() => null),
        [metaReloadKey],
    );
    const { data: versions, error: versionsError } = useAsyncData<RenovationPlanVersion[] | null>(
        () => getRenovationPlans().catch(() => null),
        [metaReloadKey],
    );
    const [viewPlanId, setViewPlanId] = useState<string | null>(null);
    // The loaded window of the working plan (see PlanWindow above). `refresh()`
    // reloads it in place (cell edits, undo/redo, remote row changes keep the
    // anchor); switching the version or the asset filter resets it to the top.
    const [planWindow, setPlanWindow] = useState<PlanWindow | null>(null);
    const [planError, setPlanError] = useState<string | null>(null);
    const [scrollCompensation, setScrollCompensation] = useState<{
        deltaRows: number;
        token: number;
    } | null>(null);
    const windowGenerationRef = useRef(0);
    const planWindowRef = useRef<PlanWindow | null>(null);
    const loadingBottomRef = useRef(false);
    const loadingTopRef = useRef(false);
    const compensationTokenRef = useRef(0);
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
    // the total (one row), the second fetches the last WINDOW_CHUNK rows.
    // Bumping the generation discards in-flight chunks.
    useEffect(() => {
        if (!meta) return;
        const generation = ++windowGenerationRef.current;
        loadingBottomRef.current = false;
        loadingTopRef.current = false;
        let cancelled = false;
        setPlanError(null);
        void (async () => {
            try {
                if (viewPlanId) {
                    const full = await getRenovationPlan(viewPlanId);
                    if (!cancelled && generation === windowGenerationRef.current) {
                        const next: PlanWindow = {
                            id: full.id,
                            rows: full.rows,
                            start: 0,
                            total: full.rows.length,
                            baselineBoundary: full.baselineBoundary ?? null,
                            complete: true,
                        };
                        planWindowRef.current = next;
                        setPlanWindow(next);
                        scrollToEdgeTokenRef.current += 1;
                        setScrollToEdge({ edge: "top", token: scrollToEdgeTokenRef.current });
                    }
                } else if (meta?.workingPlanId == null) {
                    // No working plan — right after submit, or a cold load with
                    // only pending/archived versions. Reading the working window
                    // would 404 («Нет плана в состоянии working»), so auto-select
                    // the pending plan (the most relevant) and let the effect
                    // re-run its archive branch. With nothing to show, the grid
                    // stays empty.
                    const nextPlanId = meta?.pendingPlanId ?? null;
                    if (nextPlanId && nextPlanId !== viewPlanId) {
                        setViewPlanId(nextPlanId);
                    }
                } else {
                    const head = await getRenovationPlanRows(0, 1, undefined, assetFilter);
                    if (cancelled || generation !== windowGenerationRef.current) return;
                    const total = head.total;
                    const start = Math.max(0, total - WINDOW_CHUNK);
                    if (start <= 0) {
                        // The whole plan fits into one window.
                        const page = await getRenovationPlanRows(
                            0,
                            WINDOW_CHUNK,
                            undefined,
                            assetFilter,
                        );
                        if (cancelled || generation !== windowGenerationRef.current) return;
                        const next: PlanWindow = {
                            id: page.id,
                            rows: page.rows,
                            start: 0,
                            total: page.total,
                            baselineBoundary: page.baselineBoundary ?? null,
                            complete: false,
                        };
                        planWindowRef.current = next;
                        setPlanWindow(next);
                    } else {
                        const page = await getRenovationPlanRows(
                            start,
                            WINDOW_CHUNK,
                            undefined,
                            assetFilter,
                        );
                        if (cancelled || generation !== windowGenerationRef.current) return;
                        const next: PlanWindow = {
                            id: page.id,
                            rows: page.rows,
                            start,
                            total: page.total,
                            baselineBoundary: page.baselineBoundary ?? null,
                            complete: false,
                        };
                        planWindowRef.current = next;
                        setPlanWindow(next);
                    }
                    scrollToEdgeTokenRef.current += 1;
                    setScrollToEdge({ edge: "bottom", token: scrollToEdgeTokenRef.current });
                }
            } catch (reason) {
                if (!cancelled && generation === windowGenerationRef.current) {
                    setPlanError(errorMessage(reason));
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [meta, viewPlanId, assetFilter]);

    // In-place reload (refresh()): re-read the current window, keep the anchor.
    // Only the WORKING view re-reads its window — when viewing an archive (or
    // right after submit, when the working plan is gone) the reset effect owns
    // the load; re-reading the stale working window here would hit a 404.
    useEffect(() => {
        if (reloadKey === 0 || viewPlanId !== null) return;
        const base = planWindowRef.current;
        if (!base) return;
        const generation = windowGenerationRef.current;
        let cancelled = false;
        void (async () => {
            try {
                if (base.complete) {
                    const full = await getRenovationPlan(base.id);
                    if (!cancelled && generation === windowGenerationRef.current) {
                        const next: PlanWindow = {
                            id: full.id,
                            rows: full.rows,
                            start: 0,
                            total: full.rows.length,
                            baselineBoundary: full.baselineBoundary ?? null,
                            complete: true,
                        };
                        planWindowRef.current = next;
                        setPlanWindow(next);
                    }
                } else {
                    const limit = Math.max(base.rows.length, WINDOW_CHUNK);
                    const page = await getRenovationPlanRows(
                        base.start,
                        limit,
                        undefined,
                        assetFilter,
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
                        setPlanWindow(next);
                    }
                }
            } catch {
                // Keep the current rows; the next refresh retries.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [reloadKey, assetFilter, viewPlanId]);

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
            let deltaRows = 0;
            if (rows.length > WINDOW_MAX_ROWS) {
                if (direction === "top") {
                    // Prepend: rows above the viewport grew — shift the scroll
                    // down by the same height; the overflow is trimmed from
                    // the far (bottom) side, which needs no compensation.
                    deltaRows = chunk.length;
                    rows = rows.slice(0, WINDOW_MAX_ROWS);
                } else {
                    const evict = rows.length - WINDOW_MAX_ROWS;
                    rows = rows.slice(evict);
                    newStart += evict;
                    // Append overflow trims the TOP — the content above the
                    // viewport shrank, move the scroll up accordingly.
                    deltaRows = -evict;
                }
            }
            const next: PlanWindow = {
                id,
                rows,
                start: newStart,
                total,
                baselineBoundary,
                complete: false,
            };
            planWindowRef.current = next;
            if (deltaRows !== 0) {
                compensationTokenRef.current += 1;
                setScrollCompensation({ deltaRows, token: compensationTokenRef.current });
            }
            setPlanWindow(next);
        },
        [],
    );

    const loadChunk = useCallback(
        async (direction: "top" | "bottom") => {
            const base = planWindowRef.current;
            if (!base || base.complete) return;
            const generation = windowGenerationRef.current;
            try {
                if (direction === "bottom") {
                    const chunkStart = base.start + base.rows.length;
                    if (chunkStart >= base.total) return;
                    const page = await getRenovationPlanRows(
                        chunkStart,
                        WINDOW_CHUNK,
                        undefined,
                        assetFilter,
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
                        undefined,
                        assetFilter,
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
            }
        },
        [applyWindowChunk, assetFilter],
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
    const comparedToPlanId = viewPlanId ? (diff?.comparedToPlanId ?? null) : null;
    const { data: comparisonPlan } = useAsyncData<RenovationPlan | null>(
        () => (comparedToPlanId ? getRenovationPlan(comparedToPlanId) : Promise.resolve(null)),
        [comparedToPlanId, metaReloadKey, reloadKey],
    );

    const hasMoreTop = !!planWindow && !planWindow.complete && planWindow.start > 0;
    const hasMoreBottom =
        !!planWindow &&
        !planWindow.complete &&
        planWindow.start + planWindow.rows.length < planWindow.total;

    const [user, setUser] = useState(currentUser);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isFullscreenPending, setIsFullscreenPending] = useState(false);
    const [gridStats, setGridStats] = useState<GridViewStats | null>(null);
    const [activeSection, setActiveSection] = useState<string>("plan");
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
    // Display toggles for the active grid (rendered in the view strip). The
    // plan grid starts pinned; dictionaries start unpinned, as before.
    const [planHeaderPinned, setPlanHeaderPinned] = useState(true);
    const [planServiceColumns, setPlanServiceColumns] = useState(false);
    const [dictHeaderPinned, setDictHeaderPinned] = useState(false);
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
    const remoteCellInfoRef = useRef<Map<string, { name: string; label: string | null }>>(
        new Map(),
    );
    const remoteCellInfoKeyRef = useRef<string>("");
    const remoteLocksRef = useRef<Map<string, { fullname: string; email: string }>>(new Map());
    const remoteLocksKeyRef = useRef<string>("");
    const remotePresenceRef = useRef<Map<string, { fullname: string; email: string }>>(new Map());
    const remotePresenceKeyRef = useRef<string>("");
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
    const isAdmin = isProjectAdmin(activeProject) || roles.includes("admin");
    const isApprover = roles.includes("approver");
    const isBlockOwner = roles.includes("block_owner");
    const canSubmit = isAdmin || isBlockOwner;
    const canApprove = isAdmin || isApprover;

    // The backend derives the responsibility block from the user-role link
    // (empty only for the central roles) and rejects edits outside it. The
    // /api/me response doesn't carry the block, so v1 marks every non-calc,
    // non-read-only field editable and the server 403 is surfaced as a toast.
    const editableFieldKeys = useMemo(() => {
        const keys = new Set<string>();
        if (!meta) return keys;
        if (isApprover) return keys;
        for (const field of meta.fields) {
            if (!field.isCalculated && !field.isReadOnly) keys.add(field.dbName);
        }
        return keys;
    }, [isApprover, meta]);

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
        if (!isAdmin) return [{ id: "plan", label: "Ремонт" }];
        return [
            { id: "plan", label: "Ремонт" },
            { id: "assets", label: "Предприятия" },
            ...meta.catalogs.map((catalog) => ({ id: catalog.id, label: catalog.label })),
            { id: "users", label: "Пользователи" },
        ];
    }, [isAdmin, meta]);

    useEffect(() => {
        if (sections.length > 0 && !sections.some((section) => section.id === activeSection)) {
            setActiveSection("plan");
        }
    }, [activeSection, sections]);

    const isPlanSection = activeSection === "plan";

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

    const isArchive = viewPlanId !== null;
    const collabEnabled = !isArchive;

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
        onSelectionPreempted: () => {
            suppressSelectionSendRef.current = true;
            gridRef.current?.clearSelection();
            setSelection(null);
        },
    });

    const remoteLocks = useMemo(() => {
        const result = new Map<string, { fullname: string; email: string }>();
        for (const [key, user] of room.lockedCells) {
            result.set(key, { fullname: user.name || "", email: user.email || "" });
        }
        const key = JSON.stringify([...result.entries()]);
        if (key === remoteLocksKeyRef.current) return remoteLocksRef.current;
        remoteLocksKeyRef.current = key;
        remoteLocksRef.current = result;
        return result;
    }, [room.lockedCells]);

    // Cells selected by other members (presence): they're also protected from
    // overwrite, same as leased ones.
    const remotePresence = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, { fullname: string; email: string }>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as { cellKeys?: unknown } | null | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            for (const key of cellKeys) {
                if (!result.has(key)) {
                    result.set(key, { fullname: member.name || "", email: member.email || "" });
                }
            }
        }
        const key = JSON.stringify([...result.entries()]);
        if (key === remotePresenceKeyRef.current) return remotePresenceRef.current;
        remotePresenceKeyRef.current = key;
        remotePresenceRef.current = result;
        return result;
    }, [room.me?.userId, room.members]);

    // Cells where other members are: their presence ranges (canonical keys —
    // the same for everyone regardless of the column set) plus leased cells.
    // Needed for the yellow highlight and the FIO tooltip.
    //
    // The map is stabilized by content: presence heartbeats replace
    // `room.members`/`room.lockedCells` with fresh references even when nothing
    // changed, which — as a columns-memo dependency — rebuilt the 216-column
    // grid on every tick and froze the drag cursor / janked scrolling.
    const remoteCellInfo = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, { name: string; label: string | null }>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as
                | { label?: unknown; cellKeys?: unknown }
                | null
                | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            if (cellKeys.length === 0) continue;
            const name = member.name || member.email || "";
            const label =
                typeof selection?.label === "string" && selection.label ? selection.label : null;
            for (const key of cellKeys) {
                if (!result.has(key)) result.set(key, { name, label });
            }
        }
        for (const [key, user] of room.lockedCells) {
            if (!result.has(key)) {
                result.set(key, { name: user.name || "", label: null });
            }
        }
        // Content fingerprint: return the previous Map object when nothing
        // changed so downstream memos keep their identity.
        const key = JSON.stringify([...result.entries()]);
        if (key === remoteCellInfoKeyRef.current) {
            return remoteCellInfoRef.current;
        }
        remoteCellInfoKeyRef.current = key;
        remoteCellInfoRef.current = result;
        return result;
    }, [room.lockedCells, room.me?.userId, room.members]);

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
    const gridRows = useMemo<RenovationGridRow[]>(() => {
        if (!planWindow) return [];
        const result: RenovationGridRow[] = [];
        const diffByRow = new Map((diff?.rows ?? []).map((row) => [row.rowId, row]));
        for (const row of planWindow.rows) {
            const diffRow = diffByRow.get(row.id);
            const rowState: RenovationGridRow["__rowState"] = diffRow
                ? diffRow.isNew
                    ? "new"
                    : "changed"
                : "unchanged";
            result.push({
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
                __assetName: row.assetName,
                __renovationId: row.renovationId,
                __rowState: rowState,
                __changedFields: diffRow?.cells.map((cell) => cell.field) ?? [],
                __missingComponents: row.missingComponents,
            });
        }
        if (comparisonPlan && diff) {
            const deletedByPosition = new Map(diff.deletedRows.map((row) => [row.position, row]));
            for (const row of comparisonPlan.rows) {
                if (!deletedByPosition.has(row.position)) continue;
                result.push({
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
                    __assetName: row.assetName,
                    __renovationId: row.renovationId,
                    __rowState: "deleted",
                    __changedFields: [],
                    __missingComponents: row.missingComponents,
                });
            }
        }
        result.sort((a, b) => a.__position - b.__position);
        return result;
    }, [comparisonPlan, diff, planWindow]);

    // Remote cell values (live edits of other users) override the local ones
    // for display — the working plan only.
    const displayRows = useMemo(() => {
        if (isArchive || remoteCellValues.size === 0) return gridRows;
        return gridRows.map((row) => {
            let merged: RenovationGridRow | null = null;
            for (const field of editableFieldKeys) {
                const value = remoteCellValues.get(canonicalRenovationKey(row.__id, field));
                if (value === undefined) continue;
                if (!merged) merged = { ...row };
                merged[field] = value;
            }
            return merged ?? row;
        });
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
                if (direction === "undo") {
                    setUndoCount((count) => Math.max(0, count - 1));
                    setRedoCount((count) => count + 1);
                } else {
                    setRedoCount((count) => Math.max(0, count - 1));
                    setUndoCount((count) => count + 1);
                }
                refresh();
                toast.success(direction === "undo" ? "Действие отменено" : "Действие возвращено");
                return;
            }
            if (result.reason === "empty") {
                setPendingSkip(null);
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
        [refresh],
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
        if (!collabEnabled) return;
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
    }, [collabEnabled, runRedo, runUndo]);

    useEffect(() => {
        setUndoState({
            enabled: collabEnabled && !isArchive,
            offline: collabEnabled && room.status !== "online",
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [collabEnabled, isArchive, redoCount, room.status, runRedo, runUndo, undoCount]);

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
                let rowIds: string[] | null = null;
                if (scope === "visible") {
                    // The rows currently on screen (filtered/sorted page view).
                    rowIds = (await gridRef.current?.getVisibleRowIds()) ?? null;
                }
                const { blob, fileName } = await exportRenovationPlan({
                    planId: viewPlanId ?? undefined,
                    rowIds,
                });
                downloadBlob(blob, fileName);
                toast.success("Файл выгружен", { description: fileName });
            } catch (reason) {
                toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
            } finally {
                setExportPending(false);
            }
        },
        [exportPending, viewPlanId],
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
    const canDeleteRow =
        !readOnly &&
        !isArchive &&
        selection?.rowId != null &&
        selectedRow?.__rowState !== "deleted";

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
            toast.danger("Не удалось отправить план", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }, [isMutationPending]);

    const runApprove = useCallback(async () => {
        if (isMutationPending) return;
        setIsMutationPending(true);
        try {
            await approveRenovationPlan();
            setApproveOpen(false);
            setUndoCount(0);
            setRedoCount(0);
            toast.success("План согласован и заархивирован");
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
        if (!versions || versions.length === 0) return [];
        return versions
            .filter((version) => version.state !== "working")
            .map((version) => ({
                id: version.id,
                name: `Версия ${VERSION_STATE_LABELS[version.state]} от ${formatVersionStamp(version.createdAt)}`,
                description: version.createdBy,
            }));
    }, [versions]);

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

    useEffect(() => {
        setClipboardState({
            canPaste: selection !== null,
            onCopy: () => void gridRef.current?.copySelection(),
            onPaste: () => void gridRef.current?.pasteIntoSelection(),
        });
    }, [selection]);

    const setFullscreenMode = useCallback(
        async (next: boolean) => {
            if (isFullscreenPending || next === isFullscreen) return;
            setIsFullscreenPending(true);
            try {
                if (next && !document.fullscreenElement) {
                    await document.documentElement.requestFullscreen();
                } else if (!next && document.fullscreenElement) {
                    await document.exitFullscreen();
                }
                setIsFullscreen(next);
            } catch {
                setIsFullscreen(!next);
            } finally {
                setIsFullscreenPending(false);
            }
        },
        [isFullscreen, isFullscreenPending],
    );

    useEffect(() => {
        if (!isFullscreen) return;
        const onFullscreenChange = () => {
            if (!document.fullscreenElement) {
                setIsFullscreen(false);
            }
        };
        document.addEventListener("fullscreenchange", onFullscreenChange);
        return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
    }, [isFullscreen]);

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

    if (planError) {
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

    if (!meta || !planWindow) {
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
                        <Typography>Загрузка плана ремонта...</Typography>
                    </div>
                    <Skeleton className="h-48 rounded-xl" />
                </div>
            </div>
        );
    }

    // Filters (plan only) — between the version picker and the right controls.
    const planFilters = isPlanSection ? (
        <>
            <div className="plan-filters__asset">
                <SearchAutocomplete
                    placeholder="Все активы"
                    compactLabel="Актив"
                    items={[
                        { id: "__all", name: "Все активы" },
                        ...meta.assets.map((asset) => ({
                            id: asset.name,
                            name: asset.name,
                        })),
                    ]}
                    selectedKey={assetFilter ?? "__all"}
                    onSelectionChange={(key) =>
                        setAssetFilter(key && key !== "__all" ? String(key) : null)
                    }
                />
            </div>
            <Select
                className="plan-filters__block"
                aria-label="Фильтр по блоку"
                selectionMode="single"
                variant="secondary"
                selectedKey={blockFilter ?? "__all"}
                onSelectionChange={(key) =>
                    setBlockFilter(key && key !== "__all" ? String(key) : null)
                }
            >
                <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                </Select.Trigger>
                <Select.Popover className="plan-filters__popover">
                    <ListBox>
                        <ListBox.Item id="__all" textValue="Все блоки">
                            Все блоки
                        </ListBox.Item>
                        {blocks.map((block) => (
                            <ListBox.Item
                                key={block}
                                id={block}
                                textValue={BLOCK_LABELS[block] ?? block}
                            >
                                {BLOCK_LABELS[block] ?? block}
                            </ListBox.Item>
                        ))}
                    </ListBox>
                </Select.Popover>
            </Select>
        </>
    ) : null;

    // «Рабочая версия» / version picker — moved into the toolbar.
    const hasWorkingPlan = meta?.workingPlanId != null;
    const versionItems = archiveItems.length > 0 || hasWorkingPlan ? archiveItems : [];
    const versionPicker =
        isPlanSection && versionItems.length > 0 ? (
            <div className="archive-autocomplete">
                <SearchAutocomplete
                    placeholder="Выберите версию"
                    compactLabel="Архив"
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
        ) : null;

    const toolbar = (
        <div className="table-toolbar">
            <div className="table-toolbar__controls">
                <Tabs
                    className="section-tabs"
                    selectedKey={activeSection}
                    variant="primary"
                    onSelectionChange={(key) => setActiveSection(String(key))}
                >
                    <Tabs.ListContainer>
                        <Tabs.List aria-label="Таблицы и справочники">
                            {sections.map((section, index) => {
                                const previous = sections[index - 1];
                                const isFirstDictionary =
                                    index > 0 && section.id !== "plan" && previous.id === "plan";
                                return (
                                    <Tabs.Tab key={section.id} id={section.id}>
                                        {isFirstDictionary ? <Tabs.Separator /> : null}
                                        {section.label}
                                        <Tabs.Indicator />
                                    </Tabs.Tab>
                                );
                            })}
                        </Tabs.List>
                    </Tabs.ListContainer>
                </Tabs>
            </div>
            {versionPicker}
            {planFilters}
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
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
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

    const actions: ActionItem[] = [
        ...undoRedoActions,
        ...clipboardActions,
        ...(!isArchive
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
                  } satisfies ActionItem,
                  {
                      id: "delete",
                      label: "Удалить строку",
                      icon: <Trash />,
                      onPress: () => setDeleteOpen(true),
                      variant: "danger" as const,
                      disabled: !canDeleteRow || isFullscreenPending,
                  } satisfies ActionItem,
              ]
            : []),
        ...(!isArchive
            ? [
                  {
                      id: "submit",
                      label: "Отправить на согласование",
                      icon: <Send />,
                      onPress: () => setSubmitOpen(true),
                      variant: "primary" as const,
                      disabled: isMutationPending || !canSubmitPlan || isFullscreenPending,
                  } satisfies ActionItem,
                  ...(canApprovePlan
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
              ]
            : []),
    ];

    // Dictionary tabs: export/undo/redo/copy/paste act on the dictionary grid;
    // delete is delegated by the active dictionary section (hidden for users).
    const dictActions: ActionItem[] = [
        ...undoRedoActions,
        ...clipboardActions,
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
            <RenovationActionBar items={isPlanSection ? actions : dictActions} />
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
                                    <>
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
                                                toast.warning("Вставка выполнена не полностью", {
                                                    description,
                                                })
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
                                            scrollCompensation={scrollCompensation}
                                            scrollToEdge={scrollToEdge}
                                            totalRowsOverride={planWindow.total}
                                        />
                                        {displayRows.length === 0 ? (
                                            <div className="grid-empty">
                                                В этом плане пока нет строк.
                                            </div>
                                        ) : null}
                                    </>
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
                    <div className="fullscreen-reveal fullscreen-reveal--top">
                        <div className="fullscreen-panel fullscreen-panel--top">
                            {header}
                            {toolbar}
                        </div>
                    </div>
                    <div className="fullscreen-reveal fullscreen-reveal--bottom">
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
        </main>
    );
}

export function RenovationActionBar({ items }: { items: ActionItem[] }) {
    const isCompact = useMediaQuery("(width < 64rem)");
    const renderAction = (action: ActionItem) => {
        const button = (
            <Button
                className={
                    action.variant === "primary"
                        ? "action-button action-button--primary"
                        : "action-button"
                }
                variant={action.variant ?? "tertiary"}
                isDisabled={action.disabled}
                aria-label={action.label}
                onPress={action.onPress}
            >
                <span className="action-button__icon">{action.icon}</span>
                <span className="action-button__label">{action.label}</span>
            </Button>
        );
        if (!action.shortcut) return button;
        return (
            <Tooltip delay={250}>
                <Tooltip.Trigger>{button}</Tooltip.Trigger>
                <Tooltip.Content placement="top" className="shortcut-tooltip">
                    <span>{action.label}</span>
                    <Kbd>
                        <Kbd.Abbr keyValue="ctrl" />
                        <Kbd.Content>{action.shortcut.key}</Kbd.Content>
                    </Kbd>
                </Tooltip.Content>
            </Tooltip>
        );
    };
    if (isCompact) {
        return (
            <div className="action-bar action-bar--compact" aria-label="Действия с таблицей">
                <CompactActionMenu items={items} />
            </div>
        );
    }
    return (
        <div className="action-bar" aria-label="Действия с таблицей">
            {groupActionItems(items).map((chunk) =>
                chunk.length > 1 ? (
                    // Direct Button children only: ButtonGroup injects props
                    // into them (`__button_group_child`), which a Fragment or
                    // Tooltip wrapper would reject.
                    <ButtonGroup key={`group-${chunk[0].id}`}>
                        {chunk.map((action) => (
                            <Button
                                key={action.id}
                                className={
                                    action.variant === "primary"
                                        ? "action-button action-button--primary"
                                        : "action-button"
                                }
                                variant={action.variant ?? "tertiary"}
                                isDisabled={action.disabled}
                                aria-label={action.label}
                                aria-keyshortcuts={action.shortcut?.aria}
                                onPress={action.onPress}
                            >
                                <span className="action-button__icon">{action.icon}</span>
                                <span className="action-button__label">{action.label}</span>
                            </Button>
                        ))}
                    </ButtonGroup>
                ) : (
                    <span key={chunk[0].id} className="action-button-wrapper">
                        {renderAction(chunk[0])}
                    </span>
                ),
            )}
        </div>
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
