"use client";

import { Switch, toast, Typography } from "@heroui/react";
import { CellMergePlugin, type MergeData } from "@revolist/rv-pro-trial";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import {
    KPPK_REALIZATION_SLICES,
    KppkRealizationSection,
} from "@/features/kppk/kppk-realization-section";
import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    AppHeader,
    type ExportMode,
    type ExportState,
} from "@/features/raw-materials/raw-materials-workspace";
import {
    createOrg,
    createProductPosition,
    createRaw,
    createKppkUser,
    deleteKppkUser,
    getKppkUsers,
    updateKppkUserCell,
    type KppkUserRow,
    deleteOrg,
    deleteProductPosition,
    replaceOrgRow,
    replaceProductPosition,
    replaceRawRow,
    deleteRaw,
    exportKppkTable,
    getKppkUndoState,
    getOrgRows,
    getProductPositions,
    getRawRows,
    redoKppkCells,
    undoKppkCells,
    updateOrgCell,
    updateProductPositionCell,
    updateRawCell,
    type KppkOrgRow,
    type KppkProductPositionRow,
    type KppkRawRow,
    type KppkRealizationSectionId,
    type KppkRealizationSlice,
    type KppkSectionId,
    type KppkUndoResult,
} from "@/shared/api/kppk";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import { useCollabRoom, type CollabRoom } from "@/shared/collab/use-collab-room";
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import {
    exportFileName,
    KPPK_GROUPED_POSITION_DESCRIPTORS,
    KPPK_POSITION_DESCRIPTORS,
    type ColumnDescriptor,
    type GridRow,
    type ReferenceOption,
} from "@/shared/grid/grid-config";
import { parseNumericText } from "@/shared/grid/grid-edit";
import {
    RawMaterialsGrid,
    type GridViewStats,
    type RawMaterialsGridHandle,
} from "@/shared/grid/raw-materials-grid";
import {
    collaborationAvailableFor,
    undoAvailableFor,
    exportAvailableFor,
    sectionPolicy,
} from "@/shared/grid/section-policy";
import { SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useRestoredState } from "@/shared/hooks/use-restored-state";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import {
    NO_ROW_SELECTED_REASON,
    buildClipboardActions,
    buildDeleteAction,
    buildReplaceAction,
    buildExportAction,
    reconcileExportState,
    buildHistoryActions,
    buildAddRowAction,
    DeleteRecordDialog,
} from "@/shared/ui/action-items";
import {
    AddRecordModal,
    type AddRecordField,
    type AddRecordValues,
} from "@/shared/ui/add-record-modal";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import { applyHistoryResult, runHistoryAction } from "@/shared/ui/history-action";
import { readWorkspaceQuery, writeWorkspaceQuery } from "@/shared/ui/query-state";
import { ReplaceRecordDialog, type ReplaceTargetOption } from "@/shared/ui/replace-record-dialog";
import { SearchAutocomplete } from "@/shared/ui/search-autocomplete";
import { SmartSelect } from "@/shared/ui/smart-select";
import { runTableExport } from "@/shared/ui/table-export-action";
import {
    publishSelection,
    type GridSelectionInfo,
    updateClipboardActions,
} from "@/shared/ui/text-helpers";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useViewState } from "@/shared/ui/use-view-state";
import { ViewStrip } from "@/shared/ui/view-strip";
import { GridLoadError } from "@/shared/ui/workspace-feedback";
import { WorkspaceContent, WorkspaceFullscreenPanels } from "@/shared/ui/workspace-layout";
import { useDeleteShortcut, useUndoRedoShortcuts } from "@/shared/ui/workspace-shortcuts";

/** Standard grid toasts: see `gridWarningHandlers`. */
const gridWarnings = gridWarningHandlers({ lockedVerb: "редактирует" });

/**
 * The «КППК» workspace of the source-copy phase: three static dictionary tabs
 * over the copies of the source tables («Продуктовые позиции», «Сырьевые
 * позиции», «ДЗО»). The «Периоды» dictionary stays backend-only — no tab
 * until the «Таблицы.xlsx» phase. Undo/redo and the Excel export come from the
 * shared project mechanisms: every edit goes through the collaboration journal
 * (scoped per tab and page-lifetime session id) and the export reuses the
 * «Сырьё»/«Ремонт» view-strip vocabulary. The tabs are visible to every
 * project member; editing is admin-only (enforced server-side and mirrored by
 * the read-only grid).
 */

const KPPK_PROJECT_KEY = "kppk";

// The tab strip's tabs: «Реализация» carries the realization room id (the
// per-slice scope is decided by the section), the dictionaries the grid id.
type KppkTab =
    | (WorkspaceTab & { id: "kppkRealization"; section: KppkRealizationSectionId })
    | (WorkspaceTab & { id: "kppkCost"; section: "kppkCost" })
    | (WorkspaceTab & { id: KppkSectionId; section: KppkSectionId });

const KPPK_TABS: readonly KppkTab[] = [
    {
        // «Реализация»: the room section follows the slice storage; the tab
        // carries the plan section (the render branch recognizes it).
        id: "kppkRealization",
        section: "kppkRealizationPlan",
        label: "Реализация",
        reportMonth: null,
    },
    { id: "kppkCost", section: "kppkCost", label: "Себестоимость", reportMonth: null },
    {
        id: "kppkPositions",
        section: "kppkPositions",
        label: "Продуктовые позиции",
        reportMonth: null,
        separatorBefore: true,
    },
    { id: "kppkRaws", section: "kppkRaws", label: "Сырьевые позиции", reportMonth: null },
    { id: "kppkOrgs", section: "kppkOrgs", label: "ДЗО", reportMonth: null },
    { id: "kppkUsers", section: "kppkUsers", label: "Пользователи", reportMonth: null },
];

/** Tab captions by section id — the «Заменить» dialog names the dictionary. */
const KPPK_SECTION_TITLES: Record<string, string> = Object.fromEntries(
    KPPK_TABS.map((tab) => [tab.section, tab.label]),
);

/** The «Реализация» slice filter's options (the workspace toolbar's select). */
const REALIZATION_SLICE_OPTIONS = KPPK_REALIZATION_SLICES.map(({ id, label }) => ({
    id,
    name: label,
}));

/** «Январь»…«Декабрь» for the «за <месяц>» selector — 1-based ids, lowercased as a
 * sentence member («за январь 2026»). */
const REALIZATION_MONTH_OPTIONS = [
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
].map((name, index) => ({ id: String(index + 1), name }));

/** One stable empty list: a fresh `[]` would rebuild the grid columns. */
const NO_REFERENCES: ReferenceOption[] = [];

