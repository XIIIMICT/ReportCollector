"use client";

import { toast } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { logisticsRateDescriptors } from "@/features/logistics/logistics-rates-descriptors";
import {
    rateDirectionPreview,
    rateNetRub,
    rateNetUsd,
    rateUsd,
} from "@/features/logistics/logistics-rates-rules";
import {
    createLogisticsRate,
    deleteLogisticsRate,
    exportLogisticsTable,
    getLogisticsCountries,
    getLogisticsHazardClasses,
    getLogisticsOrgs,
    getLogisticsRates,
    getLogisticsRatesUndoState,
    getLogisticsTransportTypes,
    redoLogisticsRates,
    undoLogisticsRates,
    updateLogisticsRateCell,
    type LogisticsCountryRow,
    type LogisticsHazardClassRow,
    type LogisticsOrgRow,
    type LogisticsPriceKind,
    type LogisticsRateCreatePayload,
    type LogisticsRateRow,
    type LogisticsRole,
    type LogisticsTransportRow,
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
import {
    exportFileName,
    type GridRow,
    type ReferenceOption,
    type SelectOption,
} from "@/shared/grid/grid-config";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useScopedAsyncData } from "@/shared/hooks/use-scoped-async-data";
import { DeleteRecordDialog, NO_ROW_SELECTED_REASON } from "@/shared/ui/action-items";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import {
    errorMessage,
    publishSelection,
    toGridRow,
    type ClipboardActions,
} from "@/shared/ui/text-helpers";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useTableExport, type ExportMode } from "@/shared/ui/use-table-export";
import { GridLoadError } from "@/shared/ui/workspace-feedback";

const gridWarnings = gridWarningHandlers({ lockedVerb: "редактирует" });
const NO_REFERENCES: ReferenceOption[] = [];
const NEW_RATE_ID = "new-rate";
const DEFAULT_USD_RATE = 83.9;

/** Editable cells; the server keeps the same closed list for undo/leases. */
const RATE_EDITABLE_FIELDS = [
    "assetId",
    "hazardClassValue",
    "countryFromId",
    "countryToId",
    "transportTypeId",
    "vehicleTypeId",
    "loadingTons",
    "freightRateRub",
    "pprRateRub",
    "deliveryRateRub",
    "priceKind",
    "note",
] as const;

type RateDraft = {
    assetId: string;
    assetName: string;
    hazardClassValue: string;
    product: string;
    direction: string;
    countryFromId: string;
    countryFromName: string;
    originPoint: string;
    countryToId: string;
    countryToName: string;
    destinationPoint: string;
    transportTypeId: string;
    transportTypeName: string;
    vehicleTypeId: string;
    vehicleTypeName: string;
    loadingTons: number;
    freightRateRub: number;
    freightRateUsd: number;
    pprRateRub: number;
    pprRateUsd: number;
    deliveryRateRub: number;
    deliveryRateUsd: number;
    netRateRub: number;
    netRateUsd: number;
    priceKind: LogisticsPriceKind;
    note: string;
    dangerous: boolean;
    // The draft has no history, so the deviations stay empty.
    deviationRub: null;
    deviationUsd: null;
    deviationPercent: null;
};

function emptyDraft(): RateDraft {
    return {
        assetId: "",
        assetName: "",
        hazardClassValue: "",
        product: "",
        direction: "",
        countryFromId: "",
        countryFromName: "",
        originPoint: "",
        countryToId: "",
        countryToName: "",
        destinationPoint: "",
        transportTypeId: "",
        transportTypeName: "",
        vehicleTypeId: "",
        vehicleTypeName: "",
        loadingTons: 0,
        freightRateRub: 0,
        freightRateUsd: 0,
        pprRateRub: 0,
        pprRateUsd: 0,
        deliveryRateRub: 0,
        deliveryRateUsd: 0,
        netRateRub: 0,
        netRateUsd: 0,
        priceKind: "INDICATIVE",
        note: "",
        dangerous: false,
        deviationRub: null,
        deviationUsd: null,
        deviationPercent: null,
    };
}

type RateDeriveContext = {
    orgOptions: SelectOption[];
    countryOptions: SelectOption[];
    countryById: Map<string, LogisticsCountryRow>;
    transportLabelById: Map<string, string>;
    vehicleOptionsByType: Map<string, SelectOption[]>;
    usdRate: number;
};

