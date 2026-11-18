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
    updateLogisticsUserCell,
    type LogisticsRole,
    type LogisticsUserRow,
} from "@/shared/api/logistics";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { DataGrid, type DataGridHandle, type GridViewStats } from "@/shared/grid/data-grid";
import { exportFileName, type GridRow, type ReferenceOption } from "@/shared/grid/grid-config";
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
    toGridRow,
    trimmedText,
    type ClipboardActions,
} from "@/shared/ui/text-helpers";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useTableExport, type ExportMode } from "@/shared/ui/use-table-export";
import { GridLoadError } from "@/shared/ui/workspace-feedback";

const gridWarnings = gridWarningHandlers({ lockedVerb: "редактирует" });
const NO_REFERENCES: ReferenceOption[] = [];
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

/**
 * The «Пользователи» dictionary of «Логистика».
 *
 * A membership is a ROLE — administrator, reviewer, asset worker or blocked —
 * and an asset-worker role additionally carries `dict.orgs` grants. The access
 * flag is derived from the role server-side, so the grid edits the role only;
 * the asset cell stays locked (and explains itself on hover) for every other
 * role. There is no collaboration room and no undo: the mutations are immediate
 * and the grid is refreshed from the server.
 */
export function LogisticsUsersSection({
    keycloakEnabled,
    headerPinned,
    rowHeight,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onClipboardStateChange,
    onResetState,
    onDeleteState,
    onUserCreateState,
    onExportState,
}: {
    keycloakEnabled: boolean;
    headerPinned: boolean;
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
        onExport: (mode: ExportMode) => void;
    }) => void;
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
    const { pending: exportPending, exportTable } = useTableExport({
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
        });
    }, [exportPending, exportTable, onExportState, rows]);
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
                // The asset grants belong to the asset-worker role only.
                if (row.role !== "asset_worker") gridRow.lockedCells = ["assetNames"];
                return gridRow;
            }),
        [rows],
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
                pendingCommitRef.current = null;
            }
        })();
        pendingCommitRef.current = run;
        return run;
    }

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
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => {
                clearFilters();
                void gridRef.current?.resetFiltersSorting();
            },
        });
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
                        showServiceColumns={false}
                        rowHeight={rowHeight}
                        scrollPosition={{ form: "logistics:logisticsUsers" }}
                        assets={assetReference}
                        deliveryBases={NO_REFERENCES}
                        onCommit={commit}
                        onPlaceholderCommit={async () => false}
                        onCommitFailed={refresh}
                        onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                        onValidationError={gridWarnings.onValidationError}
                        onViewStatsChange={onGridStatsChange}
                        onClipboardStateChange={handleClipboardState}
                        onSelectionChange={(selection) => {
                            onSelectionLabelChange(selection.label ?? null);
                            onSelectionDetailChange(selection.detail ?? null);
                        }}
                        filters={filters}
                        onFiltersChange={setFilters}
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
