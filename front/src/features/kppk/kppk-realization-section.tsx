"use client";

import { Card, toast } from "@heroui/react";
import { CellMergePlugin, type MergeData } from "@revolist/rv-pro-trial";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    exportKppkRealization,
    exportKppkCost,
    getCostView,
    updateCostCell,
    KPPK_COST_SLICE_FIELDS,
    getKppkUndoState,
    getRealizationView,
    KPPK_REALIZATION_SLICE_FIELDS,
    redoKppkCells,
    undoKppkCells,
    updateRealizationCell,
    type KppkRealizationSectionId,
    type KppkRealizationSlice,
    type KppkRealizationView,
    type KppkCostView,
    type KppkUndoResult,
} from "@/shared/api/kppk";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { useCollabRoom, type CollabRoom } from "@/shared/collab/use-collab-room";
import { useRemoteCellMaps } from "@/shared/collab/use-remote-cell-maps";
import type { CollabCellChanged } from "@/shared/collab/ws-client";
import { exportFileName, type ColumnDescriptor, type GridRow } from "@/shared/grid/grid-config";
import {
    RawMaterialsGrid,
    type GridViewStats,
    type RawMaterialsGridHandle,
} from "@/shared/grid/raw-materials-grid";
import { collaborationAvailableFor, undoAvailableFor } from "@/shared/grid/section-policy";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { gridWarningHandlers } from "@/shared/ui/grid-warnings";
import { applyHistoryResult, runHistoryAction } from "@/shared/ui/history-action";
import { runTableExport } from "@/shared/ui/table-export-action";
import { publishSelection, type GridSelectionInfo } from "@/shared/ui/text-helpers";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useUndoRedoShortcuts } from "@/shared/ui/workspace-shortcuts";

import { expandExcise } from "./kppk-excise";

/** Standard grid toasts: see `gridWarningHandlers`. */
const gridWarnings = gridWarningHandlers({ lockedVerb: "редактирует" });

/**
 * The «Реализация» form (Таблицы.xlsx sheet «Реализация»): the workspace
 * toolbar owns the mandatory «ДЗО» filter (no «Все активы») and the mandatory
 * slice select («Бизнес-план» / «Оперплан» / «Факт», no «Все срезы»); the
 * loaded period is also reported into that toolbar row. The columns follow the template two-level header:
 * Продукция | Рынок сбыта | Выкуп{Объем, Цена FCA} | Реализация{Объем,
 * Цена FCA}; the product names fold their region rows into one merged cell
 * (CellMergePlugin) the way the template renders them.
 *
 * The period is a server-side constant until the period filter exists; undo/
 * redo scope and the collaboration room follow the slice storage
 * (`kppkRealizationPlan` for the plan slices, `kppkRealizationFact` for the
 * fact one). Editing is admin-only, row operations are disabled (the rows
 * come from the source copies).
 */

const KPPK_PROJECT_KEY = "kppk";

const KPPK_REALIZATION_SLICES: { id: KppkRealizationSlice; label: string }[] = [
    { id: "bp", label: "Бизнес-план" },
    { id: "op", label: "Оперплан" },
    { id: "fact", label: "Факт" },
];

/**
 * The slice's column layout per the template: the fixed left columns then
 * the four editable numeric cells under the «Выкуп»/«Реализация» groups.
 */
