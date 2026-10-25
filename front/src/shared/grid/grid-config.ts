import type {
    CellTemplateProp,
    ColumnGrouping,
    ColumnProp,
    ColumnRegular,
    DataType,
    HyperFunc,
    VNode,
} from "@revolist/react-datagrid";

import type { FacetOrder } from "@/shared/ui/column-filter-facets";

export type SectionId =
    | "materialMovement"
    | "rawMaterialPrices"
    | "rawMaterials"
    | "assets"
    | "deliveryBases"
    | "users"
    | "renovationAssets"
    | "renovationUsers"
    | `renovationCatalog:${string}`
    // «КППК»: the source-copy dictionaries and the «Реализация» main form
    // (the sections are per storage: the plan slices share the plan copy).
    | "kppkPositions"
    | "kppkRaws"
    | "kppkOrgs"
    | "kppkUsers"
    | "kppkRealizationPlan"
    | "kppkRealizationFact"
    | "kppkCost";

export type GridRow = Record<string, unknown> & {
    id: string;
    rowState: "unchanged" | "new" | "deleted";
    changedFields: string[];
    isDeleted: boolean;
    canonicalCells?: Record<string, string>;
    isPlaceholder?: boolean;
    /** View-only row (for example, the signed-in user's administration row). */
    readOnlyRow?: boolean;
    /**
     * Cells of THIS row that must not be edited, whatever their column says —
     * the column descriptor is shared by the whole grid, the rule can depend on
     * the row (the «Реализация» excise exists only for some products).
     */
    lockedCells?: readonly string[];
};

export type ReferenceOption = {
    id: string;
    name: string;
};

export type ColumnKind =
    | "text"
    | "number"
    | "boolean"
    | "asset"
    | "assetsMulti"
    | "basis"
    | "datetime"
    | "calculated"
    | "select";

export type SelectOption = {
    value: string;
    label: string;
};

export type ColumnDescriptor = {
    key: string;
    label: string;
    path?: string[];
    width: number;
    kind: ColumnKind;
    options?: SelectOption[];
    fixed?: boolean;
    adminOnly?: boolean;
    exportable?: boolean;
    /** Read-only in every row (e.g. an email that is only set at creation). */
    readOnly?: boolean;
    /**
     * A NULL value is a legal state of the field (the renovation plan's
     * «Наличие МТР»), so its checkbox cycles пусто → Да → Нет → пусто.
     * Without the flag the checkbox is a plain binary toggle (dictionaries).
     */
    allowsNull?: boolean;
    /**
     * A second row field holding the cell's TEXT while `key` stays the editor's
     * value (the grouped «ДЗО»: short names on screen, full names in the
     * multi-select that writes them back).
     */
    displayKey?: string;
    horizontalAlignment?: "left" | "center" | "right";
    verticalAlignment?: "top" | "center" | "bottom";
};

export type GridColumnContext = {
    isAdmin: boolean;
    showAssetColumn: boolean;
    /** With Keycloak enabled local passwords don't exist — the reset column is hidden. */
    keycloakEnabled: boolean;
    readOnly: boolean;
    hasBaseline: boolean;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    selectedRowIds: Set<string>;
    showServiceColumns: boolean;
    remoteLocks: ReadonlyMap<string, { fullname: string; email: string }>;
    /** Cell key → the member currently on it (presence/lease). */
    remoteCellInfo: ReadonlyMap<string, { name: string; label: string | null }>;
    /**
     * A boolean cell was toggled: `value` is the next state, and `null` means
     * "clear it" — only sent for a descriptor with `allowsNull`.
     */
    onBooleanChange: (row: GridRow, field: string, value: boolean | null) => void;
    /** Custom column set — overrides the built-in descriptors for the section. */
    descriptors?: ColumnDescriptor[];
};

export type ExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
    horizontalAlignment?: "left" | "center" | "right";
    verticalAlignment?: "top" | "center" | "bottom";
};

type StaticSectionId = Exclude<SectionId, `renovationCatalog:${string}`>;

export const SECTION_LABELS: Record<StaticSectionId, string> & Partial<Record<SectionId, string>> =
    {
        materialMovement: "Движение",
        rawMaterialPrices: "Цены",
        rawMaterials: "Сырьевые позиции",
        assets: "Активы",
        deliveryBases: "Базисы поставки",
        users: "Пользователи",
        renovationAssets: "Предприятия",
        renovationUsers: "Пользователи",
        kppkPositions: "Продуктовые позиции",
        kppkRaws: "Сырьевые позиции",
        kppkOrgs: "ДЗО",
        kppkUsers: "Пользователи",
        kppkRealizationPlan: "Реализация",
        kppkRealizationFact: "Реализация",
        kppkCost: "Себестоимость",
    };

