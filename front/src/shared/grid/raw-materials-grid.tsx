import { Button, Dropdown, Label, Tooltip, type Selection } from "@heroui/react";
import {
    Editor,
    RevoGrid,
    type BeforeRangeSaveDataDetails,
    type BeforeSaveDataDetails,
    type ColumnGrouping,
    type ColumnRegular,
    type Editors,
    type HyperFunc,
    type RangeArea,
    type RowHeaders,
    type VNode,
} from "@revolist/react-datagrid";
import { MoreVert } from "iconoir-react";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    forwardRef,
    useImperativeHandle,
    type CSSProperties,
    type Ref,
} from "react";
import { createRoot, type Root } from "react-dom/client";

import type { CellUpdate } from "@/shared/api/raw-materials";

import {
    buildGridColumns,
    exportColumnsFromGrid,
    getEditableKeys,
    getVisibleDescriptors,
    placeholderLastCellCompare,
    RUSSIAN_FILTER_CONFIG,
    type ColumnDescriptor,
    type ExportColumn,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "./grid-config";
import { extractEdits } from "./grid-edit";
import { MultiAssetEditor, ReferenceEditor } from "./grid-editors";

export type RawMaterialsGridState = {
    columns: ExportColumn[];
    rowIds: string[];
};

export type GridViewStats = {
    totalRows: number;
    totalCols: number;
    visibleRows: number;
    visibleCols: number;
};

export type RawMaterialsGridHandle = {
    getExportState: () => Promise<RawMaterialsGridState>;
    copySelection: () => Promise<void>;
    pasteIntoSelection: () => Promise<void>;
};

type Props = {
    section: SectionId;
    rows: GridRow[];
    isAdmin: boolean;
    keycloakEnabled?: boolean;
    showAssetColumn: boolean;
    readOnly: boolean;
    hasBaseline: boolean;
    headerPinned: boolean;
    onHeaderPinnedChange: (isPinned: boolean) => void;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    onCommit: (updates: CellUpdate[]) => Promise<boolean>;
    onPlaceholderCommit: (patch: Record<string, unknown>) => Promise<boolean>;
    /** Сервер отклонил правки: источник нужно вернуть (откат оптимистичных значений). */
    onCommitFailed?: () => void;
    onSelectedRowsChange: (selection: { ids: string[]; wholeRows: boolean }) => void;
    onValidationError: (message: string) => void;
    onPasteSkipped?: (description: string) => void;
    /** Вставляемый диапазон больше выделенного — значения обрезаны выделением. */
    onPasteTruncated?: () => void;
    /** Чужие арендованные ячейки (активное редактирование). */
    remoteLocks?: ReadonlyMap<string, { fullname: string; email: string }>;
    /** Ячейки, выделенные другими участниками (присутствие) — тоже защищены. */
    remotePresence?: ReadonlyMap<string, { fullname: string; email: string }>;
    onLockedCellEdit?: (key: string, ownerName: string) => void;
    onAcquireLease?: (keys: string[]) => void;
    onReleaseLease?: (keys: string[]) => void;
    onViewStatsChange?: (stats: GridViewStats) => void;
    onClipboardStateChange?: (state: { canPaste: boolean }) => void;
    /** Завершённое выделение (для присутствия): подпись и ключи ячеек диапазона. */
    onSelectionChange?: (selection: { label: string | null; cellKeys: string[] }) => void;
    /** Ключи ячеек, на которых находятся другие участники (подсветка + подсказка). */
    remoteCellInfo?: ReadonlyMap<string, { name: string; label: string | null }>;
};

/** Задержка отправки завершённого выделения в присутствие (после отпускания). */
const PRESENCE_DEBOUNCE_MS = 400;

type GridSortingOrder = "asc" | "desc";

type GridSortingConfig = {
    columns: Array<{
        prop: string;
        order: GridSortingOrder;
        cellCompare: typeof placeholderLastCellCompare;
    }>;
};

type GridSortingEvent = {
    sorting?: Record<string, GridSortingOrder | undefined>;
    sortingOrder?: Array<string | number>;
};

type SelectionStoreHandle = {
    set: (key: string, value: unknown) => void;
    setRange: (start: { x: number; y: number }, end: { x: number; y: number }) => void;
};

function isGridTarget(event: Event, grid: HTMLElement): boolean {
    return grid.contains(event.target as Node | null) || event.composedPath().includes(grid);
}

/**
 * Канонические ключи ячеек выделенного диапазона — они едины для всех
 * участников независимо от состава видимых колонок (координаты разъезжаются,
 * когда у пользователей разные наборы колонок).
 */
function selectionCellKeys(
    range: RangeArea | null,
    rows: GridRow[],
    columns: ColumnRegular[],
): string[] {
    if (!range || range.x1 < range.x || range.y1 < range.y) return [];
    const keys: string[] = [];
    for (let y = range.y; y <= Math.min(range.y1, rows.length - 1); y++) {
        const row = rows[y];
        if (!row?.canonicalCells) continue;
        for (let x = range.x; x <= Math.min(range.x1, columns.length - 1); x++) {
            const prop = columns[x]?.prop;
            if (prop == null) continue;
            const key = row.canonicalCells[String(prop)];
            if (key) keys.push(key);
        }
    }
    return keys;
}

/**
 * Подпись колонки с учётом вложенного заголовка: «Потребность / План, т».
 */
function columnLabel(
    column: ColumnRegular | undefined,
    descriptorByKey: Map<string, ColumnDescriptor>,
): string | null {
    if (!column) return null;
    const descriptor = descriptorByKey.get(String(column.prop));
    if (descriptor) {
        return `«${[...(descriptor.path ?? []), descriptor.label].join(" / ")}»`;
    }
    const fallback = String(column.name ?? column.prop ?? "");
    return fallback ? `«${fallback}»` : null;
}

/**
 * Человекочитаемое описание выделенного диапазона, например
 * «Бизнес-план, т» – «Факт, т», строки 3–5 или «Потребность / План, т», строка 2.
 * Названия колонок берутся в кавычки; диапазоны записываются через тире.
 * null — выделения нет.
 */
export function selectionLabelForRange(
    range: RangeArea | null,
    columns: ColumnRegular[],
    descriptorByKey: Map<string, ColumnDescriptor>,
    rowCount: number,
): string | null {
    if (!range || range.x1 < range.x || range.y1 < range.y) return null;
    const firstLabel = columnLabel(columns[range.x], descriptorByKey);
    const lastLabel = columnLabel(columns[range.x1], descriptorByKey);
    if (!firstLabel || !lastLabel) return null;
    const columnText = firstLabel === lastLabel ? firstLabel : `${firstLabel} – ${lastLabel}`;
    const startRow = range.y + 1;
    const endRow = Math.min(range.y1 + 1, rowCount);
    const rowText = startRow === endRow ? `строка ${startRow}` : `строки ${startRow}–${endRow}`;
    return `${columnText}, ${rowText}`;
}

/**
 * Канонические ключи всех ячеек выделенных строк (по всем колонкам).
 */
function selectionRowCellKeys(
    rows: GridRow[],
    startRow: number,
    endRow: number,
    columns: ColumnRegular[],
): string[] {
    const keys: string[] = [];
    for (let y = startRow; y <= endRow; y++) {
        const row = rows[y];
        if (!row?.canonicalCells) continue;
        for (const column of columns) {
            const prop = column.prop;
            if (prop == null) continue;
            const key = row.canonicalCells[String(prop)];
            if (key) keys.push(key);
        }
    }
    return keys;
}

export function sortingConfigFromEvent(detail: GridSortingEvent): GridSortingConfig {
    const sorting = detail.sorting ?? {};
    const order = detail.sortingOrder?.map(String) ?? Object.keys(sorting);
    return {
        columns: order.flatMap((prop) => {
            const direction = sorting[prop];
            return direction
                ? [{ prop, order: direction, cellCompare: placeholderLastCellCompare }]
                : [];
        }),
    };
}

function RawMaterialsGridInner(
    {
        section,
        rows,
        isAdmin,
        keycloakEnabled = false,
        showAssetColumn,
        readOnly,
        hasBaseline,
        headerPinned,
        onHeaderPinnedChange,
        assets,
        deliveryBases,
        onCommit,
        onPlaceholderCommit,
        onCommitFailed,
        onSelectedRowsChange,
        onValidationError,
        onPasteSkipped,
        onPasteTruncated,
        remoteLocks = new Map(),
        remotePresence = new Map(),
        onLockedCellEdit,
        onAcquireLease,
        onReleaseLease,
        onViewStatsChange,
        onClipboardStateChange,
        onSelectionChange,
        remoteCellInfo = new Map(),
    }: Props,
    forwardedRef: Ref<RawMaterialsGridHandle>,
) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const verticalScrollRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    const [gridRevision, setGridRevision] = useState(0);
    const menuHostRef = useRef<HTMLElement | null>(null);
    const menuRootRef = useRef<Root | null>(null);
    const resizedWidthsRef = useRef(new Map<string, number>());
    const [showServiceColumns, setShowServiceColumns] = useState(false);
    const sortingBySectionRef = useRef(new Map<SectionId, GridSortingConfig>());
    const userSortedSectionsRef = useRef(new Set<SectionId>());
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);
    const rowValuesRef = useRef<Map<string, Readonly<Record<string, unknown>>>>(new Map());
    const activeLeaseRef = useRef<string | null>(null);
    const [selectionRange, setSelectionRange] = useState<RangeArea | null>(null);
    const [canPaste, setCanPaste] = useState(false);
    // Плавающий HeroUI Tooltip над ячейками, занятыми другим участником.
    const [remoteTooltip, setRemoteTooltip] = useState<{
        text: string;
        left: number;
        top: number;
    } | null>(null);
    const remoteTooltipTargetRef = useRef<Element | null>(null);

    useEffect(() => {
        rowValuesRef.current = new Map(rows.map((row) => [row.id, { ...row }]));
    }, [rows]);

    const sorting = sortingBySectionRef.current.get(section);

    const descriptors = useMemo(
        () => getVisibleDescriptors(section, showAssetColumn, showServiceColumns),
        [section, showAssetColumn, showServiceColumns],
    );
    const descriptorByKey = useMemo(
        () => new Map(descriptors.map((descriptor) => [descriptor.key, descriptor])),
        [descriptors],
    );

    const editableKeys = useMemo(
        () => getEditableKeys(section, isAdmin, showAssetColumn),
        [isAdmin, section, showAssetColumn],
    );

    const editors = useMemo<Editors>(
        () => ({
            multiAssets: Editor(MultiAssetEditor),
            reference: Editor(ReferenceEditor),
        }),
        [],
    );

    const columns = useMemo(() => {
        const next = buildGridColumns(section, {
            isAdmin,
            showAssetColumn,
            readOnly,
            hasBaseline,
            assets,
            deliveryBases,
            selectedRowIds: selectedRowIdSet,
            showServiceColumns,
            remoteLocks,
            remoteCellInfo,
            onBooleanChange: (row, field, value) => {
                if (row.isPlaceholder) void onPlaceholderCommit({ [field]: value });
                else void onCommit([{ rowId: row.id, field, value }]);
            },
        });
        restoreColumnWidths(next, resizedWidthsRef.current, section);
        return next;
    }, [
        assets,
        deliveryBases,
        hasBaseline,
        isAdmin,
        showAssetColumn,
        onCommit,
        onPlaceholderCommit,
        readOnly,
        remoteCellInfo,
        remoteLocks,
        selectedRowIdSet,
        section,
        showServiceColumns,
    ]);

    const flatColumns: ColumnRegular[] = useMemo(
        () =>
            columns.flatMap((column) =>
                (column as ColumnGrouping).children?.length
                    ? ((column as ColumnGrouping).children as ColumnRegular[])
                    : [column as ColumnRegular],
            ),
        [columns],
    );

    const headerHeight = descriptors.some((descriptor) => descriptor.path?.length) ? 88 : 44;
    const contentHeight = headerHeight + rows.length * 36 + 18;

    const selectionRangeRef = useRef<RangeArea | null>(null);
    const updateSelectionRange = useCallback((range: RangeArea | null) => {
        selectionRangeRef.current = range;
        setSelectionRange(range);
    }, []);

    // Выделенные строки (кнопки номеров строк) — их тоже отражаем в присутствии.
    const selectedRowsRange = useMemo(() => {
        if (selectedRowIds.length === 0) return null;
        const indexes = rows
            .map((row, index) => (selectedRowIdSet.has(row.id) ? index : -1))
            .filter((index) => index >= 0);
        if (indexes.length === 0) return null;
        return { min: indexes[0], max: indexes[indexes.length - 1] };
    }, [rows, selectedRowIdSet, selectedRowIds.length]);

    // Присутствие: сообщаем наружу только завершённое выделение (не каждое
    // событие перетаскивания), чтобы живая передача не ломала перетаскивание
    // и не спамила комнату. Дебаунс гарантирует отправку после отпускания.
    // Колбэк и расчётные данные держим в ref, чтобы эхо presence:update
    // (пересборка members на нашем же клиенте) не порождало повторные отправки.
    const presenceInputRef = useRef({
        descriptorByKey,
        flatColumns,
        rows,
        selectedRowsRange,
        onSelectionChange,
    });
    presenceInputRef.current = {
        descriptorByKey,
        flatColumns,
        rows,
        selectedRowsRange,
        onSelectionChange,
    };
    useEffect(() => {
        const timer = setTimeout(() => {
            const { descriptorByKey, flatColumns, rows, selectedRowsRange, onSelectionChange } =
                presenceInputRef.current;
            const label = selectedRowsRange
                ? selectedRowsRange.min === selectedRowsRange.max
                    ? `строка ${selectedRowsRange.min + 1}`
                    : `строки ${selectedRowsRange.min + 1}–${selectedRowsRange.max + 1}`
                : selectionLabelForRange(selectionRange, flatColumns, descriptorByKey, rows.length);
            const keys = selectedRowsRange
                ? selectionRowCellKeys(
                      rows,
                      selectedRowsRange.min,
                      selectedRowsRange.max,
                      flatColumns,
                  )
                : selectionCellKeys(selectionRange, rows, flatColumns);
            onSelectionChange?.({ label, cellKeys: keys });
        }, PRESENCE_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [selectedRowsRange, selectionRange]);

    const getSelectionStore = useCallback((): SelectionStoreHandle | undefined => {
        const grid = gridRef.current;
        const overlay = (grid?.shadowRoot?.querySelector("revogr-overlay-selection") ??
            grid?.querySelector("revogr-overlay-selection")) as
            | (HTMLElement & { selectionStore?: SelectionStoreHandle })
            | null;
        return overlay?.selectionStore;
    }, []);

    const clearGridSelection = useCallback(() => {
        const store = getSelectionStore();
        store?.set("range", null);
        store?.set("focus", null);
        store?.set("edit", null);
        updateSelectionRange(null);
    }, [getSelectionStore, updateSelectionRange]);

    const handleRowNumberClick = useCallback(
        (event: MouseEvent, rowIndex: number, id: string) => {
            event.preventDefault();
            event.stopPropagation();
            const modifiers = event.ctrlKey || event.metaKey;
            if (event.shiftKey && !modifiers) {
                const anchor = rowSelectionAnchorRef.current ?? rowIndex;
                const start = Math.min(anchor, rowIndex);
                const end = Math.max(anchor, rowIndex);
                setSelectedRowIds(
                    rows
                        .slice(start, end + 1)
                        .map((row) => row.id)
                        .filter(Boolean),
                );
                // Выделение строк полностью сбрасывает диапазон ячеек (и визуально).
                clearGridSelection();
                return;
            }
            if (modifiers) {
                const next = new Set(selectedRowIdSet);
                if (next.has(id)) {
                    next.delete(id);
                    rowSelectionAnchorRef.current = null;
                } else {
                    next.add(id);
                    rowSelectionAnchorRef.current = rowIndex;
                }
                setSelectedRowIds([...next]);
                clearGridSelection();
                return;
            }
            rowSelectionAnchorRef.current = rowIndex;
            setSelectedRowIds([id]);
            // Выделение строк полностью сбрасывает диапазон ячеек (и визуально).
            clearGridSelection();
        },
        [clearGridSelection, rows, selectedRowIdSet],
    );

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: 52,
            columnTemplate: (createElement) =>
                createElement("div", { class: "grid-options-menu-host" }),
            columnProperties: () => ({
                className: {
                    "report-row-header": true,
                    "report-row-header--heading": true,
                },
            }),
            cellProperties: ({ model }) => {
                const row = model as GridRow;
                return {
                    className: {
                        "report-row-header": true,
                        "report-row-header--selected": selectedRowIds.includes(row.id),
                    },
                };
            },
            cellTemplate: (h: HyperFunc<VNode>, { rowIndex, model }) => {
                const row = model as GridRow;
                const selected = selectedRowIds.includes(row.id);
                return h(
                    "button",
                    {
                        type: "button",
                        class: {
                            "report-row-header__button": true,
                            "report-row-header__button--selected": selected,
                        },
                        title: selected
                            ? `Снять выделение строки ${rowIndex + 1}`
                            : `Выделить строку ${rowIndex + 1}`,
                        onMouseDown: (event: MouseEvent) => {
                            event.stopPropagation();
                        },
                        onClick: (event: MouseEvent) => {
                            event.stopPropagation();
                            handleRowNumberClick(event, rowIndex, row.id);
                        },
                    },
                    String(rowIndex + 1),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [selectedRowIds, handleRowNumberClick],
    );

    useEffect(() => {
        onSelectedRowsChange({
            ids: selectedRowIds,
            wholeRows: true,
        });
    }, [onSelectedRowsChange, selectedRowIds]);

    function canEditDescriptor(descriptor: ColumnDescriptor, row: GridRow): boolean {
        return !(
            readOnly ||
            row.isDeleted ||
            descriptor.fixed ||
            descriptor.kind === "calculated" ||
            (descriptor.placeholderOnly && !row.isPlaceholder) ||
            (descriptor.placeholderReadOnly && row.isPlaceholder) ||
            (keycloakEnabled &&
                section === "users" &&
                !row.isPlaceholder &&
                (descriptor.key === "email" || descriptor.key === "fullname"))
        );
    }

    const canPasteRange = useCallback(
        (range: RangeArea | null): boolean => {
            if (!range || readOnly) return false;
            for (let y = range.y; y <= range.y1; y++) {
                const row = rows[y];
                if (!row) continue;
                for (let x = range.x; x <= range.x1; x++) {
                    const column = flatColumns[x] as ColumnRegular | undefined;
                    if (!column) continue;
                    const descriptor = descriptorByKey.get(String(column.prop));
                    if (!descriptor || !editableKeys.has(descriptor.key)) continue;
                    if (!canEditDescriptor(descriptor, row)) continue;
                    const canonicalKey = row.canonicalCells?.[descriptor.key];
                    if (
                        canonicalKey &&
                        (remoteLocks.has(canonicalKey) || remotePresence.has(canonicalKey))
                    )
                        continue;
                    return true;
                }
            }

            return false;
        },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [descriptorByKey, editableKeys, flatColumns, readOnly, remoteLocks, remotePresence, rows],
    );

    useEffect(() => {
        setCanPaste(canPasteRange(selectionRange));
    }, [canPasteRange, selectionRange]);

    useEffect(() => {
        onClipboardStateChange?.({ canPaste });
    }, [canPaste, onClipboardStateChange]);

    function normalizeValue(
        value: unknown,
        kind: ColumnDescriptor["kind"],
    ): { ok: true; value: unknown } | { ok: false; message: string } {
        if (kind === "number") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const parsed =
                typeof value === "number" ? value : Number(String(value).trim().replace(",", "."));
            if (!Number.isFinite(parsed) || parsed < 0) {
                return { ok: false, message: "введите конечное неотрицательное число." };
            }
            return { ok: true, value: parsed };
        }
        if (kind === "boolean") {
            if (typeof value === "boolean") return { ok: true, value };
            const normalized = String(value).trim().toLocaleLowerCase("ru-RU");
            if (["РґР°", "true", "1"].includes(normalized)) return { ok: true, value: true };
            if (["нет", "false", "0"].includes(normalized)) return { ok: true, value: false };
            return { ok: false, message: "выберите «Да» или «Нет»." };
        }
        if (kind === "assetsMulti") {
            // В справочных значениях запятых нет, поэтому запятая — разделитель.
            const requested = Array.isArray(value) ? value : String(value ?? "").split(",");
            const canonical = canonicalOptions(requested, assets);
            if (!canonical.ok) return canonical;
            return { ok: true, value: canonical.value };
        }
        if (kind === "asset" || kind === "basis") {
            if (value === null || value === undefined || String(value).trim() === "") {
                return { ok: true, value: null };
            }
            const canonical = canonicalOptions(
                [String(value)],
                kind === "asset" ? assets : deliveryBases,
            );
            if (!canonical.ok) return canonical;
            return { ok: true, value: canonical.value[0] ?? null };
        }
        if (value === null || value === undefined) return { ok: true, value: null };
        return { ok: true, value: String(value).trim() };
    }

    function serializeCellValue(value: unknown): string {
        if (value === null || value === undefined) return "";
        if (Array.isArray(value)) return value.map(serializeCellValue).join(", ");
        if (typeof value === "object") {
            const named = value as { name?: unknown };
            return named.name !== undefined ? String(named.name) : String(value);
        }
        return String(value);
    }

    function beforeEditStart(event: CustomEvent<BeforeSaveDataDetails>) {
        const descriptor = descriptorByKey.get(String(event.detail.prop));
        const row = event.detail.model as GridRow;
        if (
            !descriptor ||
            !editableKeys.has(descriptor.key) ||
            !canEditDescriptor(descriptor, row)
        ) {
            event.preventDefault();
            return;
        }
        const canonicalKey = row.canonicalCells?.[descriptor.key];
        if (canonicalKey) {
            // Аренда или чужое выделение — ячейка защищена: начать редактирование нельзя.
            const owner = remoteLocks.get(canonicalKey) ?? remotePresence.get(canonicalKey);
            if (owner) {
                event.preventDefault();
                onLockedCellEdit?.(
                    canonicalKey,
                    owner.fullname || owner.email || "другой пользователь",
                );
                return;
            }
        }
        if (canonicalKey && !row.isPlaceholder) {
            activeLeaseRef.current = canonicalKey;
            onAcquireLease?.([canonicalKey]);
        } else {
            activeLeaseRef.current = null;
        }
    }

    function beforeEdit(event: CustomEvent<BeforeSaveDataDetails>) {
        const descriptor = descriptorByKey.get(String(event.detail.prop));
        const row = event.detail.model as GridRow;
        if (
            !descriptor ||
            !editableKeys.has(descriptor.key) ||
            !canEditDescriptor(descriptor, row)
        ) {
            event.preventDefault();
            return;
        }
        const canonicalKey = row.canonicalCells?.[descriptor.key];
        if (canonicalKey) {
            const byLease = remoteLocks.get(canonicalKey);
            // Чужая аренда блокирует всегда; чужое выделение — кроме случая, когда
            // ячейку редактирую я сам (моя аренда сильнее чужого выделения).
            const byPresence =
                remotePresence.get(canonicalKey) && activeLeaseRef.current !== canonicalKey
                    ? remotePresence.get(canonicalKey)
                    : undefined;
            const owner = byLease ?? byPresence;
            if (owner) {
                event.preventDefault();
                onLockedCellEdit?.(
                    canonicalKey,
                    owner.fullname || owner.email || "другой пользователь",
                );
                return;
            }
        }
        const normalized = normalizeValue(event.detail.val, descriptor.kind);
        if (!normalized.ok) {
            event.preventDefault();
            onValidationError(normalized.message);
            return;
        }
        event.detail.val = normalized.value;
    }

    function beforeRangeEdit(event: CustomEvent<BeforeRangeSaveDataDetails>) {
        const patches = event.detail.data as Record<string, GridRow>;
        const models = event.detail.models as Record<string, GridRow | undefined>;
        const skippedColumns = new Map<string, string>();
        for (const [rowIndex, patch] of Object.entries(patches)) {
            const currentRow = models[Number(rowIndex)] ?? rows[Number(rowIndex)];
            for (const prop of Object.keys(patch)) {
                const descriptor = descriptorByKey.get(String(prop));
                if (!descriptor) {
                    delete patch[prop];
                    continue;
                }
                if (!editableKeys.has(descriptor.key)) {
                    delete patch[prop];
                    const reason =
                        descriptor.kind === "calculated"
                            ? "расчётное поле"
                            : descriptor.fixed
                              ? "фиксированное поле"
                              : "поле недоступно для редактирования";
                    skippedColumns.set(descriptor.label, reason);
                    continue;
                }
                if (!currentRow || !canEditDescriptor(descriptor, currentRow)) {
                    delete patch[prop];
                    const reason =
                        descriptor.kind === "calculated"
                            ? "расчётное поле"
                            : descriptor.fixed
                              ? "фиксированное поле"
                              : "поле недоступно для редактирования";
                    skippedColumns.set(descriptor.label, reason);
                    continue;
                }
                const canonicalKey = currentRow.canonicalCells?.[descriptor.key];
                if (
                    canonicalKey &&
                    (remoteLocks.has(canonicalKey) || remotePresence.has(canonicalKey))
                ) {
                    delete patch[prop];
                    skippedColumns.set(descriptor.label, "ячейка занята другим пользователем");
                    continue;
                }
                const normalized = normalizeValue(patch[prop], descriptor.kind);
                if (!normalized.ok) {
                    event.preventDefault();
                    onValidationError(normalized.message);
                    return;
                }
                patch[prop] = normalized.value;
            }
        }
        if (skippedColumns.size > 0) {
            const reasons = new Map<string, string[]>();
            for (const [title, reason] of skippedColumns) {
                const titles = reasons.get(reason) ?? [];
                titles.push(title);
                reasons.set(reason, titles);
            }
            const description = [...reasons.entries()]
                .map(
                    ([reason, titles]) =>
                        `${titles.map((title) => `В«${title}В»`).join(", ")}: ${reason}`,
                )
                .join("; ");
            onPasteSkipped?.(description);
        }
    }

    function afterEdit(event: CustomEvent<BeforeSaveDataDetails | Record<string, unknown>>) {
        const edits = extractEdits(
            event.detail,
            editableKeys,
            descriptorByKey,
            rowValuesRef.current,
        );
        const releaseKeys: string[] = [];
        if ("data" in event.detail) {
            const models = (event.detail as { models?: GridRow[] }).models ?? [];
            for (const [rowIndex, patch] of Object.entries(
                event.detail.data as Record<string, Record<string, unknown>>,
            )) {
                const previous = models[Number(rowIndex)] as GridRow | undefined;
                const row = (previous ?? patch) as GridRow;
                if (!row?.id || row.isDeleted) continue;
                for (const field of Object.keys(patch)) {
                    if (!editableKeys.has(field)) continue;
                    const key = row.canonicalCells?.[field];
                    if (key) releaseKeys.push(key);
                }
            }
        } else {
            const row = event.detail.model as GridRow;
            const key = row.canonicalCells?.[String(event.detail.prop)];
            if (key) releaseKeys.push(key);
        }
        void (async () => {
            try {
                let ok = true;
                if (edits.updates.length) {
                    ok = await onCommit(translateUpdates(section, edits.updates, assets));
                }
                if (ok && Object.keys(edits.placeholderPatch).length) {
                    ok = await onPlaceholderCommit(
                        translatePlaceholderPatch(section, edits.placeholderPatch, assets),
                    );
                }
                if (!ok) onCommitFailed?.();
            } finally {
                activeLeaseRef.current = null;
                onReleaseLease?.(releaseKeys);
            }
        })();
    }

    const refreshHorizontalScrollbar = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const size = await grid.getContentSize();
        const verticalScroller = verticalScrollRef.current;
        const scrollbarGutter = verticalScroller
            ? verticalScroller.offsetWidth - verticalScroller.clientWidth
            : 0;
        setHorizontalContentWidth(size.x + scrollbarGutter + 52);
    }, []);

    const getExportState = useCallback(async (): Promise<RawMaterialsGridState> => {
        const grid = gridRef.current;
        if (!grid) {
            return {
                columns: exportColumnsFromGrid(section, showAssetColumn, showServiceColumns, []),
                rowIds: rows
                    .filter((row) => !row.isPlaceholder && !row.isDeleted)
                    .map((row) => row.id),
            };
        }
        const [currentColumns, currentRows] = await Promise.all([
            grid.getColumns(),
            grid.getVisibleSource("rgRow"),
        ]);
        return {
            columns: exportColumnsFromGrid(
                section,
                showAssetColumn,
                showServiceColumns,
                currentColumns as (ColumnRegular | ColumnGrouping)[],
            ),
            rowIds: (currentRows as GridRow[])
                .filter((row) => !row.isPlaceholder && !row.isDeleted)
                .map((row) => row.id),
        };
    }, [rows, section, showAssetColumn, showServiceColumns]);

    const lastStatsRef = useRef<GridViewStats | null>(null);

    const measureViewport = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid || !onViewStatsChange) return;
        const [columns, visibleSource] = await Promise.all([
            grid.getColumns(),
            grid.getVisibleSource("rgRow"),
        ]);
        const rowHeight = 36;
        const bodyHeight = Math.max(0, grid.clientHeight - headerHeight);
        const visibleRowCount = (visibleSource as GridRow[]).filter(
            (row) => !row.isPlaceholder && !row.isDeleted,
        ).length;
        const visibleRows = Math.min(
            visibleRowCount,
            Math.max(0, Math.round(bodyHeight / rowHeight)),
        );
        const next: GridViewStats = {
            totalRows: rows.filter((row) => !row.isPlaceholder && !row.isDeleted).length,
            totalCols: columns.length,
            visibleRows,
            visibleCols: columns.length,
        };
        const previous = lastStatsRef.current;
        if (
            previous !== null &&
            previous.totalRows === next.totalRows &&
            previous.totalCols === next.totalCols &&
            previous.visibleRows === next.visibleRows &&
            previous.visibleCols === next.visibleCols
        ) {
            return;
        }
        lastStatsRef.current = next;
        onViewStatsChange(next);
    }, [headerHeight, onViewStatsChange, rows]);

    const resetGridView = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const plugins = await grid.getPlugins();
        const filterPlugin = plugins.find((plugin) => "clearFiltering" in plugin) as
            | { clearFiltering: () => Promise<void> }
            | undefined;
        await filterPlugin?.clearFiltering();
        await grid.clearSorting();
        userSortedSectionsRef.current.delete(section);
        gridRef.current?.classList.remove("raw-materials-grid--user-sorted");
    }, [section]);

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || rows.length === 0) return;
        const root = grid.shadowRoot ?? grid;
        const host = root.querySelector<HTMLElement>(
            ".rgHeaderCell.report-row-header--heading .grid-options-menu-host",
        );
        if (!host) return;
        if (host !== menuHostRef.current) {
            menuRootRef.current?.unmount();
            menuRootRef.current = null;
            menuHostRef.current = host;
        }
        menuRootRef.current ??= createRoot(host);
        menuRootRef.current.render(
            <GridOptionsMenu
                headerPinned={headerPinned}
                showServiceColumns={showServiceColumns}
                onHeaderPinnedChange={onHeaderPinnedChange}
                onShowServiceColumnsChange={setShowServiceColumns}
                onResetView={() => void resetGridView()}
            />,
        );
    }, [
        gridRevision,
        headerPinned,
        onHeaderPinnedChange,
        resetGridView,
        rows.length,
        section,
        showServiceColumns,
    ]);

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const root = grid.shadowRoot ?? grid;
        let last = !!root.querySelector(
            ".rgHeaderCell.report-row-header--heading .grid-options-menu-host",
        );
        const observer = new MutationObserver(() => {
            const now = !!root.querySelector(
                ".rgHeaderCell.report-row-header--heading .grid-options-menu-host",
            );
            if (now !== last) {
                last = now;
                setGridRevision((revision) => revision + 1);
            }
        });
        observer.observe(root, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, []);

    useEffect(
        () => () => {
            menuRootRef.current?.unmount();
            menuRootRef.current = null;
            menuHostRef.current = null;
        },
        [],
    );

    const buildCopyText = useCallback(
        (range: RangeArea | null): string | null => {
            if (columns.length === 0) return null;
            const allProps = flatColumns.map((column) => column.prop);
            const lines: string[] = [];
            if (selectedRowIds.length > 0) {
                for (const id of selectedRowIds) {
                    const row = rows.find((candidate) => candidate.id === id);
                    if (!row) continue;
                    lines.push(allProps.map((prop) => serializeCellValue(row[prop])).join("\t"));
                }
            } else {
                const target = range ?? {
                    x: 0,
                    y: 0,
                    x1: flatColumns.length - 1,
                    y1: rows.length - 1,
                };
                if (target.x1 < target.x || target.y1 < target.y) return null;
                const props = flatColumns
                    .slice(target.x, target.x1 + 1)
                    .map((column) => column.prop);
                for (let y = target.y; y <= target.y1; y++) {
                    const row = rows[y];
                    if (!row) continue;
                    lines.push(props.map((prop) => serializeCellValue(row[prop])).join("\t"));
                }
            }
            const text = lines.join("\n");
            return text || null;
        },
        [columns, flatColumns, rows, selectedRowIds],
    );

    const copySelection = useCallback(async () => {
        const text = buildCopyText(selectionRangeRef.current);
        if (text === null) return;
        try {
            await navigator.clipboard.writeText(text);
        } catch {
            try {
                const textarea = document.createElement("textarea");
                textarea.value = text;
                textarea.style.position = "fixed";
                textarea.style.opacity = "0";
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                textarea.remove();
            } catch {
                // clipboard may be unavailable, ignore
            }
        }
    }, [buildCopyText]);

    const cutSelection = useCallback(async () => {
        const grid = gridRef.current;
        if (!grid) return;
        const range = selectionRangeRef.current;
        if (!range) return;
        const text = buildCopyText(range);
        if (text === null) return;
        try {
            await navigator.clipboard.writeText(text);
        } catch {
            try {
                const textarea = document.createElement("textarea");
                textarea.value = text;
                textarea.style.position = "fixed";
                textarea.style.opacity = "0";
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                textarea.remove();
            } catch {
                // буфер недоступен — источник не очищаем
                return;
            }
        }
        const data: Record<string, Record<string, unknown>> = {};
        const models: GridRow[] = [];
        for (let y = range.y; y <= range.y1; y++) {
            models[y] = rows[y];
            const patch: Record<string, unknown> = {};
            for (let x = range.x; x <= range.x1; x++) {
                const column = flatColumns[x] as ColumnRegular | undefined;
                if (!column) continue;
                const descriptor = descriptorByKey.get(String(column.prop));
                if (!descriptor || !editableKeys.has(descriptor.key)) continue;
                // Булевым полям «пусто» недоступно — вырезание их пропускаем.
                if (descriptor.kind === "boolean") continue;
                patch[String(column.prop)] = descriptor.kind === "assetsMulti" ? [] : "";
            }
            if (Object.keys(patch).length > 0) data[String(y)] = patch;
        }
        if (Object.keys(data).length === 0) return;
        // Идём через rangeeditapply: beforeRangeEdit сам пропустит занятые
        // и заблокированные ячейки (значения при этом уже в буфере обмена).
        grid.dispatchEvent(
            new CustomEvent("rangeeditapply", {
                detail: {
                    data,
                    models,
                    type: "rgRow",
                    oldRange: range,
                    newRange: range,
                },
                bubbles: true,
                composed: true,
            }),
        );
        getSelectionStore()?.setRange({ x: range.x, y: range.y }, { x: range.x1, y: range.y1 });
    }, [buildCopyText, descriptorByKey, editableKeys, flatColumns, getSelectionStore, rows]);

    const pasteIntoSelection = useCallback(
        async (text?: string) => {
            const grid = gridRef.current;
            if (!grid) return;
            if (text == null) {
                try {
                    text = await navigator.clipboard.readText();
                } catch {
                    return;
                }
            }
            if (!text) return;
            // Excel и другие приложения кладут в буфер завершающий перенос строки
            // (и хвостовые табы) — без обрезки при вставке захватывается лишняя
            // пустая ячейка снизу и затирает соседнюю, которую не просили.
            const split = text.split(/\r\n|\n|\r/).map((row) => row.split("\t"));
            while (
                split.length > 0 &&
                split[split.length - 1].every((cell) => cell.trim() === "")
            ) {
                split.pop();
            }
            if (split.length === 0) return;
            let width = Math.max(...split.map((cells) => cells.length));
            while (
                width > 0 &&
                split.every((cells) => width - 1 >= cells.length || cells[width - 1].trim() === "")
            ) {
                width -= 1;
            }
            const parsed = split.map((cells) => cells.slice(0, width));
            const range = selectionRangeRef.current;
            if (!range) return;

            const pasteHeight = parsed.length;
            const pasteWidth = Math.max(0, ...parsed.map((row) => row.length));
            const selectedHeight = range.y1 - range.y + 1;
            const selectedWidth = range.x1 - range.x + 1;
            const singleCell = selectedHeight === 1 && selectedWidth === 1;

            // Одна ячейка — вставляем весь буфер, начиная с неё. Две и более —
            // вставляем только в выделение: больший буфер обрезается им (Toast),
            // меньший вставляется как есть, остальные ячейки не трогаем.
            let toY = range.y;
            let toY1 = range.y1;
            let toX = range.x;
            let toX1 = range.x1;
            if (singleCell) {
                toY1 = Math.min(rows.length - 1, range.y + pasteHeight - 1);
                toX1 = Math.min(flatColumns.length - 1, range.x + pasteWidth - 1);
            } else if (pasteHeight > selectedHeight || pasteWidth > selectedWidth) {
                onPasteTruncated?.();
            }

            const data: Record<string, Record<string, unknown>> = {};
            const models: GridRow[] = [];
            for (let y = toY; y <= toY1; y++) {
                models[y] = rows[y];
                const patch: Record<string, unknown> = {};
                for (let x = toX; x <= toX1; x++) {
                    const column = flatColumns[x] as ColumnRegular | undefined;
                    if (!column) continue;
                    const sourceCol = parsed[y - range.y]?.[x - range.x];
                    if (sourceCol === undefined) continue;
                    patch[String(column.prop)] = sourceCol;
                }
                if (Object.keys(patch).length > 0) data[String(y)] = patch;
            }
            // newRange задаёт и область применения, и итоговое выделение —
            // после вставки выделяются только что вставленные ячейки.
            const newRange: RangeArea = { y: toY, y1: toY1, x: toX, x1: toX1 };
            grid.dispatchEvent(
                new CustomEvent("rangeeditapply", {
                    detail: {
                        data,
                        models,
                        type: "rgRow",
                        oldRange: range,
                        newRange,
                    },
                    bubbles: true,
                    composed: true,
                }),
            );
            // После вставки выделяем только что вставленные ячейки (setRange
            // обновляет оверлей и эмитит setrange — присутствие подхватится само).
            getSelectionStore()?.setRange({ x: toX, y: toY }, { x: toX1, y: toY1 });
        },
        [flatColumns, getSelectionStore, onPasteTruncated, rows],
    );

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid) return;
        const handleCopy = (event: ClipboardEvent) => {
            if (!isGridTarget(event, grid)) return;
            const active = document.activeElement as HTMLElement | null;
            if (
                active &&
                (active.tagName === "INPUT" ||
                    active.tagName === "TEXTAREA" ||
                    active.isContentEditable)
            ) {
                const selection = window.getSelection()?.toString() ?? "";
                event.clipboardData?.setData("text/plain", selection);
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            const text = buildCopyText(selectionRangeRef.current);
            event.clipboardData?.setData("text/plain", text ?? "");
            event.preventDefault();
            event.stopPropagation();
        };
        const handleSetRange = (event: Event) => {
            updateSelectionRange((event as CustomEvent<RangeArea & { type?: string }>).detail);
        };
        const handleFocusCell = (event: Event) => {
            const detail = (
                event as CustomEvent<{
                    focus: { x: number; y: number };
                    end: { x: number; y: number };
                    range?: RangeArea;
                }>
            ).detail;
            if (!detail) return;
            updateSelectionRange(
                detail.range ?? {
                    x: detail.focus.x,
                    y: detail.focus.y,
                    x1: detail.end.x,
                    y1: detail.end.y,
                },
            );
            setSelectedRowIds([]);
            rowSelectionAnchorRef.current = null;
        };
        const handlePaste = (event: ClipboardEvent) => {
            const grid = gridRef.current;
            if (!grid) return;
            if (!isGridTarget(event, grid)) return;
            const isEditable = (element: Element | null) =>
                !!element &&
                (element.tagName === "INPUT" ||
                    element.tagName === "TEXTAREA" ||
                    (element as HTMLElement).isContentEditable);
            if (
                isEditable(document.activeElement) ||
                isEditable(grid.shadowRoot?.activeElement ?? null)
            ) {
                return;
            }
            const text = event.clipboardData?.getData("text/plain");
            if (text == null || text === "") return;
            event.preventDefault();
            event.stopPropagation();
            void pasteIntoSelection(text);
        };
        const handleAfterFilterApply = () => {
            void measureViewport();
        };
        const handleCancelEdit = () => {
            if (!activeLeaseRef.current) return;
            onReleaseLease?.([activeLeaseRef.current]);
            activeLeaseRef.current = null;
        };
        window.addEventListener("copy", handleCopy, true);
        window.addEventListener("paste", handlePaste, true);
        grid.addEventListener("setrange", handleSetRange);
        grid.addEventListener("focuscell", handleFocusCell);
        grid.addEventListener("afterfilterapply", handleAfterFilterApply);
        grid.addEventListener("canceledit", handleCancelEdit);
        return () => {
            window.removeEventListener("copy", handleCopy, true);
            window.removeEventListener("paste", handlePaste, true);
            grid.removeEventListener("setrange", handleSetRange);
            grid.removeEventListener("focuscell", handleFocusCell);
            grid.removeEventListener("afterfilterapply", handleAfterFilterApply);
            grid.removeEventListener("canceledit", handleCancelEdit);
        };
    }, [buildCopyText, pasteIntoSelection, updateSelectionRange, measureViewport]);
    useImperativeHandle(
        forwardedRef,
        () => ({
            getExportState,
            copySelection,
            pasteIntoSelection,
        }),
        [copySelection, getExportState, pasteIntoSelection],
    );

    useEffect(() => {
        const isEditableTarget = (active: Element | null) =>
            active instanceof HTMLElement &&
            (active.tagName === "INPUT" ||
                active.tagName === "TEXTAREA" ||
                active.isContentEditable);

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !document.fullscreenElement) {
                if (isEditableTarget(document.activeElement)) return;
                void gridRef.current?.clearFocus();
                setSelectedRowIds([]);
                rowSelectionAnchorRef.current = null;
                updateSelectionRange(null);
                return;
            }
            if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
            // event.code — физическая клавиша: работает при любой раскладке
            // (в русской event.key даёт «с»/«м»/«ч»).
            if (event.code !== "KeyC" && event.code !== "KeyV" && event.code !== "KeyX") return;
            if (isEditableTarget(document.activeElement)) return;
            const grid = gridRef.current;
            if (!grid || !isGridTarget(event, grid)) return;
            event.preventDefault();
            if (event.code === "KeyC") void copySelection();
            else if (event.code === "KeyX") void cutSelection();
            else void pasteIntoSelection();
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, [copySelection, cutSelection, pasteIntoSelection, updateSelectionRange]);

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
        });
        const observer = new ResizeObserver(() => {
            void refreshHorizontalScrollbar();
            void measureViewport();
        });
        if (gridRef.current) observer.observe(gridRef.current);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [columns, headerPinned, measureViewport, refreshHorizontalScrollbar, rows]);

    return (
        <div
            className={headerPinned ? "revo-grid-shell revo-grid-shell--pinned" : "revo-grid-shell"}
            style={
                {
                    "--grid-content-height": `${contentHeight}px`,
                    "--grid-header-height": `${headerHeight}px`,
                } as CSSProperties
            }
            onPointerMove={(event) => {
                const target = (event.target as Element).closest?.("[data-remote-lock]") ?? null;
                if (target === remoteTooltipTargetRef.current) return;
                remoteTooltipTargetRef.current = target;
                if (!target) {
                    setRemoteTooltip(null);
                    return;
                }
                const rect = target.getBoundingClientRect();
                setRemoteTooltip({
                    text:
                        target.getAttribute("data-remote-lock") ??
                        "Ячейка занята другим пользователем",
                    left: rect.left + rect.width / 2,
                    top: rect.top,
                });
            }}
            onPointerLeave={() => {
                remoteTooltipTargetRef.current = null;
                setRemoteTooltip(null);
            }}
        >
            <Tooltip isOpen={remoteTooltip !== null}>
                <Tooltip.Trigger
                    className="remote-cell-tooltip-trigger"
                    style={{
                        left: remoteTooltip?.left ?? -1000,
                        top: remoteTooltip?.top ?? -1000,
                    }}
                >
                    <span aria-hidden="true" />
                </Tooltip.Trigger>
                <Tooltip.Content placement="top">{remoteTooltip?.text}</Tooltip.Content>
            </Tooltip>
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                <RevoGrid
                    ref={gridRef}
                    className={[
                        "raw-materials-grid",
                        headerHeight > 44 && "raw-materials-grid--merged-header",
                        userSortedSectionsRef.current.has(section) &&
                            "raw-materials-grid--user-sorted",
                    ]
                        .filter(Boolean)
                        .join(" ")}
                    columns={columns}
                    source={rows}
                    theme="compact"
                    range
                    useClipboard={false}
                    resize
                    rowHeaders={rowHeaders}
                    editors={editors}
                    filter={RUSSIAN_FILTER_CONFIG}
                    sorting={sorting}
                    hideAttribution
                    readonly={readOnly}
                    rowSize={36}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        setGridRevision((revision) => revision + 1);
                        void refreshHorizontalScrollbar();
                        void measureViewport();
                    }}
                    onBeforesorting={() => {
                        userSortedSectionsRef.current.add(section);
                        gridRef.current?.classList.add("raw-materials-grid--user-sorted");
                    }}
                    onAftersortingapply={(event) => {
                        sortingBySectionRef.current.set(
                            section,
                            sortingConfigFromEvent(event.detail),
                        );
                    }}
                    onContentsizechanged={() => void refreshHorizontalScrollbar()}
                    onViewportscroll={(event) => {
                        if (event.detail.dimension === "rgCol") {
                            if (!headerPinned) {
                                const scrollbar = horizontalScrollRef.current;
                                if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                                    scrollbar.scrollLeft = event.detail.coordinate;
                                }
                            }
                        }
                        void measureViewport();
                    }}
                    onAftercolumnresize={(event) => {
                        for (const column of Object.values(event.detail)) {
                            if (column?.prop !== undefined && typeof column.size === "number") {
                                resizedWidthsRef.current.set(
                                    columnWidthKey(section, String(column.prop)),
                                    column.size,
                                );
                            }
                        }
                        void refreshHorizontalScrollbar();
                        void measureViewport();
                    }}
                    onBeforeeditstart={beforeEditStart}
                    onBeforeedit={beforeEdit}
                    onAfteredit={afterEdit}
                    onBeforerangeedit={beforeRangeEdit}
                    onBeforecellfocus={(event) =>
                        updateSelectionRange({
                            x: event.detail.colIndex,
                            y: event.detail.rowIndex,
                            x1: event.detail.colIndex,
                            y1: event.detail.rowIndex,
                        })
                    }
                    onBeforerange={(event) => updateSelectionRange(event.detail.newRange)}
                />
            </div>
            {!headerPinned ? (
                <div
                    ref={horizontalScrollRef}
                    className="revo-grid-external-scrollbar"
                    aria-label="Горизонтальная прокрутка таблицы"
                    onScroll={(event) => {
                        void gridRef.current?.scrollToCoordinate({
                            x: event.currentTarget.scrollLeft,
                        });
                    }}
                >
                    <div style={{ width: horizontalContentWidth }} />
                </div>
            ) : null}
        </div>
    );
}

