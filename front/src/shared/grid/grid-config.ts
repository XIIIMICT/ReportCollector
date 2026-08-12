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
    | "users";

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
    | "calculated";

export type ColumnDescriptor = {
    key: string;
    label: string;
    path?: string[];
    width: number;
    kind: ColumnKind;
    fixed?: boolean;
    adminOnly?: boolean;
    exportable?: boolean;
    placeholderOnly?: boolean;
    placeholderReadOnly?: boolean;
};

export type GridColumnContext = {
    isAdmin: boolean;
    showAssetColumn: boolean;
    readOnly: boolean;
    hasBaseline: boolean;
    assets: ReferenceOption[];
    deliveryBases: ReferenceOption[];
    selectedRowIds: Set<string>;
    showServiceColumns: boolean;
    remoteLocks: Map<string, { fullname: string; email: string }>;
    onBooleanChange: (row: GridRow, field: string, value: boolean) => void;
};

export const SECTION_LABELS: Record<SectionId, string> = {
    materialMovement: "Движение",
    rawMaterialPrices: "Цены",
    rawMaterials: "Сырьевые позиции",
    assets: "Активы",
    deliveryBases: "Базисы поставки",
    users: "Пользователи",
};

export const MAIN_SECTIONS = new Set<SectionId>(["materialMovement", "rawMaterialPrices"]);
export const ADMIN_SECTIONS: SectionId[] = ["rawMaterials", "assets", "deliveryBases", "users"];

const DESCRIPTORS: Record<SectionId, ColumnDescriptor[]> = {
    materialMovement: [
        fixed("rawMaterialName", "Сырьевая позиция", 270),
        { ...fixed("assetName", "Актив", 170), adminOnly: true },
        number("businessPlanTons", "Бизнес-план, т", 142),
        number("demandPlanTons", "План, т", 118, ["Потребность"]),
        number("consumptionFactTons", "Потребление, т", 142, ["Потребность"]),
        calculated("demandDeviationTons", "Отклонение, т", 138, ["Потребность"]),
        number("deliveryPlanTons", "План, т", 118, ["Поставка"]),
        number("deliveryFactTons", "Факт, т", 118, ["Поставка"]),
        calculated("deliveryDeviationTons", "Отклонение, т", 138, ["Поставка"]),
        number("balanceStartTons", "На начало периода, т", 160, ["Остатки"]),
        number("balanceOnSiteTons", "На площадке, т", 145, ["Остатки"]),
        number("balanceInTransitTons", "В пути, т", 120, ["Остатки"]),
        number("balanceEndTons", "На конец периода, т", 160, ["Остатки"]),
        number("storageStandardTons", "Норматив хранения, т", 170),
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
    ],
    rawMaterials: [
        text("name", "Сырьевая позиция", 430),
        { key: "assetNames", label: "Активы", width: 360, kind: "assetsMulti" },
    ],
    assets: [text("name", "Актив", 480)],
    deliveryBases: [text("name", "Базис поставки", 480)],
    users: [
        text("email", "Логин (email)", 245),
        text("fullname", "ФИО", 280),
        { key: "isAdmin", label: "Администратор", width: 145, kind: "boolean" },
        { key: "assetNames", label: "Активы", width: 300, kind: "assetsMulti" },
        {
            key: "isActive",
            label: "Активен",
            width: 120,
            kind: "boolean",
            placeholderReadOnly: true,
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

export function getVisibleDescriptors(
    section: SectionId,
    showAssetColumn: boolean,
    showServiceColumns = false,
): ColumnDescriptor[] {
    const visible = DESCRIPTORS[section].filter((column) => !column.adminOnly || showAssetColumn);
    return showServiceColumns ? [...visible, ...SERVICE_DESCRIPTORS] : visible;
}

export function getEditableKeys(
    section: SectionId,
    isAdmin: boolean,
    showAssetColumn: boolean,
): Set<string> {
    return new Set(
        getVisibleDescriptors(section, showAssetColumn)
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

export function buildGridColumns(
    section: SectionId,
    context: GridColumnContext,
): (ColumnRegular | ColumnGrouping)[] {
    const descriptors = getVisibleDescriptors(
        section,
        context.showAssetColumn,
        context.showServiceColumns,
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
    const remoteLock = canonicalKey ? context.remoteLocks.get(canonicalKey) : undefined;
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
            "report-cell--remote-locked": Boolean(remoteLock),
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
        title: remoteLock ? `Редактирует ${remoteLock.fullname} (${remoteLock.email})` : undefined,
        "data-remote-lock": remoteLock
            ? `Редактирует ${remoteLock.fullname} (${remoteLock.email})`
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
