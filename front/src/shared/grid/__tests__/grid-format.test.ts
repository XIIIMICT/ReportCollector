import { describe, expect, test } from "bun:test";

import * as gridConfig from "../grid-config";
import type { ColumnDescriptor } from "../grid-config-types";
import * as gridFormat from "../grid-format";
import {
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
    sortableText,
} from "../grid-format";

// ICU uses a (narrow) no-break space as the ru-RU group separator; normalize
// only that, so the assertion still catches a wrong separator or decimal mark.
const spaced = (text: string): string => text.replace(/[\u00a0\u202f\u2009]/g, " ");
const facetText = (value: unknown): string => spaced(String(value));

const numberDescriptor: ColumnDescriptor = {
    key: "value",
    label: "Значение",
    width: 100,
    kind: "number",
};
const calculatedDescriptor: ColumnDescriptor = { ...numberDescriptor, kind: "calculated" };
const selectDescriptor: ColumnDescriptor = {
    key: "status",
    label: "Статус",
    width: 100,
    kind: "select",
    options: [{ value: "blocked", label: "Заблокирован" }],
};
const displayKeyDescriptor: ColumnDescriptor = {
    key: "org",
    label: "ДЗО",
    width: 100,
    kind: "text",
    displayKey: "orgShort",
};

describe("number text", () => {
    test("renderNumberText uses the RU decimal comma for numbers and numeric strings", () => {
        expect(spaced(renderNumberText(1234.5))).toBe("1 234,5");
        expect(spaced(renderNumberText("1234,5"))).toBe("1 234,5");
        expect(renderNumberText(0)).toBe("0");
    });

    test("renderNumberText keeps empty and unparsable values as-is", () => {
        expect(renderNumberText(null)).toBe("");
        expect(renderNumberText(undefined)).toBe("");
        expect(renderNumberText("")).toBe("");
        expect(renderNumberText("не число")).toBe("не число");
    });

    test("renderFixedDecimalsText fixes the digit count without changing the value", () => {
        expect(spaced(renderFixedDecimalsText(1234.5, 2))).toBe("1 234,50");
        expect(renderFixedDecimalsText("3,251", 2)).toBe("3,25");
        expect(renderFixedDecimalsText(null, 2)).toBe("");
        expect(renderFixedDecimalsText("", 2)).toBe("");
        expect(renderFixedDecimalsText("не число", 2)).toBe("не число");
    });
});

describe("date and instant text", () => {
    test("renderDateText prints a calendar day from its own parts, without a time", () => {
        expect(renderDateText("2026-10-26")).toBe("26.10.2026");
        expect(renderDateText("2026-01-05T23:59:59Z")).toBe("05.01.2026");
        expect(renderDateText(null)).toBe("");
        expect(renderDateText("не дата")).toBe("не дата");
    });

    test("renderDateTimeText prints the same RU instant as a cell", () => {
        const instant = new Date(2026, 2, 4, 9, 15, 30);
        const expected = new Intl.DateTimeFormat("ru-RU", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        }).format(instant);
        expect(renderDateTimeText(instant.toISOString())).toBe(expected);
    });

    test("renderDateTimeText drops empty and non-string values, keeps unparsable text", () => {
        expect(renderDateTimeText("")).toBe("");
        expect(renderDateTimeText(123)).toBe("");
        expect(renderDateTimeText("не дата")).toBe("не дата");
    });
});

describe("facet display text and order", () => {
    test("facetTextOf offers the rendered text, not the raw field", () => {
        expect(facetText(facetTextOf(numberDescriptor, { value: 1234.5 }))).toBe("1 234,5");
        expect(facetText(facetTextOf(calculatedDescriptor, { value: "1234,5" }))).toBe("1 234,5");
        expect(facetTextOf(numberDescriptor, { value: null })).toBeNull();
        expect(facetTextOf(selectDescriptor, { status: "blocked" })).toBe("Заблокирован");
        expect(facetTextOf(selectDescriptor, { status: "unknown" })).toBe("unknown");
        expect(facetTextOf(displayKeyDescriptor, { org: "full", orgShort: "short" })).toBe("short");
        expect(facetTextOf(displayKeyDescriptor, { org: "full" })).toBe("full");
        expect(facetTextOf(undefined, { value: 1 })).toBeUndefined();
    });

    test("facet order follows the descriptor kind or the form field type", () => {
        expect(facetOrderOf(numberDescriptor)).toBe("number");
        expect(facetOrderOf(calculatedDescriptor)).toBe("number");
        expect(facetOrderOf({ ...numberDescriptor, kind: "datetime" })).toBe("date");
        expect(facetOrderOf({ ...numberDescriptor, kind: "date" })).toBe("date");
        expect(facetOrderOf(undefined)).toBe("text");
        expect(fieldFacetOrder({ cellType: "number", isCalculated: false })).toBe("number");
        expect(fieldFacetOrder({ cellType: "text", isCalculated: true })).toBe("number");
        expect(fieldFacetOrder({ cellType: "date", isCalculated: false })).toBe("date");
        expect(fieldFacetOrder(undefined)).toBe("text");
    });
});

