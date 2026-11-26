"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import {
    createLogisticsUser,
    deleteLogisticsUser,
    exportLogisticsTable,
    getLogisticsOrgs,
    getLogisticsUsers,
    getLogisticsUsersUndoState,
    redoLogisticsUsers,
    undoLogisticsUsers,
    updateLogisticsUserCell,
    type LogisticsRole,
    type LogisticsUserRow,
} from "@/shared/api/logistics";
import type { CellUpdate } from "@/shared/api/raw-materials";
import {
    applyRevisionedRemoteCell,
    type RevisionedRemoteCell,
} from "@/shared/collab/revisioned-remote-cells";
import { useCollabRoom, type CollabRoom } from "@/shared/collab/use-collab-room";
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import { DataGrid, type DataGridHandle, type GridViewStats } from "@/shared/grid/data-grid";
import { exportFileName, type GridRow, type ReferenceOption } from "@/shared/grid/grid-config";
import { buildGridResetState } from "@/shared/grid/grid-reset-state";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { NO_ROW_SELECTED_REASON, DeleteRecordDialog } from "@/shared/ui/action-items";
import {
    AddRecordModal,
    type AddRecordField,
    type AddRecordFieldOption,
    type AddRecordValues,
} from "@/shared/ui/add-record-modal";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import {
    errorMessage,
    publishSelection,
    toGridRow,
    trimmedText,
    type ClipboardActions,
} from "@/shared/ui/text-helpers";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useTableExport, type ExportMode } from "@/shared/ui/use-table-export";
import { GridLoadError } from "@/shared/ui/workspace-feedback";

const gridWarnings = gridWarningHandlers({ lockedVerb: "редактирует" });
const NO_REFERENCES: ReferenceOption[] = [];
const USER_TABLE = "app_users";
/** The cells the grid edits; the server keeps the same closed list for leases/undo. */
const USER_EDITABLE_FIELDS = ["fullname", "role", "assetNames"] as const;
const EMPTY_UNDO_STATE = { canUndo: false, canRedo: false };
const USER_DEFAULTS: AddRecordValues = {
    email: "",
    fullname: "",
    role: "blocked",
    assetIds: [],
};

/** The dialog fields; the asset picker lists the live `dict.orgs` rows. */
function userFields(assetOptions: AddRecordFieldOption[]): AddRecordField[] {
    return [
        { key: "email", label: "Email", kind: "text" },
        { key: "fullname", label: "ФИО", kind: "text" },
        {
            key: "role",
            label: "Роль",
            kind: "select",
            options: [
                { id: "blocked", name: "Заблокирован", danger: true },
                { id: "reviewer", name: "Ревьюер" },
                { id: "asset_worker", name: "Работник на активе" },
                { id: "admin", name: "Администратор" },
            ],
        },
        {
            key: "assetIds",
            label: "Активы",
            kind: "multi",
            placeholder: "Активы работника",
            options: assetOptions,
        },
    ];
}

/** The grants travel as a journal value (`{ids, names}`); the cell shows names. */
function assetNamesOf(value: unknown): string[] {
    if (Array.isArray(value)) return value.map(String);
    if (value !== null && typeof value === "object") {
        const names = (value as { names?: unknown }).names;
        if (Array.isArray(names)) return names.map(String);
    }
    return [];
}

/**
 * The «Пользователи» dictionary of «Логистика».
 *
 * A membership is a ROLE — administrator, reviewer, asset worker or blocked —
 * and an asset-worker role additionally carries `dict.orgs` grants. The access
 * flag is derived from the role server-side, so the grid edits the role only;
 * the asset cell stays locked (and explains itself on hover) for every other
 * role. The dictionary collaborates like every other users grid (admin-only
 * room, leases, presence) and every edit is journaled for undo/redo.
 */
