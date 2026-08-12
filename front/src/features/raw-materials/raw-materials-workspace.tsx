"use client";

import {
    AlertDialog,
    Button,
    Card,
    Dropdown,
    Kbd,
    Skeleton,
    Spinner,
    toast,
    Tooltip,
    Typography,
} from "@heroui/react";
import { Collapse, Expand, Trash } from "iconoir-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
    createDictionaryRecord,
    deleteDictionaryRecord,
    getAssetList,
    getDeliveryBasisList,
    getRawMaterialsList,
    getRawMaterialsMeta,
    getRawMaterialsMovement,
    getRawMaterialsPrices,
    getUserList,
    updateDictionaryCells,
    updateMovementCells,
    updatePriceCells,
    updateUserCells,
    type AssetRow,
    type CellUpdate,
    type CreateDictionaryPayload,
    type DeliveryBasisRow,
    type MovementRow,
    type PriceRow,
    type RawMaterialRow,
    type RawMaterialsMeta,
    type UserRow,
} from "@/shared/api/raw-materials";
import { isProjectAdmin, type CurrentUser } from "@/shared/api/report-collector";
import {
    ADMIN_SECTIONS,
    MAIN_SECTIONS,
    SECTION_LABELS,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "@/shared/grid/grid-config";
import { RawMaterialsGrid } from "@/shared/grid/raw-materials-grid";
import { formatMonthName, SectionTabs, type WorkspaceTab } from "@/shared/grid/section-tabs";
import { useAsyncData } from "@/shared/hooks/use-async-data";

import { ProfileModal } from "./profile-modal";

type RawMaterialsWorkspaceProps = {
    currentUser: CurrentUser;
};

type DictionarySection = "rawMaterials" | "assets" | "deliveryBases" | "users";

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
    return names.map((name) => ({ id: name, name }));
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
        const email = placeholderText(draft.email);
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

function SectionContent({
    section,
    reportMonth,
    isAdmin,
    assets,
    deliveryBases,
    onDeleteState,
}: {
    section: SectionId;
    reportMonth: string | null;
    isAdmin: boolean;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    onDeleteState: (state: { canDelete: boolean; onDelete: () => void }) => void;
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
        }
    }, [reportMonth, section]);

    const [reloadKey, setReloadKey] = useState(0);
    const { data: rows, error } = useAsyncData<
        MovementRow[] | PriceRow[] | RawMaterialRow[] | AssetRow[] | DeliveryBasisRow[] | UserRow[]
    >(loader, [loader, reloadKey]);

    const [headerPinned, setHeaderPinned] = useState(false);
    const [isMutationPending, setIsMutationPending] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const [placeholderDraft, setPlaceholderDraft] = useState<CreateDictionaryPayload>(() =>
        initialPlaceholderDraft(section),
    );
    const placeholderDraftRef = useRef(placeholderDraft);
    const placeholderCreatePendingRef = useRef(false);

    useEffect(() => {
        placeholderDraftRef.current = placeholderDraft;
    }, [placeholderDraft]);

    // Смена секции: сбрасываем состояние строки-заглушки, выделение и диалог
    // удаления, а данные перезагружаются через loader (меняется по section).
    useEffect(() => {
        const next = initialPlaceholderDraft(section);
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        setSelectedRowIds([]);
        setDeleteOpen(false);
    }, [section]);

    const canManageReferences = isAdmin && !MAIN_SECTIONS.has(section);
    const selectedRows = useMemo(
        () => (rows ?? []).filter((row) => selectedRowIds.includes(row.id)),
        [rows, selectedRowIds],
    );
    const canDelete = canManageReferences && selectedRows.length > 0;
    const readOnly = isMutationPending;

    useEffect(() => {
        onDeleteState({ canDelete, onDelete: () => setDeleteOpen(true) });
    }, [canDelete, onDeleteState]);

    const gridRows = useMemo(() => {
        const base = (rows ?? []).map(toGridRow);
        return canManageReferences ? [...base, placeholderRow(section, placeholderDraft)] : base;
    }, [canManageReferences, placeholderDraft, rows, section]);

    const refresh = useCallback(() => setReloadKey((key) => key + 1), []);

    async function commit(updates: CellUpdate[]) {
        if (readOnly || !updates.length) return;
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
            refresh();
        } catch (reason) {
            toast.danger("Изменения не сохранены", { description: errorMessage(reason) });
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

    async function commitPlaceholderPatch(patch: Record<string, unknown>) {
        if (!canManageReferences || isMutationPending) return;
        const next = {
            ...placeholderDraftRef.current,
            ...patch,
        } as CreateDictionaryPayload;
        placeholderDraftRef.current = next;
        setPlaceholderDraft(next);
        const payload = placeholderPayload(section, next, assets);
        if (payload) await createRecord(payload);
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

    if (error) {
        return (
            <div className="grid-empty">
                <Card>
                    <Card.Content className="text-danger p-4 text-sm">{error}</Card.Content>
                </Card>
            </div>
        );
    }

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
            <div className="grid-viewport" aria-busy={isMutationPending}>
                <RawMaterialsGrid
                    section={section}
                    rows={gridRows}
                    isAdmin={isAdmin}
                    showAssetColumn={isAdmin || assets.length > 1}
                    readOnly={readOnly}
                    hasBaseline={false}
                    headerPinned={headerPinned}
                    onHeaderPinnedChange={setHeaderPinned}
                    assets={assets}
                    deliveryBases={deliveryBases}
                    onCommit={commit}
                    onPlaceholderCommit={commitPlaceholderPatch}
                    onSelectedRowsChange={(selection) => setSelectedRowIds(selection.ids)}
                    onValidationError={(message) =>
                        toast.warning("Некорректное значение", { description: message })
                    }
                />
                {rows.length === 0 && !canManageReferences ? (
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
        </>
    );
}

function SectionFooter({
    section,
    canDelete,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
    onDelete,
}: {
    section: SectionId;
    canDelete: boolean;
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
    onDelete: () => void;
}) {
    return (
        <div className="table-footer">
            <div className="table-footer__summary" />
            <ActionBar
                section={section}
                canDelete={canDelete}
                isFullscreen={isFullscreen}
                isFullscreenPending={isFullscreenPending}
                onToggleFullscreen={onToggleFullscreen}
                onDelete={onDelete}
            />
        </div>
    );
}

const RAW_MATERIALS_PROJECT_KEY = "raw_materials";

export function RawMaterialsWorkspace({ currentUser }: RawMaterialsWorkspaceProps) {
    const { data: meta, error } = useAsyncData<RawMaterialsMeta>(() => getRawMaterialsMeta(), []);
    const [selectedTabId, setSelectedTabId] = useState<string | null>(null);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isFullscreenPending, setIsFullscreenPending] = useState(false);
    const [profileOpen, setProfileOpen] = useState(false);
    const [user, setUser] = useState(currentUser);
    const [deleteState, setDeleteState] = useState<{ canDelete: boolean; onDelete: () => void }>({
        canDelete: false,
        onDelete: () => {},
    });
    const [activeProjectKey, setActiveProjectKey] = useState<string>(
        currentUser.access?.some((project) => project.key === RAW_MATERIALS_PROJECT_KEY)
            ? RAW_MATERIALS_PROJECT_KEY
            : (currentUser.access?.[0]?.key ?? RAW_MATERIALS_PROJECT_KEY),
    );
    const { setTheme } = useTheme();

    const handleDeleteState = useCallback(
        (state: { canDelete: boolean; onDelete: () => void }) => setDeleteState(state),
        [],
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
            onProjectChange={setActiveProjectKey}
            onOpenProfile={() => setProfileOpen(true)}
        />
    );

    // Применяем сохранённую тему пользователя один раз при монтировании.
    // setTheme из next-themes меняет идентичность при каждой смене темы,
    // поэтому эффект не должен зависеть от неё — иначе он сбросит выбор пользователя.
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
                      label: SECTION_LABELS[referenceSection],
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

    const assets = toReferenceOptions(meta.assets);
    const deliveryBases = toReferenceOptions(meta.deliveryBases);

    const toolbar = (
        <div className="table-toolbar">
            <div className="table-toolbar__controls">
                <SectionTabs
                    tabs={tabs}
                    value={selectedTab.id}
                    onChange={(tab) => setSelectedTabId(tab.id)}
                />
            </div>
        </div>
    );

    const footer = (
        <SectionFooter
            section={selectedTab.section}
            canDelete={deleteState.canDelete}
            isFullscreen={isFullscreen}
            isFullscreenPending={isFullscreenPending}
            onToggleFullscreen={() => void setFullscreenMode(!isFullscreen)}
            onDelete={deleteState.onDelete}
        />
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
                                section={selectedTab.section}
                                reportMonth={selectedTab.reportMonth}
                                isAdmin={isAdmin}
                                assets={assets}
                                deliveryBases={deliveryBases}
                                onDeleteState={handleDeleteState}
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
    if (projects.length <= 1) {
        return (
            <div
                className={compact ? "brand brand--compact" : "brand"}
                aria-label={`РепортКоллектор ${projectName}`}
            >
                <span className="brand__name">РепортКоллектор</span>
                <span className="brand__edition">{projectName}</span>
            </div>
        );
    }

    return (
        <div className={compact ? "brand brand--compact" : "brand"}>
            <span className="brand__name">РепортКоллектор</span>
            <Dropdown>
                <Button
                    className="brand__edition brand__edition--button"
                    variant="ghost"
                    aria-label={`Проект: ${projectName}. Выберите проект`}
                >
                    {projectName}
                </Button>
                <Dropdown.Popover>
                    <Dropdown.Menu
                        aria-label="Доступные проекты"
                        selectedKeys={new Set([activeProjectKey])}
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
                </Dropdown.Popover>
            </Dropdown>
        </div>
    );
}

function AppHeader({
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
                    <span className="profile-pill__copy">
                        <strong>{fullname || email}</strong>
                        <small>{email}</small>
                    </span>
                </Button>
            </div>
        </header>
    );
}

type ActionItem = {
    id: string;
    label: string;
    icon: ReactNode;
    onPress: () => void;
    variant?: "primary" | "secondary" | "danger";
    disabled?: boolean;
    shortcut?: { key: "Z" | "Y" | "C" | "V" | "Del"; aria: string };
};

function ActionBar({
    section,
    canDelete,
    isFullscreen,
    isFullscreenPending,
    onToggleFullscreen,
    onDelete,
}: {
    section: SectionId;
    canDelete: boolean;
    isFullscreen: boolean;
    isFullscreenPending: boolean;
    onToggleFullscreen: () => void;
    onDelete: () => void;
}) {
    const showDelete = !MAIN_SECTIONS.has(section);
    const actions: ActionItem[] = [
        {
            id: "fullscreen",
            label: isFullscreen ? "Выйти из полного экрана" : "В полный экран",
            icon: isFullscreen ? <Collapse /> : <Expand />,
            onPress: onToggleFullscreen,
            disabled: isFullscreenPending,
        },
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
    ];

    return (
        <div className="action-bar" aria-label="Действия с таблицей">
            {actions.map((action) => {
                const button = (
                    <Button
                        className="action-button"
                        variant={action.variant ?? "secondary"}
                        isDisabled={action.disabled}
                        aria-label={action.label}
                        aria-keyshortcuts={action.shortcut?.aria}
                        onPress={action.onPress}
                    >
                        <span className="action-button__icon">{action.icon}</span>
                        <span className="action-button__label">{action.label}</span>
                    </Button>
                );
                if (!action.shortcut) {
                    return (
                        <span key={action.id} className="action-button-wrapper">
                            {button}
                        </span>
                    );
                }
                return (
                    <Tooltip key={action.id} delay={250}>
                        <Tooltip.Trigger>{button}</Tooltip.Trigger>
                        <Tooltip.Content placement="top" className="shortcut-tooltip">
                            <span>{action.label}</span>
                            <Kbd>
                                <Kbd.Abbr keyValue="ctrl" />
                                <Kbd.Content>{action.shortcut.key}</Kbd.Content>
                            </Kbd>
                        </Tooltip.Content>
                    </Tooltip>
                );
            })}
        </div>
    );
}

function DeleteRecordDialog({
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
