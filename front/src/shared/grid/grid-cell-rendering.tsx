import type { CellTemplateProp, HyperFunc, VNode } from "@revolist/react-datagrid";

import type {
    ColumnDescriptor,
    GridColumnContext,
    GridRow,
    SectionId,
    SelectOption,
} from "./grid-config-types";
import { renderDateTimeText, renderNumberText } from "./grid-format";

export function isCellReadOnly(
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

export function cellProperties(
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

export function booleanCellTemplate(
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

export function multiValueCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
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
export function renderedDisplayValue(
    model: Record<string, unknown> | undefined,
    displayKey: string,
): unknown {
    return model?.[displayKey];
}

export function selectCellTemplate(
    h: HyperFunc<VNode>,
    { value }: { value?: unknown },
    options: SelectOption[],
    emptyLabel = "",
): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, emptyLabel);
    const option = options.find((candidate) => candidate.value === String(value));
    return h("span", {}, option ? option.label : String(value));
}

export function numberCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    if (value === null || value === undefined || value === "") return h("span", {}, "");
    return h("span", { class: "report-cell__number" }, renderNumberText(value));
}

/**
 * The numeric cell of a column that fixes its decimal count (see
 * `ColumnDescriptor.decimals`). The stored value keeps every digit it was saved
 * with — only the DISPLAY is rounded, and the filter list follows the same
 * rendering (`renderFacetText`).
 */
export function fixedDecimalsCellTemplate(
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

export function dateTimeCellTemplate(h: HyperFunc<VNode>, { value }: { value?: unknown }): VNode {
    const text = renderDateTimeText(value);
    return h("span", {}, text);
}