export const MAIN_SECTIONS = new Set<SectionId>(["materialMovement", "rawMaterialPrices"]);
export const ADMIN_SECTIONS: SectionId[] = ["rawMaterials", "assets", "deliveryBases", "users"];

const DESCRIPTORS: Partial<Record<SectionId, ColumnDescriptor[]>> = {
    materialMovement: [
        fixed("rawMaterialName", "Сырьевая позиция", 270),
        { ...fixed("assetName", "Актив", 170), adminOnly: true },
        // The business plan is filled only in the «Цены» form (businessPlanQuantityTons)
        // and is projected here; the column is non-editable in «Движение».
        { ...fixed("businessPlanTons", "Бизнес-план, т", 142), kind: "number" },
        number("demandPlanTons", "План, т", 118, ["Потребность"]),
        number("consumptionFactTons", "Потребление, т", 142, ["Потребность"]),
        calculated("demandDeviationTons", "Отклонение, т", 138, ["Потребность"]),
        number("deliveryPlanTons", "План, т", 118, ["Поставка"]),
        number("deliveryFactTons", "Факт, т", 118, ["Поставка"]),
        calculated("deliveryDeviationTons", "Отклонение, т", 138, ["Поставка"]),
        number("balanceStartTons", "На начало периода, т", 165, ["Остатки"]),
        number("balanceOnSiteTons", "На площадке, т", 145, ["Остатки"]),
        number("balanceInTransitTons", "В пути, т", 120, ["Остатки"]),
        number("balanceEndTons", "На конец периода, т", 160, ["Остатки"]),
        // The storage standard is filled in the «Цены» form and projected here as read-only.
        // It is not an exportable «Движение» column (the server refuses a hidden
        // column); export it from «Цены», where it is authored.
        {
            ...fixed("storageStandardTons", "Норматив хранения, т", 170),
            kind: "number",
            exportable: false,
        },
        text("deviationReason", "Причина отклонения", 260),
    ],
    rawMaterialPrices: [
        fixed("rawMaterialName", "Сырьевая позиция", 270),
        { ...fixed("assetName", "Актив", 170), adminOnly: true },
        select("deliveryBasisName", "Базис поставки", 150, "basis"),
        number("fact2025", "Факт 2025", 130),
        number("businessPlanQuantityTons", "Объём, т", 135, ["Бизнес-план 2026"]),
        number("businessPlanUsdPerTon", "USD / т", 130, ["Бизнес-план 2026"]),
        number("businessPlanRubPerTon", "руб. / т", 130, ["Бизнес-план 2026"]),
        number("deliveryCostRub", "Стоимость доставки, руб.", 190, ["Бизнес-план 2026"]),
        number("storageStandardTons", "Норматив хранения, т", 170),
    ],
    rawMaterials: [
        text("name", "Сырьевая позиция", 430),
        { key: "assetNames", label: "Активы", width: 360, kind: "assetsMulti" },
    ],
    assets: [text("name", "Актив", 480)],
    deliveryBases: [text("name", "Базис поставки", 480)],
    users: [
        text("email", "Email", 220),
        text("fullname", "ФИО", 250),
        { key: "isAdmin", label: "Админ", width: 90, kind: "boolean" },
        { key: "assetNames", label: "Активы", width: 200, kind: "assetsMulti" },
        {
            key: "isActive",
            label: "Доступ к проекту",
            width: 120,
            kind: "boolean",
        },
        {
            key: "lastAuthenticatedAt",
            label: "Время последнего входа",
            width: 190,
            kind: "datetime",
            fixed: true,
            exportable: true,
        },
        {
            key: "resetPassword",
            label: "Сбросить пароль",
            width: 190,
            kind: "boolean",
            exportable: false,
        },
    ],
    // Renovation dictionaries. The five reference catalogs are dynamic
    // ("renovationCatalog:{catalog_id}") — their descriptors are supplied by
    // the renovation workspace via the grid's columnDescriptors prop.
    renovationAssets: [
        text("name", "Наименование", 380),
        text("repairCode", "Ремонтный код", 170),
        // Computed on the server: the number of repair works — the working
        // plan's rows for this asset.
        calculated("repairCount", "Число ремонтных работ", 170),
        // Computed on the server: the sum of «Стоимость итого» (col_031) over
        // the working plan's rows for this asset.
        calculated("totalCost", "Стоимость итого, руб. без НДС", 220),
    ],
    // «КППК» dictionaries of the source-copy phase: the normalized copies of
    // the source tables. Editing is admin-only (enforced server-side); the
    // tabs are visible to every project member.
    kppkPositions: [
        // ONE ROW PER PRODUCT × ДЗО POSITION — the «Настройка акциза» mode of
        // the tab. The default mode groups a product with its assets instead
        // (`KPPK_GROUPED_POSITION_DESCRIPTORS`); both read the same stored
        // positions, only the shape differs.
        number("productNum", "№", 90),
        text("name", "Продукт", 430),
        { key: "orgName", label: "ДЗО", width: 380, kind: "asset" },
        { key: "hasExcise", label: "Заполняемый акциз", width: 180, kind: "boolean" },
    ],
    kppkRaws: [number("rawNum", "№", 90), text("name", "Сырьевая позиция", 430)],
    kppkOrgs: [
        // Ordered by the full name server-side; only the code and the name.
        text("code", "Код", 140),
        text("name", "Наименование", 380),
    ],
    kppkUsers: [
        text("email", "Email", 220),
        text("fullname", "ФИО", 250),
        { key: "isAdmin", label: "Администратор", width: 150, kind: "boolean" },
        { key: "orgNames", label: "ДЗО", width: 200, kind: "assetsMulti", readOnly: true },
        { key: "isActive", label: "Доступ к проекту", width: 120, kind: "boolean" },
        {
            key: "lastAuthenticatedAt",
            label: "Время последнего входа",
            width: 190,
            kind: "datetime",
            readOnly: true,
        },
        {
            key: "resetPassword",
            label: "Сбросить пароль",
            width: 190,
            kind: "boolean",
            exportable: false,
        },
    ],
    // «Реализация» has no static descriptors: the layout follows the template    // (Продукция | Рынок сбыта | Выкуп{Объем, Цена FCA} | Реализация{Объем,
    // Цена FCA}), and the editable columns come from the active slice pasted
    // as `columnDescriptors` by the section component.
    renovationUsers: [
        // The email identifies the user and cannot be changed after creation
        // (no email in the PATCH schema) — users are created through the
        // «Добавить пользователя» dialog, not a placeholder row.
        { ...text("email", "Email", 220), readOnly: true },
        text("fullname", "ФИО", 250),
        optionSelect("role", "Роль", 200, [
            { value: "admin", label: "Администратор" },
            { value: "approver", label: "Утверждающий" },
            { value: "block_owner", label: "Ответственный за блок" },
            { value: "asset_worker", label: "Работник по активу" },
        ]),
        optionSelect("block", "Дирекция", 200, [
            { value: "tech", label: "Техническая дирекция" },
            { value: "ksir", label: "Дирекция КСиР" },
            { value: "mto", label: "Дирекция МТО" },
        ]),
        { key: "assetNames", label: "Активы", width: 200, kind: "assetsMulti" },
        { key: "isActive", label: "Доступ к проекту", width: 120, kind: "boolean" },
        {
            key: "lastAuthenticatedAt",
            label: "Время последнего входа",
            width: 190,
            kind: "datetime",
            fixed: true,
            exportable: true,
        },
    ],
};

