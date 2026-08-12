import { Button, Dropdown, Label, type Selection } from "@heroui/react";
import {
    Editor,
    RevoGrid,
    type BeforeSaveDataDetails,
    type Editors,
    type HyperFunc,
    type RowHeaders,
    type VNode,
} from "@revolist/react-datagrid";
import { MoreVert } from "iconoir-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import type { CellUpdate } from "@/shared/api/raw-materials";

import {
    buildGridColumns,
    getEditableKeys,
    getVisibleDescriptors,
    placeholderLastCellCompare,
    RUSSIAN_FILTER_CONFIG,
    type ColumnDescriptor,
    type GridRow,
    type ReferenceOption,
    type SectionId,
} from "./grid-config";
import { MultiAssetEditor, ReferenceEditor } from "./grid-editors";

export type RawMaterialsGridHandle = {};

type Props = {
    section: SectionId;
    rows: GridRow[];
    isAdmin: boolean;
    showAssetColumn: boolean;
    readOnly: boolean;
    hasBaseline: boolean;
    headerPinned: boolean;
    onHeaderPinnedChange: (isPinned: boolean) => void;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    onCommit: (updates: CellUpdate[]) => Promise<void>;
    onPlaceholderCommit: (patch: Record<string, unknown>) => Promise<void>;
    onSelectedRowsChange: (selection: { ids: string[]; wholeRows: boolean }) => void;
    onValidationError: (message: string) => void;
};

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

type ExtractedEdits = {
    updates: CellUpdate[];
    placeholderPatch: Record<string, unknown>;
};

