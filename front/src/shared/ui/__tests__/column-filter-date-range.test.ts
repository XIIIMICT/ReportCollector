import { describe, expect, it } from "bun:test";

import { getLocalTimeZone, parseAbsoluteToLocal } from "@internationalized/date";

import {
    EMPTY_DATE_RANGE,
    formatFilterDate,
    fromDateValue,
    isDateRangeEmpty,
    toDateValue,
} from "@/shared/ui/column-filter-date-range";

/** An instant with a non-zero time part, so a day-only conversion would show. */
const INSTANT_MS = Date.UTC(2026, 5, 15, 13, 45, 30);

describe("toDateValue", () => {
    it("keeps null as null — «no bound»", () => {
        expect(toDateValue(null)).toBeNull();
    });

    it("converts an epoch instant to a calendar value", () => {
        const value = toDateValue(INSTANT_MS);
        expect(value).not.toBeNull();
        expect(value?.year).toBe(2026);
        expect(value?.month).toBe(6);
        expect(value?.day).toBe(15);
    });

    it("keeps the TIME, not just the day", () => {
        // `granularity="second"` on the pickers is what makes this matter: a
        // day-only conversion would silently drop the time from the bound.
        // `toDateValue` returns a `CalendarDateTime`, which carries the fields.
        const value = toDateValue(INSTANT_MS);
        expect(value?.hour).toBeDefined();
        expect(value?.minute).toBeDefined();
    });
});

describe("fromDateValue", () => {
    it("keeps null as null — «no bound»", () => {
        // `NaN` here would fail every comparison and empty the table.
        expect(fromDateValue(null)).toBeNull();
    });

    it("round-trips an instant through the picker value", () => {
        const value = toDateValue(INSTANT_MS);
        expect(fromDateValue(value)).toBe(INSTANT_MS);
    });

    it("converts a value built in the local zone to its epoch", () => {
        const local = parseAbsoluteToLocal(new Date(INSTANT_MS).toISOString());
        expect(fromDateValue(local)).toBe(INSTANT_MS);
        expect(getLocalTimeZone()).toBeTruthy();
    });
});

describe("isDateRangeEmpty", () => {
    it("is empty only when BOTH bounds are absent", () => {
        expect(isDateRangeEmpty(EMPTY_DATE_RANGE)).toBe(true);
        expect(isDateRangeEmpty({ min: 0, max: null })).toBe(false);
        expect(isDateRangeEmpty({ min: null, max: INSTANT_MS })).toBe(false);
    });
});

describe("formatFilterDate", () => {
    it("renders nothing for a cleared picker", () => {
        expect(formatFilterDate(null)).toBe("");
    });

    it("prints the instant the same way the cells do", () => {
        // `ru-RU`, 24-hour, seconds — the cell format, so the popover's hint and
        // the grid never disagree about what a bound means.
        const text = formatFilterDate(toDateValue(INSTANT_MS));
        expect(text).toContain("15.06.2026");
        expect(text).toMatch(/\d{2}:\d{2}:\d{2}/);
    });
});
