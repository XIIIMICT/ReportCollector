import { expect, test } from "bun:test";

import type { HyperFunc, VNode } from "@revolist/react-datagrid";

import { formatPlanDate, formatPlanDateTime, numberTemplate } from "../renovation-grid-format";

/**
 * «Время добавления»/«Время редактирования» are instants stored as UTC ISO
 * strings. The cell used to print the ISO clock verbatim, so the plan showed
 * GMT+0 while every other form showed the browser's local zone.
 */

const formatter = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
});

test("a timestamp is printed in the browser's zone, not the UTC clock", () => {
    const instant = "2026-03-04T09:15:30Z";
    expect(formatPlanDateTime(instant)).toBe(formatter.format(new Date(instant)));
    // The old implementation sliced «09:15:30» out of the ISO string. Where the
    // zone has an offset, that answer must no longer be the one displayed.
    if (new Date(instant).getTimezoneOffset() !== 0) {
        expect(formatPlanDateTime(instant)).not.toBe("04.03.2026, 09:15:30");
    }
});

test("a timestamp without an offset is read as local time", () => {
    // Postgres/Pydantic always send an offset; a legacy zone-less value is a
    // local wall clock, and `new Date` treats it exactly that way.
    expect(formatPlanDateTime("2026-03-04T09:15:30")).toBe("04.03.2026, 09:15:30");
});

test("a plain date keeps its calendar day", () => {
    expect(formatPlanDate("2026-03-04")).toBe("04.03.2026");
    expect(formatPlanDate("2026-03-04T00:00:00Z")).toBe("04.03.2026");
});

test("an empty or unparsable timestamp is not a value", () => {
    expect(formatPlanDateTime(null)).toBe("");
    expect(formatPlanDateTime("")).toBe("");
    // A value the browser cannot parse is shown as-is, like every other cell.
    expect(formatPlanDateTime("не дата")).toBe("не дата");
});

/**
 * «Пообъектный план» stores fractional values as NUMERIC(18,4), but the form
 * declares two decimals for every fractional field. The cell used to allow up
 * to six (`Math.max(digits, 6)`), so 7062.176 leaked as «7 062,176».
 */

type Rendered = { tag: string; children: unknown[] };

const h = ((tag: string, _props: Record<string, unknown>, ...children: unknown[]) => ({
    tag,
    children,
})) as unknown as HyperFunc<VNode>;

function cellText(value: unknown, decimals: number | null | undefined): string {
    const node = numberTemplate(decimals)(h, { value }) as unknown as Rendered;
    return String(node.children[0] ?? "");
}

/** Ignore the NBSP thousands separators and the decimal comma. */
const digits = (text: string): string => text.replace(/\D/g, "");

test("a fractional plan cell shows exactly the two declared decimals", () => {
    // Rounding is display-only: the stored value and the editor keep all digits.
    expect(digits(cellText(7062.176, 2))).toBe("706218");
    expect(digits(cellText(7062.1, 2))).toBe("706210");
    expect(digits(cellText(7062, 2))).toBe("706200");
});

test("an integer plan field shows no fractional digits", () => {
    expect(digits(cellText(7062.176, 0))).toBe("7062");
});

test("an empty numeric cell stays blank", () => {
    expect(cellText(null, 2)).toBe("");
    expect(cellText("", 2)).toBe("");
});
