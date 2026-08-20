"use client";

import {
    AlertDialog,
    Button,
    Card,
    Dropdown,
    Kbd,
    Skeleton,
    Spinner,
    Tabs,
    toast,
    Tooltip,
    Typography,
} from "@heroui/react";
import {
    CheckCircle,
    Collapse,
    Copy,
    Download,
    Expand,
    PasteClipboard,
    Redo,
    Send,
    Trash,
    Undo,
} from "iconoir-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    AppHeader,
    CollabStatusBar,
    CompactActionMenu,
    DeleteRecordDialog,
    downloadBlob,
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
    type RenovationPlanVersion,
    type RenovationUndoResult,
} from "@/shared/api/renovation";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import { canonicalCellKey, useCollabRoom } from "@/shared/collab/use-collab-room";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import type { GridViewStats } from "@/shared/grid/raw-materials-grid";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";

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
const PLACEHOLDER_ROW_ID = "placeholder:renovation-plan";

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

const VERSION_STATE_LABELS: Record<RenovationPlanVersion["state"], string> = {
    working: "рабочая",
    pendingApproval: "на согласовании",
    archived: "архивная",
};

function blockLabel(block: string): string {
    return block.charAt(0).toUpperCase() + block.slice(1);
}

export function RenovationWorkspace({
    currentUser,
    activeProjectKey,
    onProjectChange,
}: RenovationWorkspaceProps) {
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const [reloadKey, setReloadKey] = useState(0);
    const { data: meta, error } = useAsyncData<RenovationMeta | null>(
        () => getRenovationMeta().catch(() => null),
        [metaReloadKey],
    );
    const { data: versions, error: versionsError } = useAsyncData<RenovationPlanVersion[] | null>(
        () => getRenovationPlans().catch(() => null),
        [metaReloadKey],
    );
    const [viewPlanId, setViewPlanId] = useState<string | null>(null);
    const { data: plan, error: planError } = useAsyncData<RenovationPlan | null>(
        () => (meta ? getRenovationPlan(viewPlanId ?? undefined) : Promise.resolve(null)),
        [meta, metaReloadKey, reloadKey, viewPlanId],
    );
    const { data: diff } = useAsyncData<RenovationDiff | null>(
        () => (viewPlanId ? getRenovationDiff(viewPlanId) : Promise.resolve(null)),
        [metaReloadKey, reloadKey, viewPlanId],
    );
    const comparedToPlanId = viewPlanId ? (diff?.comparedToPlanId ?? null) : null;
    const { data: comparisonPlan } = useAsyncData<RenovationPlan | null>(
        () => (comparedToPlanId ? getRenovationPlan(comparedToPlanId) : Promise.resolve(null)),
        [comparedToPlanId, metaReloadKey, reloadKey],
    );

    const [user, setUser] = useState(currentUser);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isFullscreenPending, setIsFullscreenPending] = useState(false);
    const [footerAutocompleteOpen, setFooterAutocompleteOpen] = useState(false);
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
    // The trailing placeholder row's picked asset (kept on a failed add).
    const [placeholderAsset, setPlaceholderAsset] = useState("");
    // Dictionary tab actions are delegated by the active dictionary section.
    const [dictDeleteState, setDictDeleteState] = useState<{
        canDelete: boolean;
        onDelete: () => void;
    }>({ canDelete: false, onDelete: () => {} });
    const [dictExportState, setDictExportState] = useState<{ onExport: () => void }>({
        onExport: () => {},
    });
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
    const [assetFilter, setAssetFilter] = useState<string | null>(null);
    const [blockFilter, setBlockFilter] = useState<string | null>(null);
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
        const result = new Set<string>();
        for (const field of meta?.fields ?? []) {
            if (field.responsibilityBlock) result.add(field.responsibilityBlock);
        }
        return [...result].sort();
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

    // Stable identities for HeroUI selection sets and the grid's assetFilter
    // prop: an inline `new Set(...)` changes identity on every render, which
    // loops HeroUI's selection sync (update-depth crash) and re-fires the
    // grid's selection effect (its deps include displaySource → assetFilter).
    const assetFilterKeys = useMemo(() => new Set([assetFilter ?? "__all"]), [assetFilter]);
    const blockFilterKeys = useMemo(() => new Set([blockFilter ?? "__all"]), [blockFilter]);
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
        return result;
    }, [room.me?.userId, room.members]);

    // Cells where other members are: their presence ranges (canonical keys —
    // the same for everyone regardless of the column set) plus leased cells.
    // Needed for the yellow highlight and the FIO tooltip.
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
        if (!plan) return [];
        const result: RenovationGridRow[] = [];
        const diffByRow = new Map((diff?.rows ?? []).map((row) => [row.rowId, row]));
        for (const row of plan.rows) {
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
        if (!readOnly) {
            result.push({
                __id: PLACEHOLDER_ROW_ID,
                __position: -1,
                __assetId: "",
                __assetName: placeholderAsset,
                __renovationId: null,
                __rowState: "unchanged",
                __changedFields: [],
                __missingComponents: [],
                isPlaceholder: true,
            });
        }
        return result;
    }, [comparisonPlan, diff, placeholderAsset, plan, readOnly]);

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

    const exportPlan = useCallback(async () => {
        if (exportPending) return;
        setExportPending(true);
        try {
            const { blob, fileName } = await exportRenovationPlan(viewPlanId ?? undefined);
            downloadBlob(blob, fileName);
            toast.success("Файл выгружен", { description: fileName });
        } catch (reason) {
            toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
        } finally {
            setExportPending(false);
        }
    }, [exportPending, viewPlanId]);

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
            setPlaceholderAsset("");
            toast.success("Строка добавлена");
            refresh();
        } catch (reason) {
            toast.danger("Не удалось добавить строку", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    async function commitPlaceholder(assetName: string): Promise<boolean> {
        if (isMutationPending) return true;
        const asset = meta?.assets.find((candidate) => candidate.name === assetName);
        if (!asset) {
            toast.danger("Не удалось добавить строку", {
                description: "Предприятие не найдено в справочнике активов.",
            });
            return true;
        }
        // Keep the picked name in the placeholder row while the request runs
        // (gridRows rebuilds with readOnly=true during the mutation).
        setPlaceholderAsset(assetName);
        await addRow(asset.id);
        return true;
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
        (plan?.rows.length ?? 0) > 0;
    const canApprovePlan = canApprove && !isArchive && !readOnly && meta?.pendingPlanId != null;

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
    const handleSelectionChange = useCallback(
        (
            next: {
                label: string | null;
                cellKeys: string[];
                rowId: string | null;
            } | null,
        ) => {
            setSelection(next);
            if (suppressSelectionSendRef.current) {
                // The clear following a preemption from another tab of the same
                // account is not sent — the server entry belongs to the other tab.
                suppressSelectionSendRef.current = false;
                if (!next?.label) return;
            }
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

    if (!meta || !plan) {
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

    const toolbar = (
        <div className="table-toolbar">
            {collabEnabled ? <CollabStatusBar room={room} /> : null}
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
                {isPlanSection ? (
                    <>
                        <Dropdown>
                            <Button
                                className="toolbar-filter-button"
                                variant="ghost"
                                aria-label="Фильтр по активу"
                            >
                                {assetFilter ?? "Все активы"}
                            </Button>
                            <Dropdown.Popover>
                                <Dropdown.Menu
                                    aria-label="Активы"
                                    selectionMode="single"
                                    selectedKeys={assetFilterKeys}
                                    onSelectionChange={(keys) => {
                                        const key = keys === "all" ? null : [...keys][0];
                                        setAssetFilter(key && key !== "__all" ? String(key) : null);
                                    }}
                                >
                                    <Dropdown.Item id="__all" textValue="Все активы">
                                        Все активы
                                    </Dropdown.Item>
                                    {meta.assets.map((asset) => (
                                        <Dropdown.Item
                                            key={asset.name}
                                            id={asset.name}
                                            textValue={asset.name}
                                        >
                                            {asset.name}
                                        </Dropdown.Item>
                                    ))}
                                </Dropdown.Menu>
                            </Dropdown.Popover>
                        </Dropdown>
                        <Dropdown>
                            <Button
                                className="toolbar-filter-button"
                                variant="ghost"
                                aria-label="Фильтр по блоку"
                            >
                                {blockFilter ? `Блок: ${blockLabel(blockFilter)}` : "Все блоки"}
                            </Button>
                            <Dropdown.Popover>
                                <Dropdown.Menu
                                    aria-label="Блоки"
                                    selectionMode="single"
                                    selectedKeys={blockFilterKeys}
                                    onSelectionChange={(keys) => {
                                        const key = keys === "all" ? null : [...keys][0];
                                        setBlockFilter(key && key !== "__all" ? String(key) : null);
                                    }}
                                >
                                    <Dropdown.Item id="__all" textValue="Все блоки">
                                        Все блоки
                                    </Dropdown.Item>
                                    {blocks.map((block) => (
                                        <Dropdown.Item
                                            key={block}
                                            id={block}
                                            textValue={blockLabel(block)}
                                        >
                                            {blockLabel(block)}
                                        </Dropdown.Item>
                                    ))}
                                </Dropdown.Menu>
                            </Dropdown.Popover>
                        </Dropdown>
                    </>
                ) : null}
            </div>
        </div>
    );

    const fullscreenAction: ActionItem = {
        id: "fullscreen",
        label: isFullscreen ? "Выйти из полного экрана" : "В полный экран",
        icon: isFullscreen ? <Collapse /> : <Expand />,
        onPress: () => void setFullscreenMode(!isFullscreen),
        disabled: isFullscreenPending,
    };

    const undoRedoActions: ActionItem[] = undoState.enabled
        ? [
              {
                  id: "undo",
                  label: "Отменить действие",
                  icon: <Undo />,
                  onPress: undoState.onUndo,
                  disabled: undoState.offline || !undoState.canUndo,
                  shortcut: { key: "Z", aria: "Control+Z" },
              } satisfies ActionItem,
              {
                  id: "redo",
                  label: "Вернуть действие",
                  icon: <Redo />,
                  onPress: undoState.onRedo,
                  disabled: undoState.offline || !undoState.canRedo,
                  shortcut: { key: "Y", aria: "Control+Shift+Z" },
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
        } satisfies ActionItem,
        {
            id: "paste",
            label: "Вставить",
            icon: <PasteClipboard />,
            onPress: clipboardState.onPaste,
            disabled: isFullscreenPending || !clipboardState.canPaste,
            shortcut: { key: "V", aria: "Control+V" },
        } satisfies ActionItem,
    ];

    const actions: ActionItem[] = [
        fullscreenAction,
        {
            id: "export",
            label: "Скачать в Excel",
            icon: exportPending ? <Spinner size="sm" /> : <Download />,
            onPress: () => void exportPlan(),
            disabled: exportPending || isFullscreenPending,
        },
        ...undoRedoActions,
        ...clipboardActions,
        ...(!isArchive
            ? [
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
        fullscreenAction,
        {
            id: "export",
            label: "Скачать в Excel",
            icon: <Download />,
            onPress: dictExportState.onExport,
            disabled: isFullscreenPending,
        },
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
            <div className="table-footer__summary">
                {isPlanSection && archiveItems.length > 0 ? (
                    <div className="archive-autocomplete">
                        <SearchAutocomplete
                            placeholder="Выберите версию"
                            compactLabel="Архив"
                            items={[
                                {
                                    id: WORKING_VERSION_ID,
                                    name: "Рабочая версия",
                                    description: "Доступна для редактирования",
                                },
                                ...archiveItems,
                            ]}
                            selectedKey={viewPlanId ?? WORKING_VERSION_ID}
                            onSelectionChange={(key) => {
                                if (key === null) return;
                                archiveState.onSelect(
                                    key === WORKING_VERSION_ID ? null : String(key),
                                );
                            }}
                            onOpenChange={setFooterAutocompleteOpen}
                        />
                    </div>
                ) : null}
                {isPlanSection &&
                gridStats !== null &&
                (selection?.label || gridStats.filtersApplied) ? (
                    <Tooltip delay={250}>
                        <Tooltip.Trigger>
                            <div className="grid-stats" aria-label="Статистика таблицы">
                                {selection?.label ? (
                                    <span>
                                        <b>Выделено:</b> {selection.label}
                                    </span>
                                ) : null}
                                {gridStats.filtersApplied ? (
                                    <span>
                                        <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                        {gridStats.totalRows} всего
                                    </span>
                                ) : null}
                            </div>
                        </Tooltip.Trigger>
                        <Tooltip.Content placement="top" className="grid-stats-tooltip">
                            <div className="grid-stats-detail">
                                {selection?.label ? (
                                    <span>
                                        <b>Выделено:</b> {selection.label}
                                    </span>
                                ) : null}
                                <span>
                                    <b>Строки:</b> {gridStats.filteredRows} найдено,{" "}
                                    {gridStats.totalRows} всего
                                </span>
                                <span>
                                    <b>Столбцы:</b> {gridStats.immutableCols} неизменяемых +{" "}
                                    {gridStats.calculatedCols} расчётных + {gridStats.editableCols}{" "}
                                    заполняемых = {gridStats.totalCols} всего
                                </span>
                            </div>
                        </Tooltip.Content>
                    </Tooltip>
                ) : null}
            </div>
            <RenovationActionBar
                items={isPlanSection ? actions : dictActions}
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
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
                                    <>
                                        <RenovationGrid
                                            ref={gridRef}
                                            fields={meta.fields}
                                            headerCells={meta.headerCells}
                                            catalogs={meta.catalogs}
                                            assets={meta.assets}
                                            rows={displayRows}
                                            assetFilter={assetFilterSet}
                                            blockFilter={blockFilter}
                                            baselineBoundary={plan.baselineBoundary}
                                            readOnly={readOnly}
                                            editableFieldKeys={editableFieldKeys}
                                            userAssetIds={userAssetIds}
                                            remoteLocks={remoteLocks}
                                            remotePresence={remotePresence}
                                            onResetFilters={() => {
                                                setAssetFilter(null);
                                                setBlockFilter(null);
                                            }}
                                            remoteCellInfo={remoteCellInfo}
                                            onAcquireLease={room.acquireLeases}
                                            onReleaseLease={room.releaseLeases}
                                            onLockedCellEdit={(_, ownerName) =>
                                                toast.warning("Ячейка занята", {
                                                    description: `Сейчас её занимает ${ownerName}.`,
                                                })
                                            }
                                            onCommit={commit}
                                            onPlaceholderCommit={commitPlaceholder}
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
                    <div
                        className={
                            footerAutocompleteOpen
                                ? "fullscreen-reveal fullscreen-reveal--bottom is-open"
                                : "fullscreen-reveal fullscreen-reveal--bottom"
                        }
                    >
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
                    versions?.find((version) => version.state === "pendingApproval")?.createdBy ??
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

export function RenovationActionBar({
    items,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
}: {
    items: ActionItem[];
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
}) {
    void isFullscreen;
    void isFullscreenPending;
    void onToggleFullscreen;
    const isCompact = useMediaQuery("(width < 64rem)");
    if (isCompact) {
        return (
            <div className="action-bar action-bar--compact" aria-label="Действия с таблицей">
                <CompactActionMenu items={items} />
            </div>
        );
    }
    return (
        <div className="action-bar" aria-label="Действия с таблицей">
            {items.map((action) => {
                const button = (
                    <Button
                        className="action-button"
                        variant={action.variant ?? "secondary"}
                        isDisabled={action.disabled}
                        aria-label={action.label}
                        onPress={action.onPress}
                    >
                        <span className="action-button__icon">{action.icon}</span>
                        <span className="action-button__label">{action.label}</span>
                    </Button>
                );
                if (!action.shortcut) {
                    return (
                        <span key={action.id} className="action-button-wrapper">
                            {button}
                        </span>
                    );
                }
                return (
                    <Tooltip key={action.id} delay={250}>
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
            })}
        </div>
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
