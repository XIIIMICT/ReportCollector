"use client";

import { AlertDialog, Button, ButtonGroup, Spinner, toast, Typography } from "@heroui/react";
import { CheckCircle, Xmark } from "iconoir-react";
import { useTheme } from "next-themes";
import { createElement, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    createDictionaryRecord,
    deleteDictionaryRecord,
    replaceDictionaryRecord,
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
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import { isValidEmail } from "@/shared/email";
import { DataGrid, type GridViewStats, type DataGridHandle } from "@/shared/grid/data-grid";
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
    collaborationAvailableFor,
    undoAvailableFor,
    sectionPolicy,
} from "@/shared/grid/section-policy";
import { SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useRestoredState } from "@/shared/hooks/use-restored-state";
import { ActionBar as SharedActionBar, type ActionItem } from "@/shared/ui/action-bar";
import {
    buildAddRowAction,
    buildClipboardActions,
    buildDeleteAction,
    buildExportAction,
    buildHistoryActions,
    buildPrimaryAction,
    buildReplaceAction,
    DeleteRecordDialog,
    type DictionaryReplaceState,
} from "@/shared/ui/action-items";
import {
    AddRecordModal,
    type AddRecordField,
    type AddRecordValues,
} from "@/shared/ui/add-record-modal";
import { AppHeader } from "@/shared/ui/app-header";
import { ControlledAlertDialog } from "@/shared/ui/controlled-alert-dialog";
import { downloadBlob as sharedDownloadBlob } from "@/shared/ui/download";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import { applyHistoryResult, runHistoryAction } from "@/shared/ui/history-action";
import { ReplaceRecordDialog, type ReplaceTargetOption } from "@/shared/ui/replace-record-dialog";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
import {
    errorMessage,
    recordLabel,
    trimmedText,
    publishSelection,
    type ClipboardActions,
    type GridSelectionInfo,
    updateClipboardActions,
} from "@/shared/ui/text-helpers";
import { refreshUndoCounters } from "@/shared/ui/undo-counters";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useTableExport } from "@/shared/ui/use-table-export";
import {
    readToolbarFilters,
    restoredFilterVerdict,
    useToolbarFilters,
} from "@/shared/ui/use-toolbar-filters";
import { useViewState } from "@/shared/ui/use-view-state";
import { ViewStrip } from "@/shared/ui/view-strip";
import { GridLoadError, WorkspaceLoadError } from "@/shared/ui/workspace-feedback";
import { WorkspaceContent, WorkspaceFullscreenPanels } from "@/shared/ui/workspace-layout";
import { useDeleteShortcut, useUndoRedoShortcuts } from "@/shared/ui/workspace-shortcuts";
import type { ArchiveItem, ArchiveState, UndoState } from "@/shared/ui/workspace-state";

import { ProfileModal } from "./profile-modal";

/** Standard grid toasts: see `gridWarningHandlers`. */
const gridWarnings = gridWarningHandlers({});

type RawMaterialsWorkspaceProps = {
    currentUser: CurrentUser;
    /** Controlled project key (used by the report-workspace project switcher). */
    activeProjectKey?: string;
    onProjectChange?: (key: string) => void;
    /** Tab restored from `?section=` (movement:{period} / prices / a dictionary). */
    initialSection?: string | null;
    /** Mirrors the active tab into the URL so a reload keeps it. */
    onSectionChange?: (section: string | null) => void;
    /** `SUPPORT_MAIL` - the mailbox of the header support control. */
    supportMail: string;
};

type DictionarySection = "rawMaterials" | "assets" | "deliveryBases" | "users";

/**
 * The action-bar delete state. `blockedReason` is set when the button must
 * stay visible but disabled (deleting yourself, or a user whose access to the
 * project is still enabled) — the tooltip explains why.
 */
type DeleteActions = {
    canDelete: boolean;
    blockedReason: string | null;
    onDelete: () => void;
};
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

/** Fields of the «Добавить пользователя» dialog of the «Сырьё» project. */
const RAW_USER_DEFAULTS: AddRecordValues = {
    email: "",
    fullname: "",
    role: "blocked",
    assetNames: [],
};

function rawUserFields(assets: ReferenceOption[]): AddRecordField[] {
    return [
        {
            kind: "text",
            key: "email",
            label: "Email",
            autoComplete: "off",
        },
        { kind: "text", key: "fullname", label: "ФИО" },
        {
            kind: "select",
            key: "role",
            label: "Роль",
            options: [
                { id: "blocked", name: "Заблокирован", danger: true },
                { id: "asset_worker", name: "Работник на активе" },
                { id: "admin", name: "Администратор" },
            ],
        },
        {
            kind: "multi",
            key: "assetNames",
            label: "Активы",
            placeholder: "Выберите активы",
            options: assets.map(({ id, name }) => ({ id, name })),
        },
    ];
}

const PLACEHOLDER_ID_PREFIX = "new";

function initialPlaceholderDraft(section: SectionId): CreateDictionaryPayload {
    if (section === "rawMaterials") return { name: "", assetNames: [] };
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
        const name = trimmedText(draft.name);
        return name ? { name } : null;
    }
    if (section === "rawMaterials") {
        const name = trimmedText(draft.name);
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
    return null;
}

export type ExportMode = "all" | "visible";

import type { ExportState } from "@/shared/ui/action-items";

export type ApproveState = {
    pending: boolean;
    canApprove: boolean;
    onApprove: () => void;
};

/**
 * Does this published state really differ from the stored one?
 *
 * The section builds a FRESH object on every render (a new closure inside it, a
 * new array). Storing it unconditionally re-rendered the workspace, which
 * re-rendered the section, which published again — the loop React reports as
 * «Maximum update depth exceeded». Functions are compared by IDENTITY, so a
 * section that keeps them stable (`useCallback`) leaves the state untouched.
 */