/**
 * «Продуктовые позиции» in the DEFAULT (grouped) mode.
 *
 * One row per product: the product is typed once, then its ДЗО set is
 * multi-selected in a single cell. The same stored positions are listed one
 * per row in the «Настройка акциза» mode (`DESCRIPTORS.kppkPositions`).
 */
export const KPPK_GROUPED_POSITION_DESCRIPTORS: ColumnDescriptor[] = [
    number("productNum", "№", 90),
    text("name", "Продукт", 430),
    {
        key: "orgNames",
        label: "ДЗО",
        width: 380,
        kind: "assetsMulti",
        displayKey: "orgNamesDisplay",
    },
];

/** «Продуктовые позиции» in the «Настройка акциза» mode: one row per position. */
export const KPPK_POSITION_DESCRIPTORS: ColumnDescriptor[] = DESCRIPTORS.kppkPositions ?? [];

/** Service columns appended by «Показать служебные столбцы» (all grids). */
export const SERVICE_DESCRIPTORS: ColumnDescriptor[] = [
    { ...fixed("createdAt", "Время добавления", 190), kind: "datetime", exportable: true },
    { ...fixed("updatedAt", "Время редактирования", 190), kind: "datetime", exportable: true },
    { ...fixed("updatedBy", "Автор последней редакции", 320), exportable: true },
];