/**
 * Re-derive the cells the server computes after a REMOTE edit.
 *
 * The room carries only the cell that changed: a reference edit arrives without
 * its label (`displayKey` still renders the old name) and a hazard/rub/transport
 * edit without the values derived from it — «Опасный», «Направление», the dollar
 * equivalents and the cleared vehicle type. Local recomputation keeps the form
 * consistent without refetching the whole period while the user is typing.
 */
function derivedRatePatch(row: GridRow, ctx: RateDeriveContext): Partial<GridRow> {
    const patch: Partial<GridRow> = {};
    const assetId = String(row.assetId ?? "");
    const asset = ctx.orgOptions.find((option) => option.value === assetId);
    if (asset) patch.assetName = asset.label;
    const fromId = String(row.countryFromId ?? "");
    const toId = String(row.countryToId ?? "");
    const countryFrom = ctx.countryOptions.find((option) => option.value === fromId);
    const countryTo = ctx.countryOptions.find((option) => option.value === toId);
    if (countryFrom) patch.countryFromName = countryFrom.label;
    if (countryTo) patch.countryToName = countryTo.label;
    // Clearing both references clears the computed direction too — otherwise
    // the cell keeps the value derived from the countries that are gone.
    patch.direction = rateDirectionPreview(
        fromId,
        toId,
        (countryId) => ctx.countryById.get(countryId)?.market,
    );
    const transportId = String(row.transportTypeId ?? "");
    const transport = ctx.transportLabelById.get(transportId);
    if (transport !== undefined) patch.transportTypeName = transport;
    const allowedVehicles = ctx.vehicleOptionsByType.get(transportId) ?? [];
    const vehicleId = String(row.vehicleTypeId ?? "");
    const vehicle = allowedVehicles.find((option) => option.value === vehicleId);
    if (vehicle !== undefined) {
        patch.vehicleTypeName = vehicle.label;
    } else if (vehicleId !== "" && transport !== undefined) {
        // The server clears a vehicle type the new transport type no longer
        // allows. Only when the transport is one of the loaded ones — a
        // dictionary row deleted after the rate referenced it is NOT a reason
        // to wipe the vehicle, and its label stays as the server sent it.
        patch.vehicleTypeId = null;
        patch.vehicleTypeName = "";
    }
    patch.dangerous =
        row.hazardClassValue !== null &&
        row.hazardClassValue !== undefined &&
        String(row.hazardClassValue) !== "";
    patch.freightRateUsd = rateUsd(Number(row.freightRateRub ?? 0), ctx.usdRate);
    patch.pprRateUsd = rateUsd(Number(row.pprRateRub ?? 0), ctx.usdRate);
    patch.deliveryRateUsd = rateUsd(Number(row.deliveryRateRub ?? 0), ctx.usdRate);
    patch.netRateRub = rateNetRub(
        Number(row.freightRateRub ?? 0),
        Number(row.pprRateRub ?? 0),
        Number(row.deliveryRateRub ?? 0),
    );
    patch.netRateUsd = rateNetUsd(
        Number(patch.freightRateUsd),
        Number(patch.pprRateUsd),
        Number(patch.deliveryRateUsd),
    );
    // The deviations ride on the previous month's net rates, which the row
    // read carries; without a previous row the server sent null and the
    // cells keep their (empty) server values.
    if (row.prevNetRateRub !== null && row.prevNetRateRub !== undefined) {
        patch.deviationRub =
            Math.round((Number(patch.netRateRub) - Number(row.prevNetRateRub)) * 100) / 100;
        patch.deviationUsd =
            Math.round((Number(patch.netRateUsd) - Number(row.prevNetRateUsd)) * 100) / 100;
        // «%» is temporarily the same rub difference (the division arrives
        // with the real percentage formula).
        patch.deviationPercent = patch.deviationRub;
    }
    return patch;
}

