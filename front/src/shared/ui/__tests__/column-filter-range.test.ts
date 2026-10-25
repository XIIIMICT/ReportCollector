import { describe, expect, it } from "bun:test";

import { EMPTY_RANGE, isRangeEmpty, parseRangeBound } from "@/shared/ui/column-filter-range";

describe("parseRangeBound", () => {
    it("treats an empty box as «no bound», not as zero", () => {
        // The distinction the whole range filter rests on: an empty box must not
        // constrain the column, while `0` is a value a numeric column can hold.
        expect(parseRangeBound("")).toBeNull();
        expect(parseRangeBound("   ")).toBeNull();
        expect(parseRangeBound("0")).toBe(0);
    });

    it("parses numbers, including negative and fractional ones", () => {
        expect(parseRangeBound("12")).toBe(12);
        expect(parseRangeBound("-3.5")).toBe(-3.5);
        expect(parseRangeBound(" 42 ")).toBe(42);
    });

    it("returns null for text that is not a finite number", () => {
        // `NaN` would fail every comparison and silently empty the table, and
        // `Infinity` is not a bound anyone means to type.
        expect(parseRangeBound("abc")).toBeNull();
        expect(parseRangeBound("1,5")).toBeNull();
        expect(parseRangeBound("Infinity")).toBeNull();
    });
});

describe("isRangeEmpty", () => {
    it("is empty only when BOTH bounds are absent", () => {
        expect(isRangeEmpty(EMPTY_RANGE)).toBe(true);
        expect(isRangeEmpty({ min: null, max: null })).toBe(true);
    });

    it("is not empty when either bound is set, even to zero", () => {
        expect(isRangeEmpty({ min: 0, max: null })).toBe(false);
        expect(isRangeEmpty({ min: null, max: 0 })).toBe(false);
        expect(isRangeEmpty({ min: 1, max: 2 })).toBe(false);
    });
});
