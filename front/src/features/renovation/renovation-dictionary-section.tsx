"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    createRenovationAsset,
    createRenovationCatalogOption,
    createRenovationUser,
    deleteRenovationAsset,
    deleteRenovationUser,
    deleteRenovationCatalogOption,
    exportRenovationDictionary,
    getRenovationCatalogOptions,
    getRenovationDictionaryAssets,
    getRenovationDictionaryUsers,
    RENOVATION_ASSETS_SECTION,
    RENOVATION_USERS_SECTION,
    type RenovationAsset,
    type RenovationAssetRow,
    type RenovationCatalog,
    type RenovationCatalogOptionRow,
    type RenovationUserRow,
    updateRenovationAssetCells,
    updateRenovationCatalogOptionCells,
    updateRenovationUserCells,
} from "@/shared/api/renovation";
import { canonicalCellKey, type CollabRoom } from "@/shared/collab/use-collab-room";
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import { isValidEmail } from "@/shared/email";
import { getEditableKeys, type ColumnDescriptor, type GridRow } from "@/shared/grid/grid-config";
import {
    RawMaterialsGrid,
    SUMMARY_ROW_ID,
    type GridViewStats,
    type RawMaterialsGridHandle,
} from "@/shared/grid/raw-materials-grid";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { DeleteRecordDialog } from "@/shared/ui/action-items";
import {
    AddRecordModal,
    type AddRecordField,
    type AddRecordValues,
} from "@/shared/ui/add-record-modal";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import { runTableExport } from "@/shared/ui/table-export-action";
import { errorMessage, recordLabel, trimmedText } from "@/shared/ui/text-helpers";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useDeleteShortcut } from "@/shared/ui/workspace-shortcuts";

/** Standard grid toasts: see `gridWarningHandlers`. */
const gridWarnings = gridWarningHandlers({});

const R_ASSETS_TABLE = "r_assets";
const R_REFERENCE_VALUES_TABLE = "r_reference_values";
const R_USERS_TABLE = "app_users";

const PLACEHOLDER_ID_PREFIX = "new";

type DictionarySection = "renovationAssets" | "renovationUsers" | `renovationCatalog:${string}`;

function isDictionarySection(section: string): section is DictionarySection {
    return (
        section === RENOVATION_ASSETS_SECTION ||
        section === RENOVATION_USERS_SECTION ||
        section.startsWith("renovationCatalog:")
    );
}

/** Collab table of a renovation collab section (plan or dictionary). */
export function renovationSectionTable(section: string): string {
    if (section === RENOVATION_ASSETS_SECTION) return R_ASSETS_TABLE;
    if (section === RENOVATION_USERS_SECTION) return R_USERS_TABLE;
    if (section.startsWith("renovationCatalog:")) return R_REFERENCE_VALUES_TABLE;
    return "r_plan_rows";
}

function collabTableOf(section: DictionarySection): string {
    return renovationSectionTable(section);
}

function toGridRow<T extends Record<string, unknown> & { id: string }>(row: T): GridRow {
    return {
        ...row,
        rowState: "unchanged",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: false,
    };
}

function toAssetOptions(assets: RenovationAsset[]): Array<{ id: string; name: string }> {
    return assets.map((asset) => ({ id: asset.id, name: asset.name }));
}

function initialPlaceholderDraft(section: DictionarySection): Record<string, unknown> {
    // The users dictionary has no placeholder row — it creates through the
    // «Добавить пользователя» dialog; this only feeds assets and catalogs.
    if (section === RENOVATION_USERS_SECTION) return {};
    return { name: "", repairCode: "" };
}

const RENOVATION_ROLE_OPTIONS = [
    { id: "admin", name: "Администратор" },
    { id: "approver", name: "Утверждающий" },
    { id: "block_owner", name: "Ответственный за блок" },
    { id: "asset_worker", name: "Работник по активу" },
];

const RENOVATION_BLOCK_OPTIONS = [
    { id: "tech", name: "1) Техническая дирекция" },
    { id: "ksir", name: "2) Дирекция КСиР" },
    { id: "mto", name: "3) Дирекция МТО" },
];

const RENOVATION_USER_DEFAULTS: Record<string, unknown> = {
    email: "",
    fullname: "",
    role: "asset_worker",
    block: "",
    assetNames: [],
};

/** A role-dependent flag: only the roles that need the responsibility block. */
function roleNeedsBlock(values: AddRecordValues): boolean {
    const role = String(values.role ?? "");
    return role === "block_owner" || role === "asset_worker";
}

