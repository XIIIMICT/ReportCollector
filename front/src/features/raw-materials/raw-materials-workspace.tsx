"use client";

import {
    AlertDialog,
    Avatar,
    Button,
    ButtonGroup,
    Card,
    Dropdown,
    Kbd,
    Label,
    Skeleton,
    Spinner,
    toast,
    Tooltip,
    Typography,
} from "@heroui/react";
import {
    CheckCircle,
    Copy,
    Download,
    MoreHoriz,
    PasteClipboard,
    Redo,
    Trash,
    Undo,
} from "iconoir-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState, type Key, type ReactNode } from "react";

import {
    createDictionaryRecord,
    deleteDictionaryRecord,
    exportTableToExcel,
    fixMovement,
    getAssetList,
    getDeliveryBasisList,
    getMovementMeta,
    getMovementSnapshot,
    getMovementVersions,
    getRawMaterialsList,
    getRawMaterialsMeta,
    getRawMaterialsMovement,
    getRawMaterialsPrices,
    getUndoState,
    getUserList,
    redoCells,
    undoCells,
    updateDictionaryCells,
    updateMovementCells,
    updatePriceCells,
    updateUserCells,
    type AssetRow,
    type CellUpdate,
    type CreateDictionaryPayload,
    type DeliveryBasisRow,
    type MovementMeta,
    type MovementRow,
    type MovementSnapshot,
    type PriceRow,
    type RawMaterialRow,
    type RawMaterialsMeta,
    type SnapshotView,
    type UndoResult,
    type UserRow,
} from "@/shared/api/raw-materials";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import {
    ASSET_TABLE,
    DELIVERY_BASIS_TABLE,
    MOVEMENT_TABLE,
    PRICE_TABLE,
    RAW_MATERIAL_TABLE,
    USER_TABLE,
    useCollabRoom,
    type CollabRoom,
} from "@/shared/collab/use-collab-room";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import { isValidEmail } from "@/shared/email";
import {
    ADMIN_SECTIONS,
    exportFileName,
    getEditableKeys,
    MAIN_SECTIONS,
    SECTION_LABELS,
    formatMonthName,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "@/shared/grid/grid-config";
import {
    RawMaterialsGrid,
    type GridViewStats,
    type RawMaterialsGridHandle,
} from "@/shared/grid/raw-materials-grid";
import { SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useMediaQuery } from "@/shared/hooks/use-media-query";
import { isFromTextEditor } from "@/shared/keyboard";
import { FormStatus } from "@/shared/ui/form-status";
import { HoverDropdown } from "@/shared/ui/hover-dropdown";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
import { ViewStrip } from "@/shared/ui/view-strip";

import { ProfileModal } from "./profile-modal";

type RawMaterialsWorkspaceProps = {
    currentUser: CurrentUser;
    /** Controlled project key (used by the report-workspace project switcher). */
    activeProjectKey?: string;
    onProjectChange?: (key: string) => void;
};

type DictionarySection = "rawMaterials" | "assets" | "deliveryBases" | "users";

const SECTION_COLLAB_TABLE: Partial<Record<SectionId, string>> = {
    materialMovement: MOVEMENT_TABLE,
    rawMaterialPrices: PRICE_TABLE,
    rawMaterials: RAW_MATERIAL_TABLE,
    assets: ASSET_TABLE,
    deliveryBases: DELIVERY_BASIS_TABLE,
    users: USER_TABLE,
};

function isDictionarySection(section: SectionId): section is DictionarySection {
    return (
        section === "rawMaterials" ||
        section === "assets" ||
        section === "deliveryBases" ||
        section === "users"
    );
}

function toGridRow<T extends { id: string }>(row: T): GridRow {
    return {
        ...row,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: false,
    };
}

function toReferenceOptions(names: string[]): ReferenceOption[] {
    // Delivery bases and other dictionaries are passed by name: id isn't needed,
    // the client works with names (id/name properties coincide).
    return names.map((name) => ({ id: name, name }));
}

function toAssetOptions(assets: RawMaterialsMeta["assets"]): ReferenceOption[] {
    // Assets come with real ids: the grid maps names to ids (namesToIds)
    // for the raw-material-items API.
    return assets.map((asset) => ({ id: asset.id, name: asset.name }));
}

const PLACEHOLDER_ID_PREFIX = "new";

function initialPlaceholderDraft(section: SectionId): CreateDictionaryPayload {
    if (section === "rawMaterials") return { name: "", assetNames: [] };
    if (section === "users") {
        return {
            email: "",
            fullname: "",
            isAdmin: false,
            assetNames: [],
            isActive: true,
            resetPassword: true,
        };
    }
    return { name: "" };
}

function placeholderRow(section: SectionId, draft: CreateDictionaryPayload): GridRow {
    return {
        ...draft,
        id: `${PLACEHOLDER_ID_PREFIX}:${section}`,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        canonicalCells: {},
        isPlaceholder: true,
    };
}

function placeholderPayload(
    section: SectionId,
    draft: CreateDictionaryPayload,
    assets: ReferenceOption[],
): CreateDictionaryPayload | null {
    if (section === "assets" || section === "deliveryBases") {
        const name = placeholderText(draft.name);
        return name ? { name } : null;
    }
    if (section === "rawMaterials") {
        const name = placeholderText(draft.name);
        if (!name) return null;
        const assetIdsByName = new Map(
            assets.map(({ id, name: assetName }) => [
                assetName.trim().toLocaleLowerCase("ru-RU"),
                id,
            ]),
        );
        const assetNames = Array.isArray(draft.assetNames) ? draft.assetNames : [];
        const assetIds = assetNames
            .map((assetName) => assetIdsByName.get(assetName.toLocaleLowerCase("ru-RU")))
            .filter((id): id is string => Boolean(id));
        return { name, assetIds };
    }
    if (section === "users") {
        // Email is lowercased: addresses are case-insensitive.
        const email = placeholderText(draft.email).toLowerCase();
        const fullname = placeholderText(draft.fullname);
        const isAdmin = draft.isAdmin === true;
        const assetNames = Array.isArray(draft.assetNames) ? draft.assetNames : [];
        if (!email || !fullname || (!isAdmin && assetNames.length === 0)) return null;
        return {
            email,
            fullname,
            isAdmin,
            assetNames: isAdmin ? [] : assetNames,
        };
    }
    return null;
}

function placeholderText(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function recordLabel(row: Record<string, unknown> | undefined): string | null {
    if (!row) return null;
    const candidates = [row.name, row.fullname, row.email];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    return typeof value === "string" ? value : null;
}

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
}

export type ExportMode = "all" | "visible";

export type ExportState = {
    pending: boolean;
    onExport: (mode: ExportMode) => Promise<void>;
};

export type UndoState = {
    enabled: boolean;
    offline: boolean;
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
};

export type ApproveState = {
    pending: boolean;
    canApprove: boolean;
    onApprove: () => void;
};

export type ArchiveItem = {
    id: string;
    name: string;
    description?: string;
};

export type ArchiveState = {
    items: ArchiveItem[];
    selectedKey: string | null;
    onSelect: (snapshotId: string | null) => void;
};

export function downloadBlob(blob: Blob, fileName: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Defer the revoke: revoking synchronously right after click() can cancel
    // an in-progress download in some browsers (notably Safari, and big files).
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type ClipboardActions = {
    canPaste: boolean;
    onCopy: () => void;
    onPaste: () => void;
};

function SectionContent({
    section,
    reportMonth,
    isAdmin,
    assets,
    deliveryBases,
    collabEnabled,
    firstOpenMonth,
    room,
    remoteCellValues,
    onDeleteState,
    onExportState,
    onResetState,
    headerPinned,
    showServiceColumns,
    onUndoState,
    onApproveState,
    onArchiveState,
    onFixed,
    onGridStatsChange,
    onClipboardStateChange,
    onSelectionLabelChange,
    onRegisterSelectionPreempt,
}: {
    section: SectionId;
    reportMonth: string | null;
    isAdmin: boolean;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    collabEnabled: boolean;
    /** The first open «Движение» month: only it can be fixed. */
    firstOpenMonth: string | null;
    room: CollabRoom;
    remoteCellValues: Map<string, unknown>;
    onDeleteState: (state: { canDelete: boolean; onDelete: () => void }) => void;
    onExportState: (state: ExportState) => void;
    /** Registers the display-reset handlers of the active section grid. */
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    /** Controlled display toggles (rendered in the workspace view strip). */
    headerPinned: boolean;
    showServiceColumns: boolean;
    onUndoState: (state: UndoState) => void;
    onApproveState: (state: ApproveState) => void;
    onArchiveState: (state: ArchiveState) => void;
    onFixed: () => void;
    onGridStatsChange: (stats: GridViewStats) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    /** Completed selection label of the current user (same as in presence). */
    onSelectionLabelChange: (label: string | null) => void;
    /**
     * Registers the handler that clears the local selection when another tab
     * of the same account takes over the user's selection. One handler at a
     * time (the workspace owns the current slot).
     */
    onRegisterSelectionPreempt: (handler: () => void) => void;
}) {
    const loader = useMemo(() => {
        switch (section) {
            case "materialMovement":
                return () =>
                    reportMonth ? getRawMaterialsMovement(reportMonth) : Promise.resolve([]);
            case "rawMaterialPrices":
                return getRawMaterialsPrices;
            case "rawMaterials":
                return getRawMaterialsList;
            case "assets":
                return getAssetList;
            case "deliveryBases":
                return getDeliveryBasisList;
            case "users":
                return getUserList;
            default:
                return () => Promise.resolve([]);
        }
    }, [reportMonth, section]);

    const [reloadKey, setReloadKey] = useState(0);
    const { data: rows, error } = useAsyncData<
        MovementRow[] | PriceRow[] | RawMaterialRow[] | AssetRow[] | DeliveryBasisRow[] | UserRow[]
    >(loader, [loader, reloadKey]);

    const { data: authConfig } = useAsyncData<{ keycloakEnabled: boolean } | null>(
        () =>
            fetch("/api/auth/config")
                .then((response) => (response.ok ? response.json() : null))
                .catch(() => null),
        [],
    );
    const keycloakEnabled = authConfig?.keycloakEnabled === true;

    const isMovement = section === "materialMovement" && reportMonth !== null;
    const { data: movementMeta } = useAsyncData<MovementMeta | null>(
        () => (isMovement ? getMovementMeta(reportMonth as string) : Promise.resolve(null)),
        [isMovement, reportMonth, reloadKey],
    );
    // All report-month snapshots (final & preliminary across months) for the
    // global «Версия…» picker — independent of which open month is focused.
    const { data: movementVersions } = useAsyncData<MovementSnapshot[] | null>(
        () => (isMovement ? getMovementVersions().catch(() => null) : Promise.resolve(null)),
        [isMovement, reloadKey],
    );

    // Pinned (sticky) header is the default view; the grid uses its own
    // scrollbars in this mode (the header stays visible while scrolling).
    const [isMutationPending, setIsMutationPending] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [approveOpen, setApproveOpen] = useState(false);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [placeholderDraft, setPlaceholderDraft] = useState<CreateDictionaryPayload>(() =>
        initialPlaceholderDraft(section),
    );
    const placeholderDraftRef = useRef(placeholderDraft);
    const placeholderCreatePendingRef = useRef(false);
    // In-flight cell commit (PATCH). Ctrl+Z pressed right after Enter must
    // wait for it: otherwise the undo request can reach the server before the
    // edit is journaled and would silently do nothing.
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const gridRef = useRef<RawMaterialsGridHandle>(null);
    const [exportPending, setExportPending] = useState(false);
    // When another tab of the same account takes over the selection, the grid
    // is cleared programmatically; the resulting empty presence update must be
    // swallowed (the server entry belongs to the other tab).
    const suppressSelectionSendRef = useRef(false);
    const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null);
    const [snapshotView, setSnapshotView] = useState<SnapshotView | null>(null);
    const [snapshotPending, setSnapshotPending] = useState(false);
    const [undoCount, setUndoCount] = useState(0);
    const [redoCount, setRedoCount] = useState(0);

    useEffect(() => {
        placeholderDraftRef.current = placeholderDraft;
    }, [placeholderDraft]);

    // Section or month change: reset the placeholder-row state, selection,
    // dialogs and archive viewing; data reloads through the loader
    // (keyed by section/reportMonth).
    useEffect(() => {
        const next = initialPlaceholderDraft(section);
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        setSelectedRowIds([]);
        setDeleteOpen(false);
        setApproveOpen(false);
        setSelectedSnapshotId(null);
        setSnapshotView(null);
    }, [reportMonth, section]);

    // Expose the preemption clear to the workspace: reset the cell range AND
    // the row selection, silently (suppressSelectionSendRef).
    useEffect(() => {
        onRegisterSelectionPreempt(() => {
            suppressSelectionSendRef.current = true;
            setSelectedRowIds([]);
            gridRef.current?.clearSelection();
        });
        return () => onRegisterSelectionPreempt(() => {});
    }, [onRegisterSelectionPreempt]);

    // Undo/redo history is per form (section + month) and lives on the server:
    // on form switch the counters are re-read from the right stack.
    useEffect(() => {
        if (!collabEnabled) return;
        void getUndoState(section, reportMonth)
            .then((state) => {
                setUndoCount(state.canUndo ? 1 : 0);
                setRedoCount(state.canRedo ? 1 : 0);
            })
            .catch(() => {});
    }, [collabEnabled, reportMonth, section]);

    const canManageReferences = isAdmin && !MAIN_SECTIONS.has(section);
    const showAssetColumn = isAdmin || assets.length > 1;
    const selectedRows = useMemo(
        () => (rows ?? []).filter((row) => selectedRowIds.includes(row.id)),
        [rows, selectedRowIds],
    );
    const canDelete = canManageReferences && selectedRows.length > 0;

    // Ctrl+Delete — the shortcut advertised on the action-bar delete button:
    // opens the same confirmation dialog as the button press.
    useEffect(() => {
        if (!canDelete) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.code !== "Delete" || !(event.ctrlKey || event.metaKey)) return;
            if (isFromTextEditor(event)) return;
            event.preventDefault();
            setDeleteOpen(true);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [canDelete]);
    const isArchive = selectedSnapshotId !== null;

    const collabTable = SECTION_COLLAB_TABLE[section] ?? MOVEMENT_TABLE;
    const editableFieldKeys = useMemo(
        () => getEditableKeys(section, isAdmin, showAssetColumn, keycloakEnabled),
        [isAdmin, section, showAssetColumn, keycloakEnabled],
    );
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
        onDeleteState({ canDelete, onDelete: () => setDeleteOpen(true) });
    }, [canDelete, onDeleteState]);

    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => void gridRef.current?.resetFiltersSorting(),
        });
    }, [onResetState]);

    const gridRows = useMemo(() => {
        const base = (rows ?? []).map((row) => {
            const gridRow = toGridRow(row);
            if (!collabEnabled || typeof row.id !== "string") return gridRow;
            gridRow.canonicalCells = {};
            for (const field of editableFieldKeys) {
                const key = `${collabTable}/${row.id}/${field}`;
                gridRow.canonicalCells[field] = key;
                const remote = remoteCellValues.get(key);
                if (remote !== undefined) gridRow[field] = remote;
            }
            return gridRow;
        });
        return canManageReferences ? [...base, placeholderRow(section, placeholderDraft)] : base;
    }, [
        canManageReferences,
        collabEnabled,
        collabTable,
        editableFieldKeys,
        placeholderDraft,
        remoteCellValues,
        rows,
        section,
    ]);

    const archiveRows = useMemo<GridRow[]>(() => {
        if (snapshotView === null) return [];
        return snapshotView.rows.map((row) => ({
            ...row,
            id: row.sourceAssetRawMaterialId,
            rowState: "unchanged" as const,
            changedFields: [],
            isDeleted: false,
            isPlaceholder: false,
        }));
    }, [snapshotView]);

    const displayRows = isArchive ? archiveRows : gridRows;

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);

    async function commit(updates: CellUpdate[]): Promise<boolean> {
        if (readOnly || !updates.length) return true;
        // Serialize commits: consecutive edits (e.g. a fast paste + Enter) must
        // apply in order, otherwise a stale refresh() from an earlier PATCH can
        // overwrite the result of a later one in the visible source.
        await pendingCommitRef.current;
        const task = doCommit(updates);
        pendingCommitRef.current = task;
        try {
            return await task;
        } finally {
            if (pendingCommitRef.current === task) pendingCommitRef.current = null;
        }
    }

    async function doCommit(updates: CellUpdate[]): Promise<boolean> {
        setIsMutationPending(true);
        try {
            if (section === "materialMovement" && reportMonth) {
                await updateMovementCells(reportMonth, updates);
            } else if (section === "rawMaterialPrices") {
                await updatePriceCells(updates);
            } else if (section === "users") {
                await updateUserCells(updates);
            } else if (
                section === "rawMaterials" ||
                section === "assets" ||
                section === "deliveryBases"
            ) {
                await updateDictionaryCells(section, updates);
            }
            setUndoCount((count) => count + 1);
            setRedoCount(0);
            refresh();
            return true;
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
            // The server rejected the edits: let the grid restore data from the
            // source (onCommitFailed) instead of keeping the optimistic value.
            return false;
        } finally {
            setIsMutationPending(false);
        }
    }

    async function createRecord(payload: CreateDictionaryPayload) {
        if (
            !canManageReferences ||
            placeholderCreatePendingRef.current ||
            !isDictionarySection(section)
        )
            return;
        placeholderCreatePendingRef.current = true;
        setIsMutationPending(true);
        try {
            if (section === "users" && !isValidEmail(String(payload.email ?? ""))) {
                toast.danger("Некорректный email", {
                    description: "Проверьте формат адреса электронной почты.",
                });
                return;
            }
            await createDictionaryRecord(section, payload);
            refresh();
            // The create is journaled server-side as an undoable action:
            // re-read the undo state so Ctrl+Z is offered immediately.
            void getUndoState(section, reportMonth)
                .then((state) => {
                    setUndoCount(state.canUndo ? 1 : 0);
                    setRedoCount(state.canRedo ? 1 : 0);
                })
                .catch(() => {});
            const next = initialPlaceholderDraft(section);
            placeholderDraftRef.current = next;
            setPlaceholderDraft(next);
            toast.success("Запись добавлена");
        } catch (reason) {
            toast.danger("Не удалось добавить запись", { description: errorMessage(reason) });
        } finally {
            placeholderCreatePendingRef.current = false;
            setIsMutationPending(false);
        }
    }

    async function commitPlaceholderPatch(patch: Record<string, unknown>): Promise<boolean> {
        if (!canManageReferences || isMutationPending) return true;
        const next = {
            ...placeholderDraftRef.current,
            ...patch,
        } as CreateDictionaryPayload;
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        const payload = placeholderPayload(section, next, assets);
        // createRecord itself shows the error and keeps the placeholder-row draft,
        // so no source rollback is needed.
        if (payload) await createRecord(payload);
        return true;
    }

    async function deleteRecords() {
        if (!canDelete || !isDictionarySection(section)) return;
        setIsMutationPending(true);
        try {
            await Promise.all(selectedRows.map((row) => deleteDictionaryRecord(section, row.id)));
            refresh();
            setDeleteOpen(false);
            setSelectedRowIds([]);
            toast.success("Запись удалена");
        } catch (reason) {
            toast.danger("Не удалось удалить запись", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    const exportTable = useCallback(
        async (mode: ExportMode) => {
            if (exportPending) return;
            const state = await gridRef.current?.getExportState();
            if (!state) return;
            setExportPending(true);
            const fileName = exportFileName(section, reportMonth);
            try {
                const blob = await exportTableToExcel({
                    section,
                    reportMonth,
                    headerPinned,
                    columns: state.columns,
                    rowIds: mode === "visible" ? state.rowIds : null,
                    snapshotId: isArchive ? selectedSnapshotId : null,
                    fileName,
                });
                downloadBlob(blob, fileName);
                toast.success("Файл выгружен", { description: fileName });
            } catch (reason) {
                toast.danger("Не удалось выгрузить файл", { description: errorMessage(reason) });
            } finally {
                setExportPending(false);
            }
        },
        [exportPending, isArchive, section, selectedSnapshotId, reportMonth, headerPinned],
    );

    useEffect(() => {
        onExportState({ pending: exportPending, onExport: exportTable });
    }, [exportPending, exportTable, onExportState]);

    const handleClipboardState = useCallback(
        (state: { canPaste: boolean }) => {
            onClipboardStateChange({
                canPaste: state.canPaste,
                onCopy: () => void gridRef.current?.copySelection(),
                onPaste: () => void gridRef.current?.pasteIntoSelection(),
            });
        },
        [onClipboardStateChange],
    );

    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);

    const applyUndoResult = useCallback(
        (result: UndoResult, direction: "undo" | "redo") => {
            if (result.applied) {
                setPendingSkip(null);
                // Re-read the stack state from the server instead of local ±1:
                // the counters only track presence (canUndo/canRedo), and a
                // locally decremented count died after a single undo even when
                // the server stack still had actions.
                void getUndoState(section, reportMonth)
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
                void getUndoState(section, reportMonth)
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
                        : conflict.field === "__created"
                          ? // A business rule rejected reverting the creation
                            // (e.g. the record is now referenced elsewhere) —
                            // the message is the backend's own explanation.
                            `${owner} Нажмите ещё раз, чтобы пропустить действие.`
                          : `Данные изменил ${owner}. Нажмите ещё раз, чтобы пропустить это действие.`;
                toast.warning("Действие не выполнено", { description });
                return;
            }
            setPendingSkip(null);
        },
        [refresh, reportMonth, section],
    );

    const runUndo = useCallback(async () => {
        await pendingCommitRef.current;
        try {
            const result = await undoCells(
                pendingSkip?.direction === "undo" ? pendingSkip.actionId : null,
                section,
                reportMonth,
            );
            applyUndoResult(result, "undo");
        } catch (reason) {
            toast.danger("Не удалось отменить действие", { description: errorMessage(reason) });
        }
    }, [applyUndoResult, pendingSkip, reportMonth, section]);

    const runRedo = useCallback(async () => {
        await pendingCommitRef.current;
        try {
            const result = await redoCells(
                pendingSkip?.direction === "redo" ? pendingSkip.actionId : null,
                section,
                reportMonth,
            );
            applyUndoResult(result, "redo");
        } catch (reason) {
            toast.danger("Не удалось вернуть действие", { description: errorMessage(reason) });
        }
    }, [applyUndoResult, pendingSkip, reportMonth, section]);

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
        onUndoState({
            // Undo/redo works on all sections: on «Движение»/«Цены» and on the
            // dictionaries (name, asset-set and user edits).
            enabled: collabEnabled && !isArchive,
            offline: collabEnabled && room.status !== "online",
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [
        collabEnabled,
        isArchive,
        MAIN_SECTIONS,
        onUndoState,
        redoCount,
        room.status,
        runRedo,
        runUndo,
        section,
        undoCount,
    ]);

    const [approvePending, setApprovePending] = useState(false);

    const runApprove = useCallback(async () => {
        if (approvePending || !reportMonth || !movementMeta?.isComplete) return;
        setApprovePending(true);
        try {
            await fixMovement(reportMonth);
            setApproveOpen(false);
            toast.success("Месяц зафиксирован");
            refresh();
            onFixed();
        } catch (reason) {
            toast.danger("Не удалось зафиксировать месяц", { description: errorMessage(reason) });
        } finally {
            setApprovePending(false);
        }
    }, [approvePending, movementMeta?.isComplete, onFixed, refresh, reportMonth]);

    useEffect(() => {
        // Only the first open month can be fixed: until the previous month is
        // closed by a final fix, the next ones are unavailable.
        onApproveState({
            pending: approvePending,
            canApprove:
                isAdmin &&
                isMovement &&
                !isArchive &&
                movementMeta !== null &&
                movementMeta.isComplete &&
                !movementMeta.isFinal &&
                !readOnly &&
                reportMonth === firstOpenMonth,
            onApprove: () => setApproveOpen(true),
        });
    }, [
        approvePending,
        firstOpenMonth,
        isAdmin,
        isArchive,
        isMovement,
        movementMeta,
        onApproveState,
        readOnly,
        reportMonth,
    ]);

    const formatSnapshotItem = useCallback((snapshot: MovementSnapshot): ArchiveItem => {
        const fixedAt = new Date(snapshot.fixedAt);
        const stamp = Number.isNaN(fixedAt.getTime())
            ? snapshot.fixedAt
            : new Intl.DateTimeFormat("ru-RU", {
                  day: "2-digit",
                  month: "2-digit",
                  year: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
              }).format(fixedAt);
        return {
            id: snapshot.id,
            name: `Движение за ${formatMonthName(snapshot.reportMonth)} (${
                snapshot.isFinal ? "финальная" : "предварительная"
            } версия)`,
            description: `${snapshot.fixedByFullname} (${stamp})`,
        };
    }, []);

    const loadSnapshotView = useCallback(async (snapshotId: string) => {
        setSnapshotPending(true);
        try {
            const view = await getMovementSnapshot(snapshotId);
            setSnapshotView(view);
            setSelectedSnapshotId(snapshotId);
        } catch (reason) {
            toast.danger("Не удалось открыть архивную версию", {
                description: errorMessage(reason),
            });
        } finally {
            setSnapshotPending(false);
        }
    }, []);

    const selectWorkingVersion = useCallback(() => {
        setSelectedSnapshotId(null);
        setSnapshotView(null);
        refresh();
    }, [refresh]);

    useEffect(() => {
        onArchiveState({
            items: isMovement ? (movementVersions ?? []).map(formatSnapshotItem) : [],
            selectedKey: isMovement ? selectedSnapshotId : null,
            onSelect: (snapshotId) => {
                if (snapshotId === null) {
                    selectWorkingVersion();
                } else {
                    void loadSnapshotView(snapshotId);
                }
            },
        });
    }, [
        formatSnapshotItem,
        isMovement,
        loadSnapshotView,
        movementVersions,
        onArchiveState,
        selectWorkingVersion,
        selectedSnapshotId,
    ]);

    if (error) {
        return (
            <div className="grid-empty">
                <Card>
                    <Card.Content className="text-danger p-4 text-sm">{error}</Card.Content>
                </Card>
            </div>
        );
    }

    // Initial load only — the skeleton replaces the grid until the first rows
    // arrive. A later background refresh (e.g. after a cell PATCH) keeps the
    // grid mounted: unmounting it here would reset the scroll to the top on
    // every edit.
    if (rows === null) {
        return (
            <div className="grid-viewport" aria-busy>
                <div className="grid-empty">
                    <div className="flex items-center gap-3">
                        <Spinner size="sm" />
                        <Typography>Загрузка таблицы...</Typography>
                    </div>
                    <Skeleton className="h-full w-full rounded-none opacity-30" />
                </div>
            </div>
        );
    }

    return (
        <>
            <div className="grid-viewport" aria-busy={isMutationPending || snapshotPending}>
                <RawMaterialsGrid
                    ref={gridRef}
                    section={section}
                    rows={displayRows}
                    isAdmin={isAdmin}
                    keycloakEnabled={keycloakEnabled}
                    showAssetColumn={isAdmin || assets.length > 1}
                    readOnly={readOnly}
                    hasBaseline={false}
                    headerPinned={headerPinned}
                    showServiceColumns={showServiceColumns}
                    assets={assets}
                    deliveryBases={deliveryBases}
                    remoteLocks={remoteLocks}
                    remotePresence={remotePresence}
                    onLockedCellEdit={(_, ownerName) =>
                        toast.warning("Ячейка занята", {
                            description: `Сейчас её занимает ${ownerName}.`,
                        })
                    }
                    onAcquireLease={room.acquireLeases}
                    onReleaseLease={room.releaseLeases}
                    onCommit={commit}
                    onPlaceholderCommit={commitPlaceholderPatch}
                    onCommitFailed={refresh}
                    onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                    onValidationError={(message) =>
                        toast.warning("Некорректное значение", { description: message })
                    }
                    onPasteSkipped={(description) =>
                        toast.warning("Вставка выполнена не полностью", { description })
                    }
                    onPasteTruncated={() =>
                        toast.warning("Вставка обрезана", {
                            description:
                                "Вставляемый диапазон больше выделенного — значения вставлены только в выделенные ячейки.",
                        })
                    }
                    onViewStatsChange={onGridStatsChange}
                    onClipboardStateChange={handleClipboardState}
                    remoteCellInfo={remoteCellInfo}
                    onSelectionChange={({ label, cellKeys }) => {
                        onSelectionLabelChange(label && label.length > 0 ? label : null);
                        if (suppressSelectionSendRef.current) {
                            // The clear following a preemption from another tab
                            // of the same account is not sent — the server
                            // entry belongs to the other tab.
                            suppressSelectionSendRef.current = false;
                            if (!label || label.length === 0) return;
                        }
                        room.setSelection({ label: label ?? "", cellKeys });
                    }}
                />
                {displayRows.length === 0 && !canManageReferences ? (
                    <div className="grid-empty">В этой таблице пока нет строк.</div>
                ) : null}
            </div>
            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={recordLabel(selectedRows[0])}
                count={selectedRows.length}
                onOpenChange={setDeleteOpen}
                onConfirm={() => void deleteRecords()}
            />
            <ApproveDialog
                isOpen={approveOpen}
                isPending={approvePending}
                isFinal={movementMeta?.isFinal === true}
                onOpenChange={setApproveOpen}
                onConfirm={() => void runApprove()}
            />
        </>
    );
}

const WORKING_VERSION_ID = "__working";

function SectionFooter({
    section,
    canDelete,
    undoState,
    approveState,
    clipboardState,
    isFullscreenPending,
    exportPending,
    onExport,
    onDelete,
    onCopy,
    onPaste,
}: {
    section: SectionId;
    canDelete: boolean;
    undoState: UndoState;
    approveState: ApproveState;
    clipboardState: { canPaste: boolean };
    isFullscreenPending: boolean;
    exportPending: boolean;
    onExport: (scope: "all" | "visible") => void;
    onDelete: () => void;
    onCopy: () => void;
    onPaste: () => void;
}) {
    return (
        <div className="table-footer">
            <ActionBar
                section={section}
                canDelete={canDelete}
                undoState={undoState}
                approveState={approveState}
                isArchive={false}
                clipboardState={clipboardState}
                isFullscreenPending={isFullscreenPending}
                exportPending={exportPending}
                onExport={onExport}
                onDelete={onDelete}
                onCopy={onCopy}
                onPaste={onPaste}
            />
        </div>
    );
}

const RAW_MATERIALS_PROJECT_KEY = "raw_materials";

export function RawMaterialsWorkspace({
    currentUser,
    activeProjectKey: controlledProjectKey,
    onProjectChange,
}: RawMaterialsWorkspaceProps) {
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const { data: meta, error } = useAsyncData<RawMaterialsMeta>(
        () => getRawMaterialsMeta(),
        [metaReloadKey],
    );
    const [selectedTabId, setSelectedTabId] = useState<string | null>(null);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isFullscreenPending, setIsFullscreenPending] = useState(false);
    const [gridStats, setGridStats] = useState<GridViewStats | null>(null);
    // Completed cell/row selection of the current user, in the same format as
    // in the «На форме» tooltip (e.g. «Потребность / План, т», строки 3–5).
    // Reported by the grid itself (onSelectionChange), not via the presence
    // echo — so it works even when collaboration is offline.
    const [selectionLabel, setSelectionLabel] = useState<string | null>(null);
    const [clipboardState, setClipboardState] = useState<ClipboardActions>({
        canPaste: false,
        onCopy: () => {},
        onPaste: () => {},
    });
    const [profileOpen, setProfileOpen] = useState(false);
    const [user, setUser] = useState(currentUser);
    const [deleteState, setDeleteState] = useState<{ canDelete: boolean; onDelete: () => void }>({
        canDelete: false,
        onDelete: () => {},
    });
    const [exportState, setExportState] = useState<ExportState>({
        pending: false,
        onExport: async () => {},
    });
    const [gridResetState, setGridResetState] = useState<{
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }>({ resetColumnWidths: () => {}, resetFiltersSorting: () => {} });
    // Display toggles for the active grid (rendered in the view strip).
    const [headerPinned, setHeaderPinned] = useState(true);
    const [showServiceColumns, setShowServiceColumns] = useState(false);
    // Declared before the early returns below: hooks must run unconditionally.
    const handleStripReset = useCallback(
        (scope: "columnWidths" | "filtersSorting") => {
            if (scope === "columnWidths") gridResetState.resetColumnWidths();
            else void gridResetState.resetFiltersSorting();
        },
        [gridResetState],
    );
    const [undoState, setUndoState] = useState<UndoState>({
        enabled: false,
        offline: false,
        canUndo: false,
        canRedo: false,
        onUndo: () => {},
        onRedo: () => {},
    });
    const [approveState, setApproveState] = useState<ApproveState>({
        pending: false,
        canApprove: false,
        onApprove: () => {},
    });
    const [archiveState, setArchiveState] = useState<ArchiveState>({
        items: [],
        selectedKey: null,
        onSelect: () => {},
    });
    const [internalProjectKey, setInternalProjectKey] = useState<string>(
        currentUser.access?.some((project) => project.key === RAW_MATERIALS_PROJECT_KEY)
            ? RAW_MATERIALS_PROJECT_KEY
            : (currentUser.access?.[0]?.key ?? RAW_MATERIALS_PROJECT_KEY),
    );
    // The report workspace owns the project switcher and drives this component
    // through props; without them the raw-materials workspace falls back to
    // its own internal switcher state.
    const activeProjectKey = controlledProjectKey ?? internalProjectKey;
    const { setTheme } = useTheme();

    const handleDeleteState = useCallback(
        (state: { canDelete: boolean; onDelete: () => void }) => setDeleteState(state),
        [],
    );

    const handleExportState = useCallback((state: ExportState) => setExportState(state), []);

    const handleUndoState = useCallback((state: UndoState) => setUndoState(state), []);

    const handleApproveState = useCallback((state: ApproveState) => setApproveState(state), []);

    const handleArchiveState = useCallback((state: ArchiveState) => setArchiveState(state), []);

    const handleFixed = useCallback(() => {
        setMetaReloadKey((key) => key + 1);
    }, []);

    const handleProjectChange = useCallback(
        (key: string) => {
            if (onProjectChange) onProjectChange(key);
            else setInternalProjectKey(key);
        },
        [onProjectChange],
    );

    const projects = user.access ?? [];
    const activeProject = projects.find((project) => project.key === activeProjectKey);
    const isAdmin = isProjectAdmin(activeProject);
    const isRawMaterials = activeProjectKey === RAW_MATERIALS_PROJECT_KEY;
    const header = (
        <AppHeader
            user={user}
            projects={projects}
            activeProjectKey={activeProjectKey}
            onProjectChange={handleProjectChange}
            onOpenProfile={() => setProfileOpen(true)}
        />
    );

    // Apply the saved user theme once on mount. next-themes' setTheme changes
    // the identity on every theme switch, so the effect must not depend on it —
    // otherwise it would reset the user's choice.
    useEffect(() => {
        setTheme(user.theme);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const tabs: WorkspaceTab[] = useMemo(() => {
        if (!meta) return [];
        return [
            ...meta.movementPeriods.map((period) => ({
                id: `movement:${period}`,
                section: "materialMovement" as const,
                label: `Движение за ${formatMonthName(period)}`,
                reportMonth: period,
            })),
            {
                id: "rawMaterialPrices",
                section: "rawMaterialPrices" as const,
                label: SECTION_LABELS.rawMaterialPrices,
                reportMonth: null,
            },
            ...(isAdmin
                ? ADMIN_SECTIONS.map((referenceSection) => ({
                      id: referenceSection,
                      section: referenceSection,
                      label: SECTION_LABELS[referenceSection] ?? referenceSection,
                      reportMonth: null,
                  }))
                : []),
        ];
    }, [isAdmin, meta]);

    useEffect(() => {
        if (selectedTabId === null && tabs.length > 0) {
            setSelectedTabId(tabs[0].id);
        }
    }, [selectedTabId, tabs]);

    const selectedTab = tabs.find((tab) => tab.id === selectedTabId) ?? tabs[0] ?? null;

    const collabEnabled = selectedTab !== null;
    const visibleAssetNames = useMemo(
        () => (meta?.assets ?? []).map((asset) => asset.name),
        [meta],
    );
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, unknown>>(new Map());
    const [collabGeneration, setCollabGeneration] = useState(0);
    const handleCellChanged = useCallback((event: CollabCellChanged) => {
        const table = SECTION_COLLAB_TABLE[event.section as SectionId];
        if (!table) return;
        const key = `${table}/${event.rowId}/${event.field}`;
        setRemoteCellValues((current) => {
            const next = new Map(current);
            next.set(key, event.value);
            return next;
        });
    }, []);
    // The archive version is read-only: its viewer doesn't take part in
    // collaboration and isn't shown in the «На форме» list.
    const archiveOpen = archiveState.selectedKey !== null;
    // Another tab of the same account took over the user's selection: clear
    // the local grid selection without touching the server entry (it belongs
    // to the other tab). SectionContent registers its clear handler here.
    const selectionPreemptRef = useRef<() => void>(() => {});
    const registerSelectionPreempt = useCallback((handler: () => void) => {
        selectionPreemptRef.current = handler;
    }, []);
    const room = useCollabRoom({
        projectKey: activeProjectKey,
        section: selectedTab?.section ?? "rawMaterialPrices",
        reportMonth: selectedTab?.reportMonth ?? null,
        enabled: collabEnabled && !archiveOpen,
        visibleAssets:
            selectedTab !== null && MAIN_SECTIONS.has(selectedTab.section)
                ? visibleAssetNames
                : null,
        onCellChanged: handleCellChanged,
        onReconnect: () => {
            setRemoteCellValues(new Map());
            setCollabGeneration((generation) => generation + 1);
        },
        onSelectionPreempted: () => {
            setSelectionLabel(null);
            selectionPreemptRef.current();
        },
    });

    useEffect(() => {
        setRemoteCellValues(new Map());
    }, [selectedTab?.reportMonth, selectedTab?.section]);

    // Selection is per-form: reset the label when the tab changes (the grid
    // reports a fresh one on the next completed selection).
    useEffect(() => {
        setSelectionLabel(null);
    }, [selectedTabId]);

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

    if (error) {
        return (
            <div className="app-shell">
                <div className="app-body">
                    <Card className="startup-error-card">
                        <Card.Content className="p-8">
                            <h2 className="mb-2 text-lg font-semibold">
                                Не удалось открыть рабочее пространство
                            </h2>
                            <p className="text-danger text-sm">{error}</p>
                        </Card.Content>
                    </Card>
                </div>
            </div>
        );
    }

    if (!isRawMaterials) {
        return (
            <main className="app-shell">
                {header}
                <div className="app-body">
                    <section className="workspace">
                        <div className="project-placeholder">
                            <Typography className="text-muted">
                                Раздел проекта ещё не реализован.
                            </Typography>
                        </div>
                    </section>
                </div>
                <ProfileModal
                    isOpen={profileOpen}
                    user={user}
                    onOpenChange={setProfileOpen}
                    onProfileSaved={setUser}
                />
            </main>
        );
    }

    if (!meta || !selectedTab) {
        return (
            <div className="app-shell">
                {header}
                <div className="app-body">
                    <div className="flex items-center gap-3 p-8">
                        <Spinner size="sm" />
                        <Typography>Загрузка справочников...</Typography>
                    </div>
                    <Skeleton className="h-48 rounded-xl" />
                </div>
            </div>
        );
    }

    const assets = toAssetOptions(meta.assets);
    const deliveryBases = toReferenceOptions(meta.deliveryBases);

    // «Рабочая версия» / version picker — shown only on the very FIRST tab
    // (the earliest movement period). It lists «Рабочая версия» on top and the
    // report-month snapshots beneath (final & preliminary months alike), like
    // the renovation picker.
    const isFirstTab = tabs.length > 0 && selectedTab?.id === tabs[0].id;
    const showVersionPicker =
        selectedTab?.section === "materialMovement" &&
        isFirstTab &&
        (archiveState.items.length > 0 || archiveState.selectedKey !== null);
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
                items={[
                    {
                        id: WORKING_VERSION_ID,
                        name: "Рабочая версия",
                        description: "Доступна для редактирования",
                    },
                    ...archiveState.items,
                ]}
                selectedKey={archiveState.selectedKey ?? WORKING_VERSION_ID}
                onSelectionChange={(key) => {
                    if (key === null) return;
                    archiveState.onSelect(key === WORKING_VERSION_ID ? null : key);
                }}
            />
        </div>
    );

    const toolbar = (
        <div className="table-toolbar">
            {versionPicker}
            <div className="table-toolbar__controls">
                <SectionTabs
                    tabs={tabs}
                    value={selectedTab.id}
                    onChange={(tab) => setSelectedTabId(tab.id)}
                />
            </div>
            <ViewStrip
                headerPinned={headerPinned}
                onHeaderPinnedChange={setHeaderPinned}
                showServiceColumns={showServiceColumns}
                onShowServiceColumnsChange={setShowServiceColumns}
                exportPending={exportState.pending}
                onExport={(scope) => void exportState.onExport(scope)}
                onReset={handleStripReset}
                hideExport
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
            />
        </div>
    );

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                <FormStatus room={room} selectionLabel={selectionLabel} gridStats={gridStats} />
            </div>
            <SectionFooter
                section={selectedTab.section}
                canDelete={deleteState.canDelete}
                undoState={undoState}
                approveState={approveState}
                clipboardState={clipboardState}
                isFullscreenPending={isFullscreenPending}
                exportPending={exportState.pending}
                onExport={(scope) => void exportState.onExport(scope)}
                onDelete={deleteState.onDelete}
                onCopy={clipboardState.onCopy}
                onPaste={clipboardState.onPaste}
            />
        </div>
    );

    return (
        <main className={isFullscreen ? "app-shell app-shell--fullscreen" : "app-shell"}>
            {header}
            <div className="app-body">
                <section className="workspace">
                    <Card className="workspace-card">
                        <Card.Content className="workspace-card__content">
                            {toolbar}
                            <SectionContent
                                key={collabGeneration}
                                section={selectedTab.section}
                                reportMonth={selectedTab.reportMonth}
                                isAdmin={isAdmin}
                                assets={assets}
                                deliveryBases={deliveryBases}
                                collabEnabled={collabEnabled}
                                firstOpenMonth={meta?.movementPeriods[0] ?? null}
                                room={room}
                                remoteCellValues={remoteCellValues}
                                onDeleteState={handleDeleteState}
                                onExportState={handleExportState}
                                onResetState={setGridResetState}
                                headerPinned={headerPinned}
                                showServiceColumns={showServiceColumns}
                                onUndoState={handleUndoState}
                                onApproveState={handleApproveState}
                                onArchiveState={handleArchiveState}
                                onFixed={handleFixed}
                                onGridStatsChange={setGridStats}
                                onClipboardStateChange={setClipboardState}
                                onSelectionLabelChange={setSelectionLabel}
                                onRegisterSelectionPreempt={registerSelectionPreempt}
                            />
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

            <ProfileModal
                isOpen={profileOpen}
                user={user}
                onOpenChange={setProfileOpen}
                onProfileSaved={setUser}
            />
        </main>
    );
}

function Brand({
    compact = false,
    projectName,
    projects,
    activeProjectKey,
    onProjectChange,
}: {
    compact?: boolean;
    projectName: string;
    projects: Array<{ key: string; name: string }>;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
}) {
    // Stable identity: an inline `new Set(...)` changes on every render and
    // loops HeroUI's selection sync ("Maximum update depth exceeded"). Declared
    // before the early return below: hooks must run unconditionally.
    const selectedProjectKeys = useMemo(() => new Set([activeProjectKey]), [activeProjectKey]);

    if (projects.length <= 1) {
        return (
            <div
                className={compact ? "brand brand--compact" : "brand"}
                aria-label={`РепортКоллектор ${projectName}`}
            >
                <img
                    className="brand__icon"
                    src="/ruschem_icon.png"
                    alt=""
                    onError={(event) => {
                        event.currentTarget.hidden = true;
                    }}
                />
                <span className="brand__name">РепортКоллектор</span>
                <span className="brand__edition">{projectName}</span>
            </div>
        );
    }

    return (
        <div className={compact ? "brand brand--compact" : "brand"}>
            <img
                className="brand__icon"
                src="/ruschem_icon.png"
                alt=""
                onError={(event) => {
                    event.currentTarget.hidden = true;
                }}
            />
            <span className="brand__name">РепортКоллектор</span>
            <HoverDropdown
                className="brand-popover"
                trigger={({ ref, onMouseEnter, onPress }) => (
                    <Button
                        ref={ref}
                        onMouseEnter={onMouseEnter}
                        onPress={onPress}
                        className="brand__edition brand__edition--button"
                        variant="ghost"
                        aria-label={`Проект: ${projectName}. Выберите проект`}
                    >
                        {projectName}
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        aria-label="Доступные проекты"
                        selectedKeys={selectedProjectKeys}
                        selectionMode="single"
                        onSelectionChange={(selection) => {
                            const keys = selection === "all" ? [] : [...selection];
                            if (keys[0]) onProjectChange(String(keys[0]));
                        }}
                    >
                        {projects.map((project) => (
                            <Dropdown.Item
                                key={project.key}
                                id={project.key}
                                textValue={project.name}
                            >
                                <Dropdown.ItemIndicator />
                                {project.name}
                            </Dropdown.Item>
                        ))}
                    </Dropdown.Menu>
                }
            />
        </div>
    );
}

export function AppHeader({
    user,
    projects,
    activeProjectKey,
    onProjectChange,
    onOpenProfile,
}: {
    user: CurrentUser;
    projects: Array<{ key: string; name: string }>;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
    onOpenProfile: () => void;
}) {
    const fullname = user.name ?? "";
    const email = user.email ?? "";
    const initials = user.name
        ? user.name
              .trim()
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 2)
              .map((part) => part[0])
              .join("")
              .toUpperCase()
        : (email[0] ?? "").toUpperCase();
    const projectName =
        projects.find((project) => project.key === activeProjectKey)?.name ?? "Сырьё";

    return (
        <header className="app-header">
            <Brand
                compact
                projectName={projectName}
                projects={projects}
                activeProjectKey={activeProjectKey}
                onProjectChange={onProjectChange}
            />
            <span aria-hidden="true" />
            <div className="app-header__actions">
                <Button
                    className="profile-pill"
                    variant="ghost"
                    onPress={onOpenProfile}
                    aria-label="Открыть профиль"
                >
                    <Avatar className="profile-pill__avatar" aria-hidden="true">
                        <Avatar.Fallback>{initials || "?"}</Avatar.Fallback>
                    </Avatar>
                    <span className="profile-pill__copy">
                        <strong>{fullname || email}</strong>
                        <small>{email}</small>
                    </span>
                </Button>
            </div>
        </header>
    );
}

export type ActionItem = {
    id: string;
    label: string;
    icon: ReactNode;
    onPress: () => void;
    variant?: "primary" | "secondary" | "danger";
    disabled?: boolean;
    shortcut?: { key: "Z" | "Y" | "C" | "V" | "Del"; aria: string };
    /**
     * Consecutive items sharing a group render joined in one HeroUI
     * ButtonGroup («Отменить/Вернуть», «Копировать/Вставить»).
     */
    group?: "history" | "clipboard" | "row" | "download";
    /**
     * A trigger with a dropdown (e.g. download with «всю таблицу» /
     * «видимую таблицу» sub-actions) instead of a one-shot button.
     */
    menu?: Array<{ id: string; label: string; onPress: () => void }>;
    /** Show a spinner in place of the icon while a background job runs. */
    pending?: boolean;
};

/** Chunk actions into ButtonGroup runs: consecutive items with equal `group`. */
export function groupActionItems(items: ActionItem[]): ActionItem[][] {
    const chunks: ActionItem[][] = [];
    for (const item of items) {
        const last = chunks[chunks.length - 1];
        if (last && item.group !== undefined && last[0].group === item.group) {
            last.push(item);
        } else {
            chunks.push([item]);
        }
    }
    return chunks;
}

function ActionBar({
    section,
    canDelete,
    undoState,
    approveState,
    isArchive,
    clipboardState,
    isFullscreenPending,
    exportPending,
    onExport,
    onDelete,
    onCopy,
    onPaste,
}: {
    section: SectionId;
    canDelete: boolean;
    undoState: UndoState;
    approveState: ApproveState;
    isArchive: boolean;
    clipboardState: { canPaste: boolean };
    isFullscreenPending: boolean;
    exportPending: boolean;
    onExport: (scope: "all" | "visible") => void;
    onDelete: () => void;
    onCopy: () => void;
    onPaste: () => void;
}) {
    const showDelete = !MAIN_SECTIONS.has(section) && section !== "users";
    const showApprove = section === "materialMovement" && !isArchive;
    const showPaste = !isArchive;
    const trailingActions: ActionItem[] = [
        {
            id: "copy",
            label: "Копировать",
            icon: <Copy />,
            onPress: onCopy,
            disabled: isFullscreenPending,
            shortcut: { key: "C", aria: "Control+C" },
            group: "clipboard",
        } satisfies ActionItem,
        ...(showPaste
            ? [
                  {
                      id: "paste",
                      label: "Вставить",
                      icon: <PasteClipboard />,
                      onPress: onPaste,
                      disabled: isFullscreenPending || !clipboardState.canPaste,
                      shortcut: { key: "V", aria: "Control+V" },
                      group: "clipboard",
                  } satisfies ActionItem,
              ]
            : []),
        ...(undoState.enabled
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
            : []),
        {
            id: "export",
            label: "Скачать в Excel",
            icon: <Download />,
            onPress: () => onExport("all"),
            disabled: exportPending || isFullscreenPending,
            pending: exportPending,
            group: "download",
            menu: [
                { id: "all", label: "Скачать всю таблицу", onPress: () => onExport("all") },
                {
                    id: "visible",
                    label: "Скачать видимую таблицу",
                    onPress: () => onExport("visible"),
                },
            ],
        } satisfies ActionItem,
        ...(showDelete
            ? [
                  {
                      id: "delete",
                      label: "Удалить запись",
                      icon: <Trash />,
                      onPress: onDelete,
                      variant: "danger",
                      disabled: !canDelete || isFullscreenPending,
                      shortcut: { key: "Del", aria: "Control+Delete" },
                  } satisfies ActionItem,
              ]
            : []),
        ...(showApprove
            ? [
                  {
                      id: "approve",
                      label: "Зафиксировать",
                      icon: <CheckCircle />,
                      onPress: approveState.onApprove,
                      variant: "primary",
                      disabled: approveState.pending || !approveState.canApprove,
                  } satisfies ActionItem,
              ]
            : []),
    ];
    // Responsive tiers (Tailwind constants): on sm (≤40rem) every action
    // collapses into one «Действия» button; on md (≤48rem) only the fix action
    // collapses to an icon; on desktop everything is expanded.
    const isSm = useMediaQuery("(max-width: 40rem)");
    const isMd = useMediaQuery("(max-width: 48rem)");
    const collapseFixAtMd = isMd && !isSm;
    // Floating tooltip showing the label (+ kbd) over every inline action
    // button, including the ones grouped in a ButtonGroup (whose children can't
    // be wrapped in a per-button Tooltip).
    const [actionTip, setActionTip] = useState<{ text: string; left: number; top: number } | null>(
        null,
    );
    const actionTipTargetRef = useRef<Element | null>(null);
    const actionTipText = (action: ActionItem) =>
        action.shortcut ? `${action.label} (Ctrl+${action.shortcut.key})` : action.label;
    if (isSm) {
        return (
            <div className="action-bar action-bar--compact" aria-label="Действия с таблицей">
                <CompactActionMenu items={trailingActions} />
            </div>
        );
    }

    const classNameFor = (action: ActionItem) =>
        collapseFixAtMd && action.variant === "primary"
            ? "action-button"
            : action.variant === "primary"
              ? "action-button action-button--primary"
              : "action-button";

    const renderTrailing = (action: ActionItem) => {
        const icon = action.pending ? <Spinner size="sm" /> : action.icon;
        // A menu-trigger (e.g. download «всю/видимую таблицу»).
        if (action.menu) {
            return (
                <HoverDropdown
                    className="action-more-popover"
                    trigger={({ ref, onMouseEnter, isOpen, onPress }) => (
                        <Button
                            ref={ref}
                            onMouseEnter={onMouseEnter}
                            onPress={onPress}
                            className={
                                isOpen
                                    ? classNameFor(action) + " action-button--open"
                                    : classNameFor(action)
                            }
                            variant={action.variant ?? "tertiary"}
                            isDisabled={action.disabled}
                            aria-label={action.label}
                            data-action-tooltip={actionTipText(action)}
                        >
                            <span className="action-button__icon">{icon}</span>
                            <span className="action-button__label">{action.label}</span>
                        </Button>
                    )}
                    content={
                        <Dropdown.Menu
                            aria-label={action.label}
                            onAction={(key) => {
                                const menuItem = action.menu?.find((m) => m.id === String(key));
                                menuItem?.onPress();
                            }}
                            className="action-more-menu"
                        >
                            {action.menu.map((menuItem) => (
                                <Dropdown.Item
                                    key={menuItem.id}
                                    id={menuItem.id}
                                    textValue={menuItem.label}
                                >
                                    <Label>{menuItem.label}</Label>
                                </Dropdown.Item>
                            ))}
                        </Dropdown.Menu>
                    }
                />
            );
        }
        const button = (
            <Button
                className={classNameFor(action)}
                variant={action.variant ?? "tertiary"}
                isDisabled={action.disabled}
                aria-label={action.label}
                aria-keyshortcuts={action.shortcut?.aria}
                data-action-tooltip={actionTipText(action)}
                onPress={action.onPress}
            >
                <span className="action-button__icon">{icon}</span>
                <span className="action-button__label">{action.label}</span>
            </Button>
        );
        return button;
    };

    return (
        <div
            className="action-bar"
            aria-label="Действия с таблицей"
            onPointerMove={(event) => {
                const target = (event.target as Element).closest?.("[data-action-tooltip]") ?? null;
                if (target === actionTipTargetRef.current) return;
                actionTipTargetRef.current = target;
                if (!target) {
                    setActionTip(null);
                    return;
                }
                const rect = target.getBoundingClientRect();
                setActionTip({
                    text: target.getAttribute("data-action-tooltip") ?? "",
                    left: rect.left + rect.width / 2,
                    top: rect.top,
                });
            }}
            onPointerLeave={() => {
                actionTipTargetRef.current = null;
                setActionTip(null);
            }}
        >
            {groupActionItems(trailingActions).map((chunk) =>
                chunk.length > 1 ? (
                    // Direct Button children only: ButtonGroup injects props
                    // into them (`__button_group_child`), which a Fragment or
                    // Tooltip wrapper would reject — the kbd hint is shown by the
                    // floating tooltip above instead.
                    <ButtonGroup key={`group-${chunk[0].id}`}>
                        {chunk.map((action) => (
                            <Button
                                key={action.id}
                                className={classNameFor(action)}
                                variant={action.variant ?? "tertiary"}
                                isDisabled={action.disabled}
                                aria-label={action.label}
                                aria-keyshortcuts={action.shortcut?.aria}
                                data-action-tooltip={actionTipText(action)}
                                onPress={action.onPress}
                            >
                                <span className="action-button__icon">{action.icon}</span>
                                <span className="action-button__label">{action.label}</span>
                            </Button>
                        ))}
                    </ButtonGroup>
                ) : (
                    <span key={chunk[0].id} className="action-button-wrapper">
                        {renderTrailing(chunk[0])}
                    </span>
                ),
            )}
            <Tooltip isOpen={actionTip !== null}>
                <Tooltip.Trigger
                    className="remote-cell-tooltip-trigger"
                    style={{
                        left: actionTip?.left ?? -1000,
                        top: actionTip?.top ?? -1000,
                    }}
                >
                    <span aria-hidden="true" />
                </Tooltip.Trigger>
                <Tooltip.Content placement="top" className="shortcut-tooltip">
                    <span>{actionTip?.text ?? ""}</span>
                </Tooltip.Content>
            </Tooltip>
        </div>
    );
}

/**
 * The compact action menu (narrow screens): a single «Действия» button whose
 * dropdown (full-text items with icons — the same actions as the inline bar)
 * opens on hover. See `HoverDropdown` for why closing is pointer-position
 * based rather than mouseleave.
 */
export function CompactActionMenu({
    items,
    onOpenChange,
}: {
    items: ActionItem[];
    /** Notifies the owner when the menu opens/closes (holds the fullscreen
        panels open while the compact «Действия» menu is expanded). */
    onOpenChange?: (open: boolean) => void;
}) {
    const handleAction = useCallback(
        (key: Key) => {
            const raw = String(key);
            for (const item of items) {
                if (item.menu) {
                    const sub = item.menu.find((m) => `${item.id}:${m.id}` === raw);
                    if (sub) {
                        sub.onPress();
                        return;
                    }
                } else if (item.id === raw) {
                    item.onPress();
                    return;
                }
            }
        },
        [items],
    );

    return (
        <HoverDropdown
            className="action-more-popover"
            onOpenChange={onOpenChange}
            trigger={({ ref, onMouseEnter, isOpen, onPress }) => (
                <Button
                    ref={ref}
                    onMouseEnter={onMouseEnter}
                    onPress={onPress}
                    className={isOpen ? "action-button action-button--open" : "action-button"}
                    variant="tertiary"
                    aria-label="Действия с таблицей"
                >
                    <span className="action-button__icon">
                        <MoreHoriz aria-hidden="true" />
                    </span>
                    <span className="action-button__label">Действия</span>
                </Button>
            )}
            content={
                <Dropdown.Menu
                    aria-label="Действия с таблицей"
                    onAction={handleAction}
                    className="action-more-menu"
                >
                    {items.flatMap((item) =>
                        item.menu
                            ? item.menu.map((sub) => (
                                  <Dropdown.Item
                                      key={`${item.id}:${sub.id}`}
                                      id={`${item.id}:${sub.id}`}
                                      textValue={sub.label}
                                  >
                                      <Label>{sub.label}</Label>
                                  </Dropdown.Item>
                              ))
                            : [
                                  <Dropdown.Item
                                      key={item.id}
                                      id={item.id}
                                      textValue={item.label}
                                      isDisabled={item.disabled}
                                      variant={item.variant === "danger" ? "danger" : undefined}
                                      // HeroUI ships only a `danger` MenuItem variant; success is
                                      // reproduced the same way — color the label slot via the
                                      // theme's `--success` token (stock Tailwind utility, no CSS).
                                      className={
                                          item.variant === "primary"
                                              ? "[&_[data-slot=label]]:text-success"
                                              : undefined
                                      }
                                  >
                                      <span className="action-more-menu__icon">{item.icon}</span>
                                      <Label>{item.label}</Label>
                                      {item.shortcut ? (
                                          <Kbd className="ms-auto" slot="keyboard" variant="light">
                                              <Kbd.Abbr keyValue="ctrl" />
                                              <Kbd.Content>{item.shortcut.key}</Kbd.Content>
                                          </Kbd>
                                      ) : null}
                                  </Dropdown.Item>,
                              ],
                    )}
                </Dropdown.Menu>
            }
        />
    );
}

export function DeleteRecordDialog({
    isOpen,
    isPending,
    label,
    count,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    label: string | null;
    count: number;
    onOpenChange: (open: boolean) => void;
    onConfirm: () => void;
}) {
    return (
        <AlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop>
                <AlertDialog.Container>
                    <AlertDialog.Dialog>
                        <AlertDialog.Header>
                            <AlertDialog.Icon status="danger">
                                <Trash />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>
                                {count === 1 ? "Удалить запись?" : `Удалить записи (${count})?`}
                            </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            {count === 1 && label ? `Будет удалена запись «${label}». ` : null}
                            {count > 1 ? `Будут удалены записи в количестве ${count}. ` : null}
                            Операция выполняется только в том случае, если на удаляемые записи ничто
                            не ссылается. Иначе ни одна запись не будет удалена.
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                            <Button
                                variant="tertiary"
                                onPress={() => onOpenChange(false)}
                                isDisabled={isPending}
                            >
                                Отмена
                            </Button>
                            <Button variant="danger" onPress={onConfirm} isDisabled={isPending}>
                                {isPending ? <Spinner color="current" size="sm" /> : <Trash />}
                                Удалить
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}

function ApproveDialog({
    isOpen,
    isPending,
    isFinal,
    onOpenChange,
    onConfirm,
}: {
    isOpen: boolean;
    isPending: boolean;
    isFinal: boolean;
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
                                <CheckCircle />
                            </AlertDialog.Icon>
                            <AlertDialog.Heading>Зафиксировать движение сырья?</AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                            {isFinal
                                ? "Это финальная фиксация прошедшего месяца. После неё период будет закрыт без возможности перезаписи, а в работе появится следующий месяц."
                                : "Это предварительная фиксация текущего месяца. Повторная фиксация этого же месяца заменит предыдущую архивную версию."}
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
                                Зафиксировать
                            </Button>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </AlertDialog>
    );
}
