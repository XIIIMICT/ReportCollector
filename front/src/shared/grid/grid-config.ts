import type {
    CellTemplateProp,
    ColumnGrouping,
    ColumnProp,
    ColumnRegular,
    DataType,
    HyperFunc,
    VNode,
} from "@revolist/react-datagrid";

export type SectionId =
    | "materialMovement"
    | "rawMaterialPrices"
    | "rawMaterials"
    | "assets"
    | "deliveryBases"
    | "users"
    | "renovationAssets"
    | "renovationUsers"
    | `renovationCatalog:${string}`;

export type GridRow = Record<string, unknown> & {
    id: string;
    rowState: "unchanged" | "new" | "deleted";
    changedFields: string[];
    isDeleted: boolean;
    canonicalCells?: Record<string, string>;
    isPlaceholder?: boolean;
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
    placeholderOnly?: boolean;
    placeholderReadOnly?: boolean;
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
    onBooleanChange: (row: GridRow, field: string, value: boolean) => void;
    /** Custom column set — overrides the built-in descriptors for the section. */
    descriptors?: ColumnDescriptor[];
};

export type ExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
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
        { ...fixed("storageStandardTons", "Норматив хранения, т", 170), kind: "number" },
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
            placeholderReadOnly: true,
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
            placeholderReadOnly: true,
        },
    ],
    // Renovation dictionaries. The five reference catalogs are dynamic
    // ("renovationCatalog:{catalog_id}") — their descriptors are supplied by
    // the renovation workspace via the grid's columnDescriptors prop.
    renovationAssets: [text("name", "Наименование", 380), text("repairCode", "Ремонтный код", 170)],
    renovationUsers: [
        // The email identifies the user and cannot be changed after creation
        // (no email in the PATCH schema) — editable in the placeholder row only.
        { ...text("email", "Email", 220), placeholderOnly: true },
        text("fullname", "ФИО", 250),
        optionSelect("role", "Роль", 200, [
            { value: "admin", label: "Администратор" },
            { value: "approver", label: "Согласующий" },
            { value: "block_owner", label: "Ответственный за блок" },
            { value: "asset_worker", label: "Работник по активу" },
        ]),
        optionSelect("block", "Блок", 160, [
            { value: "tashpaev", label: "Ташпаев" },
            { value: "borodiichuk", label: "Бородийчук" },
            { value: "babaeva", label: "Бабаева" },
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

const SERVICE_DESCRIPTORS: ColumnDescriptor[] = [
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
    return { key, label, path, width, kind: "number" };
}

function calculated(key: string, label: string, width: number, path?: string[]): ColumnDescriptor {
    return { key, label, path, width, kind: "calculated", fixed: true };
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
    options: { keycloakEnabled?: boolean; descriptors?: ColumnDescriptor[] } = {},
): ColumnDescriptor[] {
    const source = options.descriptors ?? DESCRIPTORS[section] ?? [];
    const visible = source.filter(
        (column) =>
            (!column.adminOnly || showAssetColumn) &&
            // With Keycloak enabled local passwords don't exist — the reset column is meaningless.
            !(options.keycloakEnabled && section === "users" && column.key === "resetPassword"),
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
        const parent = descriptor.path?.[0];
        if (!parent) {
            result.push(leaves[index]);
            index += 1;
            continue;
        }
        const children: ColumnRegular[] = [];
        while (index < descriptors.length && descriptors[index].path?.[0] === parent) {
            children.push(leaves[index]);
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
        column.cellTemplate = multiValueCellTemplate;
    }
    if (descriptor.kind === "select") {
        column.editor = "select";
        column.source = descriptor.options ?? [];
        column.cellTemplate = (h, props) => selectCellTemplate(h, props, descriptor.options ?? []);
    }
    if (descriptor.kind === "asset" || descriptor.kind === "basis") {
        column.editor = "reference";
        column.source = options;
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
        (descriptor.placeholderOnly === true && !row.isPlaceholder) ||
        (descriptor.placeholderReadOnly === true && Boolean(row.isPlaceholder)) ||
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

function booleanCellTemplate(
    h: HyperFunc<VNode>,
    props: CellTemplateProp,
    descriptor: ColumnDescriptor,
    context: GridColumnContext,
    section: SectionId,
): VNode {
    const row = props.model as GridRow;
    const disabled = isCellReadOnly(descriptor, context, row, section);
    return h(
        "label",
        { class: "report-cell__checkbox" },
        h("input", {
            type: "checkbox",
            checked: props.value === true,
            disabled,
            ariaLabel: descriptor.label,
            onClick: (event: MouseEvent) => event.stopPropagation(),
            onChange: (event: Event) => {
                event.stopPropagation();
                context.onBooleanChange(
                    row,
                    descriptor.key,
                    (event.target as HTMLInputElement).checked,
                );
            },
        }),
    );
}

function multiValueCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const labels = Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
        : [];
    return h(
        "span",
        { class: "report-cell__multi-value", title: labels.join(", ") },
        labels.join(", "),
    );
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
    const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
    const label = Number.isFinite(parsed)
        ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 6 }).format(parsed)
        : String(value);
    return h("span", { class: "report-cell__number" }, label);
}

function dateTimeCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    if (typeof value !== "string" || !value) return h("span", {}, "");
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return h("span", {}, value);
    return h(
        "span",
        {},
        new Intl.DateTimeFormat("ru-RU", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        }).format(date),
    );
}

export const RUSSIAN_FILTER_CONFIG = {
    localization: {
        filterNames: {
            none: "Без фильтра",
            empty: "Пусто",
            notEmpty: "Не пусто",
            eq: "Равно",
            notEq: "Не равно",
            begins: "Начинается с",
            contains: "Содержит",
            notContains: "Не содержит",
            eqN: "Равно",
            neqN: "Не равно",
            gt: "Больше",
            gte: "Больше или равно",
            lt: "Меньше",
            lte: "Меньше или равно",
        },
        captions: {
            title: "Фильтр",
            save: "Сохранить",
            reset: "Сбросить",
            ok: "Применить",
            cancel: "Отмена",
            add: "Добавить условие",
            placeholder: "Значение",
            and: "И",
            or: "ИЛИ",
            filterCondition: "Условие фильтра",
            removeFilter: "Удалить условие",
            reorderFilter: "Изменить порядок",
        },
    },
};