function samePublishedState(previous: unknown, next: unknown): boolean {
    if (previous === next) return true;
    if (
        previous === null ||
        next === null ||
        typeof previous !== "object" ||
        typeof next !== "object"
    ) {
        return false;
    }
    const a = previous as Record<string, unknown>;
    const b = next as Record<string, unknown>;
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
        const left = a[key];
        const right = b[key];
        if (typeof left === "function" && typeof right === "function") continue;
        if (Array.isArray(left) && Array.isArray(right)) {
            if (left.length !== right.length) return false;
            if (!left.every((item, index) => item === right[index])) return false;
            continue;
        }
        if (left !== right) return false;
    }
    return true;
}

export function downloadBlob(blob: Blob, fileName: string): void {
    sharedDownloadBlob(blob, fileName);
}

export type UserCreateState = {
    canCreate: boolean;
    onCreate: () => void;
};

function SectionContent({
    section,
    reportMonth,
    isAdmin,
    panelFilterTabId,
    assets,
    deliveryBases,
    collabEnabled,
    firstOpenMonth,
    room,
    remoteCellValues,
    onDeleteState,
    onReplaceState,
    onExportState,
    onResetState,
    onUserCreateState,
    currentUserEmail,
    headerPinned,
    showServiceColumns,
    onShowServiceColumnsChange,
    rowHeight,
    scrollForm,
    onUndoState,
    onApproveState,
    onArchiveState,
    onFixed,
    onGridStatsChange,
    onClipboardStateChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onRegisterSelectionPreempt,
}: {
    section: SectionId;
    reportMonth: string | null;
    isAdmin: boolean;
    /**
     * The tab id this section keeps its PANEL filters under, so the archive
     * version survives a reload (`f.materialMovement.snapshot`). Null when the
     * section has no such id (the filters then stay in memory only).
     */
    panelFilterTabId: string | null;
    /** Project key of the owning workspace — the form part of the scroll key. */
    scrollForm: string;
    /** The signed-in user's email: the delete guard refuses to remove them. */
    currentUserEmail: string | undefined;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    collabEnabled: boolean;
    /** The first open «Движение» month: only it can be fixed. */
    firstOpenMonth: string | null;
    room: CollabRoom;
    remoteCellValues: Map<string, unknown>;
    onDeleteState: (state: DeleteActions) => void;
    /** Registers the «Заменить» action of the mounted dictionary section. */
    onReplaceState: (state: DictionaryReplaceState) => void;
    onExportState: (state: ExportState) => void;
    /** Registers the display-reset handlers of the active section grid. */
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    /** Registers the «Добавить пользователя» action of the users section. */
    onUserCreateState: (state: UserCreateState) => void;
    /** Controlled display toggles (rendered in the workspace view strip). */
    headerPinned: boolean;
    showServiceColumns: boolean;
    /** Show/hide the project-wide service columns (the header + button). */
    onShowServiceColumnsChange: (visible: boolean) => void;
    rowHeight: number;
    onUndoState: (state: UndoState) => void;
    onApproveState: (state: ApproveState) => void;
    onArchiveState: (state: ArchiveState) => void;
    onFixed: () => void;
    onGridStatsChange: (stats: GridViewStats) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    /** Completed selection label of the current user (same as in presence). */
    onSelectionLabelChange: (label: string | null) => void;
    /** The same selection spelled out for the status tooltip (header chain). */
    onSelectionDetailChange?: (detail: string | null) => void;
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
    const [replaceOpen, setReplaceOpen] = useState(false);
    const [approveOpen, setApproveOpen] = useState(false);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    // Users are created through a modal (the fields are role-dependent), other
    // dictionaries still create through the trailing placeholder row.
    const [userCreateOpen, setUserCreateOpen] = useState(false);
    const [placeholderDraft, setPlaceholderDraft] = useState<CreateDictionaryPayload>(() =>
        initialPlaceholderDraft(section),
    );
    const placeholderDraftRef = useRef(placeholderDraft);
    const placeholderCreatePendingRef = useRef(false);
    // In-flight cell commit (PATCH). Ctrl+Z pressed right after Enter must
    // wait for it: otherwise the undo request can reach the server before the
    // edit is journaled and would silently do nothing.
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const gridRef = useRef<DataGridHandle>(null);
    // When another tab of the same account takes over the selection, the grid
    // is cleared programmatically; the resulting empty presence update must be
    // swallowed (the server entry belongs to the other tab).
    const suppressSelectionSendRef = useRef(false);
    // The open ARCHIVE version is a PANEL filter like any other: it describes
    // which slice of the data is open, so it belongs in the URL and must
    // survive a reload. Restored once after hydration (the URL is client-only);
    // validated against the version list with the shared rule, because that
    // list arrives LATER and an empty list must not be read as "this version is
    // gone".
    const [selectedSnapshotId, setSelectedSnapshotId] = useRestoredState<string | null>(
        null,
        () => {
            const stored = readToolbarFilters(panelFilterTabId)["snapshot"];
            return typeof stored === "string" ? stored : null;
        },
    );
    const { setFilter: setPanelFilter } = useToolbarFilters(panelFilterTabId);
    const rememberSnapshot = useCallback(
        (snapshotId: string | null) =>
            setPanelFilter(panelFilterTabId ?? "", "snapshot", snapshotId),
        [panelFilterTabId, setPanelFilter],
    );
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
        setUserCreateOpen(false);
        setSelectedRowIds([]);
        setDeleteOpen(false);
        setApproveOpen(false);
        setSelectedSnapshotId(null);
        rememberSnapshot(null);
        setSnapshotView(null);
    }, [rememberSnapshot, reportMonth, section]);

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
        void refreshUndoCounters(
            () => getUndoState(section, reportMonth),
            setUndoCount,
            setRedoCount,
        );
    }, [collabEnabled, reportMonth, section]);

    const canManageReferences = isAdmin && sectionPolicy(section).rowOps !== "none";
    // Users are created via the modal instead of the trailing placeholder row.
    const showPlaceholderRow =
        canManageReferences && sectionPolicy(section).rowOps === "placeholder";
    const showAssetColumn = isAdmin || assets.length > 1;
    const selectedRows = useMemo(
        () => (rows ?? []).filter((row) => selectedRowIds.includes(row.id)),
        [rows, selectedRowIds],
    );
    const canDelete = canManageReferences && selectedRows.length > 0;
    // The delete button stays visible but disabled in two protective cases:
    //  • the row is the signed-in user themselves — nobody removes themselves;
    //  • the row is a user whose access to the project is still enabled — the
    //    admin has to revoke access first, so a live member can never be erased
    //    by a single click. The backend enforces both rules (409).
    const deleteBlockedReason =
        selectedRows.length === 0
            ? "Выберите запись для удаления."
            : section !== "users"
              ? null
              : selectedRows.some(
                      (row) =>
                          !!currentUserEmail &&
                          String((row as { email?: unknown }).email ?? "").toLowerCase() ===
                              currentUserEmail.toLowerCase(),
                  )
                ? "Нельзя удалить себя из проекта."
                : selectedRows.some((row) => (row as { role?: string }).role !== "blocked")
                  ? "Сначала заблокируйте пользователя (роль «Заблокирован»), затем удаляйте."
                  : null;
    const deleteAllowed = canDelete && deleteBlockedReason === null;
    // «Заменить» is a dictionary operation on exactly one value: it re-points
    // the references and deletes the replaced row.
    const replaceTargets: ReplaceTargetOption[] = useMemo(() => {
        if (selectedRows.length !== 1 || section === "users") return [];
        return (rows ?? [])
            .filter((row) => row.id !== selectedRows[0]?.id)
            .map((row) => ({
                id: row.id,
                name: String((row as { name?: unknown }).name ?? row.id),
            }));
    }, [rows, section, selectedRows]);
    // «Заменить» is a dictionary operation: only the reference tables, never
    // the «Движение»/«Цены» forms, and never «Пользователи» (they are people,
    // not values that other rows point at).
    const replaceable = section !== "users" && sectionPolicy(section).rowOps !== "none";
    const canReplace = canManageReferences && replaceable && selectedRows.length === 1;
    const replaceBlockedReason = !replaceable
        ? null
        : selectedRows.length === 0
          ? "Выберите запись для замены."
          : selectedRows.length > 1
            ? "Выберите ровно одну запись."
            : null;

    // Ctrl+Delete — the shortcut advertised on the action-bar delete button:
    // opens the same confirmation dialog as the button press.
    // Ctrl+Delete opens the same dialog as the action-bar button.
    const openDeleteDialog = useCallback(() => setDeleteOpen(true), []);
    useDeleteShortcut(canDelete, openDeleteDialog);
    const isArchive = selectedSnapshotId !== null;

    const collabTable = SECTION_COLLAB_TABLE[section] ?? MOVEMENT_TABLE;
    const editableFieldKeys = useMemo(
        () => getEditableKeys(section, isAdmin, showAssetColumn, keycloakEnabled),
        [isAdmin, section, showAssetColumn, keycloakEnabled],
    );
    // Leased / selected cells and the combined highlight marker, stabilized by
    // content so a presence heartbeat with unchanged state does not rebuild the
    // grid (see the hook). Shared with the «Ремонт» workspace.
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);

    const readOnly = isMutationPending || isArchive || (collabEnabled && room.status === "offline");

    useEffect(() => {
        onDeleteState({
            canDelete: deleteAllowed,
            blockedReason: deleteBlockedReason,
            onDelete: () => setDeleteOpen(true),
        });
    }, [deleteAllowed, deleteBlockedReason, onDeleteState]);

    // «Заменить» is delegated up like delete: the section owns the selection and
    // the dialog, the workspace footer only renders the shared action.
    const openReplace = useCallback(() => setReplaceOpen(true), []);
    useEffect(() => {
        onReplaceState({
            show: replaceable,
            canReplace,
            blockedReason: replaceBlockedReason,
            onReplace: openReplace,
        });
    }, [canReplace, onReplaceState, openReplace, replaceBlockedReason, replaceable]);

    // Header column filters (client-side, shared with every table). ?????????
    // ??? ??????? ? ??????????? must clear them like it clears RevoGrid's own.
    const {
        filters: columnFilters,
        setFilters: setColumnFilters,
        clearFilters,
    } = useGridColumnFilters("raw-materials", "plan");
    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => {
                clearFilters();
                void gridRef.current?.resetFiltersSorting();
            },
        });
    }, [clearFilters, onResetState]);

    // The users section creates records through the modal, so the action-bar
    // button is delegated up like delete/export (stable identity — it feeds a
    // footer prop; a fresh object each render would rebuild the footer).
    const canCreateUser = canManageReferences && section === "users";
    const openUserCreate = useCallback(() => setUserCreateOpen(true), []);
    useEffect(() => {
        onUserCreateState({ canCreate: canCreateUser, onCreate: openUserCreate });
    }, [canCreateUser, onUserCreateState, openUserCreate]);

    const gridRows = useMemo(() => {
        const base = (rows ?? []).map((row) => {
            const gridRow = toGridRow(row);
            gridRow.readOnlyRow =
                section === "users" &&
                "email" in row &&
                String(row.email).toLowerCase() === currentUserEmail?.toLowerCase();
            if (section === "users" && (row as { role?: string }).role !== "asset_worker") {
                // The asset grants belong to the asset-worker role only.
                gridRow.lockedCells = ["assetNames"];
            }
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
        return showPlaceholderRow ? [...base, placeholderRow(section, placeholderDraft)] : base;
    }, [
        collabEnabled,
        collabTable,
        currentUserEmail,
        editableFieldKeys,
        placeholderDraft,
        remoteCellValues,
        rows,
        section,
        showPlaceholderRow,
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
            await createDictionaryRecord(section, payload);
            refresh();
            // The create is journaled server-side as an undoable action:
            // re-read the undo state so Ctrl+Z is offered immediately.
            void refreshUndoCounters(
                () => getUndoState(section, reportMonth),
                setUndoCount,
                setRedoCount,
            );
            if (section !== "users") {
                const next = initialPlaceholderDraft(section);
                placeholderDraftRef.current = next;
                setPlaceholderDraft(next);
            }
            toast.success("Запись добавлена");
        } catch (reason) {
            toast.danger("Не удалось добавить запись", { description: errorMessage(reason) });
            // The modal (users) keeps itself open and shows the message; the
            // placeholder row swallows it like before this function existed.
            if (section === "users") throw reason;
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

    async function createUserFromModal(values: AddRecordValues) {
        const role = String(values.role ?? "blocked");
        const assetNames = Array.isArray(values.assetNames)
            ? (values.assetNames as unknown[]).map(String)
            : [];
        await createRecord({
            email: String(values.email ?? "")
                .trim()
                .toLowerCase(),
            fullname: String(values.fullname ?? "").trim(),
            role,
            // An admin implicitly covers every asset — no explicit grant.
            assetNames: role === "asset_worker" ? assetNames : [],
        });
        setUserCreateOpen(false);
    }

    // Validation mirroring the server rules: email format, full name, and for
    // an asset worker at least one asset (an admin implicitly covers every one).
    function validateUser(values: AddRecordValues): string | null {
        if (!isValidEmail(String(values.email ?? ""))) {
            return "Проверьте формат адреса электронной почты.";
        }
        if (!String(values.fullname ?? "").trim()) return "Укажите ФИО.";
        if (values.role === "asset_worker") {
            const assetNames = Array.isArray(values.assetNames) ? values.assetNames : [];
            if (assetNames.length === 0)
                return "Выберите хотя бы один актив для работника на активе.";
        }
        return null;
    }

    // An admin covers every asset implicitly, so the asset picker is hidden —
    // the same rule the server applies (a non-worker's creation drops grants).
    function rawUserFieldVisible(field: AddRecordField, values: AddRecordValues): boolean {
        if (field.key === "assetNames") return values.role === "asset_worker";
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
            // The deletion is journaled like any cell edit: re-read the stack so
            // «Отменить действие» lights up straight away.
            void refreshUndoCounters(
                () => getUndoState(section, reportMonth),
                setUndoCount,
                setRedoCount,
            );
        }
    }

    async function replaceRecord(targetId: string | null) {
        if (!canReplace || !isDictionarySection(section)) return;
        const row = selectedRows[0];
        if (!row) return;
        setIsMutationPending(true);
        try {
            await replaceDictionaryRecord(section, row.id, targetId);
            refresh();
            setReplaceOpen(false);
            setSelectedRowIds([]);
            toast.success("Значение заменено");
        } catch (reason) {
            toast.danger("Не удалось заменить значение", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
            void refreshUndoCounters(
                () => getUndoState(section, reportMonth),
                setUndoCount,
                setRedoCount,
            );
        }
    }

    const { pending: exportPending, exportTable } = useTableExport({
        gridRef,
        fileName: () => exportFileName(section, reportMonth),
        request: (view, fileName) =>
            exportTableToExcel({
                section,
                reportMonth,
                headerPinned,
                columns: view.columns,
                rowIds: view.rowIds,
                snapshotId: isArchive ? selectedSnapshotId : null,
                fileName,
            }),
    });

    useEffect(() => {
        onExportState({
            pending: exportPending,
            rowCount: rows?.length ?? 0,
            onExport: exportTable,
        });
    }, [exportPending, exportTable, rows, onExportState]);

    // Stable identity: the grid's clipboard effect runs on every prop change
    // and publishes back through this handler — an inline one would re-trigger
    // it on every render (setState in the parent -> new handler -> effect
    // again) and overflow the React update-depth guard.
    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);

    const handleSelectionChange = useCallback(
        (selection: GridSelectionInfo) =>
            publishSelection({
                selection,
                room,
                onLabel: onSelectionLabelChange,
                onDetail: onSelectionDetailChange,
                suppressNextClear: suppressSelectionSendRef,
            }),
        [onSelectionDetailChange, onSelectionLabelChange, room],
    );

    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);

    const applyUndoResult = useCallback(
        (result: UndoResult, direction: "undo" | "redo") => {
            applyHistoryResult({
                result,
                direction,
                setPendingSkip,
                refresh,
                businessRuleFields: ["__created"],
                refreshState: () =>
                    void refreshUndoCounters(
                        () => getUndoState(section, reportMonth),
                        setUndoCount,
                        setRedoCount,
                    ),
            });
        },
        [refresh, reportMonth, section],
    );

    const runUndo = useCallback(async () => {
        await runHistoryAction({
            direction: "undo",
            pendingWrite: pendingCommitRef.current,
            pendingSkip,
            request: (skip) => undoCells(skip, section, reportMonth),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, reportMonth, section]);

    const runRedo = useCallback(async () => {
        await runHistoryAction({
            direction: "redo",
            pendingWrite: pendingCommitRef.current,
            pendingSkip,
            request: (skip) => redoCells(skip, section, reportMonth),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, reportMonth, section]);

    useUndoRedoShortcuts({
        enabled: collabEnabled && !isArchive && undoAvailableFor(section, isAdmin),
        onUndo: () => void runUndo(),
        onRedo: () => void runRedo(),
    });

    useEffect(() => {
        onUndoState({
            // Undo/redo works on all sections: on «Движение»/«Цены» and on the
            // dictionaries (name, asset-set and user edits).
            enabled: collabEnabled && !isArchive && undoAvailableFor(section, isAdmin),
            offline: collabEnabled && room.status !== "online",
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [
        collabEnabled,
        isAdmin,
        isArchive,
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

    const loadSnapshotView = useCallback(
        async (snapshotId: string) => {
            setSnapshotPending(true);
            try {
                const view = await getMovementSnapshot(snapshotId);
                setSnapshotView(view);
                setSelectedSnapshotId(snapshotId);
                rememberSnapshot(snapshotId);
            } catch (reason) {
                toast.danger("Не удалось открыть архивную версию", {
                    description: errorMessage(reason),
                });
            } finally {
                setSnapshotPending(false);
            }
        },
        [rememberSnapshot, setSelectedSnapshotId],
    );

    const selectWorkingVersion = useCallback(() => {
        setSelectedSnapshotId(null);
        rememberSnapshot(null);
        setSnapshotView(null);
        refresh();
    }, [refresh, rememberSnapshot, setSelectedSnapshotId]);

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

    // A restored version must actually be OPENED (the picker alone would show
    // it selected while the grid still shows the working rows), and one that no
    // longer exists must be dropped. The verdict only settles once the version
    // list has loaded — validating against the empty first-render list is what
    // reset the selection on every reload.
    const versionsKnown = !isMovement || movementVersions !== null;
    const snapshotVerdict = restoredFilterVerdict(
        selectedSnapshotId,
        versionsKnown && movementVersions !== null
            ? movementVersions.map((version) => version.id)
            : null,
    );
    const restoredSnapshotRef = useRef<string | null>(null);
    useEffect(() => {
        if (!isMovement || selectedSnapshotId === null) return;
        if (snapshotVerdict === "clear") {
            // Gone from the archive: fall back to the working version quietly.
            setSelectedSnapshotId(null);
            rememberSnapshot(null);
            return;
        }
        if (snapshotVerdict !== "keep") return;
        // Open a restored version exactly once.
        if (restoredSnapshotRef.current === selectedSnapshotId) return;
        restoredSnapshotRef.current = selectedSnapshotId;
        if (snapshotView === null && !snapshotPending) void loadSnapshotView(selectedSnapshotId);
    }, [
        isMovement,
        loadSnapshotView,
        rememberSnapshot,
        selectedSnapshotId,
        setSelectedSnapshotId,
        snapshotPending,
        snapshotVerdict,
        snapshotView,
    ]);

    if (error) {
        return <GridLoadError error={error} />;
    }

    // Initial load only — the skeleton replaces the grid until the first rows
    // arrive. A later background refresh (e.g. after a cell PATCH) keeps the
    // grid mounted: unmounting it here would reset the scroll to the top on
    // every edit.
    if (rows === null) {
        return <GridLoadingSkeleton />;
    }

    return (
        <>
            <div className="grid-viewport" aria-busy={isMutationPending || snapshotPending}>
                <GridErrorBoundary>
                    <DataGrid
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
                        onServiceColumnsChange={onShowServiceColumnsChange}
                        rowHeight={rowHeight}
                        scrollPosition={{ form: `${scrollForm}:${section}` }}
                        assets={assets}
                        deliveryBases={deliveryBases}
                        remoteLocks={remoteLocks}
                        remotePresence={remotePresence}
                        onLockedCellEdit={gridWarnings.onLockedCellEdit}
                        onAcquireLease={room.acquireLeases}
                        onReleaseLease={room.releaseLeases}
                        onCommit={commit}
                        onPlaceholderCommit={commitPlaceholderPatch}
                        onCommitFailed={refresh}
                        onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                        onValidationError={gridWarnings.onValidationError}
                        onPasteSkipped={gridWarnings.onPasteSkipped}
                        onPasteTruncated={gridWarnings.onPasteTruncated}
                        onViewStatsChange={onGridStatsChange}
                        onClipboardStateChange={handleClipboardState}
                        remoteCellInfo={remoteCellInfo}
                        onSelectionChange={handleSelectionChange}
                        filters={columnFilters}
                        onFiltersChange={setColumnFilters}
                    />
                </GridErrorBoundary>
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
            <ReplaceRecordDialog
                isOpen={replaceOpen}
                isPending={isMutationPending}
                label={recordLabel(selectedRows[0]) ?? ""}
                sectionLabel={SECTION_LABELS[section] ?? section}
                options={replaceTargets}
                onOpenChange={setReplaceOpen}
                onConfirm={(targetId) => void replaceRecord(targetId)}
            />
            <ApproveDialog
                isOpen={approveOpen}
                isPending={approvePending}
                isFinal={movementMeta?.isFinal === true}
                onOpenChange={setApproveOpen}
                onConfirm={() => void runApprove()}
            />
            {section === "users" ? (
                <AddRecordModal
                    isOpen={userCreateOpen}
                    title="Добавить пользователя"
                    defaults={RAW_USER_DEFAULTS}
                    fields={rawUserFields(assets)}
                    isPending={isMutationPending}
                    isVisible={rawUserFieldVisible}
                    validate={validateUser}
                    onSubmit={createUserFromModal}
                    onOpenChange={setUserCreateOpen}
                />
            ) : null}
        </>
    );
}

const WORKING_VERSION_ID = "__working";

function SectionFooter({
    section,
    canDelete,
    canCreateUser,
    undoState,
    approveState,
    clipboardState,
    isFullscreenPending,
    exportPending,
    exportRowCount,
    onExport,
    onDelete,
    deleteBlockedReason,
    canReplace,
    replaceBlockedReason,
    onReplace,
    onCreateUser,
    onCopy,
    onPaste,
    onDropdownOpenChange,
}: {
    section: SectionId;
    canDelete: boolean;
    /** Why delete is disabled (deleting yourself / a user with access). */
    deleteBlockedReason: string | null;
    /** «Заменить» needs exactly one dictionary value selected. */
    canReplace: boolean;
    replaceBlockedReason: string | null;
    onReplace: () => void;
    /** «Пользователи» is created through the modal, so the action is shown. */
    canCreateUser: boolean;
    undoState: UndoState;
    approveState: ApproveState;
    clipboardState: { canPaste: boolean };
    isFullscreenPending: boolean;
    exportPending: boolean;
    exportRowCount?: number | undefined;
    onExport: (scope: "all" | "visible") => void;
    onDelete: () => void;
    onCreateUser: () => void;
    onCopy: () => void;
    onPaste: () => void;
    /** Holds the fullscreen panels open while an action menu is expanded. */
    onDropdownOpenChange?: (open: boolean) => void;
}) {
    return (
        <div className="table-footer">
            <ActionBar
                section={section}
                canDelete={canDelete}
                canCreateUser={canCreateUser}
                undoState={undoState}
                approveState={approveState}
                isArchive={false}
                clipboardState={clipboardState}
                isFullscreenPending={isFullscreenPending}
                exportPending={exportPending}
                exportRowCount={exportRowCount}
                onExport={onExport}
                onDelete={onDelete}
                deleteBlockedReason={deleteBlockedReason}
                canReplace={canReplace}
                replaceBlockedReason={replaceBlockedReason}
                onReplace={onReplace}
                onCreateUser={onCreateUser}
                onCopy={onCopy}
                onPaste={onPaste}
                {...(onDropdownOpenChange && { onDropdownOpenChange })}
            />
        </div>
    );
}

const RAW_MATERIALS_PROJECT_KEY = "raw_materials";

export function RawMaterialsWorkspace({
    currentUser,
    activeProjectKey: controlledProjectKey,
    onProjectChange,
    initialSection = null,
    onSectionChange,
    supportMail,
}: RawMaterialsWorkspaceProps) {
    const [internalProjectKey, setInternalProjectKey] = useState<string>(
        currentUser.access?.some((project) => project.key === RAW_MATERIALS_PROJECT_KEY)
            ? RAW_MATERIALS_PROJECT_KEY
            : (currentUser.access?.[0]?.key ?? RAW_MATERIALS_PROJECT_KEY),
    );
    // The active project decides the storage keys, so it is resolved up front.
    // The report workspace owns the project switcher and drives this component
    // through props; without them the workspace falls back to its own switch.
    const activeProjectKey = controlledProjectKey ?? internalProjectKey;
    const [metaReloadKey, setMetaReloadKey] = useState(0);
    const { data: meta, error } = useAsyncData<RawMaterialsMeta>(
        () => getRawMaterialsMeta(),
        [metaReloadKey],
    );
    const [selectedTabId, setSelectedTabId] = useState<string | null>(initialSection);
    const {
        isFullscreen,
        isFullscreenPending,
        setFullscreenMode,
        setToolbarControlOpen,
        revealClass,
    } = useFullscreenPanels();
    const [gridStats, setGridStats] = useState<GridViewStats | null>(null);
    // Completed cell/row selection of the current user, in the same format as
    // in the «На форме» tooltip (e.g. «Потребность / План, т», строки 3–5).
    // Reported by the grid itself (onSelectionChange), not via the presence
    // echo — so it works even when collaboration is offline.
    const [selectionLabel, setSelectionLabel] = useState<string | null>(null);
    // The same selection spelled out for the status tooltip: the header chain of
    // a single-column selection («Потребность → План, т») — the renovation style.
    const [selectionDetail, setSelectionDetail] = useState<string | null>(null);
    const [clipboardState, setClipboardState] = useState<ClipboardActions>({
        canPaste: false,
        onCopy: () => {},
        onPaste: () => {},
    });
    const handleClipboardActions = useCallback(
        (next: ClipboardActions) => updateClipboardActions(setClipboardState, next),
        [],
    );
    const [profileOpen, setProfileOpen] = useState(false);
    const [user, setUser] = useState(currentUser);
    const [deleteState, setDeleteState] = useState<DeleteActions>({
        canDelete: false,
        blockedReason: null,
        onDelete: () => {},
    });
    // «Заменить» of the mounted dictionary tab (published by `SectionContent`).
    const [replaceState, setReplaceState] = useState<DictionaryReplaceState>({
        show: false,
        canReplace: false,
        blockedReason: null,
        onReplace: () => {},
    });
    const [exportState, setExportState] = useState<ExportState>({
        pending: false,
        onExport: async () => {},
    });
    const [gridResetState, setGridResetState] = useState<{
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }>({ resetColumnWidths: () => {}, resetFiltersSorting: () => {} });
    // Display toggles for the active grid (rendered in the view strip) —
    // shared by every project and remembered between sessions.
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
    // Rebuilt only when the versions change: a fresh array on every render made
    // the picker re-render, which re-rendered the toolbar and looped.
    const versionItems = useMemo<ArchiveItem[]>(
        () => [
            {
                id: WORKING_VERSION_ID,
                name: "Рабочая версия",
                description: "Доступна для редактирования",
            },
            ...archiveState.items,
        ],
        [archiveState.items],
    );

    const [userCreateState, setUserCreateState] = useState<UserCreateState>({
        canCreate: false,
        onCreate: () => {},
    });
    // The report workspace owns the project switcher and drives this component
    // through props; without them the raw-materials workspace falls back to
    // its own internal switcher state.
    const { setTheme } = useTheme();
    const {
        headerPinned,
        showServiceColumns,
        rowHeight,
        setHeaderPinned,
        setShowServiceColumns,
        setRowHeight,
        resetRowHeight,
    } = useViewState(activeProjectKey);

    const handleDeleteState = useCallback(
        (state: DeleteActions) =>
            setDeleteState((previous) => (samePublishedState(previous, state) ? previous : state)),
        [],
    );

    const handleReplaceState = useCallback(
        (state: DictionaryReplaceState) =>
            setReplaceState((previous) => (samePublishedState(previous, state) ? previous : state)),
        [],
    );

    const handleExportState = useCallback(
        (state: ExportState) =>
            setExportState((previous) => (samePublishedState(previous, state) ? previous : state)),
        [],
    );

    const handleUndoState = useCallback(
        (state: UndoState) =>
            setUndoState((previous) => (samePublishedState(previous, state) ? previous : state)),
        [],
    );

    const handleApproveState = useCallback(
        (state: ApproveState) =>
            setApproveState((previous) => (samePublishedState(previous, state) ? previous : state)),
        [],
    );

    const handleArchiveState = useCallback((state: ArchiveState) => setArchiveState(state), []);

    const handleUserCreateState = useCallback(
        (state: UserCreateState) => setUserCreateState(state),
        [],
    );

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
            supportMail={supportMail}
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
                ? ADMIN_SECTIONS.map((referenceSection, index) => ({
                      id: referenceSection,
                      section: referenceSection,
                      label: SECTION_LABELS[referenceSection] ?? referenceSection,
                      reportMonth: null,
                      // The strip separates the forms from the reference
                      // dictionaries, and the users dictionary from both:
                      // main tables <splitter> dictionaries <splitter> users.
                      separatorBefore: index === 0 || referenceSection === "users",
                  }))
                : []),
        ];
    }, [isAdmin, meta]);

    useEffect(() => {
        if (tabs.length === 0) return;
        // No selection yet, or a stale `?section=` that this project has no tab
        // for (e.g. an archived movement period) — fall back to the first tab.
        const firstTab = tabs[0];
        if (firstTab && (selectedTabId === null || !tabs.some((tab) => tab.id === selectedTabId))) {
            setSelectedTabId(firstTab.id);
            onSectionChange?.(firstTab.id);
        }
    }, [selectedTabId, tabs, onSectionChange]);

    // Browser back/forward changes `?section=` without remounting this
    // component (the `key` only tracks the project), so follow the prop.
    useEffect(() => {
        if (initialSection && tabs.some((tab) => tab.id === initialSection)) {
            setSelectedTabId(initialSection);
        }
    }, [initialSection, tabs]);

    const selectedTab = tabs.find((tab) => tab.id === selectedTabId) ?? tabs[0] ?? null;

    // Toolbar ("panel") filters of the OPEN tab, in the URL under its id
    // (`f.materialMovement.…`). This project renders its filters as the grid's
    // own header filters (RevoGrid state, deliberately not in the URL), so the
    // set is empty today — the hook is wired so a future panel filter is
    // restored on reload without inventing a second mechanism.
    const { filters: panelFilters } = useToolbarFilters(selectedTab?.id ?? null);
    void panelFilters;

    const collabEnabled =
        selectedTab !== null && collaborationAvailableFor(selectedTab.section, isAdmin);
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

    if (error) {
        return <WorkspaceLoadError error={error} />;
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
                    <GridLoadingSkeleton />
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
    const firstTab = tabs[0];
    const isFirstTab = firstTab && selectedTab?.id === firstTab.id;
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
                compactLabel="Архив"
                isDisabled={!showVersionPicker}
                onOpenChange={setToolbarControlOpen}
                items={versionItems}
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
                    onChange={(tab) => {
                        setSelectedTabId(tab.id);
                        onSectionChange?.(tab.id);
                    }}
                />
            </div>
            <ViewStrip
                headerPinned={headerPinned}
                onHeaderPinnedChange={setHeaderPinned}
                rowHeight={rowHeight}
                onRowHeightChange={setRowHeight}
                onResetRowHeight={resetRowHeight}
                onReset={handleStripReset}
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
                onDropdownOpenChange={setToolbarControlOpen}
                columnResetLabel="Сбросить ширину столбцов и высоту строк"
            />
        </div>
    );

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                <FormStatus
                    room={room}
                    selectionLabel={selectionLabel}
                    selectionDetail={selectionDetail}
                    gridStats={gridStats}
                />
            </div>
            <SectionFooter
                section={selectedTab.section}
                canDelete={deleteState.canDelete}
                canCreateUser={userCreateState.canCreate}
                undoState={undoState}
                approveState={approveState}
                clipboardState={clipboardState}
                isFullscreenPending={isFullscreenPending}
                exportPending={exportState.pending}
                exportRowCount={exportState.rowCount}
                onExport={(scope) => void exportState.onExport(scope)}
                onDelete={deleteState.onDelete}
                deleteBlockedReason={deleteState.blockedReason}
                canReplace={replaceState.canReplace}
                replaceBlockedReason={replaceState.blockedReason}
                onReplace={replaceState.onReplace}
                onCreateUser={userCreateState.onCreate}
                onCopy={clipboardState.onCopy}
                onPaste={clipboardState.onPaste}
                onDropdownOpenChange={setToolbarControlOpen}
            />
        </div>
    );

    return (
        <main className={isFullscreen ? "app-shell app-shell--fullscreen" : "app-shell"}>
            {header}
            <WorkspaceContent toolbar={toolbar} footer={footer}>
                <SectionContent
                    key={collabGeneration}
                    section={selectedTab.section}
                    reportMonth={selectedTab.reportMonth}
                    isAdmin={isAdmin}
                    panelFilterTabId={selectedTab.id}
                    assets={assets}
                    deliveryBases={deliveryBases}
                    collabEnabled={collabEnabled}
                    firstOpenMonth={meta?.movementPeriods[0] ?? null}
                    room={room}
                    remoteCellValues={remoteCellValues}
                    onDeleteState={handleDeleteState}
                    onReplaceState={handleReplaceState}
                    onExportState={handleExportState}
                    onResetState={setGridResetState}
                    onUserCreateState={handleUserCreateState}
                    headerPinned={headerPinned}
                    showServiceColumns={showServiceColumns}
                    onShowServiceColumnsChange={setShowServiceColumns}
                    rowHeight={rowHeight}
                    scrollForm={activeProjectKey}
                    onUndoState={handleUndoState}
                    onApproveState={handleApproveState}
                    onArchiveState={handleArchiveState}
                    onFixed={handleFixed}
                    onGridStatsChange={setGridStats}
                    onClipboardStateChange={handleClipboardActions}
                    onSelectionLabelChange={setSelectionLabel}
                    onSelectionDetailChange={setSelectionDetail}
                    onRegisterSelectionPreempt={registerSelectionPreempt}
                    currentUserEmail={user.email}
                />
            </WorkspaceContent>
            <WorkspaceFullscreenPanels
                visible={isFullscreen && !isFullscreenPending}
                revealClass={revealClass}
                header={header}
                toolbar={toolbar}
                footer={footer}
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

function ActionBar({
    section,
    canDelete,
    canCreateUser,
    undoState,
    approveState,
    isArchive,
    clipboardState,
    isFullscreenPending,
    exportPending,
    exportRowCount,
    onExport,
    onDelete,
    deleteBlockedReason,
    canReplace,
    replaceBlockedReason,
    onReplace,
    onCreateUser,
    onCopy,
    onPaste,
    onDropdownOpenChange,
}: {
    section: SectionId;
    canDelete: boolean;
    /** Why delete is disabled (deleting yourself / a user with access). */
    deleteBlockedReason: string | null;
    /** «Заменить» needs exactly one dictionary value selected. */
    canReplace: boolean;
    replaceBlockedReason: string | null;
    onReplace: () => void;
    canCreateUser: boolean;
    undoState: UndoState;
    approveState: ApproveState;
    isArchive: boolean;
    clipboardState: { canPaste: boolean };
    isFullscreenPending: boolean;
    exportPending: boolean;
    exportRowCount?: number | undefined;
    onExport: (scope: "all" | "visible") => void;
    onDelete: () => void;
    onCreateUser: () => void;
    onCopy: () => void;
    onPaste: () => void;
    /** Holds the fullscreen panels open while an action menu is expanded. */
    onDropdownOpenChange?: (open: boolean) => void;
}) {
    const showApprove = section === "materialMovement" && !isArchive;
    const showPaste = !isArchive;
    // The button set is the shared vocabulary of `action-items.ts`: every
    // project composes the same builders, so a label, an icon or a protective
    // hint can't drift between forms. What differs per section is only whether
    // a button shows and whether it is enabled.
    const trailingActions: ActionItem[] = [
        ...buildClipboardActions({
            show: true,
            showPaste,
            disabled: isFullscreenPending,
            canPaste: clipboardState.canPaste,
            onCopy,
            onPaste,
        }),
        ...buildHistoryActions({
            show: undoState.enabled,
            offline: undoState.offline,
            canUndo: undoState.canUndo,
            canRedo: undoState.canRedo,
            onUndo: undoState.onUndo,
            onRedo: undoState.onRedo,
        }),
        buildExportAction({
            pending: exportPending,
            disabled: exportPending || isFullscreenPending,
            rowCount: exportRowCount,
            onExport,
        }),
        ...buildReplaceAction({
            // Dictionaries only: the «Движение»/«Цены» forms are not values.
            show: section !== "users" && sectionPolicy(section).rowOps !== "none",
            disabled: !canReplace || isFullscreenPending,
            blockedReason: isFullscreenPending ? null : replaceBlockedReason,
            onReplace,
        }),
        ...buildDeleteAction({
            show: sectionPolicy(section).rowOps !== "none",
            label: "Удалить запись",
            disabled: !canDelete || isFullscreenPending,
            blockedReason: deleteBlockedReason,
            onDelete,
            shortcut: { key: "Del", aria: "Control+Delete" },
        }),
        ...buildAddRowAction({
            show: canCreateUser,
            label: "Добавить пользователя",
            disabled: isFullscreenPending,
            onAdd: onCreateUser,
        }),
        ...buildPrimaryAction({
            show: showApprove,
            id: "fix",
            label: "Зафиксировать",
            icon: createElement(CheckCircle),
            disabled: approveState.pending || !approveState.canApprove,
            onPress: approveState.onApprove,
        }),
    ];
    return <SharedActionBar items={trailingActions} onDropdownOpenChange={onDropdownOpenChange} />;
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
        <ControlledAlertDialog isOpen={isOpen} onOpenChange={onOpenChange}>
            <AlertDialog.Backdrop isDismissable isKeyboardDismissDisabled={false}>
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
                            <ButtonGroup>
                                <Button
                                    variant="tertiary"
                                    onPress={() => onOpenChange(false)}
                                    isDisabled={isPending}
                                >
                                    <Xmark aria-hidden="true" />
                                    Отменить
                                </Button>
                                <Button
                                    variant="primary"
                                    onPress={onConfirm}
                                    isDisabled={isPending}
                                >
                                    <ButtonGroup.Separator />
                                    {isPending ? (
                                        <Spinner color="current" size="sm" />
                                    ) : (
                                        <CheckCircle />
                                    )}
                                    Зафиксировать
                                </Button>
                            </ButtonGroup>
                        </AlertDialog.Footer>
                    </AlertDialog.Dialog>
                </AlertDialog.Container>
            </AlertDialog.Backdrop>
        </ControlledAlertDialog>
    );
}