const PLACEHOLDER_ID_PREFIX = "new";

// A tab reports its own undo/export state on mount; until then the footer
// shows the neutral disabled state of the freshly mounted tab.
const EMPTY_UNDO_ACTIONS: KppkUndoActions = {
    canUndo: false,
    canRedo: false,
    onUndo: () => {},
    onRedo: () => {},
};
const EMPTY_EXPORT_STATE: ExportState = { pending: false, rowCount: 0, onExport: async () => {} };

type KppkRow = KppkProductPositionRow | KppkRawRow | KppkOrgRow | KppkUserRow;

const USER_FIELDS: AddRecordField[] = [
    { key: "email", label: "Email", kind: "text" },
    { key: "fullname", label: "ФИО", kind: "text" },
    {
        key: "role",
        label: "Роль",
        kind: "select",
        options: [{ id: "admin", name: "Администратор" }],
    },
];
const USER_DEFAULTS: AddRecordValues = { email: "", fullname: "", role: "admin" };

type KppkDraft = Record<string, unknown>;

export type DictionaryReplaceState = {
    /** Whether the section is a replaceable dictionary at all. */
    show?: boolean;
    canReplace: boolean;
    blockedReason: string | null;
    onReplace: () => void;
};

type ClipboardActions = { canPaste: boolean; onCopy: () => void; onPaste: () => void };

/** Undo/redo availability of the mounted tab, reported to the workspace footer. */
type KppkUndoActions = {
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
};

function toGridRow<T extends { id: string }>(row: T): GridRow {
    const shortNames = (row as { orgShortNames?: unknown }).orgShortNames;
    return {
        ...row,
        // The cell TEXT of the grouped «ДЗО» column: the assets' short names.
        // `orgNames` stays the editor's value (the full names it writes back).
        ...(Array.isArray(shortNames) && shortNames.length > 0
            ? { orgNamesDisplay: shortNames.join(", ") }
            : {}),
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: false,
    };
}

/**
 * The editable fields of each tab — the same list the collaboration room
 * validates (`back/src/collab/access.py`, `KPPK_SECTIONS`) and the PATCH
 * schemas accept. Used for `canonicalCells`, without which leases have no keys.
 */
const KPPK_EDITABLE_FIELDS: Record<KppkSectionId, readonly string[]> = {
    // `orgNames` (the grouped default) and `hasExcise` (the «Настройка акциза»
    // view) belong to `kppkPositions` too: the same tab, two shapes.
    kppkPositions: ["productNum", "name", "orgNames", "hasExcise"],
    kppkRaws: ["rawNum", "name"],
    kppkOrgs: ["code", "name"],
    kppkUsers: ["email", "fullname", "isAdmin", "isActive"],
};

/** The empty draft behind the trailing placeholder row of each dictionary. */
function initialDraft(section: KppkSectionId, exciseMode = false): KppkDraft {
    switch (section) {
        case "kppkPositions":
            // Grouped (default): one product + its ДЗО multi-select. Excise
            // mode: one position (a single ДЗО) plus its flag.
            return exciseMode
                ? { productNum: null, name: "", orgName: "", hasExcise: false }
                : { productNum: null, name: "", orgNames: [] };
        case "kppkRaws":
            return { rawNum: null, name: "" };
        case "kppkOrgs":
            return { code: "", name: "" };
        case "kppkUsers":
            return {};
    }
}

function placeholderRow(section: KppkSectionId, draft: KppkDraft): GridRow {
    return {
        ...draft,
        id: `${PLACEHOLDER_ID_PREFIX}:${section}`,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: true,
    };
}

function draftNumber(value: unknown): number | null {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") return parseNumericText(value);
    return null;
}

/**
 * The create call of a placeholder row once its mandatory cells are filled.
 * `null` while something is missing — the draft keeps accumulating cells.
 */
function placeholderCreate(
    section: KppkSectionId,
    draft: KppkDraft,
    exciseMode = false,
): (() => Promise<unknown>) | null {
    switch (section) {
        case "kppkPositions": {
            const name = draftText(draft.name);
            if (!name) return null;
            if (exciseMode) {
                // One position of the «Настройка акциза» view: exactly one ДЗО.
                const orgName = draftText(draft.orgName);
                if (!orgName) return null;
                return () =>
                    createProductPosition({
                        name,
                        orgName,
                        hasExcise: draft.hasExcise === true,
                        productNum: draftNumber(draft.productNum),
                    });
            }
            const orgNames = Array.isArray(draft.orgNames)
                ? draft.orgNames
                      .filter((item): item is string => typeof item === "string")
                      .map((item) => item.trim())
                      .filter((item) => item.length > 0)
                : [];
            // A product is typed once; its ДЗО set may be empty and filled in
            // later — the positions are a separate structure over the product.
            return () =>
                createProductPosition({
                    name,
                    orgNames,
                    productNum: draftNumber(draft.productNum),
                });
        }
        case "kppkRaws": {
            const name = draftText(draft.name);
            return name ? () => createRaw({ name, rawNum: draftNumber(draft.rawNum) }) : null;
        }
        case "kppkOrgs": {
            const code = draftText(draft.code);
            const name = draftText(draft.name);
            if (!code || !name) return null;
            return () => createOrg({ code, name });
        }
        case "kppkUsers":
            return null;
    }
}

// The per-row endpoints are applied one by one: every PATCH takes the project
// write lock, and the order of the cells of one row stays deterministic
// (e.g. the «Продуктовые позиции» name before its ДЗО links).
async function updateCells(section: KppkSectionId, updates: CellUpdate[]): Promise<void> {
    for (const update of updates) {
        switch (section) {
            case "kppkPositions":
                await updateProductPositionCell(update.rowId, update.field, update.value);
                break;
            case "kppkRaws":
                await updateRawCell(update.rowId, update.field, update.value);
                break;
            case "kppkOrgs":
                await updateOrgCell(update.rowId, update.field, update.value);
                break;
            case "kppkUsers":
                await updateKppkUserCell(update.rowId, update.field, update.value);
                break;
        }
    }
}

async function deleteRecord(section: KppkSectionId, id: string): Promise<void> {
    switch (section) {
        case "kppkPositions":
            await deleteProductPosition(id);
            break;
        case "kppkRaws":
            await deleteRaw(id);
            break;
        case "kppkOrgs":
            await deleteOrg(id);
            break;
        case "kppkUsers":
            await deleteKppkUser(id);
            break;
    }
}