const KPPK_REALIZATION_DESCRIPTORS: Record<KppkRealizationSlice, ColumnDescriptor[]> = {
    bp: [
        { key: "product", label: "Продукция", width: 320, kind: "text", fixed: true },
        { key: "region", label: "Рынок сбыта", width: 140, kind: "text", fixed: true },
        { key: "buyoutBpPlan", label: "Объем", width: 140, kind: "number", path: ["Выкуп"] },
        { key: "fcaPriceBpPlan", label: "Цена FCA", width: 130, kind: "number", path: ["Выкуп"] },
        { key: "monthBpPlan", label: "Объем", width: 140, kind: "number", path: ["Реализация"] },
        {
            key: "fcaPriceMarketBpPlan",
            label: "Цена FCA",
            width: 130,
            kind: "number",
            path: ["Реализация"],
        },
    ],
    op: [
        { key: "product", label: "Продукция", width: 320, kind: "text", fixed: true },
        { key: "region", label: "Рынок сбыта", width: 140, kind: "text", fixed: true },
        { key: "buyoutMaxPlan", label: "Объем", width: 140, kind: "number", path: ["Выкуп"] },
        { key: "fcaPriceMaxPlan", label: "Цена FCA", width: 130, kind: "number", path: ["Выкуп"] },
        { key: "monthMaxPlan", label: "Объем", width: 140, kind: "number", path: ["Реализация"] },
        {
            key: "fcaPriceMarketMaxPlan",
            label: "Цена FCA",
            width: 130,
            kind: "number",
            path: ["Реализация"],
        },
    ],
    fact: [
        { key: "product", label: "Продукция", width: 320, kind: "text", fixed: true },
        { key: "region", label: "Рынок сбыта", width: 140, kind: "text", fixed: true },
        { key: "buyoutFact", label: "Объем", width: 140, kind: "number", path: ["Выкуп"] },
        { key: "fcaPriceFact", label: "Цена FCA", width: 130, kind: "number", path: ["Выкуп"] },
        { key: "fact", label: "Объем", width: 140, kind: "number", path: ["Реализация"] },
        {
            key: "fcaPriceMarketFact",
            label: "Цена FCA",
            width: 130,
            kind: "number",
            path: ["Реализация"],
        },
    ],
};

/** The collaboration room / undo scope of a slice. */
function realizationRoomSection(slice: KppkRealizationSlice): KppkRealizationSectionId {
    return slice === "fact" ? "kppkRealizationFact" : "kppkRealizationPlan";
}

type KppkUndoActions = {
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
};

type ClipboardActions = { canPaste: boolean; onCopy: () => void; onPaste: () => void };

/**
 * The workspace export button is hidden on this tab (no export app yet), so
 * the section keeps the no-op export state of the freshly mounted tab.
 */

const EMPTY_UNDO_ACTIONS: KppkUndoActions = {
    canUndo: false,
    canRedo: false,
    onUndo: () => {},
    onRedo: () => {},
};

function errorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : "Неизвестная ошибка.";
}

export { KPPK_REALIZATION_SLICES, realizationRoomSection, type KppkRealizationSectionProps };

type KppkRealizationSectionProps = {
    mode?: "realization" | "cost";
    isAdmin: boolean;
    /** The selected ДЗО: the workspace toolbar always provides one. */
    orgId: string | null;
    /** The active slice: the workspace toolbar select owns the choice. */
    slice: KppkRealizationSlice;
    /** 1-based month id («1»…«12») or null — null keeps the server-fixed period. */
    periodMonth: string | null;
    /** The period year («2023»…«2027») or null — null keeps the server-fixed period. */
    periodYear: string | null;
    headerPinned: boolean;
    showServiceColumns: boolean;
    rowHeight: number;
    onGridStatsChange: (stats: GridViewStats) => void;
    onSelectionLabelChange: (label: string | null) => void;
    onSelectionDetailChange?: (detail: string | null) => void;
    onClipboardStateChange: (state: ClipboardActions) => void;
    onUndoStateChange: (state: KppkUndoActions) => void;
    /** Publishes the Excel export state (the workspace action bar renders it). */
    onExportState: (state: {
        pending: boolean;
        rowCount?: number | undefined;
        onExport: (mode: "all" | "visible") => void;
    }) => void;
    onDeleteState: (state: { canDelete: boolean; onDelete: () => void }) => void;
    onResetState: (state: {
        resetColumnWidths: () => void;
        resetFiltersSorting: () => void;
    }) => void;
    /** Publishes this form's collaboration room to the footer status. */
    onRoomChange?: (room: CollabRoom | null) => void;
    /** The loaded view's period banner (the toolbar renders it) — null while empty. */
    onPeriodTextChange: (period: string | null) => void;
};