/** Only an asset worker gets an explicit asset grant — everyone else covers all. */
function roleNeedsAssets(values: AddRecordValues): boolean {
    return String(values.role ?? "") === "asset_worker";
}

function placeholderRow(section: DictionarySection, draft: Record<string, unknown>): GridRow {
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
    section: DictionarySection,
    draft: Record<string, unknown>,
): Record<string, unknown> | null {
    if (section === RENOVATION_ASSETS_SECTION) {
        const name = trimmedText(draft.name);
        const repairCode = trimmedText(draft.repairCode);
        if (!name || !repairCode) return null;
        return { name, repairCode };
    }
    if (section.startsWith("renovationCatalog:")) {
        const label = trimmedText(draft.label);
        return label ? { label } : null;
    }
    return null;
}

function stampFileName(base: string, now = new Date()): string {
    const pad2 = (value: number) => String(value).padStart(2, "0");
    const stamp = [
        pad2(now.getDate()),
        ".",
        pad2(now.getMonth() + 1),
        ".",
        now.getFullYear(),
        ", ",
        pad2(now.getHours()),
        ".",
        pad2(now.getMinutes()),
        ".",
        pad2(now.getSeconds()),
    ].join("");
    return `${base} (${stamp}).xlsx`;
}

/**
 * An editable dictionary section of the «Ремонт» project: renovation assets,
 * reference catalogs and project users. Mirrors the «Сырьё» SectionContent:
 * the placeholder row creates records, cells commit per record, delete works
 * on the selected rows, export and undo/redo are section-scoped.
 */
