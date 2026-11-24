import { describe, expect, it } from "bun:test";

import {
    columnFiltersStorageKey,
    reviveFilters,
    readStoredFilters,
    writeStoredFilters,
} from "@/shared/grid/use-grid-column-filters";

describe("columnFiltersStorageKey", () => {
    it("keeps exact code selections distinct from group labels after reload", () => {
        const stored = {
            work_or_repair_code: {
                column: "work_or_repair_code",
                values: ["Корректные"],
                includeNulls: false,
                valueMode: "exact" as const,
            },
        };
        expect(reviveFilters(JSON.stringify(stored))).toEqual(stored);
    });
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
    it.each([
        { from: "2026-10-13T21:00:00.000Z", to: null },
        { from: null, to: "2026-10-14T21:00:00.000Z" },
        { from: "2026-10-13T21:00:00.000Z", to: "2026-10-14T21:00:00.000Z" },
    ])("round-trips timestamp bounds without changing the row query (%j)", (window) => {
        const filters = {
            createdAt: { column: "createdAt", values: [], includeNulls: false, ...window },
        };
        writeStoredFilters("test", "plan", filters);
        expect(readStoredFilters("test", "plan")).toEqual(filters);
        writeStoredFilters("test", "plan", {});
        expect(readStoredFilters("test", "plan")).toEqual({});
    });

    it("rejects invalid timestamp bounds instead of silently changing the filter type", () => {
        expect(
            reviveFilters(
                JSON.stringify({
                    createdAt: { values: [], includeNulls: false, from: "broken", to: null },
                }),
            ),
        ).toEqual({});
    });
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
                process_position: {
                    column: "process_position",
                    values: ["a", "b"],
                    includeNulls: false,
                },
            }),
        );
        const filter = revived.process_position;
        expect(filter).toBeDefined();
        expect(filter?.values).toEqual(["a", "b"]);
        expect(filter?.includeNulls).toBe(false);
        // The column prop comes from the KEY, so a stale inner `column` cannot
        // make the filter apply to the wrong column.
        expect(filter?.column).toBe("process_position");
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