describe("placeholderLastCellCompare", () => {
    type CompareRow = Parameters<typeof placeholderLastCellCompare>[1];
    const row = (value: unknown, flags: Record<string, unknown> = {}): CompareRow =>
        ({ name: value, ...flags }) as CompareRow;
    const asc = (left: CompareRow, right: CompareRow): number =>
        placeholderLastCellCompare.call({ order: "asc" }, "name", left, right);
    const desc = (left: CompareRow, right: CompareRow): number =>
        placeholderLastCellCompare.call({ order: "desc" }, "name", left, right);

    test("placeholders and deleted rows rank after data rows, reversed in desc", () => {
        const normal = row("a");
        const deleted = row("a", { isDeleted: true });
        const placeholder = row("a", { isPlaceholder: true });
        expect(asc(normal, placeholder)).toBeLessThan(0);
        expect(asc(placeholder, normal)).toBeGreaterThan(0);
        expect(asc(normal, deleted)).toBeLessThan(0);
        expect(asc(deleted, placeholder)).toBeLessThan(0);
        expect(desc(normal, placeholder)).toBeGreaterThan(0);
        expect(desc(placeholder, normal)).toBeLessThan(0);
    });

    test("equal-rank cells compare numerically or via the RU collator", () => {
        expect(asc(row(2), row(10))).toBe(-8);
        expect(asc(row("10"), row("2"))).toBeGreaterThan(0);
        expect(asc(row(5), row("5"))).toBe(0);
    });

    test("sortableText normalizes arrays, null and undefined", () => {
        expect(sortableText(["a", "b"])).toBe("a, b");
        expect(sortableText(null)).toBe("");
        expect(sortableText(undefined)).toBe("");
        expect(asc(row(["a", "b"]), row("a, b"))).toBe(0);
        expect(asc(row(null), row(""))).toBe(0);
        expect(asc(row(undefined), row(""))).toBe(0);
    });
});

describe("export file name", () => {
    test("the movement form spells out the month, every other section uses its label", () => {
        const now = new Date(2026, 7, 13, 1, 12, 5);
        expect(exportFileName("materialMovement", "2026-08-01", now)).toBe(
            "Движение сырья — август (13.08.2026, 01.12.05).xlsx",
        );
        expect(exportFileName("materialMovement", null, now)).toBe(
            "Движение сырья — отчёт (13.08.2026, 01.12.05).xlsx",
        );
        expect(exportFileName("rawMaterialPrices", null, now)).toBe(
            "Цены (13.08.2026, 01.12.05).xlsx",
        );
        expect(exportFileName("renovationCatalog:work_category", null, now)).toBe(
            "renovationCatalog:work_category (13.08.2026, 01.12.05).xlsx",
        );
    });

    test("formatMonthName is lowercase Russian and falls back on bad input", () => {
        expect(formatMonthName("2026-08-01")).toBe("август");
        expect(formatMonthName("не месяц")).toBe("не месяц");
    });

    test("pixelsToExcelWidth clamps to the Excel width window", () => {
        expect(pixelsToExcelWidth(12)).toBe(8);
        expect(pixelsToExcelWidth(75)).toBe(10);
        expect(pixelsToExcelWidth(500)).toBe(60);
    });
});

test("grid-config still re-exports the whole moved boundary", () => {
    const config = gridConfig as unknown as Record<string, unknown>;
    const format = gridFormat as unknown as Record<string, unknown>;
    const names = [
        "exportFileName",
        "facetOrderOf",
        "facetTextOf",
        "fieldFacetOrder",
        "formatMonthName",
        "pixelsToExcelWidth",
        "placeholderLastCellCompare",
        "renderDateText",
        "renderDateTimeText",
        "renderFixedDecimalsText",
        "renderNumberText",
        "SECTION_LABELS",
        "sortableText",
    ] as const;
    for (const name of names) {
        expect(config[name]).toBe(format[name]);
    }
});
