import { describe, expect, test } from "bun:test";

import { serverFacetSource, type FacetSearch } from "@/shared/ui/column-filter-facets";

/**
 * The PLAN's source passes the search to the server over raw values. This test
 * models that contract and verifies that date ranges use the same epoch-ms
 * semantics as numeric ranges.
 */
describe("serverFacetSource narrows a date range by instant", () => {
    const window =
        (values: string[]) =>
        async (
            _column: string,
            params: { offset: number; limit: number; search?: FacetSearch | null | undefined },
        ) => {
            const search = params.search;
            const filtered = !search
                ? values
                : search.kind === "text"
                  ? values.filter((value) =>
                        value
                            .toLocaleLowerCase("ru-RU")
                            .includes(search.query.toLocaleLowerCase("ru-RU")),
                    )
                  : values.filter((value) => {
                        const comparable =
                            search.kind === "datetime"
                                ? Date.parse(value.split(".").reverse().join("-"))
                                : Number(value);
                        return (
                            (search.min === null || comparable >= search.min) &&
                            (search.max === null || comparable <= search.max)
                        );
                    });
            return {
                values: filtered.map((value) => ({ value, count: 1 })),
                total: filtered.length,
                hasNulls: true,
                nullCount: 3,
                rowCount: values.length + 3,
            };
        };

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
