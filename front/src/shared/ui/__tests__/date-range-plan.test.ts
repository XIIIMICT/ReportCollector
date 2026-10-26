import { describe, expect, test } from "bun:test";

import { computeRowFacets, searchRowFacets } from "@/shared/ui/column-filter-facets";

describe("a DATE range on a column whose facets are display strings", () => {
    // The plan answers a date column with «24.10.2026» (its cell text), and the
    // picker stores epoch ms. Both must compare as the SAME instant.
    const text = (row: Record<string, unknown>) => row.d;

    test("a minimum keeps the values at or after it", () => {
        const rows = [{ d: "24.10.2026" }, { d: "01.01.2027" }, { d: "02.01.2027" }];
        const facets = computeRowFacets(rows, text, "date");
        const min = new Date(2026, 9, 20).getTime();
        const narrowed = searchRowFacets(facets, { kind: "datetime", min, max: null });
        expect(narrowed.values.length).toBe(3);
    });

    test("a value BELOW the minimum is dropped", () => {
        const rows = [{ d: "24.10.2026" }, { d: "01.01.2027" }];
        const facets = computeRowFacets(rows, text, "date");
        const min = new Date(2026, 9, 25).getTime();
        const narrowed = searchRowFacets(facets, { kind: "datetime", min, max: null });
        expect(narrowed.values).toEqual(["01.01.2027"]);
    });
});
