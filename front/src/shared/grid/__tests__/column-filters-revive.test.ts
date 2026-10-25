import { describe, expect, it } from "bun:test";

import { columnFiltersStorageKey, reviveFilters } from "@/shared/grid/use-grid-column-filters";

describe("columnFiltersStorageKey", () => {
    it("keys by project AND section, so no two tables share a bucket", () => {
        expect(columnFiltersStorageKey("renovation", "assets")).toBe(
            "rc:filters:renovation:assets",
        );
        // The bug this prevents: one project's filters reaching another
        // section's table, which describes columns it may not even have.
        expect(columnFiltersStorageKey("renovation", "catalogs")).not.toBe(
            columnFiltersStorageKey("renovation", "assets"),
        );
        expect(columnFiltersStorageKey("kppk", "products")).not.toBe(
            columnFiltersStorageKey("renovation", "products"),
        );
    });
});

describe("reviveFilters", () => {
    it("returns nothing for input that is not an object of filters", () => {
        // Untrusted on every axis: a hand-edited entry, a shape from a previous
        // release, a truncated write. All must open the table unfiltered.
        expect(reviveFilters("not json at all")).toEqual({});
        expect(reviveFilters("null")).toEqual({});
        expect(reviveFilters("[]")).toEqual({});
    });

    it("keeps a well-formed filter", () => {
        const revived = reviveFilters(
            JSON.stringify({
                col_011: { column: "col_011", values: ["a", "b"], includeNulls: false },
            }),
        );
        const filter = revived.col_011;
        expect(filter).toBeDefined();
        expect(filter?.values).toEqual(["a", "b"]);
        expect(filter?.includeNulls).toBe(false);
        // The column prop comes from the KEY, so a stale inner `column` cannot
        // make the filter apply to the wrong column.
        expect(filter?.column).toBe("col_011");
    });

    it("defaults includeNulls to true when the stored entry omits it", () => {
        const revived = reviveFilters(JSON.stringify({ col_001: { values: ["x"] } }));
        expect(revived.col_001?.includeNulls).toBe(true);
    });

    it("drops entries whose values are missing or not strings", () => {
        const revived = reviveFilters(
            JSON.stringify({
                good: { values: ["ok"] },
                noValues: { column: "noValues" },
                notArray: { values: "single" },
                mixed: { values: ["ok", 3] },
                nullEntry: null,
                numberEntry: 7,
            }),
        );
        expect(Object.keys(revived)).toEqual(["good"]);
    });
});
