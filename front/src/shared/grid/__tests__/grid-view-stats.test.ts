import { describe, expect, test } from "bun:test";

import type { GridViewStats } from "../data-grid";
import { sameGridViewStats } from "../grid-view-stats";

const stats = (overrides: Partial<GridViewStats> = {}): GridViewStats => ({
    totalRows: 10,
    filteredRows: 4,
    totalCols: 3,
    filtersApplied: true,
    filteredColumns: [["Базис", "Поставки"]],
    sortedColumns: [{ path: ["Объём"], order: "asc" }],
    immutableCols: 1,
    calculatedCols: 1,
    editableCols: 1,
    ...overrides,
});

describe("sameGridViewStats", () => {
    test("an equal publication keeps the previous object", () => {
        expect(sameGridViewStats(stats(), stats())).toBe(true);
    });

    test("a different field is a real change", () => {
        expect(sameGridViewStats(stats(), stats({ filteredRows: 5 }))).toBe(false);
    });

    test("a different filter/sort chain is a real change", () => {
        const current = stats();
        expect(sameGridViewStats(current, stats({ filteredColumns: [["Базис", "Цена"]] }))).toBe(
            false,
        );
        expect(
            sameGridViewStats(
                current,
                stats({ sortedColumns: [{ path: ["Объём"], order: "desc" }] }),
            ),
        ).toBe(false);
    });

    test("nothing published yet is never equal to a real publication", () => {
        expect(sameGridViewStats(null, stats())).toBe(false);
    });

    test("an all-zero, unfiltered, empty-chain stats object still compares", () => {
        const empty = stats({
            totalRows: 0,
            filteredRows: 0,
            totalCols: 0,
            filtersApplied: false,
            filteredColumns: [],
            sortedColumns: [],
            immutableCols: 0,
            calculatedCols: 0,
            editableCols: 0,
        });
        expect(sameGridViewStats(empty, stats())).toBe(false);
        expect(sameGridViewStats(empty, empty)).toBe(true);
    });
});
