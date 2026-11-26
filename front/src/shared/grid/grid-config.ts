import type {
    CellTemplateProp,
    ColumnGrouping,
    ColumnRegular,
    HyperFunc,
    VNode,
} from "@revolist/react-datagrid";

import { getVisibleDescriptors } from "./grid-config-descriptors";
import type {
    ColumnDescriptor,
    ExportColumn,
    GridColumnContext,
    GridRow,
    SectionId,
    SelectOption,
} from "./grid-config-types";
import {
    pixelsToExcelWidth,
    placeholderLastCellCompare,
    renderDateTimeText,
    renderNumberText,
} from "./grid-format";

export type {
    ColumnDescriptor,
    ColumnKind,
    ExportColumn,
    GridColumnContext,
    GridRow,
    ReferenceOption,
    SectionId,
    SelectOption,
} from "./grid-config-types";
export {
    exportFileName,
    facetOrderOf,
    facetTextOf,
    fieldFacetOrder,
    formatMonthName,
    pixelsToExcelWidth,
    placeholderLastCellCompare,
    renderDateText,
    renderDateTimeText,
    renderFixedDecimalsText,
    renderNumberText,
    SECTION_LABELS,
    sortableText,
} from "./grid-format";
export {
    ADMIN_SECTIONS,
    calculated,
    DESCRIPTORS,
    fixed,
    getEditableKeys,
    getVisibleDescriptors,
    KPPK_GROUPED_POSITION_DESCRIPTORS,
    KPPK_POSITION_DESCRIPTORS,
    MAIN_SECTIONS,
    number,
    optionSelect,
    RENOVATION_AGGREGATE_DESCRIPTORS,
    renovationCatalogDescriptors,
    select,
    SERVICE_DESCRIPTORS,
    text,
} from "./grid-config-descriptors";

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

export function buildGridColumns(
    section: SectionId,
    context: GridColumnContext,
): (ColumnRegular | ColumnGrouping)[] {
    const descriptors = getVisibleDescriptors(
        section,
        context.showAssetColumn,
        context.showServiceColumns,
        { keycloakEnabled: context.keycloakEnabled, descriptors: context.descriptors },
    ).filter((descriptor) => descriptor.pinned || !context.hiddenKeys?.has(descriptor.key));
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
        if (children.length > 0) {
            result.push({
                name: parent,
                children,
                // The header menu reads the grouping this cell stands for; a
                // leaf carries its prop on the title span instead.
                columnProperties: () => ({
                    className: "report-header-cell--outer-top",
                    "data-group": parent,
                }),
            } as ColumnGrouping);
        }
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
        ...(context.pinnedKeys?.has(descriptor.key) && { pin: "colPinStart" }),
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
        column.cellTemplate =
            descriptor.decimals === undefined
                ? numberCellTemplate
                : fixedDecimalsCellTemplate(descriptor.decimals);
        // Numbers read aligned to the right edge in EVERY grid, not only where a
        // template happens to wrap them (the plan's own cells, the KPPK forms):
        // the column's own cell props are the one place a revo-grid cell template
        // cannot override.
        const baseCellProperties = column.cellProperties;
        const numberCellProperties = ((props: { model: unknown }) => {
            const own =
                typeof baseCellProperties === "function"
                    ? (baseCellProperties as (input: unknown) => unknown)(props)
                    : baseCellProperties;
            const classes =
                own && typeof own === "object" && "className" in own
                    ? (own as { className?: unknown }).className
                    : undefined;
            return {
                ...(own as object),
                className: { ...(classes as object), "report-cell--number": true },
            };
        }) as NonNullable<ColumnRegular["cellProperties"]>;
        column.cellProperties = numberCellProperties;
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
        column.cellTemplate = descriptor.displayKey
            ? (h, props) =>
                  multiValueCellTemplate(h, {
                      value: renderedDisplayValue(props.model, descriptor.displayKey!),
                  })
            : (h, props) =>
                  selectCellTemplate(h, props, descriptor.options ?? [], descriptor.emptyLabel);
        if (descriptor.optionsForRow) {
            (
                column as ColumnRegular & {
                    optionsForRow?: (row: GridRow) => SelectOption[];
                }
            ).optionsForRow = descriptor.optionsForRow;
        }
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
    if (descriptor.cellTitle) {
        // A hover hint for a cell whose editability depends on another cell
        // (the vehicle type before a transport type is chosen). The text rides
        // on a data attribute: the grid shell turns it into a HeroUI Tooltip
        // (the same channel as the remote-cell lock hint).
        const baseTemplate = column.cellTemplate;
        column.cellTemplate = (h, props) => {
            const node =
                baseTemplate?.(h, props) ??
                h(
                    "span",
                    {},
                    props.value === null || props.value === undefined ? "" : String(props.value),
                );
            const hint = descriptor.cellTitle?.(props.model as GridRow);
            if (!hint) return node;
            return h("span", { class: "report-cell__hint", "data-cell-hint": hint }, [
                node as VNode,
            ]);
        };
    }
    return column;
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
        row.__pinnedCopy === true ||
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
    // A plain «＋» row has no computed values yet, so its calculated cells stay
    // neutral. The «Ставки» draft is different: it previews the direction, the
    // dollar rates and the deviations as the user fills the editable cells, and
    // those cells must read as «calculated — nothing to fill in here» there too.
    const calculated =
        (descriptor.kind === "calculated" || descriptor.calculatedText === true) &&
        !row.isDeleted &&
        (!row.isPlaceholder || row.highlightCalculatedInPlaceholder === true);
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
            "data-grid__pinned-copy": row.__pinnedCopy === true,
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
        // Only a pinned copy carries the row id on its DATA cells: right-click
        // anywhere on the copy opens the «Открепить» menu. Real rows are
        // addressed through their row header.
        "data-row-id": row.__pinnedCopy === true ? row.__pinSourceId : undefined,
        "data-pinned-copy": row.__pinnedCopy === true ? "true" : undefined,
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
                // `data-prop` is the hooks' column lookup; `data-header` is the
                // one CSS can match on (an attribute selector needs a STABLE
                // name, and `[data-prop="…"]` would need the value).
                return hyper(
                    "span",
                    { class: "report-header__title", "data-prop": prop, "data-header": "true" },
                    [cell?.name ?? label],
                );
            };
        }
    }
}

function selectCellTemplate(
    h: HyperFunc<VNode>,
    { value }: { value?: unknown },
    options: SelectOption[],
    emptyLabel = "",
): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, emptyLabel);
    const option = options.find((candidate) => candidate.value === String(value));
    return h("span", {}, option ? option.label : String(value));
}

function numberCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, "");
    return h("span", { class: "report-cell__number" }, renderNumberText(value));
}

/**
 * The numeric cell of a column that fixes its decimal count (see
 * `ColumnDescriptor.decimals`). The stored value keeps every digit it was saved
 * with — only the DISPLAY is rounded, and the filter list follows the same
 * rendering (`renderFacetText`).
 */
function fixedDecimalsCellTemplate(
    decimals: number,
): (h: HyperFunc<VNode>, props: { value?: unknown }) => VNode {
    const formatter = new Intl.NumberFormat("ru-RU", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
    return (h, { value }) => {
        if (value === null || value === undefined || value === "") return h("span", {}, "");
        const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
        const label = Number.isFinite(parsed) ? formatter.format(parsed) : String(value);
        return h("span", { class: "report-cell__number" }, label);
    };
}

function dateTimeCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const text = renderDateTimeText(value);
    return h("span", {}, text);
}