function draftRow(draft: RateDraft): GridRow {
    return {
        ...draft,
        id: NEW_RATE_ID,
        rowState: "new",
        changedFields: [],
        isDeleted: false,
        isPlaceholder: true,
        // The draft previews the computed cells (direction, dollars, deviations)
        // while it is being filled, so they keep the green «do not fill me» tint.
        highlightCalculatedInPlaceholder: true,
        // The vehicle type is a dependent field: without a transport type there
        // is nothing to choose from, so the cell stays read-only (and explains
        // itself on hover) instead of opening an empty popover.
        ...(draft.transportTypeId ? {} : { lockedCells: ["vehicleTypeId"] }),
    };
}

/** Lock the dependent cells of a row (the vehicle needs a transport type). */
function applyRowLocks(row: GridRow): GridRow {
    if (String(row.transportTypeId ?? "") === "") row.lockedCells = ["vehicleTypeId"];
    else delete row.lockedCells;
    return row;
}

/**
 * The «Ставки» main form.
 *
 * The table is period-scoped: the month/year selects in the toolbar choose the
 * rows (and the period a new row is inserted with). The trailing empty row is a
 * local draft: it accumulates edits and the server inserts the record only when
 * every required field is filled. An incomplete draft survives a period switch.
 * Collaboration, leases and undo/redo follow the КППК pattern; the computed
 * direction/dollar/deviation columns are never stored.
 */
