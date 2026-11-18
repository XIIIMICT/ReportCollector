"use client";

import { CellMergePlugin } from "@revolist/rv-pro-trial";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { transportTypeMerges, countryMarketMerges } from "@/features/logistics/logistics-merge";
import { LogisticsRatesSection } from "@/features/logistics/logistics-rates-section";
import { LogisticsUsersSection } from "@/features/logistics/logistics-users-section";
import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    exportLogisticsTable,
    getLogisticsCountries,
    getLogisticsOrgs,
    getLogisticsTransportTypes,
    type LogisticsCountryRow,
    type LogisticsDictionary,
    type LogisticsOrgRow,
    type LogisticsRole,
    type LogisticsSectionId,
    type LogisticsTransportRow,
} from "@/shared/api/logistics";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import type { CollabRoom } from "@/shared/collab/use-collab-room";
import { DataGrid, type DataGridHandle, type GridViewStats } from "@/shared/grid/data-grid";
import { exportFileName, type ReferenceOption } from "@/shared/grid/grid-config";
import { SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useAsyncData } from "@/shared/hooks/use-async-data";
import { useScopedAsyncData } from "@/shared/hooks/use-scoped-async-data";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import {
    NO_ROW_SELECTED_REASON,
    buildAddRowAction,
    buildClipboardActions,
    buildDeleteAction,
    buildExportAction,
    buildHistoryActions,
} from "@/shared/ui/action-items";
import { AppHeader } from "@/shared/ui/app-header";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { SmartSelect } from "@/shared/ui/smart-select";
import { toGridRow, type ClipboardActions } from "@/shared/ui/text-helpers";
import { ToolbarFilters } from "@/shared/ui/toolbar-filters";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useTableExport, type ExportMode } from "@/shared/ui/use-table-export";
import { readToolbarFilters, useToolbarFilters } from "@/shared/ui/use-toolbar-filters";
import { useViewState } from "@/shared/ui/use-view-state";
import { ViewStrip } from "@/shared/ui/view-strip";
import { GridLoadError } from "@/shared/ui/workspace-feedback";
import { WorkspaceContent, WorkspaceFullscreenPanels } from "@/shared/ui/workspace-layout";
import { useDeleteShortcut } from "@/shared/ui/workspace-shortcuts";

/** What a section reports so the workspace can render the export action. */
type LogisticsExportState = {
    pending: boolean;
    rowCount: number;
    onExport: (mode: ExportMode) => void;
};

const EMPTY_EXPORT_STATE: LogisticsExportState = {
    pending: false,
    rowCount: 0,
    onExport: () => {},
};

/**
 * The «Логистика» workspace.
 *
 * Its dictionaries are READ-ONLY projections of the corporate `dict` schema:
 * countries, transport types with their vehicle types, and organization
 * prefixes. The project currently has only the administrator role, so the
 * whole workspace is admin-only. Nothing here edits, journals or exports the
 * dictionaries — the data belongs to another system.
 *
 * The transport dictionary is rendered as a grouped table: consecutive rows of
 * one transport type merge the type cell, exactly like the «Продуктовые
 * позиции» matrix in «КППК» merges a product over its assets. A user sorting
 * breaks the groups, so the merge is disabled while sorting is active.
 */

const LOGISTICS_PROJECT_KEY = "logistics";

type LogisticsRow = LogisticsCountryRow | LogisticsTransportRow | LogisticsOrgRow;

type LogisticsDictionaryTab = WorkspaceTab & {
    section: LogisticsSectionId;
    load: () => Promise<LogisticsDictionary<LogisticsRow>>;
};

type LogisticsUsersTab = WorkspaceTab & {
    section: "logisticsUsers";
};

type LogisticsRatesTab = WorkspaceTab & {
    section: "logisticsRates";
};

type LogisticsTab = LogisticsDictionaryTab | LogisticsUsersTab | LogisticsRatesTab;

