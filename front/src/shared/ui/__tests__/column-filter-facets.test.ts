import { expect, test } from "bun:test";

import {
    computeRowFacets,
    facetWindow,
    rowFacetSource,
    serverFacetSource,
} from "../column-filter-facets";

const rows = [
    { id: "1", name: "Шкаф" },
    { id: "2", name: "насос" },
    { id: "3", name: "Насос" },
    { id: "4", name: "" },
    { id: "5", name: null },
    { id: "6", name: "Шкаф" },
];

const name = (row: Record<string, unknown>) => row.name;

test("values are deduplicated by display text and sorted case-insensitively", () => {
    const facets = computeRowFacets(rows, name);
    // Casefold first, then the value itself — the backend's ordering, so a
    // lowercase twin sorts next to its capitalised form, not apart from it.
    expect(facets.values).toEqual(["насос", "Насос", "Шкаф"]);
    expect(facets.counts.get("Шкаф")).toBe(2);
    expect(facets.counts.get("Насос")).toBe(1);
});

test("NULL and an empty string collapse into one «Пустые» bucket", () => {
    const facets = computeRowFacets(rows, name);
    expect(facets.hasNulls).toBe(true);
    expect(facets.nullCount).toBe(2);
    expect(facets.rowCount).toBe(6);
    // The empty bucket never appears among the values.
    expect(facets.values).not.toContain("");
});

test("a column without NULLs has no «Пустые» bucket at all", () => {
    const facets = computeRowFacets([{ a: "x" }, { a: "y" }], (row) => row.a);
    expect(facets.hasNulls).toBe(false);
    expect(facets.nullCount).toBe(0);
});

test("numbers, booleans and multi-valued cells render as the grid shows them", () => {
    const facets = computeRowFacets(
        [{ v: 3 }, { v: 3.5 }, { v: true }, { v: false }, { v: ["АО", "ООО"] }],
        (row) => row.v,
    );
    expect(facets.values).toEqual(["3", "3.5", "АО, ООО", "Да", "Нет"]);
});

test("windows page over the SAME sorted list, so they concatenate", () => {
    const many = Array.from({ length: 450 }, (_, index) => ({ name: `Позиция ${index}` }));
    const facets = computeRowFacets(many, name);
    const first = facetWindow(facets, 0, 200);
    const second = facetWindow(facets, 200, 200);
    const third = facetWindow(facets, 400, 200);
    expect(first.total).toBe(450);
    expect(first.values).toHaveLength(200);
    expect(third.values).toHaveLength(50);
    expect([...first.values, ...second.values, ...third.values].map((v) => v.value)).toEqual(
        facets.values,
    );
});

test("rowFacetSource honours offset/limit like the server source does", async () => {
    const source = rowFacetSource(
        Array.from({ length: 5 }, (_, index) => ({ name: `Значение ${index}` })),
        name,
    );
    const window = await source("name", { offset: 2, limit: 2 });
    expect(window.values.map((entry) => entry.value)).toEqual(["Значение 2", "Значение 3"]);
    expect(window.total).toBe(5);
    expect(window.rowCount).toBe(5);
});

test("serverFacetSource drops the null-labelled entries of the wire format", async () => {
    const source = serverFacetSource(() =>
        Promise.resolve({
            values: [
                { value: "Насос", count: 2 },
                { value: null, count: 5 },
            ],
            total: 1,
            hasNulls: true,
            nullCount: 5,
            rowCount: 7,
        }),
    );
    const window = await source("col_010", { offset: 0, limit: 200 });
    expect(window.values).toEqual([{ value: "Насос", count: 2 }]);
    expect(window.hasNulls).toBe(true);
    expect(window.nullCount).toBe(5);
});
