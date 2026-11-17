"use client";

import { CellMergePlugin } from "@revolist/rv-pro-trial";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { transportTypeMerges } from "@/features/logistics/logistics-merge";
import { ProfileModal } from "@/features/raw-materials/profile-modal";
import {
    getLogisticsCountries,
    getLogisticsOrgs,
    getLogisticsTransportTypes,
    type LogisticsCountryRow,
    type LogisticsDictionary,
    type LogisticsOrgRow,
    type LogisticsSectionId,
    type LogisticsTransportRow,
} from "@/shared/api/logistics";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import { DataGrid, type DataGridHandle, type GridViewStats } from "@/shared/grid/data-grid";
import type { ReferenceOption } from "@/shared/grid/grid-config";
import { SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useGridColumnFilters } from "@/shared/grid/use-grid-column-filters";
import { useScopedAsyncData } from "@/shared/hooks/use-scoped-async-data";
import { ActionBar, type ActionItem } from "@/shared/ui/action-bar";
import { buildClipboardActions } from "@/shared/ui/action-items";
import { AppHeader } from "@/shared/ui/app-header";
import { FormStatus } from "@/shared/ui/form-status";
import { GridErrorBoundary } from "@/shared/ui/grid-error-boundary";
import { GridLoadingSkeleton } from "@/shared/ui/grid-loading-skeleton";
import { toGridRow, type ClipboardActions } from "@/shared/ui/text-helpers";
import { useFullscreenPanels } from "@/shared/ui/use-fullscreen-panels";
import { useGridClipboardActions } from "@/shared/ui/use-grid-clipboard-actions";
import { useViewState } from "@/shared/ui/use-view-state";
import { ViewStrip } from "@/shared/ui/view-strip";
import { GridLoadError } from "@/shared/ui/workspace-feedback";
import { WorkspaceContent, WorkspaceFullscreenPanels } from "@/shared/ui/workspace-layout";

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

type LogisticsTab = WorkspaceTab & {
    section: LogisticsSectionId;
    load: () => Promise<LogisticsDictionary<LogisticsRow>>;
};

const LOGISTICS_TABS: readonly LogisticsTab[] = [
    {
        id: "logisticsCountries",
        section: "logisticsCountries",
        label: "Страны",
        reportMonth: null,
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
];

/** One stable empty list: a fresh `[]` would rebuild the grid columns. */
const NO_REFERENCES: ReferenceOption[] = [];

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
    const { headerPinned, rowHeight, setHeaderPinned, setRowHeight, resetRowHeight } =
        useViewState(activeProjectKey);

    const projects = user.access ?? [];
    const activeProject = projects.find((project) => project.key === LOGISTICS_PROJECT_KEY);
    const isAdmin = isProjectAdmin(activeProject);
    const visibleTabs = LOGISTICS_TABS;

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
    ];

    const toolbar = (
        <div className="table-toolbar">
            <div className="table-toolbar__row">
                <div className="table-toolbar__controls">
                    <SectionTabs
                        tabs={visibleTabs}
                        value={selectedTab?.id ?? ""}
                        onChange={(tab) => {
                            if (tab.id === selectedTabId) return;
                            setSelectedTabId(tab.id);
                            onSectionChange?.(tab.id);
                            setGridResetState(EMPTY_RESET_STATE);
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
        </div>
    );

    const footer = (
        <div className="table-footer">
            <div className="table-footer__status">
                <FormStatus
                    room={null}
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
                    />
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

    const rows = useMemo(() => data?.rows ?? [], [data]);
    const gridRows = useMemo(() => rows.map((row) => toGridRow(row)), [rows]);
    const source = data?.source ?? "external";
    const scopeStale = loading || loadedScope !== section;

    // The transport view merges the type cell over its vehicle types. The
    // backend returns the rows already grouped (type, then vehicle); a user
    // sorting reorders them, so the merge is switched off while it is active —
    // exactly like the «Продуктовые позиции» matrix.
    const cellMerge = useMemo(
        () =>
            section === "logisticsTransportTypes"
                ? transportTypeMerges(rows as LogisticsTransportRow[], sorted)
                : [],
        [rows, section, sorted],
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