export function KppkRealizationSection({
    mode = "realization",
    isAdmin,
    orgId,
    slice,
    periodMonth,
    periodYear,
    headerPinned,
    showServiceColumns,
    rowHeight,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onClipboardStateChange,
    onUndoStateChange,
    onExportState,
    onDeleteState,
    onResetState,
    onRoomChange,
    onPeriodTextChange,
}: KppkRealizationSectionProps) {
    const isCost = mode === "cost";
    const roomSection = isCost ? "kppkCost" : realizationRoomSection(slice);
    // The excise field of the active slice (one column per plan/fact copy).
    const exciseKey = slice === "bp" ? "exciseBp" : slice === "op" ? "exciseMaxPlan" : "exciseFact";
    const descriptors = useMemo<ColumnDescriptor[]>(() => {
        if (!isCost)
            return [
                ...KPPK_REALIZATION_DESCRIPTORS[slice],
                // The column is declared here and REMOVED further down when the
                // asset levies no excise at all (`showExcise` needs the loaded
                // view). Keeping it declarative here means the descriptors stay
                // independent of the fetch.
                { key: exciseKey, label: "Акциз", width: 130, kind: "number" as const },
            ];
        const fields = KPPK_COST_SLICE_FIELDS[slice];
        const labels = [
            "Полная себестоимость, руб./т",
            "Производственная себестоимость, руб./т",
            "Переменные затраты, руб./т",
        ];
        return [
            {
                key: "product",
                label: "Продукция",
                width: 320,
                kind: "text",
                fixed: true,
                readOnly: true,
            },
            ...fields.map((key, index): ColumnDescriptor => ({
                key,
                label: labels[index]!,
                width: 220,
                kind: "number",
            })),
        ];
    }, [exciseKey, isCost, slice]);

    const [reloadKey, setReloadKey] = useState(0);
    const [isGridSorted, setIsGridSorted] = useState(false);
    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);
    const loader = useCallback(
        () =>
            orgId !== null
                ? isCost
                    ? getCostView(orgId, periodMonth, periodYear)
                    : getRealizationView(orgId, slice, periodMonth, periodYear)
                : Promise.resolve(null),
        [isCost, orgId, periodMonth, periodYear, slice],
    );
    const { data: view, error } = useAsyncData<KppkRealizationView | KppkCostView | null>(loader, [
        loader,
        reloadKey,
    ]);
    // An excise exists only for SOME product × ДЗО pairs (today «Аммиак»,
    // «Аммиачная селитра», «Фосфорные удобрения» of «Минудобрения»). The view
    // reports the asset's flagged products, so: no flagged product → the column
    // is hidden; a flagged asset → only those products get an editable cell.
    // The figure itself is per product (migration 0016): the grid renders it as
    // one merged cell per product block and fans an edit out to the whole block.
    const exciseProducts = useMemo(() => {
        const names = isCost ? [] : ((view as KppkRealizationView | null)?.exciseProducts ?? []);
        return new Set(names.map((name) => name.toLowerCase().split(/\s+/).join(" ")));
    }, [isCost, view]);
    const showExcise = !isCost && exciseProducts.size > 0;
    // The «Акциз» column exists only for an asset that levies an excise on at
    // least one of its products; for anything else it is dropped, so the grid
    // never offers an excise cell nobody can fill.
    const visibleDescriptors = useMemo(
        () =>
            showExcise || isCost
                ? descriptors
                : descriptors.filter((item) => item.key !== exciseKey),
        [descriptors, exciseKey, isCost, showExcise],
    );
    const productHasExcise = useCallback(
        (product: string) => exciseProducts.has(product.toLowerCase().split(/\s+/).join(" ")),
        [exciseProducts],
    );

    // Collaboration room per slice storage; the section the room is keyed by
    // also carries the undo history scope.
    const [remoteCellValues, setRemoteCellValues] = useState<Map<string, unknown>>(new Map());
    const selfCellHandlerRef = useRef<(event: CollabCellChanged) => void>(() => {});
    const room = useCollabRoom({
        projectKey: KPPK_PROJECT_KEY,
        section: roomSection,
        reportMonth:
            periodMonth && periodYear ? `${periodYear}-${periodMonth.padStart(2, "0")}-01` : null,
        scope: orgId,
        enabled:
            collaborationAvailableFor(roomSection, isAdmin) &&
            orgId !== null &&
            periodMonth !== null &&
            periodYear !== null,
        onCellChanged: (event) => selfCellHandlerRef.current(event),
        onRowsChanged: () => refresh(),
        onReconnect: () => {
            setRemoteCellValues(new Map());
            refresh();
        },
    });
    const meId = room.me?.userId ?? null;
    selfCellHandlerRef.current = (event) => {
        if (event.user.userId === meId) return;
        const key = `${roomSection}/${event.rowId}/${event.field}`;
        setRemoteCellValues((current) => new Map(current).set(key, event.value));
    };
    const { remoteLocks, remotePresence, remoteCellInfo } = useRemoteCellMaps(room);

    useEffect(() => {
        onRoomChange?.(room);
        return () => onRoomChange?.(null);
    }, [onRoomChange, room]);

    // ── Undo/redo (per slice scope, same mechanics as the dictionaries) ──
    const pendingWriteRef = useRef<Promise<unknown> | null>(null);
    function track<T>(write: Promise<T>): Promise<T> {
        pendingWriteRef.current = write;
        return write;
    }
    const [undoCounts, setUndoCounts] = useState({ canUndo: false, canRedo: false });
    const [pendingSkip, setPendingSkip] = useState<{
        actionId: number;
        direction: "undo" | "redo";
    } | null>(null);
    const refreshUndoState = useCallback(() => {
        void getKppkUndoState(roomSection)
            .then((state) => setUndoCounts({ canUndo: state.canUndo, canRedo: state.canRedo }))
            .catch(() => {});
    }, [roomSection]);
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
            });
        },
        [refresh, refreshUndoState],
    );
    const runUndo = useCallback(async () => {
        await runHistoryAction({
            direction: "undo",
            pendingWrite: pendingWriteRef.current,
            pendingSkip,
            request: (skip) => undoKppkCells(skip, roomSection),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, roomSection]);
    const runRedo = useCallback(async () => {
        await runHistoryAction({
            direction: "redo",
            pendingWrite: pendingWriteRef.current,
            pendingSkip,
            request: (skip) => redoKppkCells(skip, roomSection),
            apply: applyUndoResult,
        });
    }, [applyUndoResult, pendingSkip, roomSection]);

    useUndoRedoShortcuts({
        enabled: undoAvailableFor(roomSection, isAdmin),
        onUndo: () => void runUndo(),
        onRedo: () => void runRedo(),
    });

    // Chain the grid's edit events to the slice PATCH endpoint.
    async function commit(updates: CellUpdate[]): Promise<boolean> {
        if (!isAdmin || updates.length === 0) return true;
        setPendingSkip(null);
        // The excise is one figure per product but is STORED on every market
        // row (migration 0016), so an edit of the merged cell is fanned out to
        // the whole product block — otherwise the other two rows would keep the
        // old rate and a later sort would reveal the disagreement.
        const expanded = expandExcise(updates, view?.rows ?? [], exciseKey, productHasExcise);
        const write = (async () => {
            for (const update of expanded) {
                if (isCost) await updateCostCell(update.rowId, update.field, update.value);
                else await updateRealizationCell(update.rowId, slice, update.field, update.value);
            }
        })();
        try {
            await track(write);
            refresh();
            return true;
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
            return false;
        } finally {
            refreshUndoState();
        }
    }
    const commitRef = useRef(commit);
    commitRef.current = commit;
    const stableCommit = useCallback((updates: CellUpdate[]) => commitRef.current(updates), []);
    const stablePlaceholderCommit = useCallback(async () => true, []);
    const stableSelectedRowsChange = useCallback(() => {}, []);

    // Keep complete source values: merged cells hide covered labels visually,
    // but sorting and filtering must always read the real product on every row.
    const gridRows = useMemo<GridRow[]>(() => {
        const rows = view?.rows ?? [];
        const fields = isCost
            ? KPPK_COST_SLICE_FIELDS[slice]
            : KPPK_REALIZATION_SLICE_FIELDS[slice];
        let previous: string | null = null;
        // The first row of the current product block: the excise of a merged
        // block is edited through that row only, and every row of the block
        // subscribes to the SAME canonical key, so a change made by anybody
        // lands on all three cells at once.
        let blockLeadId: string | null = null;
        return rows.map((row) => {
            const isGroupStart = row.product !== previous;
            previous = row.product;
            if (isGroupStart) blockLeadId = row.id;
            const gridRow: GridRow = {
                ...row,
                product: isCost || isGridSorted || isGroupStart || !showExcise ? row.product : "",
                rowState: "unchanged",
                changedFields: [],
                isDeleted: false,
                isPlaceholder: false,
                canonicalCells: {},
                // The excise cell of a product that does not carry one is not
                // merely "empty": it must not be enterable at all — the row's
                // lock makes react-aria start no editor and the grid skip it on
                // paste, so the user never sees the server's rejection.
                ...(showExcise && !productHasExcise(row.product)
                    ? { lockedCells: [exciseKey] }
                    : {}),
            };
            if (!isCost && showExcise) {
                // An excise outside this product's scope: empty and LOCKED (the
                // server enforces the same rule) — and with no canonical key, so
                // it never publishes or receives a value.
                if (!productHasExcise(row.product)) {
                    gridRow[exciseKey] = null;
                } else if (!isGridSorted && !isGroupStart) {
                    // The covered cells of the merged excise block show nothing —
                    // the value belongs to the block, not to the market row.
                    gridRow[exciseKey] = null;
                }
            }
            if (!isAdmin) return gridRow;
            const cells = gridRow.canonicalCells;
            for (const field of fields) {
                if (!cells) return gridRow;
                if (field === exciseKey && !isCost) {
                    if (!productHasExcise(row.product)) continue;
                    // One key per PRODUCT for the excise (see above).
                    const key = `${roomSection}/${isGridSorted ? row.id : blockLeadId}/${field}`;
                    cells[field] = key;
                    const remote = remoteCellValues.get(key);
                    if (remote !== undefined && !gridRow[exciseKey]) gridRow[field] = remote;
                    continue;
                }
                const key = `${roomSection}/${row.id}/${field}`;
                cells[field] = key;
                const remote = remoteCellValues.get(key);
                if (remote !== undefined) gridRow[field] = remote;
            }
            return gridRow;
        });
    }, [
        exciseKey,
        isAdmin,
        isCost,
        isGridSorted,
        productHasExcise,
        remoteCellValues,
        roomSection,
        showExcise,
        slice,
        view?.rows,
    ]);

    // The template folds each product's region rows into one merged cell: one
    // rowSpan range per run of rows sharing the product. The merge covers the
    // leading `product` leaf (index 0 — the «№» column is gone) plus the
    // trailing excise column, whose figure belongs to the product, not to the
    // market.
    const cellMerge = useMemo<MergeData[]>(() => {
        if (isCost || isGridSorted) return [];
        const rows = view?.rows ?? [];
        const sweeps: MergeData[] = [];
        // The single Level/leaf array the merged columns sit in.
        const mergedColumns = [0];
        if (showExcise) mergedColumns.push(KPPK_REALIZATION_DESCRIPTORS[slice].length);
        const push = (from: number, to: number) => {
            if (from < 0 || to - from < 2) return;
            for (const column of mergedColumns) {
                sweeps.push({ row: from, column, rowSpan: to - from });
            }
        };
        let start = -1;
        for (let i = 0; i < rows.length; i += 1) {
            const isGroupStart = i === 0 || rows[i]!.product !== rows[i - 1]!.product;
            if (!isGroupStart) continue;
            if (start >= 0) push(start, i);
            start = i;
        }
        if (start >= 0) push(start, rows.length);
        return sweeps;
    }, [isCost, isGridSorted, showExcise, slice, view?.rows]);

    // ── Workspace wiring (footer actions, strip handlers) ──
    const gridRef = useRef<RawMaterialsGridHandle>(null);
    // The exported row count is the one the footer status shows: the grid
    // measures it (filters applied, placeholders dropped) and publishes it
    // through `onViewStatsChange`. Deriving it here instead kept the two
    // numbers apart — the status said «N строк», the export button stayed
    // disabled because the section counted its own pre-render list.
    const [viewStats, setViewStats] = useState<GridViewStats | null>(null);
    const handleViewStats = useCallback(
        (stats: GridViewStats) => {
            setViewStats(stats);
            onGridStatsChange(stats);
        },
        [onGridStatsChange],
    );
    // The Excel export replays this form's toolbar selection (slice × ДЗО ×
    // period) and its column shape; the server re-reads the same ordered view.
    const [exportPending, setExportPending] = useState(false);
    const onExport = useCallback(
        (mode: "all" | "visible") => {
            // «Видимая» equals «вся» here: the form has no client-side filters
            // — the ДЗО/slice/period selection has already narrowed the view.
            void mode;
            if (orgId === null) return;
            void runTableExport({
                pending: exportPending,
                gridRef,
                setPending: setExportPending,
                fileName: () => exportFileName(roomSection, null),
                request: (view, fileName) =>
                    (isCost ? exportKppkCost : exportKppkRealization)({
                        slice,
                        orgId,
                        month: periodMonth,
                        year: periodYear,
                        headerPinned,
                        columns: view.columns,
                        rowIds: isCost ? view.rowIds : null,
                        fileName,
                    }),
            });
        },
        [exportPending, headerPinned, isCost, orgId, periodMonth, periodYear, roomSection, slice],
    );
    useEffect(() => {
        onExportState({
            pending: exportPending,
            rowCount: viewStats?.filteredRows ?? 0,
            onExport,
        });
    }, [exportPending, onExport, onExportState, viewStats]);
    useEffect(() => {
        onUndoStateChange(
            undoCounts.canUndo || undoCounts.canRedo
                ? {
                      canUndo: undoCounts.canUndo,
                      canRedo: undoCounts.canRedo,
                      onUndo: () => void runUndo(),
                      onRedo: () => void runRedo(),
                  }
                : EMPTY_UNDO_ACTIONS,
        );
    }, [onUndoStateChange, runRedo, runUndo, undoCounts]);
    useEffect(() => {
        onDeleteState({ canDelete: false, onDelete: () => {} });
    }, [onDeleteState]);
    // Header column filters (client-side, shared with every table). ?????????
    // ??? ??????? ? ??????????? must clear them like it clears RevoGrid's own.
    const {
        filters: columnFilters,
        setFilters: setColumnFilters,
        clearFilters,
    } = useGridColumnFilters("realization");
    useEffect(() => {
        onResetState({
            resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
            resetFiltersSorting: () => {
                clearFilters();
                void gridRef.current?.resetFiltersSorting();
            },
        });
    }, [clearFilters, onResetState]);
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

    // A new slice/ДЗО is a new selection context — a stale footer label must
    // not survive the refetch.
    useEffect(() => {
        onSelectionLabelChange(null);
    }, [onSelectionLabelChange, slice, orgId]);

    const isPending = view === null && error === null;

    // The loaded view's period goes to the workspace toolbar row (next to the
    // ДЗО/срез selects there); null while nothing is loaded.
    const periodText = view === null ? null : `${view.period.monthName} ${view.period.yearNum}`;
    useEffect(() => {
        onPeriodTextChange(periodText);
    }, [onPeriodTextChange, periodText]);

    // The toolbar always supplies a ДЗО; an empty id is the first-paint moment
    // while the dictionaries load.
    if (orgId === null) {
        return <GridLoadingSkeleton />;
    }

    if (error) {
        return (
            <div className="grid-viewport">
                <div className="grid-empty">
                    <Card>
                        <Card.Content className="text-danger p-4 text-sm">{error}</Card.Content>
                    </Card>
                </div>
            </div>
        );
    }

    if (isPending) {
        return <GridLoadingSkeleton />;
    }

    return (
        <div className="grid-viewport">
            <RawMaterialsGrid
                ref={gridRef}
                section={roomSection}
                rows={gridRows}
                isAdmin={isAdmin}
                showAssetColumn={false}
                readOnly={!isAdmin}
                hasBaseline={false}
                headerPinned={headerPinned}
                showServiceColumns={showServiceColumns}
                rowHeight={rowHeight}
                scrollPosition={{ form: `kppk:${roomSection}` }}
                columnDescriptors={visibleDescriptors}
                // Merge only in the original product/market order. Keep the
                // plugin mounted: replacing it would rebuild sorting too.
                sortable
                plugins={isCost ? [] : [CellMergePlugin]}
                cellMerge={cellMerge}
                assets={[]}
                deliveryBases={[]}
                onCommit={stableCommit}
                onPlaceholderCommit={stablePlaceholderCommit}
                onCommitFailed={refresh}
                remoteLocks={remoteLocks}
                remotePresence={remotePresence}
                remoteCellInfo={remoteCellInfo}
                onAcquireLease={(keys) => room.acquireLeases(keys)}
                onReleaseLease={(keys) => room.releaseLeases(keys)}
                onLockedCellEdit={gridWarnings.onLockedCellEdit}
                onValidationError={gridWarnings.onValidationError}
                onViewStatsChange={handleViewStats}
                onSortingChange={setIsGridSorted}
                // Row operations are disabled on this form (the rows come from
                // the source copies) — the grid just requires the handler.
                onSelectedRowsChange={stableSelectedRowsChange}
                onClipboardStateChange={handleClipboardState}
                onSelectionChange={handleSelectionChange}
                filters={columnFilters}
                onFiltersChange={setColumnFilters}
            />
        </div>
    );
}