export const RawMaterialsGrid = forwardRef(RawMaterialsGridInner);

type GridOptionsMenuProps = {
    headerPinned: boolean;
    showServiceColumns: boolean;
    onHeaderPinnedChange: (value: boolean) => void;
    onShowServiceColumnsChange: (value: boolean) => void;
    onResetView: () => void;
};

function GridOptionsMenu({
    headerPinned,
    showServiceColumns,
    onHeaderPinnedChange,
    onShowServiceColumnsChange,
    onResetView,
}: GridOptionsMenuProps) {
    const selectedKeys = new Set<string>();
    if (headerPinned) selectedKeys.add("pin-header");
    if (showServiceColumns) selectedKeys.add("service-columns");
    const applySelection = (selection: Selection) => {
        const keys = selection === "all" ? new Set(["pin-header", "service-columns"]) : selection;
        if (keys.has("reset-view")) {
            onResetView();
            return;
        }
        onHeaderPinnedChange(keys.has("pin-header"));
        onShowServiceColumnsChange(keys.has("service-columns"));
    };
    return (
        <div className="grid-options-menu">
            <Dropdown>
                <Button isIconOnly aria-label="Настройки таблицы" variant="ghost">
                    <MoreVert aria-hidden="true" />
                </Button>
                <Dropdown.Popover className="min-w-[260px]">
                    <Dropdown.Menu
                        selectedKeys={selectedKeys}
                        selectionMode="multiple"
                        onSelectionChange={applySelection}
                    >
                        <Dropdown.Item id="pin-header" textValue="Закрепить заголовок">
                            <Dropdown.ItemIndicator />
                            <Label>Закрепить заголовок</Label>
                        </Dropdown.Item>
                        <Dropdown.Item id="service-columns" textValue="Показать служебные столбцы">
                            <Dropdown.ItemIndicator />
                            <Label>Показать служебные столбцы</Label>
                        </Dropdown.Item>
                        <Dropdown.Item
                            id="reset-view"
                            textValue="Сбросить все фильтры и сортировки"
                            className="grid-options-menu__reset"
                        >
                            <Label>Сбросить все фильтры и сортировки</Label>
                        </Dropdown.Item>
                    </Dropdown.Menu>
                </Dropdown.Popover>
            </Dropdown>
        </div>
    );
}

