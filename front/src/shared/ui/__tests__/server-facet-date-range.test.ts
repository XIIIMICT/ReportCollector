import { describe, expect, test } from "bun:test";

import { serverFacetSource } from "@/shared/ui/column-filter-facets";

/**
 * The PLAN's source: it narrows inside `serverFacetSource`, over display
 * strings it does not format. A date range there compared VALUES AS NUMBERS,
 * which is `null` for «24.10.2026» — the whole list vanished the moment a bound
 * was typed, leaving only «Пустые».
 */
describe("serverFacetSource narrows a date range by instant", () => {
    const window = (values: string[]) => async () => ({
        values: values.map((value) => ({ value, count: 1 })),
        total: values.length,
        hasNulls: true,
        nullCount: 3,
        rowCount: values.length + 3,
    });

    const VALUES = ["24.10.2026", "01.01.2027", "02.01.2027"];

    test("a minimum keeps every value at or after it", async () => {
        const source = serverFacetSource(window(VALUES));
        const min = new Date(2026, 9, 20).getTime();
        const result = await source("col", {
            offset: 0,
            limit: 200,
            search: { kind: "datetime", min, max: null },
        });
        expect(result.values.map((entry) => entry.value)).toEqual(VALUES);
    });

    test("a minimum drops the values before it", async () => {
        const source = serverFacetSource(window(VALUES));
        const min = new Date(2026, 9, 25).getTime();
        const result = await source("col", {
            offset: 0,
            limit: 200,
            search: { kind: "datetime", min, max: null },
        });
        expect(result.values.map((entry) => entry.value)).toEqual(["01.01.2027", "02.01.2027"]);
    });

    test("a NUMERIC range still compares numbers", async () => {
        const source = serverFacetSource(window(["7", "15", "120"]));
        const result = await source("col", {
            offset: 0,
            limit: 200,
            search: { kind: "range", min: 10, max: null },
        });
        expect(result.values.map((entry) => entry.value)).toEqual(["15", "120"]);
    });
});