const LOGISTICS_TABS: readonly LogisticsTab[] = [
    {
        id: "logisticsRates",
        section: "logisticsRates",
        label: "Ставки",
        reportMonth: null,
    },
    {
        id: "logisticsCountries",
        section: "logisticsCountries",
        label: "Страны",
        reportMonth: null,
        separatorBefore: true,
        load: getLogisticsCountries,
    },
    {
        id: "logisticsTransportTypes",
        section: "logisticsTransportTypes",
        label: "Типы ТС и перевозки",
        reportMonth: null,
        load: getLogisticsTransportTypes,
    },
    {
        id: "logisticsOrgs",
        section: "logisticsOrgs",
        label: "Активы",
        reportMonth: null,
        load: getLogisticsOrgs,
    },
    {
        id: "logisticsUsers",
        section: "logisticsUsers",
        label: "Пользователи",
        reportMonth: null,
        separatorBefore: true,
    },
];

/** One stable empty list: a fresh `[]` would rebuild the grid columns. */
const NO_REFERENCES: ReferenceOption[] = [];

const RATE_MONTH_OPTIONS = [
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

const RATE_YEAR_OPTIONS = ["2025", "2026", "2027", "2028"].map((year) => ({
    id: year,
    name: year,
}));

const EMPTY_UNDO_ACTIONS = {
    canUndo: false,
    canRedo: false,
    onUndo: () => {},
    onRedo: () => {},
};

function defaultRatePeriod(): { month: string; year: string } {
    const now = new Date();
    const year = now.getFullYear();
    if (year >= 2025 && year <= 2028) {
        return { month: String(now.getMonth() + 1), year: String(year) };
    }
    return { month: "1", year: "2025" };
}

const EMPTY_RESET_STATE = {
    resetColumnWidths: () => {},
    resetFiltersSorting: () => {},
};

export function LogisticsWorkspace({
    currentUser,
    activeProjectKey,
    onProjectChange,
    initialSection = null,
    onSectionChange,
    supportMail,
}: {
    currentUser: CurrentUser;
    activeProjectKey: string;
    onProjectChange: (key: string) => void;
    /** Tab restored from `?section=`. */
    initialSection?: string | null;
    /** Mirrors the active tab into the URL so a reload keeps it. */
    onSectionChange?: (section: string | null) => void;
    /** `SUPPORT_MAIL` — the mailbox of the header support control. */
    supportMail: string;
}) {
    const [user, setUser] = useState(currentUser);
    const [profileOpen, setProfileOpen] = useState(false);
    const [selectedTabId, setSelectedTabId] = useState<string | null>(initialSection);
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
    const [clipboardState, setClipboardState] = useState<ClipboardActions>({
        canPaste: false,
        onCopy: () => {},
        onPaste: () => {},
    });
    const [gridResetState, setGridResetState] = useState(EMPTY_RESET_STATE);
    const [initialRatePeriod] = useState(defaultRatePeriod);
    const [rateMonth, setRateMonth] = useState<string>(() => {
        const stored = readToolbarFilters("logisticsRates")["month"];
        return typeof stored === "string" ? stored : initialRatePeriod.month;
    });
    const [rateYear, setRateYear] = useState<string>(() => {
        const stored = readToolbarFilters("logisticsRates")["year"];
        return typeof stored === "string" ? stored : initialRatePeriod.year;
    });
    const [undoActions, setUndoActions] = useState(EMPTY_UNDO_ACTIONS);
    const [exportState, setExportState] = useState<LogisticsExportState>(EMPTY_EXPORT_STATE);
    const [sectionRoom, setSectionRoom] = useState<CollabRoom | null>(null);
    const { setFilter: setPanelFilter } = useToolbarFilters("logisticsRates");
    const [deleteState, setDeleteState] = useState<{
        canDelete: boolean;
        blockedReason: string | null;
        onDelete: () => void;
    }>({ canDelete: false, blockedReason: null, onDelete: () => {} });
    const [userCreateState, setUserCreateState] = useState<{
        canCreate: boolean;
        onCreate: () => void;
    }>({ canCreate: false, onCreate: () => {} });
    const { data: authConfig } = useAsyncData<{ keycloakEnabled: boolean } | null>(
        () =>
            fetch("/api/auth/config")
                .then((response) => (response.ok ? response.json() : null))
                .catch(() => null),
        [],
    );
    const keycloakEnabled = authConfig?.keycloakEnabled === true;
    const {
        headerPinned,
        rowHeight,
        setHeaderPinned,
        setRowHeight,
        resetRowHeight,
        showServiceColumns,
        setShowServiceColumns,
    } = useViewState(activeProjectKey);
    const projects = user.access ?? [];
    const activeProject = projects.find((project) => project.key === LOGISTICS_PROJECT_KEY);
    const isAdmin = isProjectAdmin(activeProject);
    // The membership role decides the surface: an administrator gets every tab;
    // a reviewer and an asset worker get the rates form only (no dictionaries).
    const logisticsRole: LogisticsRole = isAdmin
        ? "admin"
        : activeProject?.roles.includes("reviewer")
          ? "reviewer"
          : activeProject?.roles.includes("asset_worker")
            ? "asset_worker"
            : "blocked";
    const visibleTabs = isAdmin
        ? LOGISTICS_TABS
        : LOGISTICS_TABS.filter((tab) => tab.section === "logisticsRates");

    const handleProjectChange = useCallback(
        (key: string) => {
            onProjectChange(key);
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

    const handleGridStats = useCallback((state: GridViewStats) => {
        setGridStats((current) => {
            if (current && JSON.stringify(current) === JSON.stringify(state)) return current;
            return state;
        });
    }, []);

    const handleClipboardState = useCallback((next: ClipboardActions) => {
        setClipboardState((current) =>
            current.canPaste === next.canPaste &&
            current.onCopy === next.onCopy &&
            current.onPaste === next.onPaste
                ? current
                : next,
        );
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
        setSelectionDetail(null);
    }, [selectedTabId]);

    // Apply the saved user theme once on mount. next-themes' setTheme changes
    // the identity on every theme switch, so the effect must not depend on it.
    useEffect(() => {
        setTheme(user.theme);
        // oxlint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const selectedTab =
        visibleTabs.find((tab) => tab.id === selectedTabId) ?? visibleTabs[0] ?? null;
    const isUsersTab = selectedTab?.section === "logisticsUsers";
    const isRatesTab = selectedTab?.section === "logisticsRates";
    useDeleteShortcut((isRatesTab || isUsersTab) && deleteState.canDelete, deleteState.onDelete);

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
            show: isRatesTab,
            offline: false,
            canUndo: undoActions.canUndo,
            canRedo: undoActions.canRedo,
            onUndo: undoActions.onUndo,
            onRedo: undoActions.onRedo,
        }),
        ...(isUsersTab
            ? buildAddRowAction({
                  show: userCreateState.canCreate,
                  label: "Добавить пользователя",
                  disabled: isFullscreenPending,
                  onAdd: userCreateState.onCreate,
              })
            : []),
        ...(isUsersTab || isRatesTab
            ? buildDeleteAction({
                  show: true,
                  label: isRatesTab ? "Удалить ставку" : "Удалить запись",
                  disabled: !deleteState.canDelete || isFullscreenPending,
                  blockedReason: deleteState.canDelete
                      ? null
                      : (deleteState.blockedReason ?? NO_ROW_SELECTED_REASON),
                  onDelete: deleteState.onDelete,
                  shortcut: { key: "Del", aria: "Control+Delete" },
              })
            : []),
        buildExportAction({
            pending: exportState.pending,
            disabled: exportState.pending || isFullscreenPending,
            rowCount: exportState.rowCount,
            onExport: exportState.onExport,
        }),
    ];

    const toolbar = (
        <div className="table-toolbar">
            {/* The period filter sits on the TABS row (like the «Сырьё» version
                picker / «Ремонт» block filter), before the strip: `inline`
                collapses the width when the tab has no period. */}
            <ToolbarFilters placement="inline" visible={isRatesTab}>
                <SmartSelect
                    options={RATE_MONTH_OPTIONS}
                    value={rateMonth}
                    placeholder="месяц"
                    compactLabel="мес."
                    compactBelow="sm"
                    ariaLabel="Месяц периода"
                    fullWidth={false}
                    onOpenChange={setToolbarControlOpen}
                    onChange={(value) => {
                        if (typeof value !== "string") return;
                        setRateMonth(value);
                        setPanelFilter("logisticsRates", "month", value);
                    }}
                />
                <SmartSelect
                    options={RATE_YEAR_OPTIONS}
                    value={rateYear}
                    placeholder="год"
                    compactLabel="год"
                    compactBelow="sm"
                    ariaLabel="Год периода"
                    fullWidth={false}
                    onOpenChange={setToolbarControlOpen}
                    onChange={(value) => {
                        if (typeof value !== "string") return;
                        setRateYear(value);
                        setPanelFilter("logisticsRates", "year", value);
                    }}
                />
            </ToolbarFilters>
            <div className="table-toolbar__controls">
                <SectionTabs
                    tabs={visibleTabs}
                    value={selectedTab?.id ?? ""}
                    onChange={(tab) => {
                        if (tab.id === selectedTabId) return;
                        setSelectedTabId(tab.id);
                        onSectionChange?.(tab.id);
                        setGridResetState(EMPTY_RESET_STATE);
                        setUndoActions(EMPTY_UNDO_ACTIONS);
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
                columnResetLabel="Сбросить ширину столбцов"
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
                onDropdownOpenChange={setToolbarControlOpen}
            />
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

    if (!isAdmin) {
        return (
            <main className="app-shell">
                {header}
                <div className="app-body">
                    <section className="workspace">
                        <div className="project-placeholder">
                            <p className="text-muted">
                                В «Логистике» пока доступна только роль администратора.
                            </p>
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
                    selectedTab.section === "logisticsRates" ? (
                        <LogisticsRatesSection
                            month={Number(rateMonth)}
                            year={Number(rateYear)}
                            role={logisticsRole}
                            headerPinned={headerPinned}
                            rowHeight={rowHeight}
                            showServiceColumns={showServiceColumns}
                            onServiceColumnsChange={setShowServiceColumns}
                            onGridStatsChange={handleGridStats}
                            onSelectionLabelChange={setSelectionLabel}
                            onSelectionDetailChange={setSelectionDetail}
                            onClipboardStateChange={handleClipboardState}
                            onResetState={setGridResetState}
                            onUndoStateChange={setUndoActions}
                            onDeleteState={setDeleteState}
                            onExportState={setExportState}
                            onRoomChange={setSectionRoom}
                        />
                    ) : selectedTab.section === "logisticsUsers" ? (
                        <LogisticsUsersSection
                            key={selectedTab.id}
                            keycloakEnabled={keycloakEnabled}
                            headerPinned={headerPinned}
                            rowHeight={rowHeight}
                            onGridStatsChange={handleGridStats}
                            onSelectionLabelChange={setSelectionLabel}
                            onSelectionDetailChange={setSelectionDetail}
                            onClipboardStateChange={handleClipboardState}
                            onResetState={setGridResetState}
                            onDeleteState={setDeleteState}
                            onUserCreateState={setUserCreateState}
                            onExportState={setExportState}
                        />
                    ) : (
                        <LogisticsDictionarySection
                            key={selectedTab.id}
                            section={selectedTab.section}
                            load={selectedTab.load}
                            headerPinned={headerPinned}
                            rowHeight={rowHeight}
                            onGridStatsChange={handleGridStats}
                            onSelectionLabelChange={setSelectionLabel}
                            onSelectionDetailChange={setSelectionDetail}
                            onClipboardStateChange={handleClipboardState}
                            onResetState={setGridResetState}
                            onExportState={setExportState}
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

/** One read-only dictionary tab: rows come straight from the external tables. */
function LogisticsDictionarySection({
    section,
    load,
    headerPinned,
    rowHeight,
    onGridStatsChange,
    onSelectionLabelChange,
    onSelectionDetailChange,
    onClipboardStateChange,
    onResetState,
    onExportState,
}: {
    section: LogisticsSectionId;
    load: () => Promise<LogisticsDictionary<LogisticsRow>>;
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
    onExportState: (state: LogisticsExportState) => void;
}) {
    const gridRef = useRef<DataGridHandle>(null);
    const [sorted, setSorted] = useState(false);
    const { filters, setFilters, clearFilters } = useGridColumnFilters(
        LOGISTICS_PROJECT_KEY,
        section,
    );
    const { data, error, loading, loadedScope } = useScopedAsyncData(section, load, [
        load,
        section,
    ]);
    const handleClipboardState = useGridClipboardActions(gridRef, onClipboardStateChange);
    const { pending: exportPending, exportTable } = useTableExport({
        gridRef,
        fileName: () => exportFileName(section, null),
        request: (view, fileName) =>
            exportLogisticsTable({
                section,
                headerPinned,
                columns: view.columns,
                rowIds: view.rowIds,
                fileName,
            }),
    });

    const rows = useMemo(() => data?.rows ?? [], [data]);
    const gridRows = useMemo(() => rows.map((row) => toGridRow(row)), [rows]);
    const source = data?.source ?? "external";
    const scopeStale = loading || loadedScope !== section;

    // The countries and transport views merge the FIRST column over its run:
    // market → countries, transport type → vehicle types. The backend returns
    // the rows already grouped (key, then the sorted label). A user sorting or
    // any active column filter makes the merge impossible (the ranges are
    // computed over the data array, while the grid may hide/reorder rows), so
    // it is switched off in both cases.
    const mergeDisabled = sorted || Object.keys(filters).length > 0;
    const cellMerge = useMemo(() => {
        if (mergeDisabled) return [];
        if (section === "logisticsCountries") {
            return countryMarketMerges(rows as LogisticsCountryRow[], false);
        }
        if (section === "logisticsTransportTypes") {
            return transportTypeMerges(rows as LogisticsTransportRow[], false);
        }
        return [];
    }, [mergeDisabled, rows, section]);

    useEffect(() => {
        onExportState({ pending: exportPending, rowCount: rows.length, onExport: exportTable });
    }, [exportPending, exportTable, onExportState, rows.length]);

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
    if (scopeStale) {
        return <GridLoadingSkeleton />;
    }

    return (
        <>
            {source === "fallback" ? (
                <div className="grid-source-warning" role="status">
                    Внешний справочник недоступен — показаны демонстрационные значения.
                </div>
            ) : null}
            <div className="grid-viewport">
                <GridErrorBoundary>
                    <DataGrid
                        ref={gridRef}
                        section={section}
                        rows={gridRows}
                        isAdmin
                        showAssetColumn={false}
                        readOnly
                        hasBaseline={false}
                        headerPinned={headerPinned}
                        showServiceColumns={false}
                        rowHeight={rowHeight}
                        scrollPosition={{ form: `${LOGISTICS_PROJECT_KEY}:${section}` }}
                        assets={NO_REFERENCES}
                        deliveryBases={NO_REFERENCES}
                        onCommit={async () => false}
                        onPlaceholderCommit={async () => false}
                        onSelectedRowsChange={() => {}}
                        onValidationError={() => {}}
                        onViewStatsChange={onGridStatsChange}
                        onClipboardStateChange={handleClipboardState}
                        onSelectionChange={(selection) => {
                            onSelectionLabelChange(selection.label ?? null);
                            onSelectionDetailChange(selection.detail ?? null);
                        }}
                        filters={filters}
                        onFiltersChange={setFilters}
                        plugins={cellMerge.length > 0 ? [CellMergePlugin] : []}
                        cellMerge={cellMerge}
                        onSortingChange={setSorted}
                    />
                </GridErrorBoundary>
                {gridRows.length === 0 ? (
                    <div className="grid-empty">В этом справочнике пока нет строк.</div>
                ) : null}
            </div>
        </>
    );
}
