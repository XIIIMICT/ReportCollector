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
    | "kppkCost"
    // «Логистика»: read-only dictionaries of the external `dict` schema.
    | "logisticsCountries"
    | "logisticsTransportTypes"
    | "logisticsOrgs"
    | "logisticsUsers"
    | "logisticsRates";

export type GridRow = Record<string, unknown> & {
    id: string;
    rowState: "unchanged" | "new" | "deleted";
    changedFields: string[];
    isDeleted: boolean;
    canonicalCells?: Record<string, string>;
    isPlaceholder?: boolean;
    /**
     * A placeholder row that PREVIEWS computed values (the «Ставки» draft derives
     * the direction, dollar rates and deviations while it is being filled). Its
     * calculated cells keep the green «do not fill me» highlight; a plain
     * «＋ добавить запись» row has no computed values yet and stays neutral.
     */
    highlightCalculatedInPlaceholder?: boolean;
    /** View-only copy of a pinned row, rendered above the data. */
    __pinnedCopy?: boolean;
    /** The original row id a pinned copy mirrors. */
    __pinSourceId?: string;
    /** The row's number in the main table, shown on the pinned copy. */
    __pinNumber?: number | null;
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
    | "date"
    | "calculated"
    | "select";

export type SelectOption = {
    value: string;
    label: string;
    /** Rendered in the danger color in the dropdown (the «Заблокирован» role). */
    danger?: boolean;
};

export type ColumnDescriptor = {
    key: string;
    label: string;
    path?: string[];
    width: number;
    kind: ColumnKind;
    options?: SelectOption[];
    /**
     * Digits after the decimal mark in a `number`/`calculated` cell. The VALUE is
     * stored as entered; only its display is rounded (`renovation-grid-format`
     * uses the same rule for the plan's own form fields).
     */
    decimals?: number;
    fixed?: boolean;
    adminOnly?: boolean;
    exportable?: boolean;
    /** Read-only in every row (e.g. an email that is only set at creation). */
    readOnly?: boolean;
    /**
     * Dynamic options of a `select` column, computed from the row being edited
     * (the vehicle type depends on the row's transport type).
     */
    optionsForRow?: (row: GridRow) => SelectOption[];
    /**
     * Cell text of an EMPTY `select` value (no option chosen). Without it the
     * cell renders blank; «Нет» reads better than nothing on a nullable
     * reference such as «Класс опасности».
     */
    emptyLabel?: string;
    /**
     * Hover hint rendered as the cell's `title` — used when the cell is locked
     * by another value in the row («выберите тип транспортного средства…»).
     * Returning `undefined` leaves the cell without a hint.
     */
    cellTitle?: (row: GridRow) => string | undefined;
    /**
     * Rendered with the calculated (green) highlight but as PLAIN TEXT — for a
     * computed string such as the country market. `kind: "calculated"` is for
     * numbers and would send the value through the numeric formatter.
     */
    calculatedText?: boolean;
    /**
     * The table's identifying column(s), pinned left: they cannot be hidden.
     * Distinct from `fixed`, which means "read-only" (a calculated or projected
     * column is hideable but never editable).
     */
    pinned?: boolean;
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
    /**
     * Data columns the user hid. Fixed columns are never filtered out: they are
     * not hideable, and the grid keeps at least its identifying column set.
     */
    hiddenKeys?: ReadonlySet<string>;
    /** Columns the user pinned to the left (`colPinStart`). */
    pinnedKeys?: ReadonlySet<string>;
};

export type ExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
    horizontalAlignment?: "left" | "center" | "right";
    verticalAlignment?: "top" | "center" | "bottom";
};
