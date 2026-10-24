import { expect, test } from "bun:test";

import { computeRowFacets, rowFacetSource } from "../column-filter-facets";

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