export function RenovationDictionarySection({
    section,
    room,
    assets,
    catalogs,
    collabEnabled,
    refreshKey,
    remoteCellValues,
    onDeleteState,
    onExportState,
    onResetState,
    onUserCreateState,
    headerPinned,
    showServiceColumns,
    rowHeight,
    onClipboardStateChange,
    onUndoHistoryChanged,
    onAssetsChanged,
    onSelectionLabelChange,
    onGridStatsChange,
    currentUserEmail,
}: {
    section: string;
    room: CollabRoom;
    assets: RenovationAsset[];
    catalogs: RenovationCatalog[];
    collabEnabled: boolean;
    /** Bumped by the workspace when rows change elsewhere (collab rows_changed). */
    refreshKey: number;
    remoteCellValues: ReadonlyMap<string, unknown>;
    onDeleteState: (state: {
        canDelete: boolean;
        blockedReason: string | null;
        onDelete: () => void;
    }) => void;
    onExportState: (state: {
        pending: boolean;
        onExport: (scope: "all" | "visible") => void;
    }) => void;
    /** Registers the display-reset handlers of the active dictionary grid. */
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    /** Registers the «Добавить пользователя» action of the users dictionary. */
    onUserCreateState: (state: { canCreate: boolean; onCreate: () => void }) => void;
    /** Controlled display toggles (rendered in the workspace view strip). */
    headerPinned: boolean;
    showServiceColumns: boolean;
    rowHeight: number;
    onClipboardStateChange: (state: {
        canPaste: boolean;
        onCopy: () => void;
        onPaste: () => void;
    }) => void;
    /** A dictionary mutation landed: undo/redo counters must be re-read. */
    onUndoHistoryChanged: () => void;
    onAssetsChanged: () => void;
    /** The completed selection of the footer status (same as in every grid). */
    onSelectionLabelChange?: (label: string | null) => void;
    /** Row/column statistics of the footer status (same as in every grid). */
    onGridStatsChange: (stats: GridViewStats) => void;
    currentUserEmail: string | undefined;
}) {
    const gridRef = useRef<RawMaterialsGridHandle>(null);
    const [reloadKey, setReloadKey] = useState(0);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [deleteOpen, setDeleteOpen] = useState(false);
    // Users are created through a modal (role-dependent fields), other
    // dictionaries still create through the trailing placeholder row.
    const [userCreateOpen, setUserCreateOpen] = useState(false);
    const [exportPending, setExportPending] = useState(false);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const placeholderCreatePendingRef = useRef(false);
    const [placeholderDraft, setPlaceholderDraft] = useState<Record<string, unknown>>(() =>
        initialPlaceholderDraft(isDictionarySection(section) ? section : RENOVATION_ASSETS_SECTION),
    );
    const placeholderDraftRef = useRef(placeholderDraft);

    const dictSection = isDictionarySection(section) ? section : RENOVATION_ASSETS_SECTION;
    const catalogId = dictSection.startsWith("renovationCatalog:")
        ? dictSection.slice("renovationCatalog:".length)
        : null;
    const sectionLabel =
        dictSection === RENOVATION_ASSETS_SECTION
            ? "Предприятия"
            : dictSection === RENOVATION_USERS_SECTION
              ? "Пользователи"
              : (catalogs.find((catalog) => catalog.id === catalogId)?.label ?? dictSection);

    const loader = useMemo(() => {
        switch (dictSection) {
            case RENOVATION_ASSETS_SECTION:
                return getRenovationDictionaryAssets;
            case RENOVATION_USERS_SECTION:
                return getRenovationDictionaryUsers;
            default:
                return catalogId
                    ? () => getRenovationCatalogOptions(catalogId)
                    : () => Promise.resolve([]);
        }
    }, [catalogId, dictSection]);

    const { data: rows, error } = useAsyncData<
        RenovationAssetRow[] | RenovationUserRow[] | RenovationCatalogOptionRow[]
    >(loader, [loader, reloadKey, refreshKey]);

    const collabTable = collabTableOf(dictSection);
    const showAssetColumn = true;
    const editableFieldKeys = useMemo(
        () =>
            getEditableKeys(
                dictSection,
                true,
                showAssetColumn,
                false,
                catalogDescriptors(catalogId),
            ),
        [dictSection],
    );

    // Section switch: reset the placeholder-row state and selection.
    useEffect(() => {
        const next = initialPlaceholderDraft(dictSection);
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        setUserCreateOpen(false);
        setSelectedRowIds([]);
        setDeleteOpen(false);
    }, [dictSection]);

    useEffect(() => {
        placeholderDraftRef.current = placeholderDraft;
    }, [placeholderDraft]);

    useEffect(() => {
        if (isMutationPending) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && deleteOpen) {
                setDeleteOpen(false);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [deleteOpen, isMutationPending]);

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);

    const gridRows = useMemo<GridRow[]>(() => {
        const base = (rows ?? []).map((row) => {
            const source = row as RenovationAssetRow &
                RenovationUserRow &
                RenovationCatalogOptionRow;
            const gridRow = toGridRow({
                ...source,
                id:
                    dictSection === RENOVATION_ASSETS_SECTION ||
                    dictSection === RENOVATION_USERS_SECTION
                        ? source.id
                        : source.valueId,
            });
            gridRow.readOnlyRow =
                dictSection === RENOVATION_USERS_SECTION &&
                String(source.email ?? "").toLowerCase() === currentUserEmail?.toLowerCase();
            if (!collabEnabled) return gridRow;
            gridRow.canonicalCells = {};
            for (const field of editableFieldKeys) {
                gridRow.canonicalCells[field] = canonicalCellKey(collabTable, gridRow.id, field);
            }
            return gridRow;
        });
        if (collabEnabled && dictSection !== RENOVATION_USERS_SECTION) {
            base.push(placeholderRow(dictSection, placeholderDraftRef.current));
        }
        return base;
    }, [currentUserEmail, collabEnabled, dictSection, editableFieldKeys, rows]);

    // Remote cell values (live edits of other users) override the local ones.
    const displayRows = useMemo(() => {
        if (remoteCellValues.size === 0) return gridRows;
        return gridRows.map((row) => {
            if (row.isPlaceholder || !row.canonicalCells) return row;
            let merged: GridRow | null = null;
            for (const field of editableFieldKeys) {
                const value = remoteCellValues.get(row.canonicalCells[field] ?? "");
                if (value === undefined) continue;
                if (!merged) merged = { ...row };
                merged[field] = value;
            }
            return merged ?? row;
        });
    }, [editableFieldKeys, gridRows, remoteCellValues]);

    const selectedRows = useMemo(
        () => gridRows.filter((row) => selectedRowIds.includes(row.id)),
        [gridRows, selectedRowIds],
    );
    // Users are deletable too, but only once their access to the project is
    // revoked (the «Активен» flag off) — the same protective rule as in the
    // other projects, enforced by the backend (409).
    const rowsDeletable = selectedRows.filter(
        (row) => dictSection !== RENOVATION_USERS_SECTION || row.isActive !== true,
    );
    const canDelete = rowsDeletable.length > 0;
    const deleteBlockedReason =
        selectedRows.length === 0
            ? "Выберите строку для удаления."
            : canDelete
              ? null
              : "Сначала снимите доступ пользователя к проекту, затем удаляйте.";

    // Ctrl+Delete — the shortcut advertised on the action-bar delete button:
    // opens the same confirmation dialog as the button press.
    // Ctrl+Delete opens the same dialog as the action-bar button.
    const openDeleteDialog = useCallback(() => setDeleteOpen(true), []);
    useDeleteShortcut(canDelete, openDeleteDialog);

    const readOnly = isMutationPending || (collabEnabled && room.status === "offline");

    // The three remote maps come from the SHARED hook: it keeps their identity
    // stable across presence heartbeats (`stabilized`), while the inline copy
    // that lived here rebuilt the maps on every tick and re-created the grid's
    // columns with them.
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);

    useEffect(() => {
        onDeleteState({
            canDelete,
            blockedReason: deleteBlockedReason,
            onDelete: () => setDeleteOpen(true),
        });
    }, [canDelete, deleteBlockedReason, onDeleteState]);

    const commit = useCallback(
        async (updates: CellUpdate[]): Promise<boolean> => {
            if (readOnly || !updates.length) return true;
            await pendingCommitRef.current;
            const task = doCommit(updates);
            pendingCommitRef.current = task;
            try {
                return await task;
            } finally {
                if (pendingCommitRef.current === task) pendingCommitRef.current = null;
            }
        },
        [readOnly],
    );

    async function doCommit(updates: CellUpdate[]): Promise<boolean> {
        setIsMutationPending(true);
        try {
            if (dictSection === RENOVATION_ASSETS_SECTION) {
                await updateRenovationAssetCells(updates);
            } else if (dictSection === RENOVATION_USERS_SECTION) {
                // The email is not part of the user PATCH schema — it is only
                // set at creation (placeholder row). Drop stray updates (e.g. a
                // paste into an existing row) instead of failing the batch.
                const userUpdates = updates.filter((update) => update.field !== "email");
                if (userUpdates.length) await updateRenovationUserCells(userUpdates);
            } else if (catalogId) {
                await updateRenovationCatalogOptionCells(catalogId, updates);
            }
            onUndoHistoryChanged();
            refresh();
            return true;
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
            return false;
        } finally {
            setIsMutationPending(false);
        }
    }

    async function createRecord(payload: Record<string, unknown>) {
        if (placeholderCreatePendingRef.current) return;
        placeholderCreatePendingRef.current = true;
        setIsMutationPending(true);
        try {
            if (dictSection === RENOVATION_ASSETS_SECTION) {
                await createRenovationAsset(String(payload.name), String(payload.repairCode));
                onAssetsChanged();
            } else if (dictSection === RENOVATION_USERS_SECTION) {
                await createRenovationUser({
                    email: String(payload.email),
                    fullname: String(payload.fullname),
                    role: String(payload.role),
                    block:
                        typeof payload.block === "string" && payload.block
                            ? String(payload.block)
                            : null,
                    assetNames: Array.isArray(payload.assetNames)
                        ? payload.assetNames.map(String)
                        : [],
                });
            } else if (catalogId) {
                await createRenovationCatalogOption(catalogId, String(payload.label));
            }
            refresh();
            // The create is journaled server-side as an undoable action:
            // notify the workspace so Ctrl+Z is offered right away.
            onUndoHistoryChanged();
            if (dictSection !== RENOVATION_USERS_SECTION) {
                const next = initialPlaceholderDraft(dictSection);
                placeholderDraftRef.current = next;
                setPlaceholderDraft(next);
            }
            toast.success("Запись добавлена");
        } catch (reason) {
            toast.danger("Не удалось добавить запись", { description: errorMessage(reason) });
            // The modal (users) keeps itself open and shows the message; the
            // placeholder row swallows it like before this function existed.
            if (dictSection === RENOVATION_USERS_SECTION) throw reason;
        } finally {
            placeholderCreatePendingRef.current = false;
            setIsMutationPending(false);
        }
    }

    async function createUserFromModal(values: AddRecordValues) {
        await createRecord({
            email: String(values.email ?? "")
                .trim()
                .toLowerCase(),
            fullname: String(values.fullname ?? "").trim(),
            role: String(values.role ?? "asset_worker"),
            block: roleNeedsBlock(values) ? String(values.block ?? "") : "",
            assetNames: roleNeedsAssets(values)
                ? (Array.isArray(values.assetNames) ? values.assetNames : []).map(String)
                : [],
        });
        setUserCreateOpen(false);
    }

    // Validation mirroring the server rules: email, full name, a block for the
    // block owner/asset worker and at least one asset for an asset worker.
    function validateUser(values: AddRecordValues): string | null {
        if (!isValidEmail(String(values.email ?? ""))) {
            return "Проверьте формат адреса электронной почты.";
        }
        if (!String(values.fullname ?? "").trim()) return "Укажите ФИО.";
        if (!String(values.role ?? "")) return "Выберите роль.";
        if (roleNeedsBlock(values) && !String(values.block ?? "")) {
            return "Выберите дирекцию.";
        }
        if (roleNeedsAssets(values)) {
            const assetNames = Array.isArray(values.assetNames) ? values.assetNames : [];
            if (assetNames.length === 0) return "Выберите хотя бы один актив.";
        }
        return null;
    }

    const userFieldVisible = useCallback((field: AddRecordField, values: AddRecordValues) => {
        if (field.key === "block") return roleNeedsBlock(values);
        if (field.key === "assetNames") return roleNeedsAssets(values);
        return true;
    }, []);

    // Stable identity for the grid's `onPlaceholderCommit` (a `columns` memo
    // dep): an inline/recreated function would rebuild columns on every render,
    // which re-emits cell focus → fresh row-selection → parent setState → repeat.
    const commitPlaceholderPatch = useCallback(
        async function (patch: Record<string, unknown>): Promise<boolean> {
            if (isMutationPending) return true;
            const next = { ...placeholderDraftRef.current, ...patch };
            placeholderDraftRef.current = next;
            setPlaceholderDraft(next);
            const payload = placeholderPayload(dictSection, next);
            // createRecord itself shows the error and keeps the placeholder-row draft,
            // so no source rollback is needed.
            if (payload) await createRecord(payload);
            return true;
        },
        [createRecord, dictSection, isMutationPending],
    );

    async function deleteRecords() {
        if (!canDelete) return;
        setIsMutationPending(true);
        try {
            for (const row of selectedRows) {
                if (dictSection === RENOVATION_ASSETS_SECTION) {
                    await deleteRenovationAsset(row.id);
                } else if (dictSection === RENOVATION_USERS_SECTION) {
                    if (row.isActive === true) continue;
                    await deleteRenovationUser(row.id);
                } else if (catalogId) {
                    await deleteRenovationCatalogOption(catalogId, row.id);
                }
            }
            refresh();
            setDeleteOpen(false);
            setSelectedRowIds([]);
            onUndoHistoryChanged();
            toast.success("Запись удалена");
        } catch (reason) {
            toast.danger("Не удалось удалить запись", { description: errorMessage(reason) });
        } finally {
            setIsMutationPending(false);
        }
    }

    const exportTable = useCallback(
        (scope: "all" | "visible") =>
            runTableExport({
                pending: exportPending,
                gridRef,
                setPending: setExportPending,
                fileName: () => stampFileName(sectionLabel),
                request: (state, fileName) =>
                    exportRenovationDictionary({
                        section: dictSection,
                        headerPinned,
                        columns: state.columns,
                        // «Видимая таблица» — the rows currently on screen
                        // (filters/sorting applied), same as the raw project.
                        rowIds: scope === "visible" ? (state.rowIds ?? null) : null,
                        fileName,
                    }),
            }),
        [dictSection, exportPending, headerPinned, sectionLabel],
    );

    useEffect(() => {
        onExportState({ pending: exportPending, onExport: (scope) => void exportTable(scope) });
    }, [exportPending, exportTable, onExportState]);

    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => void gridRef.current?.resetFiltersSorting(),
        });
    }, [onResetState]);

    // The users dictionary creates records through the modal, so the button is
    // delegated up like delete/export (stable identity — it feeds a footer
    // prop; a fresh object each render would rebuild the footer).
    const canCreateUser = dictSection === RENOVATION_USERS_SECTION;
    const openUserCreate = useCallback(() => setUserCreateOpen(true), []);
    useEffect(() => {
        onUserCreateState({ canCreate: canCreateUser, onCreate: openUserCreate });
    }, [canCreateUser, onUserCreateState, openUserCreate]);

    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);

    if (error) {
        return <div className="grid-empty">Не удалось загрузить справочник: {String(error)}</div>;
    }

    // Stabilize the props we hand to the grid: the `columns` memo inside
    // RawMaterialsGrid depends on `assets`/`deliveryBases`/`onPlaceholderCommit`,
    // and the selection row-identity depends on `onSelectedRowsChange`. Passing
    // fresh references each render rebuilds columns → RevoGrid re-emits cell
    // focus → fresh row-selection → grid's selection effect calls this setter →
    // re-render → fresh references again → infinite update-depth loop.
    // The «Предприятия» tab gets a pinned totals row — the total number of
    // repair works and the sum of every asset's «Стоимость итого» (the same
    // aggregates as the plan's summary row).
    const pinnedTopSource = useMemo<
        | {
              id: string;
              name: string;
              repairCode: string;
              repairCount: number;
              totalCost: number;
          }[]
        | undefined
    >(() => {
        if (dictSection !== RENOVATION_ASSETS_SECTION) return undefined;
        let repairCount = 0;
        let totalCost = 0;
        for (const row of rows ?? []) {
            const asset = row as RenovationAssetRow;
            repairCount += asset.repairCount ?? 0;
            totalCost += asset.totalCost ?? 0;
        }
        return [{ id: SUMMARY_ROW_ID, name: "", repairCode: "", repairCount, totalCost }];
    }, [dictSection, rows]);
    const assetOptions = useMemo(() => toAssetOptions(assets), [assets]);
    const deliveryBasisOptions = useMemo(() => [] as { id: string; name: string }[], []);
    const descriptorOptions = useMemo(() => catalogDescriptors(catalogId), [catalogId]);
    const lastRowSelectionRef = useRef("");
    const handleSelectedRowsChange = useCallback(
        (selection: { ids: string[]; wholeRows: boolean }) => {
            const content = selection.ids.join("\u0000");
            if (content === lastRowSelectionRef.current) return;
            lastRowSelectionRef.current = content;
            setSelectedRowIds(selection.ids);
        },
        [],
    );

    return (
        <>
            <RawMaterialsGrid
                ref={gridRef}
                section={dictSection}
                rows={displayRows}
                isAdmin
                showAssetColumn
                readOnly={readOnly}
                hasBaseline={false}
                headerPinned={headerPinned}
                showServiceColumns={showServiceColumns}
                rowHeight={rowHeight}
                pinnedTopSource={pinnedTopSource}
                assets={assetOptions}
                deliveryBases={deliveryBasisOptions}
                {...(descriptorOptions && { columnDescriptors: descriptorOptions })}
                namespace={`renovation:${dictSection}`}
                onCommit={commit}
                onPlaceholderCommit={commitPlaceholderPatch}
                onCommitFailed={refresh}
                onSelectedRowsChange={handleSelectedRowsChange}
                onValidationError={gridWarnings.onValidationError}
                onClipboardStateChange={handleClipboardState}
                onLockedCellEdit={gridWarnings.onLockedCellEdit}
                onAcquireLease={room.acquireLeases}
                onReleaseLease={room.releaseLeases}
                remoteLocks={remoteLocks}
                remotePresence={remotePresence}
                remoteCellInfo={remoteCellInfo}
                onViewStatsChange={onGridStatsChange}
                onSelectionChange={({ label }) =>
                    onSelectionLabelChange?.(label && label.length > 0 ? label : null)
                }
            />
            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={recordLabel(selectedRows[0] as Record<string, unknown>)}
                count={selectedRows.length}
                onOpenChange={setDeleteOpen}
                onConfirm={() => void deleteRecords()}
            />
            {dictSection === RENOVATION_USERS_SECTION ? (
                <AddRecordModal
                    isOpen={userCreateOpen}
                    title="Добавить пользователя"
                    defaults={RENOVATION_USER_DEFAULTS}
                    fields={renovationUserFields(assetOptions)}
                    isPending={isMutationPending}
                    isVisible={userFieldVisible}
                    validate={validateUser}
                    onSubmit={createUserFromModal}
                    onOpenChange={setUserCreateOpen}
                />
            ) : null}
        </>
    );
}

function catalogDescriptors(catalogId: string | null): ColumnDescriptor[] | undefined {
    if (catalogId === null) return undefined;
    return [{ key: "label", label: "Значение", width: 380, kind: "text" }];
}

function renovationUserFields(assets: Array<{ id: string; name: string }>): AddRecordField[] {
    return [
        { kind: "text", key: "email", label: "Email", autoComplete: "off" },
        { kind: "text", key: "fullname", label: "ФИО" },
        {
            kind: "select",
            key: "role",
            label: "Роль",
            options: RENOVATION_ROLE_OPTIONS,
        },
        {
            kind: "select",
            key: "block",
            label: "Дирекция",
            options: RENOVATION_BLOCK_OPTIONS,
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
