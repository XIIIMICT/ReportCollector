import type { ColumnDescriptor, SectionId, SelectOption } from "./grid-config-types";

export const MAIN_SECTIONS = new Set<SectionId>(["materialMovement", "rawMaterialPrices"]);
export const ADMIN_SECTIONS: SectionId[] = ["rawMaterials", "assets", "deliveryBases", "users"];

/**
 * The two computed renovation-dictionary columns, shared by every dictionary.
 *
 * The server aggregates the working plan per dictionary value — «Число
 * ремонтных работ» is its row count, «Стоимость итого» the sum of `total_cost_rub_ex_vat` —
 * so «Предприятия» and every reference catalog render (and export) the same
 * columns from one definition instead of per-dictionary copies.
 */
export const RENOVATION_AGGREGATE_DESCRIPTORS: ColumnDescriptor[] = [
    calculated("repairCount", "Число ремонтных работ", 170),
    calculated("totalCost", "Стоимость итого, руб. без НДС", 220),
];

/**
 * Descriptors of a reference-catalog dictionary grid.
 *
 * The catalogs are dynamic sections (`renovationCatalog:{catalog_id}`), so the
 * workspace passes these through the grid's `columnDescriptors` prop. The
 * computed columns are the same ones «Предприятия» renders — the server
 * aggregates the working plan per dictionary value.
 */
export function renovationCatalogDescriptors(): ColumnDescriptor[] {
    return [
        { key: "label", label: "Значение", width: 380, kind: "text" },
        ...RENOVATION_AGGREGATE_DESCRIPTORS,
    ];
}

export const DESCRIPTORS: Partial<Record<SectionId, ColumnDescriptor[]>> = {
    materialMovement: [
        { ...fixed("rawMaterialName", "Сырьевая позиция", 270), pinned: true },
        { ...fixed("assetName", "Актив", 170), adminOnly: true, pinned: true },
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
        { ...fixed("rawMaterialName", "Сырьевая позиция", 270), pinned: true },
        { ...fixed("assetName", "Актив", 170), adminOnly: true, pinned: true },
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
        {
            // One role instead of the «Админ» + «Доступ к проекту» pair: the
            // access bit is derived from it (blocked → no access).
            key: "role",
            label: "Роль",
            width: 190,
            kind: "select",
            options: [
                { value: "admin", label: "Администратор" },
                { value: "asset_worker", label: "Работник на активе" },
                { value: "blocked", label: "Заблокирован", danger: true },
            ],
        },
        {
            key: "assetNames",
            label: "Активы",
            width: 200,
            kind: "assetsMulti",
            // The grants belong to the asset-worker role only.
            cellTitle: (row) =>
                row.role === "asset_worker"
                    ? undefined
                    : "Сначала выберите роль работника на активе, чтобы закрепить актив",
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
        // Computed on the server per dictionary value (the working plan's rows).
        ...RENOVATION_AGGREGATE_DESCRIPTORS,
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
        {
            // One role instead of «Администратор» + «Доступ к проекту».
            key: "role",
            label: "Роль",
            width: 190,
            kind: "select",
            options: [
                { value: "admin", label: "Администратор" },
                { value: "blocked", label: "Заблокирован", danger: true },
            ],
        },
        { key: "orgNames", label: "ДЗО", width: 200, kind: "assetsMulti", readOnly: true },
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
    // «Логистика»: read-only dictionaries backed by the external `dict` schema.
    // The values belong to another system, so the grid never edits them; the
    // transport view merges the type cell over its vehicle types.
    logisticsCountries: [
        {
            key: "market",
            label: "Рынок",
            width: 140,
            kind: "text",
            fixed: true,
            calculatedText: true,
        },
        fixed("shortname", "Страна", 320),
    ],
    logisticsTransportTypes: [
        fixed("transportType", "Тип перевозки", 320),
        fixed("vehicleType", "Тип подвижного состава", 420),
    ],
    logisticsOrgs: [fixed("prefixRu", "Актив", 420)],
    logisticsUsers: [
        // The email identifies the user and cannot be changed after creation;
        // the person is added through the «Добавить пользователя» dialog.
        { ...text("email", "Email", 220), readOnly: true },
        text("fullname", "ФИО", 250),
        {
            key: "role",
            label: "Роль",
            width: 190,
            kind: "select",
            options: [
                { value: "admin", label: "Администратор" },
                { value: "reviewer", label: "Ревьюер" },
                { value: "asset_worker", label: "Работник на активе" },
                { value: "blocked", label: "Заблокирован", danger: true },
            ],
        },
        {
            // The generic multi-asset cell carries asset NAMES; the server
            // resolves them to `dict.orgs` ids. Only an asset worker keeps them.
            key: "assetNames",
            label: "Активы",
            width: 240,
            kind: "assetsMulti",
            cellTitle: (row) =>
                row.role === "asset_worker"
                    ? undefined
                    : "Сначала выберите роль работника на активе, чтобы закрепить актив",
        },
        {
            key: "lastAuthenticatedAt",
            label: "Время последнего входа",
            width: 190,
            kind: "datetime",
            readOnly: true,
        },
    ],
    // The main form's descriptors are dynamic (reference options and the
    // vehicle-type dependency) and are supplied by the rates section.
    logisticsRates: [],
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
            { value: "blocked", label: "Заблокирован", danger: true },
        ]),
        {
            ...optionSelect("block", "Дирекция", 200, [
                { value: "tech", label: "Техническая дирекция" },
                { value: "ksir", label: "Дирекция КСиР" },
                { value: "mto", label: "Дирекция МТО" },
            ]),
            // The directorate belongs to the roles that work inside a block.
            cellTitle: (row) =>
                row.role === "block_owner" || row.role === "asset_worker"
                    ? undefined
                    : "Выберите роль ответственного за блок или работника по активу, чтобы указать дирекцию",
        },
        {
            key: "assetNames",
            label: "Активы",
            width: 200,
            kind: "assetsMulti",
            cellTitle: (row) =>
                row.role === "asset_worker"
                    ? undefined
                    : "Сначала выберите роль работника по активу, чтобы закрепить актив",
        },
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

export function fixed(key: string, label: string, width: number): ColumnDescriptor {
    return { key, label, width, kind: "text", fixed: true };
}

export function text(key: string, label: string, width: number): ColumnDescriptor {
    return { key, label, width, kind: "text" };
}

/**
 * A numeric column. `decimals` fixes the DISPLAY only (the value keeps every
 * digit it was saved with); omit it to render whole numbers as they are.
 */
export function number(
    key: string,
    label: string,
    width: number,
    path?: string[],
    decimals?: number,
): ColumnDescriptor {
    return {
        key,
        label,
        ...(path && { path }),
        width,
        kind: "number",
        ...(decimals !== undefined && { decimals }),
    };
}

export function calculated(
    key: string,
    label: string,
    width: number,
    path?: string[],
): ColumnDescriptor {
    return { key, label, ...(path && { path }), width, kind: "calculated", fixed: true };
}

export function select(
    key: string,
    label: string,
    width: number,
    kind: "asset" | "basis",
): ColumnDescriptor {
    return { key, label, width, kind };
}

export function optionSelect(
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