function fixed(key: string, label: string, width: number): ColumnDescriptor {
    return { key, label, width, kind: "text", fixed: true };
}

function text(key: string, label: string, width: number): ColumnDescriptor {
    return { key, label, width, kind: "text" };
}

function number(key: string, label: string, width: number, path?: string[]): ColumnDescriptor {
    return { key, label, ...(path && { path }), width, kind: "number" };
}

function calculated(key: string, label: string, width: number, path?: string[]): ColumnDescriptor {
    return { key, label, ...(path && { path }), width, kind: "calculated", fixed: true };
}

function select(
    key: string,
    label: string,
    width: number,
    kind: "asset" | "basis",
): ColumnDescriptor {
    return { key, label, width, kind };
}

function optionSelect(
    key: string,
    label: string,
    width: number,
    options: SelectOption[],
): ColumnDescriptor {
    return { key, label, width, kind: "select", options };
}

export function getVisibleDescriptors(
    section: SectionId,
    showAssetColumn: boolean,
    showServiceColumns = false,
    options: {
        keycloakEnabled?: boolean | undefined;
        descriptors?: ColumnDescriptor[] | undefined;
    } = {},
): ColumnDescriptor[] {
    const source = options.descriptors ?? DESCRIPTORS[section] ?? [];
    const visible = source.filter(
        (column) =>
            (!column.adminOnly || showAssetColumn) &&
            // With Keycloak enabled local passwords don't exist — the reset column is meaningless.
            !(
                options.keycloakEnabled &&
                (section === "users" || section === "kppkUsers") &&
                column.key === "resetPassword"
            ),
    );
    return showServiceColumns ? [...visible, ...SERVICE_DESCRIPTORS] : visible;
}

export function getEditableKeys(
    section: SectionId,
    isAdmin: boolean,
    showAssetColumn: boolean,
    keycloakEnabled = false,
    descriptors?: ColumnDescriptor[],
): Set<string> {
    return new Set(
        getVisibleDescriptors(section, showAssetColumn, false, { keycloakEnabled, descriptors })
            .filter(
                (column) =>
                    !column.fixed &&
                    !column.readOnly &&
                    column.kind !== "calculated" &&
                    !(
                        !isAdmin &&
                        section === "rawMaterialPrices" &&
                        column.key === "deliveryBasisName"
                    ),
            )
            .map((column) => column.key),
    );
}

export function exportColumnsFromGrid(
    section: SectionId,
    showAssetColumn: boolean,
    showServiceColumns: boolean,
    current: (ColumnRegular | ColumnGrouping)[],
    keycloakEnabled = false,
    descriptors?: ColumnDescriptor[],
): ExportColumn[] {
    const visibleDescriptors = getVisibleDescriptors(section, showAssetColumn, showServiceColumns, {
        keycloakEnabled,
        descriptors,
    }).filter((descriptor) => descriptor.exportable !== false);
    const byKey = new Map(
        current
            .flatMap((column) =>
                "children" in column && column.children ? column.children : [column],
            )
            .map((column) => [String(column.prop), column]),
    );
    return visibleDescriptors.map((descriptor) => ({
        key: descriptor.key,
        headerPath: [...(descriptor.path ?? []), descriptor.label],
        width: pixelsToExcelWidth(byKey.get(descriptor.key)?.size ?? descriptor.width),
        numberFormat: null,
        horizontalAlignment:
            descriptor.horizontalAlignment ??
            (descriptor.kind === "number" || descriptor.kind === "calculated"
                ? "right"
                : descriptor.kind === "boolean"
                  ? "center"
                  : "left"),
        verticalAlignment: descriptor.verticalAlignment ?? "center",
    }));
}

export function pixelsToExcelWidth(pixels: number): number {
    return Math.max(8, Math.min(60, (pixels - 5) / 7));
}

