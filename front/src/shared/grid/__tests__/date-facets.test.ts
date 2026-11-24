import { expect, test } from "bun:test";

import { facetTextOf, renderDateText } from "../grid-config";

/**
 * A plain DATE column (the plan's «Дата начала/окончания работ») holds a calendar
 * day, not an instant. Two things follow, and both were wrong while the column was
 * treated as a timestamp:
 *  - the cell and the filter must print «26.10.2026» — the same shape as today's
 *    date, with no «00:00:00» attached;
 *  - the day must come from the value's OWN parts, so a timezone never shifts an
 *    ISO «2026-10-26» onto the 25th.
 */

const descriptor = {
    key: "cost_allocation_type_id",
    label: "Дата начала работ",
    width: 120,
    kind: "date" as const,
};

test("a date is printed as a day, without a time part", () => {
    expect(renderDateText("2026-10-26")).toBe("26.10.2026");
    expect(renderDateText("2026-01-05")).toBe("05.01.2026");
    expect(renderDateText("2026-10-26")).not.toMatch(/,|00:00/);
});

test("a date with a time suffix still prints only its day", () => {
    // Some sources hand a midnight timestamp where the column is a day.
    expect(renderDateText("2026-10-26T00:00:00")).toBe("26.10.2026");
    expect(renderDateText("2026-10-26T23:59:59Z")).toBe("26.10.2026");
});

test("the day never shifts with the timezone", () => {
    // Built from the parts, so the answer cannot depend on where it runs.
    const iso = "2026-01-01T00:00:00Z";
    expect(renderDateText(iso)).toBe("01.01.2026");
    expect(facetTextOf(descriptor, { cost_allocation_type_id: iso })).toBe("01.01.2026");
});

test("the filter offers exactly what the cell prints", () => {
    const iso = "2026-10-26";
    expect(facetTextOf(descriptor, { cost_allocation_type_id: iso })).toBe(renderDateText(iso));
});

test("an empty or unparsable date is not a value", () => {
    expect(facetTextOf(descriptor, { cost_allocation_type_id: null })).toBeNull();
    expect(facetTextOf(descriptor, { cost_allocation_type_id: "" })).toBeNull();
    expect(renderDateText("")).toBe("");
    // A value the browser cannot parse is shown as-is, like the cell does.
    expect(renderDateText("не дата")).toBe("не дата");
});

test("a date column sorts as a date, not as text", () => {
    // «01.02.2026» before «31.01.2026» is what a string sort would produce; the
    // kind must route the column through the numeric/date ordering instead.
    const order = facetTextOf(descriptor, { cost_allocation_type_id: "2026-02-01" });
    expect(order).toBe("01.02.2026");
});
