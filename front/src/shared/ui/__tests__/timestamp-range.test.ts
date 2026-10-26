import { describe, expect, test } from "bun:test";

import { computeRowFacets, searchRowFacets } from "@/shared/ui/column-filter-facets";

describe("timestamp buckets stay comparable", () => {
    const rows = [
        { at: "2026-10-15T05:03:25.000Z" },
        { at: "2026-10-15T05:30:00.000Z" },
        { at: "2026-10-16T09:00:00.000Z" },
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

    test("a date column never marks its buckets as non-numeric", () => {
        // `nonNumeric` means «not comparable at all», and `searchRowFacets`
        // drops such buckets before the date branch runs. A timestamp is not a
        // number but IS comparable, so the set must stay empty — this is what
        // emptied every timestamp list the moment a bound was typed.
        const facets = computeRowFacets(rows, text, "date");
        expect([...facets.nonNumeric]).toEqual([]);
    });

    test("a minimum on the day keeps the values of that day", () => {
        const facets = computeRowFacets(rows, text, "date");
        const dayStart = new Date(2026, 9, 15, 0, 0, 0).getTime();
        const narrowed = searchRowFacets(facets, {
            kind: "datetime",
            min: dayStart,
            max: null,
        });
        expect(narrowed.values.length).toBe(3);
    });
});