export function formatMonthName(value: string): string {
    const date = new Date(`${value.slice(0, 7)}-01T00:00:00`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ru-RU", { month: "long" })
        .format(date)
        .toLocaleLowerCase("ru-RU");
}

function pad2(value: number): string {
    return String(value).padStart(2, "0");
}

export function exportFileName(
    section: SectionId,
    reportMonth: string | null,
    now = new Date(),
): string {
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
    const base =
        section === "materialMovement"
            ? `Движение сырья — ${reportMonth ? formatMonthName(reportMonth) : "отчёт"}`
            : (SECTION_LABELS[section] ?? section);
    return `${base} (${stamp}).xlsx`;
}

export function buildGridColumns(
    section: SectionId,
    context: GridColumnContext,
): (ColumnRegular | ColumnGrouping)[] {
    const descriptors = getVisibleDescriptors(
        section,
        context.showAssetColumn,
        context.showServiceColumns,
        { keycloakEnabled: context.keycloakEnabled, descriptors: context.descriptors },
    );
    const hasGroupedHeaders = descriptors.some((descriptor) => descriptor.path?.length);
    const leaves = descriptors.map((descriptor, index) =>
        buildLeaf(descriptor, context, index, descriptors.length, hasGroupedHeaders, section),
    );
    const result: (ColumnRegular | ColumnGrouping)[] = [];
    let index = 0;
    while (index < descriptors.length) {
        const descriptor = descriptors[index];
        if (!descriptor) {
            index += 1;
            continue;
        }
        const parent = descriptor.path?.[0];
        if (!parent) {
            const leaf = leaves[index];
            if (leaf) result.push(leaf);
            index += 1;
            continue;
        }
        const children: ColumnRegular[] = [];
        while (index < descriptors.length && descriptors[index]?.path?.[0] === parent) {
            const leaf = leaves[index];
            if (leaf) children.push(leaf);
            index += 1;
        }
        result.push({
            name: parent,
            children,
            columnProperties: () => ({ className: "report-header-cell--outer-top" }),
        } as ColumnGrouping);
    }
    return result;
}

function buildLeaf(
    descriptor: ColumnDescriptor,
    context: GridColumnContext,
    index: number,
    columnCount: number,
    hasGroupedHeaders: boolean,
    section: SectionId,
): ColumnRegular {
    const options =
        descriptor.kind === "asset"
            ? context.assets.map(({ name }) => name)
            : descriptor.kind === "basis"
              ? ["", ...context.deliveryBases.map(({ name }) => name)]
              : null;
    const column: ColumnRegular = {
        prop: descriptor.key,
        name: descriptor.label,
        size: descriptor.width,
        minSize: 82,
        maxSize: 620,
        sortable: true,
        cellCompare: placeholderLastCellCompare,
        readonly: ({ model }) => {
            const row = model as GridRow;
            return isCellReadOnly(descriptor, context, row, section);
        },
        cellProperties: (props) =>
            cellProperties(
                descriptor,
                context,
                props.model as GridRow,
                section,
                index,
                columnCount,
            ),
        columnProperties: () => ({
            className: {
                "report-header-cell--outer-top": !descriptor.path?.length,
                "report-header-cell--last-column": index === columnCount - 1,
                "report-header-cell--rowspan": hasGroupedHeaders && !descriptor.path?.length,
            },
        }),
    };
    if (descriptor.kind === "number" || descriptor.kind === "calculated") {
        column.cellTemplate = numberCellTemplate;
    }
    if (descriptor.kind === "datetime") {
        column.cellTemplate = dateTimeCellTemplate;
    }
    if (descriptor.kind === "assetsMulti") {
        column.editor = "multiAssets";
        column.source = context.assets.map(({ name }) => name);
        const displayKey = descriptor.displayKey;
        // A `displayKey` swaps the rendered text for that row field; the cell's
        // VALUE (what the editor and a commit read) stays `key`. A plain string
        // is rendered as-is, so the display may already be the joined text.
        column.cellTemplate = displayKey
            ? (h, props) =>
                  multiValueCellTemplate(h, {
                      value: renderedDisplayValue(props.model, displayKey),
                  })
            : multiValueCellTemplate;
    }
    if (descriptor.kind === "select") {
        column.editor = "select";
        column.source = descriptor.options ?? [];
        column.cellTemplate = (h, props) => selectCellTemplate(h, props, descriptor.options ?? []);
    }
    if (descriptor.kind === "asset" || descriptor.kind === "basis") {
        column.editor = "reference";
        // `null` means «no reference options» (a non-asset/basis column); the
        // grid treats an empty source the same way. Inferred as `string[]`
        // because the Pro dropdown editor widens `ColumnRegular.source` to
        // `DropdownColumnSourceEntry[]` (no null) via module augmentation.
        column.source = options ?? [];
    }
    if (descriptor.kind === "boolean") {
        column.cellTemplate = (h, props) =>
            booleanCellTemplate(h, props, descriptor, context, section);
    }
    return column;
}

const GRID_COLLATOR = new Intl.Collator("ru-RU", {
    numeric: true,
    sensitivity: "base",
});

export function placeholderLastCellCompare(
    this: { order?: "asc" | "desc" },
    prop: ColumnProp,
    first: DataType,
    second: DataType,
): number {
    const firstRank = first.isPlaceholder ? 2 : first.isDeleted ? 1 : 0;
    const secondRank = second.isPlaceholder ? 2 : second.isDeleted ? 1 : 0;
    if (firstRank !== secondRank) {
        const fixedOrder = this.order === "desc" ? -1 : 1;
        return (firstRank - secondRank) * fixedOrder;
    }
    const firstValue = first[String(prop)];
    const secondValue = second[String(prop)];
    if (typeof firstValue === "number" && typeof secondValue === "number") {
        return firstValue - secondValue;
    }
    return GRID_COLLATOR.compare(sortableText(firstValue), sortableText(secondValue));
}

function sortableText(value: unknown): string {
    if (Array.isArray(value)) return value.join(", ");
    return value === null || value === undefined ? "" : String(value);
}

function isCellReadOnly(
    descriptor: ColumnDescriptor,
    context: GridColumnContext,
    row: GridRow,
    section: SectionId,
): boolean {
    const canonicalKey = row.canonicalCells?.[descriptor.key];
    return (
        context.readOnly ||
        (canonicalKey !== undefined && context.remoteLocks.has(canonicalKey)) ||
        descriptor.fixed === true ||
        descriptor.kind === "calculated" ||
        (!context.isAdmin &&
            section === "rawMaterialPrices" &&
            descriptor.key === "deliveryBasisName") ||
        Boolean(row.isDeleted) ||
        row.readOnlyRow === true ||
        // A row can lock specific cells: the «Реализация» excise of a product
        // that does not carry one must not be editable at all (the server
        // rejects it anyway — but the user must not be led into the error).
        (row.lockedCells?.includes(descriptor.key) ?? false) ||
        descriptor.readOnly === true ||
        (context.keycloakEnabled &&
            (section === "users" || section === "kppkUsers") &&
            (descriptor.key === "email" || descriptor.key === "fullname")) ||
        (descriptor.key === "resetPassword" && row.resetPassword === true)
    );
}

function cellProperties(
    descriptor: ColumnDescriptor,
    context: GridColumnContext,
    row: GridRow,
    section: SectionId,
    index: number,
    columnCount: number,
) {
    const value = row[descriptor.key];
    const isReadOnly = isCellReadOnly(descriptor, context, row, section);
    const canonicalKey = row.canonicalCells?.[descriptor.key];
    const remoteUser = canonicalKey ? context.remoteCellInfo.get(canonicalKey) : undefined;
    const empty =
        value === null ||
        value === undefined ||
        value === "" ||
        (Array.isArray(value) && value.length === 0);
    const showDiff = context.hasBaseline && !row.isPlaceholder;
    const calculated = descriptor.kind === "calculated" && !row.isDeleted && !row.isPlaceholder;
    return {
        className: {
            "report-cell": true,
            "report-cell--number": descriptor.kind === "number" || descriptor.kind === "calculated",
            "report-cell--readonly": isReadOnly,
            "report-cell--remote-locked": Boolean(remoteUser),
            "report-cell--selected": !row.isPlaceholder && context.selectedRowIds.has(row.id),
            "report-cell--last-column": index === columnCount - 1,
            "report-cell--placeholder": Boolean(row.isPlaceholder),
            "report-cell--deleted": showDiff && row.isDeleted,
            "report-cell--new": showDiff && !row.isDeleted && row.rowState === "new" && !calculated,
            "report-cell--changed":
                showDiff &&
                !row.isDeleted &&
                row.rowState !== "new" &&
                !calculated &&
                row.changedFields.includes(descriptor.key),
            "report-cell--calculated": calculated,
            "report-cell--empty":
                !row.isDeleted &&
                !row.isPlaceholder &&
                (!showDiff ||
                    (row.rowState !== "new" && !row.changedFields.includes(descriptor.key))) &&
                !calculated &&
                empty,
        },
        "data-remote-lock": remoteUser
            ? remoteUser.label
                ? `${remoteUser.name} — ${remoteUser.label}`
                : remoteUser.name
            : undefined,
    };
}

/**
 * The ONE boolean cell for every grid (dictionaries and the renovation plan).
 *
 * A native checkbox, so the whole app reads the same:
 * - `true` / `false` — a normal checkbox, checked or unchecked;
 * - `null` in an `allowsNull` column (the plan's «Наличие МТР») — NOTHING at
 *   all: an empty cell, no box, no frame. The click cycle is
 *   пусто → Да → Нет → пусто, and «Нет» behaves like Delete: clicking the
 *   unchecked box clears the value instead of toggling it to `true`;
 * - a plain column (`allowsNull` off — «Доступ к проекту», «Админ») has no
 *   empty state: an empty cell still renders an unchecked box and the click
 *   cycle is `false ⇄ true`.
 */
export function nextBooleanValue(
    current: boolean | null | undefined,
    allowsNull: boolean,
): boolean | null {
    if (!allowsNull) return current !== true;
    if (current === true) return false;
    if (current === false) return null;
    return true;
}

function booleanCellTemplate(
    h: HyperFunc<VNode>,
    props: CellTemplateProp,
    descriptor: ColumnDescriptor,
    context: GridColumnContext,
    section: SectionId,
): VNode {
    const row = props.model as GridRow;
    const disabled = isCellReadOnly(descriptor, context, row, section);
    const allowsNull = descriptor.allowsNull === true;
    const value = props.value === true ? true : props.value === false ? false : null;
    // A nullable field with no value is a genuinely EMPTY cell: no checkbox.
    // The label still fills the whole cell (100% width/height), so the first
    // click sets «Да».
    if (allowsNull && value === null) {
        return h("label", {
            class: "report-cell__checkbox report-cell__checkbox--empty",
            "aria-label": descriptor.label,
            onClick: (event: MouseEvent) => {
                event.stopPropagation();
                if (disabled) return;
                context.onBooleanChange(row, descriptor.key, true);
            },
        });
    }
    return h(
        "label",
        { class: "report-cell__checkbox" },
        h("input", {
            type: "checkbox",
            checked: value === true,
            disabled,
            ariaLabel: descriptor.label,
            onClick: (event: MouseEvent) => {
                event.stopPropagation();
                if (disabled) return;
                // Commit on CLICK with the toggle cancelled: the browser would
                // first flip the box to the next native state and only then
                // `onChange` would reach us — a cleared «Нет» flashed a checked
                // box for one frame. `preventDefault()` suppresses the native
                // flip (and therefore `onChange`); the value arrives from the
                // server round-trip like any other edit.
                event.preventDefault();
                context.onBooleanChange(row, descriptor.key, nextBooleanValue(value, allowsNull));
            },
        }),
    );
}

function multiValueCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    // A `displayKey` column hands over the ready text; the multi-select fields
    // hand over an array of names.
    const labels =
        typeof value === "string"
            ? value
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean)
            : Array.isArray(value)
              ? value.filter(
                    (item): item is string => typeof item === "string" && Boolean(item.trim()),
                )
              : [];
    return h(
        "span",
        { class: "report-cell__multi-value", title: labels.join(", ") },
        labels.join(", "),
    );
}