function canonicalOptions(
    values: unknown[],
    options: ReferenceOption[],
): { ok: true; value: string[] } | { ok: false; message: string } {
    const byName = new Map(
        options.map(({ name }) => [name.trim().toLocaleLowerCase("ru-RU"), name.trim()]),
    );
    const result: string[] = [];
    const seen = new Set<string>();
    for (const value of values) {
        const requested = String(value ?? "").trim();
        if (!requested) continue;
        const canonical = byName.get(requested.toLocaleLowerCase("ru-RU"));
        if (!canonical) {
            return { ok: false, message: `значение «${requested}» отсутствует в справочнике.` };
        }
        const key = canonical.toLocaleLowerCase("ru-RU");
        if (!seen.has(key)) {
            seen.add(key);
            result.push(canonical);
        }
    }
    return { ok: true, value: result };
}

function translateUpdates(
    section: SectionId,
    updates: CellUpdate[],
    assets: ReferenceOption[],
): CellUpdate[] {
    if (section === "rawMaterials") {
        return updates.map((update) =>
            update.field === "assetNames"
                ? { ...update, field: "assetIds", value: namesToIds(update.value, assets) }
                : update,
        );
    }
    return updates;
}

function translatePlaceholderPatch(
    section: SectionId,
    patch: Record<string, unknown>,
    assets: ReferenceOption[],
): Record<string, unknown> {
    if (section === "rawMaterials" && "assetNames" in patch) {
        return {
            ...patch,
            assetIds: namesToIds(patch.assetNames as CellUpdate["value"], assets),
        };
    }
    return patch;
}

function namesToIds(value: CellUpdate["value"], assets: ReferenceOption[]): CellUpdate["value"] {
    const names = Array.isArray(value) ? value : [];
    const idsByName = new Map(
        assets.map(({ id, name }) => [name.trim().toLocaleLowerCase("ru-RU"), id]),
    );
    return names.map((name) => idsByName.get(name.toLocaleLowerCase("ru-RU")) ?? name);
}

function columnWidthKey(section: SectionId, prop: string): string {
    return `${section}\u0000${prop}`;
}

export function restoreColumnWidths(
    columns: Array<{ prop?: unknown; size?: number; children?: unknown[] }>,
    widths: Map<string, number>,
    section: SectionId,
) {
    for (const column of columns) {
        if (column.prop !== undefined) {
            const width = widths.get(columnWidthKey(section, String(column.prop)));
            if (width !== undefined) column.size = width;
        }
        if (Array.isArray(column.children)) {
            restoreColumnWidths(
                column.children as Array<{ prop?: unknown; size?: number; children?: unknown[] }>,
                widths,
                section,
            );
        }
    }
}