export function LogisticsUsersSection({
    keycloakEnabled,
    headerPinned,
    showServiceColumns,
    onServiceColumnsChange,
    rowHeight,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onClipboardStateChange,
    onResetState,
    onDeleteState,
    onUserCreateState,
    onExportState,
    onUndoStateChange,
    onRoomChange,
}: {
    keycloakEnabled: boolean;
    headerPinned: boolean;
    showServiceColumns: boolean;
    onServiceColumnsChange: (visible: boolean) => void;
    rowHeight: number;
    onGridStatsChange: (stats: GridViewStats) => void;
    onSelectionLabelChange: (label: string | null) => void;
    onSelectionDetailChange: (detail: string | null) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    onDeleteState: (state: {
        canDelete: boolean;
        blockedReason: string | null;
        onDelete: () => void;
    }) => void;
    onUserCreateState: (state: { canCreate: boolean; onCreate: () => void }) => void;
    onExportState: (state: {
        pending: boolean;
        rowCount: number;
        onExport: (mode: ExportMode, valuesOnly?: boolean) => void;
        cancel?: (() => void) | undefined;
    }) => void;
    onUndoStateChange: (state: {
        canUndo: boolean;
        canRedo: boolean;
        onUndo: () => void;
        onRedo: () => void;
    }) => void;
    onRoomChange: (room: CollabRoom | null) => void;
}) {
    const gridRef = useRef<DataGridHandle>(null);
    const [reloadKey, setReloadKey] = useState(0);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [userCreateOpen, setUserCreateOpen] = useState(false);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const { filters, setFilters, clearFilters } = useGridColumnFilters(
        "logistics",
        "logisticsUsers",
    );
    const { data: rows, error } = useAsyncData<LogisticsUserRow[]>(
        () => getLogisticsUsers(),
        [reloadKey],
    );
    const { data: orgs } = useAsyncData(() => getLogisticsOrgs(), []);
    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);
    const readStartedAtRef = useRef<number | null>(null);
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, RevisionedRemoteCell>>(
        new Map(),
    );
    const [undoState, setUndoState] = useState(EMPTY_UNDO_STATE);
    const selfCellHandlerRef = useRef<(event: CollabCellChanged) => void>(() => {});
    const room = useCollabRoom({
        projectKey: "logistics",
        section: "logisticsUsers",
        reportMonth: null,
        enabled: true, // the whole tab is admin-only
        onCellChanged: (event) => selfCellHandlerRef.current(event),
        onRowsChanged: refresh,
        onReconnect: () => {
            setRemoteCellValues(new Map());
            refresh();
        },
    });
    selfCellHandlerRef.current = (event) => {
        if (event.section !== "logisticsUsers") return;
        const key = `${USER_TABLE}/${event.rowId}/${event.field}`;
        setRemoteCellValues((current) =>
            applyRevisionedRemoteCell(current, key, event, readStartedAtRef.current),
        );
    };
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);

    useEffect(() => {
        onRoomChange(room);
        return () => onRoomChange(null);
    }, [onRoomChange, room]);

    useEffect(() => {
        if (rows !== null) readStartedAtRef.current = Date.now();
    }, [rows]);

    const refreshUndoState = useCallback(async () => {
        try {
            setUndoState(await getLogisticsUsersUndoState());
        } catch {
            setUndoState(EMPTY_UNDO_STATE);
        }
    }, []);

    useEffect(() => {
        void refreshUndoState();
    }, [refreshUndoState]);

    const {
        pending: exportPending,
        exportTable,
        cancel: cancelExport,
    } = useTableExport({
        gridRef,
        fileName: () => exportFileName("logisticsUsers", null),
        request: (view, fileName) =>
            exportLogisticsTable({
                section: "logisticsUsers",
                headerPinned,
                columns: view.columns,
                rowIds: view.rowIds,
                fileName,
            }),
    });
    useEffect(() => {
        onExportState({
            pending: exportPending,
            rowCount: rows?.length ?? 0,
            onExport: exportTable,
            cancel: cancelExport,
        });
    }, [exportPending, exportTable, cancelExport, onExportState, rows]);
    const assetReference = useMemo<ReferenceOption[]>(
        () => (orgs?.rows ?? []).map((row) => ({ id: row.id, name: row.prefixRu })),
        [orgs],
    );
    const assetOptions = useMemo<AddRecordFieldOption[]>(
        () => assetReference.map((option) => ({ id: option.id, name: option.name })),
        [assetReference],
    );
    const fields = useMemo(() => userFields(assetOptions), [assetOptions]);
    const gridRows = useMemo<GridRow[]>(
        () =>
            (rows ?? []).map((row) => {
                const gridRow = toGridRow(row);
                if (room) {
                    gridRow.canonicalCells = {};
                    for (const field of USER_EDITABLE_FIELDS) {
                        const key = `${USER_TABLE}/${row.id}/${field}`;
                        gridRow.canonicalCells[field] = key;
                        const remote = remoteCellValues.get(key);
                        if (remote === undefined) continue;
                        gridRow[field] =
                            field === "assetNames" ? assetNamesOf(remote.value) : remote.value;
                    }
                }
                // The asset grants belong to the asset-worker role only — also
                // after a REMOTE role change re-derives the lock.
                gridRow.lockedCells = String(gridRow.role) === "asset_worker" ? [] : ["assetNames"];
                return gridRow;
            }),
        [remoteCellValues, room, rows],
    );
    const selectedRows = useMemo(
        () => gridRows.filter((row) => selectedRowIds.includes(row.id)),
        [gridRows, selectedRowIds],
    );
    const selectedRow = selectedRows[0];
    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);

    async function commit(updates: CellUpdate[]): Promise<boolean> {
        if (updates.length === 0 || pendingCommitRef.current) return false;
        const run = (async () => {
            setIsMutationPending(true);
            try {
                for (const update of updates) {
                    await updateLogisticsUserCell(update.rowId, update.field, update.value);
                }
                return true;
            } catch (reason) {
                toast.danger("Изменения не сохранены", {
                    description: errorMessage(reason),
                });
                return false;
            } finally {
                setIsMutationPending(false);
                refresh();
                void refreshUndoState();
                pendingCommitRef.current = null;
            }
        })();
        pendingCommitRef.current = run;
        return run;
    }

    const runUndo = useCallback(async () => {
        try {
            // An edit that has not landed yet must reach the server first: undo
            // walks the journal, and a late PATCH would re-add the action behind it.
            if (pendingCommitRef.current) await pendingCommitRef.current;
            const result = await undoLogisticsUsers();
            if (!result.applied) {
                toast.danger("Не удалось отменить", {
                    description: result.conflicts?.[0]?.user || "Действие недоступно.",
                });
            }
            refresh();
        } catch (reason) {
            toast.danger("Не удалось отменить", { description: errorMessage(reason) });
        } finally {
            void refreshUndoState();
        }
    }, [refresh, refreshUndoState]);

    const runRedo = useCallback(async () => {
        try {
            if (pendingCommitRef.current) await pendingCommitRef.current;
            const result = await redoLogisticsUsers();
            if (!result.applied) {
                toast.danger("Не удалось повторить", {
                    description: result.conflicts?.[0]?.user || "Действие недоступно.",
                });
            }
            refresh();
        } catch (reason) {
            toast.danger("Не удалось повторить", { description: errorMessage(reason) });
        } finally {
            void refreshUndoState();
        }
    }, [refresh, refreshUndoState]);

    useEffect(() => {
        onUndoStateChange({
            canUndo: undoState.canUndo,
            canRedo: undoState.canRedo,
            onUndo: () => void runUndo(),
            onRedo: () => void runRedo(),
        });
    }, [onUndoStateChange, runRedo, runUndo, undoState.canRedo, undoState.canUndo]);

    const handleSelectionChange = useCallback(
        (selection: { label: string | null; cellKeys: string[]; detail?: string | null }) => {
            publishSelection({
                selection,
                room,
                onLabel: onSelectionLabelChange,
                onDetail: onSelectionDetailChange,
            });
        },
        [onSelectionDetailChange, onSelectionLabelChange, room],
    );

    const openDelete = useCallback(() => setDeleteOpen(true), []);
    const openUserCreate = useCallback(() => setUserCreateOpen(true), []);

    useEffect(() => {
        const selectedActive = selectedRow?.isActive === true;
        onDeleteState({
            canDelete: selectedRowIds.length === 1 && !isMutationPending && !selectedActive,
            blockedReason:
                selectedRowIds.length !== 1
                    ? NO_ROW_SELECTED_REASON
                    : selectedActive
                      ? "Сначала снимите доступ пользователя к проекту, затем удаляйте."
                      : null,
            onDelete: openDelete,
        });
    }, [isMutationPending, onDeleteState, openDelete, selectedRow, selectedRowIds.length]);

    useEffect(() => {
        onUserCreateState({ canCreate: true, onCreate: openUserCreate });
    }, [onUserCreateState, openUserCreate]);

    useEffect(() => {
        onResetState(buildGridResetState(gridRef, clearFilters));
    }, [clearFilters, onResetState]);

    if (error) {
        return <GridLoadError error={error} />;
    }
    if (rows === null) {
        return <GridLoadingSkeleton />;
    }

    return (
        <>
            <div className="grid-viewport" aria-busy={isMutationPending}>
                <GridErrorBoundary>
                    <DataGrid
                        ref={gridRef}
                        section="logisticsUsers"
                        rows={gridRows}
                        isAdmin
                        keycloakEnabled={keycloakEnabled}
                        showAssetColumn={false}
                        readOnly={isMutationPending}
                        hasBaseline={false}
                        headerPinned={headerPinned}
                        showServiceColumns={showServiceColumns}
                        onServiceColumnsChange={onServiceColumnsChange}
                        rowHeight={rowHeight}
                        scrollPosition={{ form: "logistics:logisticsUsers" }}
                        assets={assetReference}
                        deliveryBases={NO_REFERENCES}
                        onCommit={commit}
                        onPlaceholderCommit={async () => false}
                        onCommitFailed={refresh}
                        onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                        onValidationError={gridWarnings.onValidationError}
                        onLockedCellEdit={gridWarnings.onLockedCellEdit}
                        onPasteSkipped={gridWarnings.onPasteSkipped}
                        onPasteTruncated={gridWarnings.onPasteTruncated}
                        onViewStatsChange={onGridStatsChange}
                        onClipboardStateChange={handleClipboardState}
                        onSelectionChange={handleSelectionChange}
                        filters={filters}
                        onFiltersChange={setFilters}
                        remoteLocks={remoteLocks}
                        remotePresence={remotePresence}
                        remoteCellInfo={remoteCellInfo}
                        onAcquireLease={(keys) => room.acquireLeases(keys)}
                        onReleaseLease={(keys) => room.releaseLeases(keys)}
                    />
                </GridErrorBoundary>
                {gridRows.length === 0 ? (
                    <div className="grid-empty">В этом справочнике пока нет строк.</div>
                ) : null}
            </div>
            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={String(selectedRow?.fullname || selectedRow?.email || "")}
                count={1}
                onOpenChange={setDeleteOpen}
                onConfirm={() => {
                    const row = selectedRow;
                    if (!row) return;
                    void (async () => {
                        setIsMutationPending(true);
                        try {
                            await deleteLogisticsUser(row.id);
                            setDeleteOpen(false);
                            setSelectedRowIds([]);
                            toast.success("Запись удалена");
                        } catch (reason) {
                            toast.danger("Не удалось удалить запись", {
                                description: errorMessage(reason),
                            });
                        } finally {
                            setIsMutationPending(false);
                            refresh();
                            void refreshUndoState();
                        }
                    })();
                }}
            />
            <AddRecordModal
                isOpen={userCreateOpen}
                onOpenChange={setUserCreateOpen}
                title="Добавить пользователя"
                description="Новый участник получает роль «Заблокирован», пока вы не выберете другую."
                fields={fields}
                defaults={USER_DEFAULTS}
                isPending={isMutationPending}
                // Assets belong to the asset-worker role only.
                isVisible={(field, values) =>
                    field.key !== "assetIds" || values.role === "asset_worker"
                }
                validate={(values) =>
                    !z.email().safeParse(trimmedText(values.email)).success
                        ? "Введите корректный email."
                        : !trimmedText(values.fullname)
                          ? "Введите ФИО."
                          : values.role === "asset_worker" &&
                              (values.assetIds as string[] | undefined)?.length === 0
                            ? "Закрепите хотя бы один актив за работником."
                            : null
                }
                onSubmit={async (values) => {
                    setIsMutationPending(true);
                    try {
                        await createLogisticsUser({
                            email: trimmedText(values.email).toLowerCase(),
                            fullname: trimmedText(values.fullname),
                            role: String(values.role ?? "blocked") as LogisticsRole,
                            assetIds: (values.assetIds as string[] | undefined) ?? [],
                        });
                        setUserCreateOpen(false);
                        refresh();
                        void refreshUndoState();
                        toast.success("Пользователь добавлен");
                    } catch (reason) {
                        toast.danger("Не удалось добавить пользователя", {
                            description: errorMessage(reason),
                        });
                    } finally {
                        setIsMutationPending(false);
                    }
                }}
            />
        </>
    );
}