/** The row field a `displayKey` column renders (the cell's own value otherwise). */
function renderedDisplayValue(
    model: Record<string, unknown> | undefined,
    displayKey: string,
): unknown {
    return model?.[displayKey];
}

/**
 * What a filterable column must treat as one distinct VALUE — the string the
 * cell renders, not the raw field.
 *
 * The header filter's facets are computed from the same text the user reads:
 * a `displayKey` column (the grouped «ДЗО») offers its short names, a select
 * column its labels, a checkbox «Да»/«Нет». Without this the filter would offer
 * raw UUIDs and storage values that appear nowhere on screen.
 */
export function facetTextOf(
    descriptor: ColumnDescriptor | undefined,
    row: Record<string, unknown>,
): unknown {
    if (!descriptor) return undefined;
    if (descriptor.displayKey) {
        const display = row[descriptor.displayKey];
        return display === undefined ? row[descriptor.key] : display;
    }
    const value = row[descriptor.key];
    if (descriptor.kind === "select" && descriptor.options) {
        const option = descriptor.options.find((candidate) => candidate.value === String(value));
        // An unknown code stays visible as itself in the cell, so it stays
        // filterable as itself too.
        return option ? option.label : value;
    }
    if (descriptor.kind === "number" || descriptor.kind === "calculated") {
        // The cell renders a RU-formatted number (`numberCellTemplate`), so the
        // filter has to offer - and match by - the same text, not the raw
        // value: "1 234,5" is what the user reads and therefore clicks.
        const text = renderNumberText(value);
        // An empty aggregate is the «Пустые» bucket, not a value named "".
        return text === "" ? null : text;
    }
    if (descriptor.kind === "datetime") {
        // The cell renders the timestamp through `renderDateTimeText`, so the
        // filter must offer the same text — the raw ISO string reads poorly and
        // would not match what the user sees in the column.
        const text = renderDateTimeText(value);
        return text === "" ? null : text;
    }
    return value;
}

