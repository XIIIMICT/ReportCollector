import { describe, expect, test } from "bun:test";

import { fromDateValue, formatFilterDate, toDateValue } from "@/shared/ui/column-filter-date-range";

const INSTANT = Date.UTC(2026, 5, 15, 13, 45, 30);

describe("datetime bounds keep their time", () => {
    test("the picker value carries the hour, not just the day", () => {
        const value = toDateValue(INSTANT, "datetime");
        expect(value).not.toBeNull();
        // A day-only conversion would drop these and silently widen every bound.
        expect("hour" in (value as object)).toBe(true);
    });

    test("the round trip preserves the instant", () => {
        expect(fromDateValue(toDateValue(INSTANT, "datetime"))).toBe(INSTANT);
    });

    test("the hint prints the time the cells print", () => {
        const text = formatFilterDate(toDateValue(INSTANT, "datetime"), "datetime");
        expect(text).toMatch(/\d{2}\.\d{2}\.\d{4}/);
        expect(text).toMatch(/\d{2}:\d{2}:\d{2}/);
    });
});