function KppkSection({
    section,
    isAdmin,
    assets,
    headerPinned,
    showServiceColumns,
    rowHeight,
    onDeleteState,
    onReplaceState,
    onClipboardStateChange,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onRoomChange,
    onResetState,
    onUndoStateChange,
    onExportState,
    onUserCreateState,
    currentUserEmail,
    exciseMode,
}: {
    section: KppkSectionId;
    isAdmin: boolean;
    /** The ДЗО reference list feeding the «Продуктовые позиции» multi-column. */
    assets: ReferenceOption[];
    headerPinned: boolean;
    showServiceColumns: boolean;
    rowHeight: number;
    onDeleteState: (state: { canDelete: boolean; onDelete: () => void }) => void;
    /** Publishes the shared ?????????? action of this tab to the footer. */
    onReplaceState: (state: DictionaryReplaceState) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    onGridStatsChange: (stats: GridViewStats) => void;
    onSelectionLabelChange: (label: string | null) => void;
    /** The same selection spelled out for the status tooltip (header chain). */
    onSelectionDetailChange?: (detail: string | null) => void;
    /** Publishes this tab's collaboration room to the footer status. */
    onRoomChange?: (room: CollabRoom | null) => void;
    /** Registers the display-reset handlers of this grid in the view strip. */
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    /** Registers the undo/redo actions of this tab in the footer bar. */
    onUndoStateChange: (state: KppkUndoActions) => void;
    /** Registers the Excel export action of this tab in the footer bar. */
    onExportState: (state: ExportState) => void;
    currentUserEmail: string | undefined;
    onUserCreateState: (state: { canCreate: boolean; onCreate: () => void }) => void;
    /** «Настройка акциза»: one row per product × ДЗО, with the excise flag. */
    exciseMode: boolean;
}) {
    const loader = useMemo<() => Promise<KppkRow[]>>(() => {
        switch (section) {
            case "kppkPositions":
                // «Настройка акциза» lists one row per position; the default
                // groups a product with its whole ДЗО set.
                return () => getProductPositions(exciseMode ? "positions" : "grouped");
            case "kppkRaws":
                return getRawRows;
            case "kppkOrgs":
                return getOrgRows;
            case "kppkUsers":
                return getKppkUsers;
        }
    }, [exciseMode, section]);
    const [reloadKey, setReloadKey] = useState(0);
    const { data: rows, error } = useAsyncData<KppkRow[]>(loader, [loader, reloadKey]);
    // Real-time collaboration of this tab: the room is admin-scoped (only
    // admins may edit the dictionaries), so ordinary members don't join it.
    // The page-lifetime session id travels in the mutations and leases.
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, unknown>>(new Map());
    const selfCellHandlerRef = useRef<(event: CollabCellChanged) => void>(() => {});
    const room = useCollabRoom({
        projectKey: KPPK_PROJECT_KEY,
        section,
        reportMonth: null,
        enabled: collaborationAvailableFor(section, isAdmin),
        onCellChanged: (event) => selfCellHandlerRef.current(event),
        onRowsChanged: () => refresh(),
        onReconnect: () => {
            setRemoteCellValues(new Map());
            refresh();
        },
    });
    // Another admin changed a cell: keep it in memory and merge it into the
    // rows below (the local grid must not refetch on every keystroke).
    const meId = room.me?.userId ?? null;
    selfCellHandlerRef.current = (event) => {
        if (event.user.userId === meId) return;
        const key = `${section === "kppkUsers" ? "app_users" : section}/${event.rowId}/${event.field}`;
        setRemoteCellValues((current) => new Map(current).set(key, event.value));
    };
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [userCreateOpen, setUserCreateOpen] = useState(false);
    const { data: authConfig } = useAsyncData<{ keycloakEnabled: boolean } | null>(
        () =>
            fetch("/api/auth/config")
                .then((response) => (response.ok ? response.json() : null))
                .catch(() => null),
        [],
    );
    const keycloakEnabled = authConfig?.keycloakEnabled === true;
    const openUserCreate = useCallback(() => setUserCreateOpen(true), []);
    useEffect(() => {
        onUserCreateState({
            canCreate: isAdmin && section === "kppkUsers",
            onCreate: openUserCreate,
        });
    }, [isAdmin, section, openUserCreate, onUserCreateState]);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [placeholderDraft, setPlaceholderDraft] = useState<KppkDraft>(() =>
        initialDraft(section, exciseMode),
    );
    const placeholderDraftRef = useRef(placeholderDraft);
    const createPendingRef = useRef(false);
    const gridRef = useRef<RawMaterialsGridHandle>(null);

    useEffect(() => {
        placeholderDraftRef.current = placeholderDraft;
    }, [placeholderDraft]);

    // Switching a tab's shape (or the tab itself) drops the half-filled draft:
    // the cells of the grouped view mean nothing in the excise view.
    useEffect(() => {
        const reset = initialDraft(section, exciseMode);
        placeholderDraftRef.current = reset;
        setPlaceholderDraft(reset);
    }, [exciseMode, section]);

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);

    // The footer shows this tab's presence while it is mounted; the tab is
    // keyed by section, so the cleanup clears the previous tab's room.
    useEffect(() => {
        onRoomChange?.(room);
        return () => onRoomChange?.(null);
    }, [onRoomChange, room]);

    // The last in-flight write: undo/redo waits for it, so an immediate Ctrl+Z
    // after a cell edit undoes that edit and not the previous action.
    const pendingWriteRef = useRef<Promise<unknown> | null>(null);

    function track<T>(write: Promise<T>): Promise<T> {
        pendingWriteRef.current = write;
        return write;
    }

    // Undo/redo live on the server (the shared collaboration journal, scoped
    // per tab and page-lifetime session id), so the counters are re-read after
    // every mutation and after every undo/redo attempt.
    const [undoCounts, setUndoCounts] = useState({ canUndo: false, canRedo: false });
    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);

    const refreshUndoState = useCallback(() => {
        void getKppkUndoState(section)
            .then((state) => setUndoCounts({ canUndo: state.canUndo, canRedo: state.canRedo }))
            .catch(() => {});
    }, [section]);

    useEffect(() => {
        if (!isAdmin) return;
        refreshUndoState();
    }, [isAdmin, refreshUndoState]);

    const applyUndoResult = useCallback(
        (result: KppkUndoResult, direction: "undo" | "redo") => {
            applyHistoryResult({
                result,
                direction,
                setPendingSkip,
                refresh,
                refreshState: refreshUndoState,
                businessRuleFields: ["__created", "__deleted"],
            });
        },
        [refresh, refreshUndoState],
    );

    const runUndo = useCallback(async () => {
        await runHistoryAction({
            direction: "undo",
            pendingWrite: pendingWriteRef.current,
            pendingSkip,
            request: (skip) => undoKppkCells(skip, section),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, section]);

    const runRedo = useCallback(async () => {
        await runHistoryAction({
            direction: "redo",
            pendingWrite: pendingWriteRef.current,
            pendingSkip,
            request: (skip) => redoKppkCells(skip, section),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, section]);

    // Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z): event.code — the physical key, works in
    // the Russian layout. Non-admins have nothing to undo — no listener.
    useUndoRedoShortcuts({
        enabled: undoAvailableFor(section, isAdmin),
        onUndo: () => void runUndo(),
        onRedo: () => void runRedo(),
    });

    const [exportPending, setExportPending] = useState(false);

    const exportTable = useCallback(
        (mode: ExportMode) =>
            runTableExport({
                pending: exportPending,
                gridRef,
                setPending: setExportPending,
                fileName: () => exportFileName(section, null),
                request: (state, fileName) =>
                    exportKppkTable({
                        section,
                        headerPinned,
                        columns: state.columns,
                        rowIds: mode === "visible" ? state.rowIds : null,
                        fileName,
                    }),
            }),
        [exportPending, headerPinned, section],
    );

    useEffect(() => {
        onExportState({
            pending: exportPending,
            rowCount: rows?.length ?? 0,
            onExport: exportTable,
        });
    }, [exportPending, exportTable, rows, onExportState]);

    useEffect(() => {
        onUndoStateChange({
            canUndo: undoCounts.canUndo,
            canRedo: undoCounts.canRedo,
            onUndo: runUndo,
            onRedo: runRedo,
        });
    }, [onUndoStateChange, runRedo, runUndo, undoCounts]);

    const selectedRows = useMemo(
        () => (rows ?? []).filter((row) => selectedRowIds.includes(row.id)),
        [rows, selectedRowIds],
    );
    const canDelete =
        isAdmin &&
        selectedRows.length > 0 &&
        (section !== "kppkUsers" ||
            selectedRows.every(
                (row) => "isActive" in row && !row.isActive && row.email !== currentUserEmail,
            ));

    // «Заменить»: raw items and ДЗО merge into another value; on «Продуктовые
    // позиции» the picked row is a product × ДЗО position but the replacement
    // works on the PRODUCT — its positions move to the target.
    const replaceable =
        section === "kppkRaws" || section === "kppkOrgs" || section === "kppkPositions";
    const replaceBlockedReason = !replaceable
        ? null
        : selectedRows.length === 0
          ? "Выберите запись для замены."
          : selectedRows.length > 1
            ? "Выберите ровно одну запись."
            : null;
    const canReplace =
        isAdmin && replaceable && selectedRows.length === 1 && replaceBlockedReason === null;
    const replaceTargets: ReplaceTargetOption[] = useMemo(() => {
        if (!replaceable || selectedRows.length !== 1) return [];
        if (section === "kppkPositions") {
            // One option per PRODUCT: the same product at three assets is three
            // rows, so the list is deduplicated by the product id.
            const positionRows = rows as KppkProductPositionRow[] | null | undefined;
            const current = (selectedRows[0] as KppkProductPositionRow | undefined)?.productId;
            const seen = new Set<string>();
            return (positionRows ?? [])
                .filter((row) => {
                    const productId = row.productId;
                    if (!productId || productId === current || seen.has(productId)) return false;
                    seen.add(productId);
                    return true;
                })
                .map((row) => ({ id: row.productId, name: String(row.name ?? row.productId) }));
        }
        const currentId = selectedRows[0]?.id;
        return (rows ?? [])
            .filter((row) => row.id !== currentId)
            .map((row) => ({
                id: row.id,
                name: String((row as { name?: unknown }).name ?? row.id),
            }));
    }, [replaceable, rows, section, selectedRows]);
    const [replaceOpen, setReplaceOpen] = useState(false);

    useEffect(() => {
        onReplaceState({
            show: replaceable,
            canReplace,
            blockedReason: replaceBlockedReason,
            onReplace: () => setReplaceOpen(true),
        });
    }, [canReplace, onReplaceState, replaceBlockedReason, replaceable]);

    async function replaceRecord(targetId: string | null) {
        if (!canReplace) return;
        const row = selectedRows[0];
        if (!row) return;
        setIsMutationPending(true);
        try {
            if (section === "kppkRaws") {
                await track(replaceRawRow(row.id, targetId));
            } else if (section === "kppkPositions") {
                // The row is a product × ДЗО position, but the replacement
                // works on the PRODUCT: its positions move to the target.
                await track(replaceProductPosition(row.id, targetId));
            } else {
                await track(replaceOrgRow(row.id, targetId));
            }
            gridRef.current?.clearSelection();
            setSelectedRowIds([]);
            setReplaceOpen(false);
            refresh();
            toast.success("Значение заменено");
        } catch (reason) {
            toast.danger("Не удалось заменить значение", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
            refreshUndoState();
        }
    }

    // Ctrl+Delete — the shortcut advertised on the action-bar delete button:
    // opens the same confirmation dialog as the button press.
    const openDeleteDialog = useCallback(() => setDeleteOpen(true), []);
    useDeleteShortcut(canDelete, openDeleteDialog);

    useEffect(() => {
        onDeleteState({ canDelete, onDelete: () => setDeleteOpen(true) });
    }, [canDelete, onDeleteState]);

    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => void gridRef.current?.resetFiltersSorting(),
        });
    }, [onResetState]);

    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);

    const handleSelectionChange = useCallback(
        (selection: GridSelectionInfo) =>
            publishSelection({
                selection,
                room,
                onLabel: onSelectionLabelChange,
                onDetail: onSelectionDetailChange,
            }),
        [onSelectionDetailChange, onSelectionLabelChange, room],
    );

    const gridRows = useMemo(() => {
        // «Продуктовые позиции» carries two cell sets over the same stored
        // positions: the default groups a product with its ДЗО multi-select,
        // «Настройка акциза» lists one position (a single ДЗО + the flag).
        const fields = KPPK_EDITABLE_FIELDS[section].filter((field) => {
            if (section !== "kppkPositions") return true;
            if (field === "orgNames") return !exciseMode;
            if (field === "orgName" || field === "hasExcise") return exciseMode;
            return true;
        });
        const base = (rows ?? []).map((row) => {
            const gridRow = toGridRow(row);
            gridRow.readOnlyRow =
                section === "kppkUsers" &&
                "email" in row &&
                row.email.toLowerCase() === currentUserEmail?.toLowerCase();
            if (!isAdmin) return gridRow;
            // Cell keys make the row reachable by the room's leases/presence;
            // the remote values applied here come from another admin's PATCH.
            gridRow.canonicalCells = {};
            for (const field of fields) {
                const key = `${section === "kppkUsers" ? "app_users" : section}/${row.id}/${field}`;
                gridRow.canonicalCells[field] = key;
                const remote = remoteCellValues.get(key);
                if (remote !== undefined) gridRow[field] = remote;
            }
            return gridRow;
        });
        return isAdmin && section !== "kppkUsers"
            ? [...base, placeholderRow(section, placeholderDraft)]
            : base;
    }, [currentUserEmail, exciseMode, isAdmin, placeholderDraft, remoteCellValues, rows, section]);

    // The two shapes of «Продуктовые позиции»: one column set each.
    const [sorted, setSorted] = useState(false);
    const columnDescriptors = useMemo<ColumnDescriptor[] | undefined>(() => {
        if (section !== "kppkPositions") return undefined;
        return exciseMode ? KPPK_POSITION_DESCRIPTORS : KPPK_GROUPED_POSITION_DESCRIPTORS;
    }, [exciseMode, section]);

    // The positions view merges the «№» and «Продукт» cells of each product's
    // run, so a product with three assets reads as one block (the source
    // matrix). The grouped view has one row per product already — nothing to
    // merge there.
    const cellMerge = useMemo<MergeData[]>(() => {
        if (section !== "kppkPositions" || !exciseMode || sorted) return [];
        const data = (rows ?? []) as unknown as { productId?: string; name?: string }[];
        const sweeps: MergeData[] = [];
        let start = -1;
        const push = (from: number, to: number) => {
            if (from < 0 || to - from < 2) return;
            for (const column of [0, 1]) sweeps.push({ row: from, column, rowSpan: to - from });
        };
        for (let i = 0; i < data.length; i += 1) {
            const isGroupStart = i === 0 || data[i]!.productId !== data[i - 1]!.productId;
            if (!isGroupStart) continue;
            if (start >= 0) push(start, i);
            start = i;
        }
        if (start >= 0) push(start, data.length);
        return sweeps;
    }, [exciseMode, rows, section, sorted]);

    const readOnly = isMutationPending || !isAdmin;

    async function commit(updates: CellUpdate[]): Promise<boolean> {
        if (!isAdmin || updates.length === 0) return true;
        setIsMutationPending(true);
        try {
            await track(updateCells(section, updates));
            refresh();
            return true;
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
            return false;
        } finally {
            setIsMutationPending(false);
            refreshUndoState();
        }
    }

    async function commitPlaceholderPatch(patch: Record<string, unknown>): Promise<boolean> {
        if (!isAdmin || createPendingRef.current) return true;
        const next = { ...placeholderDraftRef.current, ...patch };
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        const create = placeholderCreate(section, next, exciseMode);
        if (!create) return true;
        createPendingRef.current = true;
        setIsMutationPending(true);
        try {
            await track(create());
            const reset = initialDraft(section, exciseMode);
            placeholderDraftRef.current = reset;
            setPlaceholderDraft(reset);
            refresh();
            toast.success("Запись добавлена");
        } catch (reason) {
            toast.danger("Не удалось добавить запись", { description: errorMessage(reason) });
        } finally {
            createPendingRef.current = false;
            setIsMutationPending(false);
            refreshUndoState();
        }
        return true;
    }

    async function deleteRecords() {
        if (!canDelete) return;
        setIsMutationPending(true);
        try {
            for (const row of selectedRows) await track(deleteRecord(section, row.id));
            gridRef.current?.clearSelection();
            setSelectedRowIds([]);
            setDeleteOpen(false);
            refresh();
            toast.success(selectedRows.length > 1 ? "Записи удалены" : "Запись удалена");
        } catch (reason) {
            toast.danger("Не удалось удалить запись", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
            refreshUndoState();
        }
    }

    if (error) {
        return <GridLoadError error={error} />;
    }

    // Initial load only — the skeleton replaces the grid until the first rows
    // arrive. A later background refresh (after a PATCH) keeps the grid
    // mounted: unmounting it here would reset the scroll to the top on every
    // edit.
    if (rows === null) {
        return <GridLoadingSkeleton />;
    }

    return (
        <>
            <div className="grid-viewport" aria-busy={isMutationPending}>
                <GridErrorBoundary>
                    <RawMaterialsGrid
                        ref={gridRef}
                        section={section}
                        rows={gridRows}
                        isAdmin={isAdmin}
                        keycloakEnabled={keycloakEnabled}
                        showAssetColumn={false}
                        readOnly={readOnly}
                        hasBaseline={false}
                        headerPinned={headerPinned}
                        showServiceColumns={showServiceColumns}
                        rowHeight={rowHeight}
                        assets={assets}
                        deliveryBases={NO_REFERENCES}
                        onCommit={commit}
                        onPlaceholderCommit={commitPlaceholderPatch}
                        onCommitFailed={refresh}
                        remoteLocks={remoteLocks}
                        remotePresence={remotePresence}
                        remoteCellInfo={remoteCellInfo}
                        onAcquireLease={(keys) => room.acquireLeases(keys)}
                        onReleaseLease={(keys) => room.releaseLeases(keys)}
                        onLockedCellEdit={gridWarnings.onLockedCellEdit}
                        onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                        onValidationError={gridWarnings.onValidationError}
                        onPasteSkipped={gridWarnings.onPasteSkipped}
                        onPasteTruncated={gridWarnings.onPasteTruncated}
                        onViewStatsChange={onGridStatsChange}
                        onClipboardStateChange={handleClipboardState}
                        onSelectionChange={handleSelectionChange}
                        {...(columnDescriptors && { columnDescriptors })}
                        plugins={cellMerge.length > 0 ? [CellMergePlugin] : []}
                        cellMerge={cellMerge}
                        onSortingChange={setSorted}
                    />
                </GridErrorBoundary>
                {gridRows.length === 0 ? (
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
                sectionLabel={KPPK_SECTION_TITLES[section] ?? section}
                options={replaceTargets}
                onOpenChange={setReplaceOpen}
                onConfirm={(targetId) => void replaceRecord(targetId)}
            />
            {section === "kppkUsers" && (
                <AddRecordModal
                    isOpen={userCreateOpen}
                    onOpenChange={setUserCreateOpen}
                    title="Добавить пользователя"
                    description="Роль — администратор. Связка с ДЗО пока не задаётся."
                    fields={USER_FIELDS}
                    defaults={USER_DEFAULTS}
                    isPending={isMutationPending}
                    validate={(values) =>
                        !z.email().safeParse(draftText(values.email)).success
                            ? "Введите корректный email."
                            : !draftText(values.fullname)
                              ? "Введите ФИО."
                              : null
                    }
                    onSubmit={async (values) => {
                        setIsMutationPending(true);
                        try {
                            await track(
                                createKppkUser({
                                    email: draftText(values.email).toLowerCase(),
                                    fullname: draftText(values.fullname),
                                    isAdmin: true,
                                }),
                            );
                            setUserCreateOpen(false);
                            refresh();
                            refreshUndoState();
                        } finally {
                            setIsMutationPending(false);
                        }
                    }}
                />
            )}
        </>
    );
}

function draftText(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function recordLabel(row: Record<string, unknown> | undefined): string | null {
    if (!row) return null;
    const candidates = [row.name, row.code];
    const value = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
    return typeof value === "string" ? value : null;
}

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
}

export function KppkWorkspace({
    currentUser,
    activeProjectKey: controlledProjectKey,
    onProjectChange,
    initialSection = null,
    onSectionChange,
    supportMail,
}: {
    currentUser: CurrentUser;
    /** Controlled project key (used by the report-workspace project switcher). */
    activeProjectKey?: string;
    onProjectChange?: (key: string) => void;
    /** Tab restored from `?section=` (one of the dictionary ids). */
    initialSection?: string | null;
    /** Mirrors the active tab into the URL so a reload keeps it. */
    onSectionChange?: (section: string | null) => void;
    /** `SUPPORT_MAIL` - the mailbox of the header support control. */
    supportMail: string;
}) {
    const [user, setUser] = useState(currentUser);
    const [userCreateState, setUserCreateState] = useState({
        canCreate: false,
        onCreate: () => {},
    });
    const [profileOpen, setProfileOpen] = useState(false);
    const [selectedTabId, setSelectedTabId] = useState<string | null>(initialSection);
    const [internalProjectKey, setInternalProjectKey] = useState<string>(
        currentUser.access?.some((project) => project.key === KPPK_PROJECT_KEY)
            ? KPPK_PROJECT_KEY
            : (currentUser.access?.[0]?.key ?? KPPK_PROJECT_KEY),
    );
    const { setTheme } = useTheme();
    const {
        isFullscreen,
        isFullscreenPending,
        setFullscreenMode,
        setToolbarControlOpen,
        revealClass,
    } = useFullscreenPanels();
    const [gridStats, setGridStats] = useState<GridViewStats | null>(null);
    const [selectionLabel, setSelectionLabel] = useState<string | null>(null);
    const [selectionDetail, setSelectionDetail] = useState<string | null>(null);
    // The collaboration room of the mounted tab (registered by it) — the footer
    // shows its presence when the user is an admin who can join.
    const [sectionRoom, setSectionRoom] = useState<CollabRoom | null>(null);
    const [clipboardState, setClipboardState] = useState<ClipboardActions>({
        canPaste: false,
        onCopy: () => {},
        onPaste: () => {},
    });
    const handleClipboardActions = useCallback(
        (next: ClipboardActions) => updateClipboardActions(setClipboardState, next),
        [],
    );
    const [undoActions, setUndoActions] = useState<KppkUndoActions>(EMPTY_UNDO_ACTIONS);
    const [exportState, setExportState] = useState<ExportState>(EMPTY_EXPORT_STATE);
    const [deleteState, setDeleteState] = useState<{ canDelete: boolean; onDelete: () => void }>({
        canDelete: false,
        onDelete: () => {},
    });
    // «Заменить» of the mounted dictionary tab (published by `KppkSection`).
    const [replaceState, setReplaceState] = useState<DictionaryReplaceState>({
        show: false,
        canReplace: false,
        blockedReason: null,
        onReplace: () => {},
    });
    // The «Реализация» tab's filter state lives at workspace level: its
    // controls render in the toolbar row (alongside the tabs), like every
    // other subproject. A ДЗО is always selected — the first dictionary row
    // until the user picks one; the period defaults to the loaded view's
    // (server-fixed) month/year until a selection is made.
    const [realizationOrgId, setRealizationOrgId] = useRestoredState<string | null>(null, () =>
        readWorkspaceQuery("org"),
    );
    const [realizationSlice, setRealizationSlice] = useRestoredState<KppkRealizationSlice>(
        "bp",
        () => (readWorkspaceQuery("slice") as KppkRealizationSlice) || "bp",
    );
    const [realizationMonth, setRealizationMonth] = useRestoredState<string | null>(null, () =>
        readWorkspaceQuery("month"),
    );
    const [realizationYear, setRealizationYear] = useRestoredState<string | null>(null, () =>
        readWorkspaceQuery("year"),
    );
    const [realizationPeriodText, setRealizationPeriodText] = useState<string | null>(null);
    const [gridResetState, setGridResetState] = useState<{
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }>({ resetColumnWidths: () => {}, resetFiltersSorting: () => {} });
    // Display toggles for the active grid (rendered in the view strip) —
    // shared by every project and remembered between sessions.
    // The report workspace owns the project switcher and drives this component
    // through props; without them the KPPK workspace falls back to its own
    // internal switcher state.
    const activeProjectKey = controlledProjectKey ?? internalProjectKey;
    const {
        headerPinned,
        showServiceColumns,
        rowHeight,
        setHeaderPinned,
        setShowServiceColumns,
        setRowHeight,
        resetRowHeight,
    } = useViewState(activeProjectKey);
    const projects = user.access ?? [];
    const activeProject = projects.find((project) => project.key === activeProjectKey);
    const isAdmin = isProjectAdmin(activeProject);
    const isKppk = activeProjectKey === KPPK_PROJECT_KEY;
    const visibleTabs = useMemo(
        () => KPPK_TABS.filter((tab) => tab.id !== "kppkUsers" || isAdmin),
        [isAdmin],
    );

    const handleProjectChange = useCallback(
        (key: string) => {
            if (onProjectChange) onProjectChange(key);
            else setInternalProjectKey(key);
        },
        [onProjectChange],
    );

    const handleStripReset = useCallback(
        (scope: "columnWidths" | "filtersSorting") => {
            if (scope === "columnWidths") gridResetState.resetColumnWidths();
            else void gridResetState.resetFiltersSorting();
        },
        [gridResetState],
    );

    const handleUndoActions = useCallback((state: KppkUndoActions) => {
        setUndoActions((current) =>
            current.canUndo === state.canUndo &&
            current.canRedo === state.canRedo &&
            current.onUndo === state.onUndo &&
            current.onRedo === state.onRedo
                ? current
                : state,
        );
    }, []);
    const handleGridStats = useCallback((state: GridViewStats) => {
        setGridStats((current) => {
            if (current && JSON.stringify(current) === JSON.stringify(state)) return current;
            return state;
        });
    }, []);
    const handleSectionRoom = useCallback((room: CollabRoom | null) => {
        setSectionRoom((current) => (current === room ? current : room));
    }, []);
    const handleExportState = useCallback((state: ExportState) => {
        setExportState((current) => reconcileExportState(current, state));
    }, []);

    useEffect(() => {
        // No selection yet, or a stale `?section=` that has no tab — fall back
        // to the first dictionary.
        const firstTab = visibleTabs[0];
        if (
            firstTab &&
            (selectedTabId === null || !visibleTabs.some((tab) => tab.id === selectedTabId))
        ) {
            setSelectedTabId(firstTab.id);
            onSectionChange?.(firstTab.id);
        }
    }, [selectedTabId, onSectionChange, visibleTabs]);

    // Browser back/forward changes `?section=` without remounting this
    // component, so follow the prop.
    useEffect(() => {
        if (initialSection && visibleTabs.some((tab) => tab.id === initialSection)) {
            setSelectedTabId(initialSection);
        }
    }, [initialSection, visibleTabs]);

    // A new tab is a new selection context — a label from the previous grid
    // must not linger in the footer.
    useEffect(() => {
        setSelectionLabel(null);
    }, [selectedTabId]);

    // The ДЗО list feeds the «Продуктовые позиции» multi-column. It is
    // re-fetched on every tab switch so a just-created ДЗО appears in the
    // positions tab without a page reload.
    const { data: orgRows } = useAsyncData<KppkOrgRow[]>(
        () => (isKppk ? getOrgRows() : Promise.resolve([])),
        [isKppk, selectedTabId],
    );
    const orgOptions = useMemo<ReferenceOption[]>(
        () => (orgRows ?? []).map((org) => ({ id: org.id, name: org.name })),
        [orgRows],
    );
    // The «Реализация» ДЗО filter keeps a value at all times: the first
    // dictionary row is the default selection.
    useEffect(() => {
        if (realizationOrgId !== null) return;
        const first = orgOptions[0];
        if (first) setRealizationOrgId(first.id);
    }, [orgOptions, realizationOrgId]);
    // Default the period to the loaded view's month/year (the server picks
    // the data's newest period); the user's explicit choice overrides it.
    useEffect(() => {
        if (realizationPeriodText === null) return;
        const [monthName, year] = realizationPeriodText.split(" ");
        if (!monthName || !year) return;
        const monthIndex = REALIZATION_MONTH_OPTIONS.findIndex(
            (option) => option.name === monthName.toLocaleLowerCase("ru"),
        );
        setRealizationMonth(
            (current) => current ?? (monthIndex >= 0 ? String(monthIndex + 1) : null),
        );
        setRealizationYear((current) => current ?? year);
    }, [realizationPeriodText]);

    // Apply the saved user theme once on mount. next-themes' setTheme changes
    // the identity on every theme switch, so the effect must not depend on it —
    // otherwise it would reset the user's choice.
    useEffect(() => {
        setTheme(user.theme);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const selectedTab =
        visibleTabs.find((tab) => tab.id === selectedTabId) ?? visibleTabs[0] ?? null;

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

    const actionItems: ActionItem[] = [
        ...buildClipboardActions({
            show: true,
            disabled: isFullscreenPending,
            canPaste: clipboardState.canPaste,
            onCopy: clipboardState.onCopy,
            onPaste: clipboardState.onPaste,
        }),
        ...buildHistoryActions({
            show: selectedTab !== null && undoAvailableFor(selectedTab.section, isAdmin),
            offline: false,
            canUndo: undoActions.canUndo,
            canRedo: undoActions.canRedo,
            onUndo: undoActions.onUndo,
            onRedo: undoActions.onRedo,
        }),
        ...(selectedTab && !exportAvailableFor(selectedTab.section, isAdmin)
            ? []
            : [
                  buildExportAction({
                      pending: exportState.pending,
                      disabled: exportState.pending || isFullscreenPending,
                      rowCount: exportState.rowCount,
                      onExport: (mode) => exportState.onExport(mode),
                  }),
              ]),
        // The same shared «Заменить» action every project renders: the mounted
        // section only reports whether its dictionary supports it.
        ...buildReplaceAction({
            show: replaceState.show ?? false,
            disabled: !replaceState.canReplace || isFullscreenPending,
            blockedReason: isFullscreenPending ? null : replaceState.blockedReason,
            onReplace: replaceState.onReplace,
        }),
        ...buildDeleteAction({
            show:
                isAdmin &&
                selectedTab !== null &&
                sectionPolicy(selectedTab.section).rowOps !== "none",
            label: "Удалить запись",
            disabled: !deleteState.canDelete || isFullscreenPending,
            blockedReason: deleteState.canDelete ? null : NO_ROW_SELECTED_REASON,
            onDelete: deleteState.onDelete,
            shortcut: { key: "Del", aria: "Control+Delete" },
        }),
        ...buildAddRowAction({
            show: isAdmin && selectedTab?.id === "kppkUsers" && userCreateState.canCreate,
            label: "Добавить пользователя",
            disabled: isFullscreenPending,
            onAdd: userCreateState.onCreate,
        }),
    ];

    // The «Реализация» period selector: «<Срез> по <ДЗО> за <Месяц> <Год>» —
    // the selectable range is slice × ДЗО × month × year. The whole row stands
    // LEFT of the tabs and collapses with ONE fade/slide animation (the
    // `toolbar-collapse` mechanics, like the «Ремонт» block filter) — one
    // transition for the entire block, not per-control.
    const isRealizationTab = selectedTab?.id === "kppkRealization";
    const isCostTab = selectedTab?.id === "kppkCost";
    const isMainTab = isRealizationTab || isCostTab;
    // «Продуктовые позиции» has two views over the same stored positions: the
    // default groups a product with its multi-selected ДЗО set (the historical
    // look), «Настройка акциза» lists one row per position with its flag.
    const [exciseMode, setExciseMode] = useState(false);
    const isPositionsTab = selectedTab?.id === "kppkPositions";
    const MONTH_OPTIONS = REALIZATION_MONTH_OPTIONS;
    const yearOptions = useMemo(() => {
        const current = new Date().getFullYear();
        const years: string[] = [];
        for (let year = current + 1; year >= 2023; year -= 1) years.push(String(year));
        return years.map((year) => ({ id: year, name: year }));
    }, []);
    const realizationFilters = (
        <div
            className={
                isMainTab
                    ? "kppk-realization-filters-row"
                    : "kppk-realization-filters-row kppk-realization-filters-row--hidden"
            }
            aria-hidden={isMainTab ? undefined : true}
        >
            <SmartSelect
                options={REALIZATION_SLICE_OPTIONS}
                value={realizationSlice}
                compactLabel="Срез"
                compactBelow="sm"
                ariaLabel="Срез"
                fullWidth={false}
                isDisabled={!isMainTab}
                onOpenChange={setToolbarControlOpen}
                onChange={(value) => {
                    if (typeof value !== "string") return;
                    setRealizationSlice(value as KppkRealizationSlice);
                    writeWorkspaceQuery("slice", value);
                }}
            />
            <span className="kppk-realization-filter-text">по</span>
            <div className="kppk-realization-filter-asset">
                <SearchAutocomplete
                    placeholder="ДЗО"
                    compactLabel="ДЗО"
                    compactBelow="sm"
                    items={orgOptions}
                    selectedKey={realizationOrgId}
                    isDisabled={!isMainTab || orgOptions.length === 0}
                    onOpenChange={setToolbarControlOpen}
                    onSelectionChange={(key) => {
                        if (key === null) return;
                        setRealizationOrgId(String(key));
                        writeWorkspaceQuery("org", String(key));
                    }}
                />
            </div>
            <span className="kppk-realization-filter-text">за</span>
            {/* «месяц» / «год» follow the house style for in-sentence labels. */}
            <SmartSelect
                options={MONTH_OPTIONS}
                value={realizationMonth}
                placeholder="месяц"
                compactLabel="мес."
                compactBelow="sm"
                ariaLabel="Месяц периода"
                fullWidth={false}
                isDisabled={!isMainTab}
                onOpenChange={setToolbarControlOpen}
                onChange={(value) => {
                    if (typeof value !== "string") return;
                    setRealizationMonth(value);
                    writeWorkspaceQuery("month", value);
                }}
            />
            <SmartSelect
                options={yearOptions}
                value={realizationYear}
                placeholder="год"
                compactLabel="год"
                compactBelow="sm"
                ariaLabel="Год периода"
                fullWidth={false}
                isDisabled={!isMainTab}
                onOpenChange={setToolbarControlOpen}
                onChange={(value) => {
                    if (typeof value !== "string") return;
                    setRealizationYear(value);
                    writeWorkspaceQuery("year", value);
                }}
            />
        </div>
    );

    // The tabs row keeps the shared toolbar rhythm; the «Реализация» period
    // filters sit BELOW it in their own row, so they open downwards (height)
    // rather than sideways (width) — the upper panel then grows/shrinks with
    // them instead of having one control slide in from the left.
    const toolbar = (
        <div className="table-toolbar table-toolbar--stacked">
            <div className="table-toolbar__row">
                <div className="table-toolbar__controls">
                    {isPositionsTab ? (
                        <Switch isSelected={exciseMode} onChange={setExciseMode}>
                            <Switch.Content>
                                <Switch.Control>
                                    <Switch.Thumb />
                                </Switch.Control>
                                Настройка акциза
                            </Switch.Content>
                        </Switch>
                    ) : null}
                    <SectionTabs
                        tabs={visibleTabs}
                        value={selectedTab?.id ?? ""}
                        onChange={(tab) => {
                            setSelectedTabId(tab.id);
                            onSectionChange?.(tab.id);
                            // The mounted section reports its own state right
                            // after the switch; clear the previous tab's until
                            // then.
                            setUndoActions(EMPTY_UNDO_ACTIONS);
                            setExportState(EMPTY_EXPORT_STATE);
                            setRealizationPeriodText(null);
                        }}
                    />
                </div>
                <ViewStrip
                    headerPinned={headerPinned}
                    onHeaderPinnedChange={setHeaderPinned}
                    showServiceColumns={showServiceColumns}
                    onShowServiceColumnsChange={setShowServiceColumns}
                    rowHeight={rowHeight}
                    onRowHeightChange={setRowHeight}
                    onResetRowHeight={resetRowHeight}
                    // Excel export is not part of the strip at all: it lives in
                    // the bottom action bar, one button per form.
                    onReset={handleStripReset}
                    columnResetLabel="Сбросить ширину столбцов и высоту заголовка"
                    isFullscreen={isFullscreen}
                    isFullscreenPending={isFullscreenPending}
                    onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
                    onDropdownOpenChange={setToolbarControlOpen}
                />
            </div>
            {realizationFilters}
        </div>
    );

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                <FormStatus
                    room={sectionRoom}
                    selectionLabel={selectionLabel}
                    selectionDetail={selectionDetail}
                    gridStats={gridStats}
                />
            </div>
            <ActionBar items={actionItems} onDropdownOpenChange={setToolbarControlOpen} />
        </div>
    );

    if (!isKppk) {
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

    return (
        <main className={isFullscreen ? "app-shell app-shell--fullscreen" : "app-shell"}>
            {header}
            <WorkspaceContent toolbar={toolbar} footer={footer}>
                {selectedTab ? (
                    selectedTab.id === "kppkRealization" || selectedTab.id === "kppkCost" ? (
                        <KppkRealizationSection
                            key={selectedTab.id}
                            mode={selectedTab.id === "kppkCost" ? "cost" : "realization"}
                            isAdmin={isAdmin}
                            orgId={realizationOrgId}
                            slice={realizationSlice}
                            periodMonth={realizationMonth}
                            periodYear={realizationYear}
                            headerPinned={headerPinned}
                            showServiceColumns={showServiceColumns}
                            rowHeight={rowHeight}
                            onDeleteState={setDeleteState}
                            onClipboardStateChange={handleClipboardActions}
                            onGridStatsChange={handleGridStats}
                            onSelectionLabelChange={setSelectionLabel}
                            onSelectionDetailChange={setSelectionDetail}
                            onRoomChange={handleSectionRoom}
                            onResetState={setGridResetState}
                            onUndoStateChange={handleUndoActions}
                            onExportState={handleExportState}
                            onPeriodTextChange={setRealizationPeriodText}
                        />
                    ) : (
                        <KppkSection
                            key={selectedTab.id}
                            section={selectedTab.section}
                            isAdmin={isAdmin}
                            assets={orgOptions}
                            headerPinned={headerPinned}
                            showServiceColumns={showServiceColumns}
                            rowHeight={rowHeight}
                            onDeleteState={setDeleteState}
                            onClipboardStateChange={handleClipboardActions}
                            onGridStatsChange={handleGridStats}
                            onSelectionLabelChange={setSelectionLabel}
                            onSelectionDetailChange={setSelectionDetail}
                            onRoomChange={handleSectionRoom}
                            onResetState={setGridResetState}
                            onUndoStateChange={handleUndoActions}
                            onExportState={handleExportState}
                            currentUserEmail={user.email}
                            onUserCreateState={setUserCreateState}
                            onReplaceState={setReplaceState}
                            exciseMode={exciseMode}
                        />
                    )
                ) : null}
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