/**
 * The text a numeric cell shows — the single formatting rule for numbers in
 * the grid: its `numberCellTemplate` renders through it, and a filter matches
 * through it, so the facet list and the cells can never disagree.
 */
export function renderNumberText(value: unknown): string {
    if (value === null || value === undefined || value === "") return "";
    const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
    return Number.isFinite(parsed)
        ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 6 }).format(parsed)
        : String(value);
}

/**
 * How a column's filter list is ordered: text alphabetically, numbers and dates
 * NUMERICALLY — a string sort would put «10» before «9» and «01.02.2024»
 * before «31.01.2024», which reads as broken next to the cells.
 */
export function facetOrderOf(descriptor: ColumnDescriptor | undefined): FacetOrder {
    if (descriptor?.kind === "number" || descriptor?.kind === "calculated") return "number";
    if (descriptor?.kind === "datetime") return "date";
    return "text";
}

/**
 * Mark the leaf columns that carry the header filter funnel: every leaf whose
 * prop has a descriptor. Computed columns are included — they carry a real
 * per-row number, just not an editable one.
 *
 * RevoGrid draws `.rv-filter` only when the column has a `columnTemplate` (or
 * when the grid gets a `filter` prop, which also brings its own condition panel
 * — we do not want that). The template REPLACES the header text, so it must
 * render `name` itself: a template that returns an empty span leaves the header
 * blank. The only thing it adds is the funnel host.
 *
 * It must be an arrow function: RevoGrid calls it as a method of the header
 * props.
 */
