import type { ColumnProp, DataType } from "@revolist/react-datagrid";

import type { FacetOrder } from "@/shared/ui/column-filter-facets";

import type { ColumnDescriptor, SectionId } from "./grid-config-types";

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
        logisticsCountries: "Страны",
        logisticsTransportTypes: "Типы ТС и перевозки",
        logisticsOrgs: "Активы",
        logisticsUsers: "Пользователи",
        logisticsRates: "Ставки",
    };

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

export function sortableText(value: unknown): string {
    if (Array.isArray(value)) return value.join(", ");
    return value === null || value === undefined ? "" : String(value);
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
    if (descriptor.kind === "date") {
        // A calendar DAY, printed like the cell («26.10.2026») — no time part.
        const text = renderDateText(value);
        return text === "" ? null : text;
    }
    return value;
}

/**
 * The text a numeric cell shows with a FIXED decimal count — the same rule the
 * grid's `fixedDecimalsCellTemplate` uses, so a filter option reads exactly like
 * the cell it stands for.
 */
export function renderFixedDecimalsText(value: unknown, decimals: number): string {
    if (value === null || value === undefined || value === "") return "";
    const parsed = typeof value === "number" ? value : Number(String(value).replace(",", "."));
    return Number.isFinite(parsed)
        ? new Intl.NumberFormat("ru-RU", {
              minimumFractionDigits: decimals,
              maximumFractionDigits: decimals,
          }).format(parsed)
        : String(value);
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
 * How a renovation FORM FIELD is searched by its header filter.
 *
 * The field's own type decides, so the filter can never drift from the editor:
 * a numeric (or calculated) cell gets the MIN–MAX pair — «12» as a substring
 * also matches 312 and 1200 — a `date` cell a day range, everything else
 * (text, reference, boolean) a substring search over the displayed options.
 * «Дефектная ведомость/ВОР №» is text since migration 0030 and is searched as
 * text; it used to be numeric and kept offering a number range long after.
 */
export function fieldFacetOrder(
    field: { cellType: string; isCalculated: boolean } | undefined,
): FacetOrder {
    if (!field) return "text";
    if (field.cellType === "number" || field.isCalculated) return "number";
    if (field.cellType === "date") return "date";
    return "text";
}

/**
 * How a column's filter list is ordered: text alphabetically, numbers and dates
 * NUMERICALLY — a string sort would put «10» before «9» and «01.02.2024»
 * before «31.01.2024», which reads as broken next to the cells.
 *
 * Descriptor kinds describe the GRID column; for a plan column that comes from
 * the form field (see `fieldFacetOrder`), so this only covers the dictionaries
 * and the audit columns.
 */
export function facetOrderOf(descriptor: ColumnDescriptor | undefined): FacetOrder {
    if (descriptor?.kind === "number" || descriptor?.kind === "calculated") return "number";
    if (descriptor?.kind === "datetime" || descriptor?.kind === "date") return "date";
    return "text";
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

/**
 * The text of a plain DATE value — «26.10.2026», the same shape as today's date
 * and exactly what a `date` column's cell prints.
 *
 * The timestamp formatter above is the wrong tool: it would add a meaningless
 * «00:00:00» to a calendar day. A day is taken from its own parts, so an ISO
 * string never shifts by a timezone: «2026-10-26» is the 26th everywhere.
 */
export function renderDateText(value: unknown): string {
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "string") {
        const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim());
        if (iso) return `${iso[3]}.${iso[2]}.${iso[1]}`;
    }
    const date = value instanceof Date ? value : new Date(String(value));
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(date);
}