export function defaultSorting(descriptors: ColumnDescriptor[]): GridSortingConfig {
    const visible = new Set(descriptors.map(({ key }) => key));
    const preferred = ["rawMaterialName", "name", "assetName", "assetNames", "fullname", "email"];
    const columns: GridSortingConfig["columns"] = [];
    for (const prop of preferred) {
        if (!visible.has(prop)) continue;
        columns.push({ prop, order: "asc", cellCompare: placeholderLastCellCompare });
        if (columns.length === 2) break;
    }
    return { columns };
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

export function RawMaterialsGrid({
    section,
    rows,
    isAdmin,
    showAssetColumn,
    readOnly,
    hasBaseline,
    headerPinned,
    onHeaderPinnedChange,
    assets,
    deliveryBases,
    onCommit,
    onPlaceholderCommit,
    onSelectedRowsChange,
    onValidationError,
}: Props) {
    const gridRef = useRef<HTMLRevoGridElement>(null);
    const verticalScrollRef = useRef<HTMLDivElement>(null);
    const horizontalScrollRef = useRef<HTMLDivElement>(null);
    const [horizontalContentWidth, setHorizontalContentWidth] = useState(0);
    const resizedWidthsRef = useRef(new Map<string, number>());
    const [showServiceColumns, setShowServiceColumns] = useState(false);
    const sortingBySectionRef = useRef(new Map<SectionId, GridSortingConfig>());
    const userSortedSectionsRef = useRef(new Set<SectionId>());
    const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
    const selectedRowIdSet = useMemo(() => new Set(selectedRowIds), [selectedRowIds]);
    const rowSelectionAnchorRef = useRef<number | null>(null);

    let sorting = sortingBySectionRef.current.get(section);
    if (!sorting) {
        sorting = defaultSorting(getVisibleDescriptors(section, showAssetColumn));
        sortingBySectionRef.current.set(section, sorting);
    }

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
            remoteLocks: new Map(),
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
        selectedRowIdSet,
        section,
        showServiceColumns,
    ]);

    const headerHeight = descriptors.some((descriptor) => descriptor.path?.length) ? 88 : 44;
    const contentHeight = headerHeight + rows.length * 36 + 18;

    const rowHeaders = useMemo<RowHeaders>(
        () => ({
            prop: "__rowNumber",
            size: 52,
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
                        onClick: (event: MouseEvent) => {
                            event.preventDefault();
                            event.stopPropagation();
                            toggleRowSelection(rowIndex, row.id);
                        },
                    },
                    String(rowIndex + 1),
                );
            },
        }),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [selectedRowIds],
    );

    const toggleRowSelection = useCallback((rowIndex: number, id: string) => {
        setSelectedRowIds((current) => {
            if (rowSelectionAnchorRef.current === null) {
                rowSelectionAnchorRef.current = rowIndex;
            }
            const next = new Set(current);
            if (next.has(id)) {
                next.delete(id);
                rowSelectionAnchorRef.current = null;
            } else {
                next.add(id);
            }
            return [...next];
        });
    }, []);

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
            (descriptor.placeholderReadOnly && row.isPlaceholder)
        );
    }

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
            if (["да", "true", "1"].includes(normalized)) return { ok: true, value: true };
            if (["нет", "false", "0"].includes(normalized)) return { ok: true, value: false };
            return { ok: false, message: "выберите «Да» или «Нет»." };
        }
        if (kind === "assetsMulti") {
            const requested = Array.isArray(value) ? value : String(value ?? "").split(/[,;\n]+/u);
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
        const normalized = normalizeValue(event.detail.val, descriptor.kind);
        if (!normalized.ok) {
            event.preventDefault();
            onValidationError(normalized.message);
            return;
        }
        event.detail.val = normalized.value;
    }

    function afterEdit(event: CustomEvent<BeforeSaveDataDetails | Record<string, unknown>>) {
        const edits = extractEdits(event.detail, editableKeys, descriptorByKey);
        void (async () => {
            if (edits.updates.length) {
                await onCommit(translateUpdates(section, edits.updates, assets));
            }
            if (Object.keys(edits.placeholderPatch).length) {
                await onPlaceholderCommit(
                    translatePlaceholderPatch(section, edits.placeholderPatch, assets),
                );
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

    useEffect(() => {
        if (headerPinned) return;
        const frame = requestAnimationFrame(() => void refreshHorizontalScrollbar());
        const observer = new ResizeObserver(() => void refreshHorizontalScrollbar());
        if (gridRef.current) observer.observe(gridRef.current);
        return () => {
            cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [columns, headerPinned, refreshHorizontalScrollbar, rows]);

    return (
        <div
            className={headerPinned ? "revo-grid-shell revo-grid-shell--pinned" : "revo-grid-shell"}
            style={
                {
                    "--grid-content-height": `${contentHeight}px`,
                } as CSSProperties
            }
        >
            <div ref={verticalScrollRef} className="revo-grid-vertical-scroller">
                {rows.length > 0 ? (
                    <GridOptionsMenu
                        headerHeight={headerHeight}
                        headerPinned={headerPinned}
                        showServiceColumns={showServiceColumns}
                        onHeaderPinnedChange={onHeaderPinnedChange}
                        onShowServiceColumnsChange={setShowServiceColumns}
                    />
                ) : null}
                <RevoGrid
                    ref={gridRef}
                    className={
                        userSortedSectionsRef.current.has(section)
                            ? "raw-materials-grid raw-materials-grid--user-sorted"
                            : "raw-materials-grid"
                    }
                    columns={columns}
                    source={rows}
                    theme="compact"
                    range
                    resize
                    rowHeaders={rowHeaders}
                    editors={editors}
                    filter={RUSSIAN_FILTER_CONFIG}
                    sorting={sorting}
                    hideAttribution
                    useClipboard
                    readonly={readOnly}
                    rowSize={36}
                    colSize={150}
                    accessible
                    onAftersourceset={() => {
                        void refreshHorizontalScrollbar();
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
                        if (event.detail.dimension !== "rgCol" || headerPinned) return;
                        const scrollbar = horizontalScrollRef.current;
                        if (scrollbar && scrollbar.scrollLeft !== event.detail.coordinate) {
                            scrollbar.scrollLeft = event.detail.coordinate;
                        }
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
                    }}
                    onBeforeedit={beforeEdit}
                    onAfteredit={afterEdit}
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

type GridOptionsMenuProps = {
    headerHeight: number;
    headerPinned: boolean;
    showServiceColumns: boolean;
    onHeaderPinnedChange: (value: boolean) => void;
    onShowServiceColumnsChange: (value: boolean) => void;
};

function GridOptionsMenu({
    headerHeight,
    headerPinned,
    showServiceColumns,
    onHeaderPinnedChange,
    onShowServiceColumnsChange,
}: GridOptionsMenuProps) {
    const selectedKeys = new Set<string>();
    if (headerPinned) selectedKeys.add("pin-header");
    if (showServiceColumns) selectedKeys.add("service-columns");
    const applySelection = (selection: Selection) => {
        const keys = selection === "all" ? new Set(["pin-header", "service-columns"]) : selection;
        onHeaderPinnedChange(keys.has("pin-header"));
        onShowServiceColumnsChange(keys.has("service-columns"));
    };
    return (
        <div className="grid-options-menu" style={{ height: headerHeight }}>
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
                    </Dropdown.Menu>
                </Dropdown.Popover>
            </Dropdown>
        </div>
    );
}

export function extractEdits(
    detail: BeforeSaveDataDetails | Record<string, unknown>,
    editableKeys: Set<string>,
    descriptorByKey: Map<string, ColumnDescriptor>,
): ExtractedEdits {
    const placeholderPatch: Record<string, unknown> = {};
    if ("data" in detail) {
        const updates: CellUpdate[] = [];
        const models = (detail as { models?: GridRow[] }).models ?? [];
        for (const [rowIndex, patch] of Object.entries(
            detail.data as Record<string, Record<string, unknown>>,
        )) {
            const previous = models[Number(rowIndex)] as GridRow | undefined;
            const row = (previous ?? patch) as GridRow;
            if (!row?.id || row.isDeleted) continue;
            for (const [field, value] of Object.entries(patch)) {
                if (!editableKeys.has(field) || !descriptorByKey.has(field)) continue;
                if (row.isPlaceholder) {
                    placeholderPatch[field] = toCellValue(value);
                    continue;
                }
                updates.push({ rowId: row.id, field, value: toCellValue(value) });
            }
        }
        return { updates: deduplicateUpdates(updates), placeholderPatch };
    }
    const field = String(detail.prop);
    const row = detail.model as GridRow;
    if (!editableKeys.has(field) || !descriptorByKey.has(field) || !row?.id || row.isDeleted) {
        return { updates: [], placeholderPatch };
    }
    if (row.isPlaceholder) {
        placeholderPatch[field] = toCellValue(detail.val);
        return { updates: [], placeholderPatch };
    }
    return {
        updates: [{ rowId: row.id, field, value: toCellValue(detail.val) }],
        placeholderPatch,
    };
}

function toCellValue(value: unknown): CellUpdate["value"] {
    if (Array.isArray(value)) {
        return value
            .filter((item): item is string => typeof item === "string")
            .map((item) => item.trim())
            .filter(Boolean);
    }
    if (value === null || typeof value === "number" || typeof value === "boolean") {
        return value;
    }
    if (typeof value === "string") return value.trim();
    return value === undefined ? null : String(value);
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

function deduplicateUpdates(updates: CellUpdate[]): CellUpdate[] {
    const result = new Map<string, CellUpdate>();
    for (const update of updates) result.set(`${update.rowId}\u0000${update.field}`, update);
    return [...result.values()];
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