export function markFilterableColumns(
    columns: (ColumnRegular | ColumnGrouping)[],
    descriptors: readonly ColumnDescriptor[],
): void {
    const byKey = new Map(descriptors.map((descriptor) => [descriptor.key, descriptor]));
    for (const column of columns) {
        const leaves = (column as ColumnGrouping).children?.length
            ? ((column as ColumnGrouping).children as ColumnRegular[])
            : [column as ColumnRegular];
        for (const leaf of leaves) {
            // Turn RevoGrid's filter UI OFF for good. Its funnel lives in a
            // shadow root, so it cannot be restyled and it opens ITS condition
            // panel — neither is wanted: every table uses the app's own popover.
            // `filter: false` is the documented per-column opt-out, which is
            // stronger than hiding the button after the fact.
            leaf.filter = false;
            const prop = String(leaf.prop);
            if (!byKey.has(prop)) continue;
            // Our own header carries the label + the app's funnel button; the
            // button's handler reads the prop from here.
            const label = leaf.name ?? byKey.get(prop)?.label ?? prop;
            leaf.columnTemplate = (h: HyperFunc<VNode>, cell: { name?: string }) => {
                const hyper = h as unknown as (
                    tag: string,
                    props: Record<string, unknown>,
                    children?: unknown[],
                ) => VNode;
                return hyper("span", { class: "report-header__title", "data-prop": prop }, [
                    cell?.name ?? label,
                    // The button carries the SAME icon element the plan renders
                    // (a masked SVG), so both headers show one glyph — a `::after`
                    // alone did not paint on this element.
                    hyper(
                        "button",
                        {
                            type: "button",
                            class: "header-filter-button",
                            "aria-label": `Фильтр: ${cell?.name ?? label}`,
                            "data-prop": prop,
                        },
                        [
                            hyper("svg", {
                                class: "header-filter-button__icon",
                                "aria-hidden": "true",
                            }),
                        ],
                    ),
                ]);
            };
        }
    }
}

function selectCellTemplate(
    h: HyperFunc<VNode>,
    { value }: { value?: unknown },
    options: SelectOption[],
): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, "");
    const option = options.find((candidate) => candidate.value === String(value));
    return h("span", {}, option ? option.label : String(value));
}

function numberCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, "");
    return h("span", { class: "report-cell__number" }, renderNumberText(value));
}

/**
 * The text a timestamp shows — the ONE rule for dates, shared by the cell and
 * the column filter, so a value selected in the filter is spelled exactly as the
 * cell spells it.
 *
 * The timezone is deliberately not rendered: a table of timestamps would be
 * unreadable with the offset repeated in every cell, and the app works in the
 * user's local zone (the browser's `Intl` uses it).
 */
export function renderDateTimeText(value: unknown): string {
    if (typeof value !== "string" || !value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}

function dateTimeCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const text = renderDateTimeText(value);
    return h("span", {}, text);
}