export function LogisticsRatesSection({
    month,
    year,
    role,
    headerPinned,
    rowHeight,
    showServiceColumns,
    onServiceColumnsChange,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onClipboardStateChange,
    onResetState,
    onUndoStateChange,
    onDeleteState,
    onExportState,
    onUsdRateChange,
    onRoomChange,
}: {
    month: number;
    year: number;
    /** The current user's «Логистика» role: admin, reviewer or asset worker. */
    role: LogisticsRole;
    headerPinned: boolean;
    rowHeight: number;
    showServiceColumns: boolean;
    onServiceColumnsChange: (visible: boolean) => void;
    onGridStatsChange: (stats: GridViewStats) => void;
    onSelectionLabelChange: (label: string | null) => void;
    onSelectionDetailChange: (detail: string | null) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    onUndoStateChange: (state: {
        canUndo: boolean;
        canRedo: boolean;
        onUndo: () => void;
        onRedo: () => void;
    }) => void;
    onDeleteState: (state: {
        canDelete: boolean;
        blockedReason: string | null;
        onDelete: () => void;
    }) => void;
    onExportState: (state: {
        pending: boolean;
        rowCount: number;
        onExport: (mode: ExportMode, valuesOnly?: boolean) => void;
        cancel?: (() => void) | undefined;
    }) => void;
    onUsdRateChange: (rate: number | null) => void;
    onRoomChange: (room: CollabRoom | null) => void;
}) {
    const periodDate = `${year}-${String(month).padStart(2, "0")}-01`;
    // A reviewer only reads; an asset worker writes within the server's scope.
    const canEdit = role === "admin" || role === "asset_worker";
    const isAdminRole = role === "admin";
    const gridRef = useRef<DataGridHandle>(null);
    const [reloadKey, setReloadKey] = useState(0);
    const [draft, setDraft] = useState<RateDraft>(emptyDraft);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const createPendingRef = useRef(false);
    const pendingCommitRef = useRef<Promise<boolean> | null>(null);
    const readStartedAtRef = useRef<number | null>(null);
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, RevisionedRemoteCell>>(
        new Map(),
    );
    const [undoState, setUndoState] = useState({ canUndo: false, canRedo: false });
    const { filters, setFilters, clearFilters } = useGridColumnFilters(
        "logistics",
        "logisticsRates",
    );

    const loader = useCallback(() => {
        readStartedAtRef.current = Date.now();
        return getLogisticsRates(month, year);
    }, [month, year]);
    const { data, error, loading, loadedScope } = useScopedAsyncData(periodDate, loader, [
        loader,
        periodDate,
        reloadKey,
    ]);
    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);
    useEffect(() => {
        // The toolbar prints the rate the SERVER returned; nothing is shown
        // while the period loads (the constant default would be a guess).
        onUsdRateChange(data?.usdRate ?? null);
        return () => onUsdRateChange(null);
    }, [data, onUsdRateChange]);
    const { data: orgs } = useAsyncData(() => getLogisticsOrgs(), []);
    const { data: countries } = useAsyncData(() => getLogisticsCountries(), []);
    const { data: transport } = useAsyncData(() => getLogisticsTransportTypes(), []);
    const { data: hazards } = useAsyncData(() => getLogisticsHazardClasses(), []);

    const orgRows = orgs?.rows ?? [];
    const countryRows = countries?.rows ?? [];
    const transportRows = transport?.rows ?? [];
    const hazardRows = hazards ?? [];
    const usdRate = data?.usdRate ?? DEFAULT_USD_RATE;
    const scopeStale = loading || loadedScope !== periodDate;

    const orgOptions = useMemo<SelectOption[]>(
        () => orgRows.map((row: LogisticsOrgRow) => ({ value: row.id, label: row.prefixRu })),
        [orgRows],
    );
    const countryOptions = useMemo<SelectOption[]>(
        () =>
            countryRows.map((row: LogisticsCountryRow) => ({
                value: row.id,
                label: row.shortname,
            })),
        [countryRows],
    );
    const transportOptions = useMemo<SelectOption[]>(() => {
        const seen = new Set<string>();
        const result: SelectOption[] = [];
        for (const row of transportRows as LogisticsTransportRow[]) {
            if (seen.has(row.transportTypeId)) continue;
            seen.add(row.transportTypeId);
            result.push({ value: row.transportTypeId, label: row.transportType });
        }
        return result;
    }, [transportRows]);
    const vehicleOptionsByType = useMemo(() => {
        const result = new Map<string, SelectOption[]>();
        for (const row of transportRows as LogisticsTransportRow[]) {
            if (row.vehicleTypeId === null || row.vehicleType === "") continue;
            const options = result.get(row.transportTypeId) ?? [];
            options.push({ value: row.vehicleTypeId, label: row.vehicleType });
            result.set(row.transportTypeId, options);
        }
        return result;
    }, [transportRows]);
    const hazardOptions = useMemo<SelectOption[]>(
        () => [
            { value: "", label: "Нет" },
            ...hazardRows.map((row: LogisticsHazardClassRow) => ({
                value: String(row.value),
                label: row.name,
            })),
        ],
        [hazardRows],
    );
    const countryById = useMemo(
        () => new Map(countryRows.map((row) => [row.id, row])),
        [countryRows],
    );
    const transportLabelById = useMemo(
        () => new Map(transportOptions.map((option) => [option.value, option.label])),
        [transportOptions],
    );
    const vehicleOptionsForRow = useCallback(
        (row: GridRow): SelectOption[] =>
            vehicleOptionsByType.get(String(row.transportTypeId ?? "")) ?? [],
        [vehicleOptionsByType],
    );
    const columnDescriptors = useMemo(
        () =>
            logisticsRateDescriptors({
                assets: orgOptions,
                countries: countryOptions,
                transportTypes: transportOptions,
                hazardClasses: hazardOptions,
                vehicleOptionsForRow,
                includeAsset: role !== "asset_worker",
            }),
        [orgOptions, countryOptions, transportOptions, hazardOptions, vehicleOptionsForRow, role],
    );

    const selfCellHandlerRef = useRef<(event: CollabCellChanged) => void>(() => {});
    const room = useCollabRoom({
        projectKey: "logistics",
        section: "logisticsRates",
        reportMonth: periodDate,
        enabled: canEdit,
        onCellChanged: (event) => selfCellHandlerRef.current(event),
        onRowsChanged: refresh,
        onReconnect: () => {
            setRemoteCellValues(new Map());
            refresh();
        },
    });
    selfCellHandlerRef.current = (event) => {
        if (event.section !== "logisticsRates") return;
        const key = `l_rates/${event.rowId}/${event.field}`;
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
        setRemoteCellValues(new Map());
    }, [periodDate]);

    const refreshUndoState = useCallback(async () => {
        try {
            setUndoState(await getLogisticsRatesUndoState(periodDate));
        } catch {
            setUndoState({ canUndo: false, canRedo: false });
        }
    }, [periodDate]);

    useEffect(() => {
        void refreshUndoState();
    }, [refreshUndoState]);

    const runUndo = useCallback(async () => {
        try {
            // An edit that has not landed yet must reach the server first: undo
            // walks the journal, and a late PATCH would re-add the action behind it.
            if (pendingCommitRef.current) await pendingCommitRef.current;
            const result = await undoLogisticsRates(periodDate);
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
    }, [periodDate, refresh, refreshUndoState]);

    const runRedo = useCallback(async () => {
        try {
            if (pendingCommitRef.current) await pendingCommitRef.current;
            const result = await redoLogisticsRates(periodDate);
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
    }, [periodDate, refresh, refreshUndoState]);

    useEffect(() => {
        onUndoStateChange({
            canUndo: undoState.canUndo,
            canRedo: undoState.canRedo,
            onUndo: () => void runUndo(),
            onRedo: () => void runRedo(),
        });
    }, [onUndoStateChange, runRedo, runUndo, undoState.canRedo, undoState.canUndo]);

    const existingRows = useMemo(() => {
        const rows = data?.rows ?? [];
        const derive: RateDeriveContext = {
            orgOptions,
            countryOptions,
            countryById,
            transportLabelById,
            vehicleOptionsByType,
            usdRate,
        };
        return rows.map((row: LogisticsRateRow) => {
            const gridRow = toGridRow(row);
            if (room) {
                gridRow.canonicalCells = {};
                let remoteApplied = false;
                for (const field of RATE_EDITABLE_FIELDS) {
                    const key = `l_rates/${row.id}/${field}`;
                    gridRow.canonicalCells[field] = key;
                    const remote = remoteCellValues.get(key);
                    if (remote !== undefined) {
                        gridRow[field] = remote.value;
                        remoteApplied = true;
                    }
                }
                // A remote edit delivers only the changed cell — its labels and
                // the cells computed from it must be re-derived locally.
                if (remoteApplied) Object.assign(gridRow, derivedRatePatch(gridRow, derive));
            }
            // A dependent cell locks when its trigger is still empty (the
            // transport type may arrive remotely, so this is computed last).
            return applyRowLocks(gridRow);
        });
    }, [
        countryById,
        countryOptions,
        data,
        orgOptions,
        remoteCellValues,
        room,
        transportLabelById,
        usdRate,
        vehicleOptionsByType,
    ]);
    const gridRows = useMemo(
        () => (canEdit ? [...existingRows, draftRow(draft)] : existingRows),
        [canEdit, draft, existingRows],
    );

    const applyDraftPatch = useCallback(
        (patch: Record<string, unknown>) => {
            setDraft((current) => {
                const next = { ...current, ...patch } as RateDraft;
                if ("assetId" in patch) {
                    next.assetName =
                        orgOptions.find((option) => option.value === String(patch.assetId))
                            ?.label ?? "";
                }
                if ("countryFromId" in patch) {
                    next.countryFromName =
                        countryOptions.find(
                            (option) => option.value === String(patch.countryFromId),
                        )?.label ?? "";
                }
                if ("countryToId" in patch) {
                    next.countryToName =
                        countryOptions.find((option) => option.value === String(patch.countryToId))
                            ?.label ?? "";
                }
                if ("countryFromId" in patch || "countryToId" in patch) {
                    // Both references cleared → no direction at all (a saved row
                    // always has both; the draft must not keep a stale preview).
                    next.direction = rateDirectionPreview(
                        String(next.countryFromId ?? ""),
                        String(next.countryToId ?? ""),
                        (countryId) => countryById.get(countryId)?.market,
                    );
                }
                if ("transportTypeId" in patch) {
                    const transportId = String(patch.transportTypeId);
                    next.transportTypeName = transportLabelById.get(transportId) ?? "";
                    const allowed = vehicleOptionsByType.get(transportId) ?? [];
                    if (!allowed.some((option) => option.value === next.vehicleTypeId)) {
                        next.vehicleTypeId = "";
                        next.vehicleTypeName = "";
                    }
                }
                if ("vehicleTypeId" in patch) {
                    next.vehicleTypeName =
                        (vehicleOptionsByType.get(next.transportTypeId) ?? []).find(
                            (option) => option.value === String(patch.vehicleTypeId),
                        )?.label ?? "";
                }
                if ("hazardClassValue" in patch) {
                    next.dangerous = String(patch.hazardClassValue ?? "") !== "";
                }
                for (const field of ["freightRateRub", "pprRateRub", "deliveryRateRub"] as const) {
                    if (field in patch) {
                        const rub = Number(patch[field]);
                        next[field] = Number.isFinite(rub) ? rub : 0;
                    }
                }
                next.freightRateUsd = rateUsd(next.freightRateRub, usdRate);
                next.pprRateUsd = rateUsd(next.pprRateRub, usdRate);
                next.deliveryRateUsd = rateUsd(next.deliveryRateRub, usdRate);
                // «Ставка за 1 тонну» is calculated: the rub sum and the sum of
                // the three already-computed dollar cells (not a fresh
                // conversion of the rub sum — rounding would disagree by cents).
                next.netRateRub = rateNetRub(
                    next.freightRateRub,
                    next.pprRateRub,
                    next.deliveryRateRub,
                );
                next.netRateUsd = rateNetUsd(
                    next.freightRateUsd,
                    next.pprRateUsd,
                    next.deliveryRateUsd,
                );
                return next;
            });
        },
        [
            countryById,
            countryOptions,
            orgOptions,
            transportLabelById,
            usdRate,
            vehicleOptionsByType,
        ],
    );

    const requiredIdsFilled = useCallback(
        (current: RateDraft): boolean => {
            // An asset worker has no «Актив» cell: the server stamps their asset.
            const assetFilled = role === "asset_worker" || Boolean(current.assetId);
            if (
                !assetFilled ||
                !current.countryFromId ||
                !current.countryToId ||
                !current.transportTypeId
            ) {
                return false;
            }
            const vehicleOptions = vehicleOptionsByType.get(current.transportTypeId) ?? [];
            if (vehicleOptions.length > 0 && !current.vehicleTypeId) return false;
            return true;
        },
        [role, vehicleOptionsByType],
    );

    const createFromDraft = useCallback(
        async (current: RateDraft) => {
            if (createPendingRef.current) return;
            createPendingRef.current = true;
            setIsMutationPending(true);
            try {
                const payload: LogisticsRateCreatePayload = {
                    periodDate,
                    // The worker sends no asset: the server stamps the granted one.
                    assetId: role === "asset_worker" ? null : current.assetId,
                    hazardClassValue:
                        current.hazardClassValue === "" ? null : Number(current.hazardClassValue),
                    countryFromId: current.countryFromId,
                    countryToId: current.countryToId,
                    transportTypeId: current.transportTypeId,
                    vehicleTypeId: current.vehicleTypeId || null,
                    loadingTons: current.loadingTons,
                    freightRateRub: current.freightRateRub,
                    pprRateRub: current.pprRateRub,
                    deliveryRateRub: current.deliveryRateRub,
                    priceKind: current.priceKind,
                    note: current.note.trim() || null,
                };
                await createLogisticsRate(payload);
                setDraft(emptyDraft());
                refresh();
                void refreshUndoState();
                toast.success("Ставка добавлена");
            } catch (reason) {
                toast.danger("Не удалось добавить ставку", {
                    description: errorMessage(reason),
                });
            } finally {
                createPendingRef.current = false;
                setIsMutationPending(false);
            }
        },
        [periodDate, refresh, refreshUndoState],
    );

    const handlePlaceholderCommit = useCallback(
        async (patch: Record<string, unknown>): Promise<boolean> => {
            applyDraftPatch(patch);
            const next = { ...draft, ...patch } as RateDraft;
            // Re-run the dependent names for the completeness check on the
            // merged draft (the state update above is asynchronous).
            if (
                "transportTypeId" in patch &&
                (vehicleOptionsByType.get(String(patch.transportTypeId)) ?? []).every(
                    (option) => option.value !== next.vehicleTypeId,
                )
            ) {
                next.vehicleTypeId = "";
            }
            if (requiredIdsFilled(next)) {
                await createFromDraft(next);
            }
            return true;
        },
        [applyDraftPatch, createFromDraft, draft, requiredIdsFilled, vehicleOptionsByType],
    );

    async function commit(updates: CellUpdate[]): Promise<boolean> {
        if (updates.length === 0 || pendingCommitRef.current) return false;
        const run = (async () => {
            setIsMutationPending(true);
            try {
                for (const update of updates) {
                    await updateLogisticsRateCell(update.rowId, update.field, update.value);
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

    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => {
                clearFilters();
                void gridRef.current?.resetFiltersSorting();
            },
        });
    }, [clearFilters, onResetState]);

    // One row at a time, like the dictionary grids: select, confirm, delete.
    const selectedRow = useMemo(
        () => existingRows.find((row) => selectedRowIds.includes(String(row.id))),
        [existingRows, selectedRowIds],
    );
    const deleteLabel = selectedRow
        ? [
              String(selectedRow.assetName ?? ""),
              `${String(selectedRow.countryFromName ?? "")} → ${String(selectedRow.countryToName ?? "")}`,
          ]
              .filter(Boolean)
              .join(", ")
        : "";

    const deleteSelected = useCallback(() => {
        const row = selectedRow;
        if (!row) return;
        setDeleteOpen(false);
        setIsMutationPending(true);
        void (async () => {
            try {
                await deleteLogisticsRate(String(row.id));
                setSelectedRowIds([]);
                toast.success("Ставка удалена");
            } catch (reason) {
                toast.danger("Не удалось удалить ставку", { description: errorMessage(reason) });
            } finally {
                setIsMutationPending(false);
                refresh();
                void refreshUndoState();
            }
        })();
    }, [refresh, refreshUndoState, selectedRow]);

    useEffect(() => {
        onDeleteState({
            canDelete: selectedRowIds.length === 1 && !isMutationPending && isAdminRole,
            blockedReason:
                selectedRowIds.length === 1
                    ? isAdminRole
                        ? null
                        : "Удалять ставки может только администратор проекта."
                    : NO_ROW_SELECTED_REASON,
            onDelete: () => setDeleteOpen(true),
        });
    }, [isAdminRole, isMutationPending, onDeleteState, selectedRowIds.length]);

    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);

    const {
        pending: exportPending,
        exportTable,
        cancel: cancelExport,
    } = useTableExport({
        gridRef,
        fileName: () => exportFileName("logisticsRates", periodDate),
        request: (view, fileName) =>
            exportLogisticsTable({
                section: "logisticsRates",
                headerPinned,
                columns: view.columns,
                rowIds: view.rowIds,
                fileName,
                month,
                year,
            }),
    });
    useEffect(() => {
        onExportState({
            pending: exportPending,
            rowCount: data?.rows.length ?? 0,
            onExport: exportTable,
            cancel: cancelExport,
        });
    }, [data, exportPending, exportTable, cancelExport, onExportState]);

    if (error) {
        return <GridLoadError error={error} />;
    }
    if (data === null) {
        return <GridLoadingSkeleton />;
    }

    return (
        <div className="grid-viewport" aria-busy={isMutationPending || scopeStale}>
            {scopeStale ? (
                <div
                    role="status"
                    style={{
                        position: "absolute",
                        inset: "0.5rem 0.75rem auto",
                        zIndex: 4,
                        padding: "0.35rem 0.6rem",
                        color: "var(--muted)",
                        background: "var(--surface)",
                        border: "1px solid var(--border)",
                        borderRadius: "0.35rem",
                    }}
                >
                    Загружается выбранный период. Редактирование временно недоступно.
                </div>
            ) : null}
            <GridErrorBoundary>
                <DataGrid
                    ref={gridRef}
                    section="logisticsRates"
                    rows={gridRows}
                    isAdmin={canEdit}
                    showAssetColumn={false}
                    readOnly={!canEdit || isMutationPending || scopeStale}
                    hasBaseline={false}
                    headerPinned={headerPinned}
                    showServiceColumns={showServiceColumns}
                    onServiceColumnsChange={onServiceColumnsChange}
                    rowHeight={rowHeight}
                    scrollPosition={{ form: "logistics:logisticsRates" }}
                    assets={NO_REFERENCES}
                    deliveryBases={NO_REFERENCES}
                    onCommit={commit}
                    onPlaceholderCommit={handlePlaceholderCommit}
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
                    columnDescriptors={columnDescriptors}
                    remoteLocks={remoteLocks}
                    remotePresence={remotePresence}
                    remoteCellInfo={remoteCellInfo}
                    onAcquireLease={(keys) => room.acquireLeases(keys)}
                    onReleaseLease={(keys) => room.releaseLeases(keys)}
                />
            </GridErrorBoundary>
            <DeleteRecordDialog
                isOpen={deleteOpen}
                isPending={isMutationPending}
                label={deleteLabel}
                count={1}
                onOpenChange={setDeleteOpen}
                onConfirm={deleteSelected}
            />
        </div>
    );
}
