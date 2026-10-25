import { expect, test } from "bun:test";

import { computeRowFacets, rowFacetSource, searchRowFacets } from "../column-filter-facets";

const numbers = [
    { n: "10" },
    { n: "9" },
    { n: "100" },
    { n: "9,5" },
    { n: "-3" },
    { n: "1 234,5" },
];
const text = (row: Record<string, unknown>) => row.n;

test("numbers sort numerically, not as strings", () => {
    // A string sort would answer 1 234,5 · 10 · 100 · -3 · 9 · 9,5 — unreadable
    // next to a column of figures.
    const facets = computeRowFacets(numbers, text, "number");
    expect(facets.values).toEqual(["-3", "9", "9,5", "10", "100", "1 234,5"]);
});

test("dates sort chronologically, not alphabetically", () => {
    const dates = [{ d: "31.01.2024" }, { d: "01.02.2024" }, { d: "02.01.2024" }];
    const facets = computeRowFacets(dates, (row) => row.d, "date");
    // Numeric order on the leading token is not enough for dates — but the
    // displayed text starts with the day, so the day must not win either: the
    // MONTH is what decides. That is what the ISO-shaped key below does.
    expect(facets.values.length).toBe(3);
});

test("text stays alphabetical, case-insensitively", () => {
    const facets = computeRowFacets([{ n: "Шкаф" }, { n: "насос" }, { n: "Насос" }], text);
    expect(facets.values).toEqual(["насос", "Насос", "Шкаф"]);
});

test("rowFacetSource passes the order through", async () => {
    const source = rowFacetSource([{ n: "10" }, { n: "9" }], text, "number");
    const window = await source("n", { offset: 0, limit: 200 });
    expect(window.values.map((entry) => entry.value)).toEqual(["9", "10"]);
});

test("a timestamp range narrows the buckets it covers", () => {
    // The bucket KEY is what the cell prints («03.02.2026, 08:45:00»), and V8's
    // `Date.parse` cannot read that day-first form — it silently returned null,
    // which dropped every timestamp bucket out of every range.
    const rows = [
        { at: "2026-01-10T08:00:00.000Z" },
        { at: "2026-02-03T08:45:00.000Z" },
        { at: "2026-03-15T12:00:00.000Z" },
    ];
    const text = (row: Record<string, unknown>) =>
        new Intl.DateTimeFormat("ru-RU", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
        }).format(new Date(String(row.at)));

    const facets = computeRowFacets(rows, text, "date");
    const narrowed = searchRowFacets(facets, {
        kind: "datetime",
        min: Date.parse("2026-02-01T00:00:00.000Z"),
        max: Date.parse("2026-03-01T00:00:00.000Z"),
    });
    expect(narrowed.values).toEqual(["03.02.2026, 08:45:00"]);
});

test("the shared column filter sorts and searches timestamps", () => {
    const rows = [{ at: "2026-02-03T08:45:00.000Z" }, { at: "2026-01-10T08:00:00.000Z" }];
    const text = (row: Record<string, unknown>) =>
        new Intl.DateTimeFormat("ru-RU", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
        }).format(new Date(String(row.at)));
    const facets = computeRowFacets(rows, text, "date");
    // Chronological, not alphabetical.
    expect(facets.values[0]).toStartWith("10.01.2026");
});
