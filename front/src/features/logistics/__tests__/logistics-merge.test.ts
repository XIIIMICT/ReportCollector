import { describe, expect, test } from "bun:test";

import { countryMarketMerges, transportTypeMerges } from "../logistics-merge";

const rows = (...ids: string[]) => ids.map((transportTypeId) => ({ transportTypeId }));
const markets = (...values: string[]) => values.map((market) => ({ market }));

describe("transportTypeMerges", () => {
    test("merges consecutive rows of one transport type", () => {
        expect(transportTypeMerges(rows("a", "a", "a", "b", "b"), false)).toEqual([
            { row: 0, column: 0, rowSpan: 3 },
            { row: 3, column: 0, rowSpan: 2 },
        ]);
    });

    test("a type with one vehicle type is not merged", () => {
        expect(transportTypeMerges(rows("a", "b", "b"), false)).toEqual([
            { row: 1, column: 0, rowSpan: 2 },
        ]);
    });

    test("an empty table has no merges", () => {
        expect(transportTypeMerges([], false)).toEqual([]);
    });

    test("a user sorting disables every merge", () => {
        expect(transportTypeMerges(rows("a", "a", "b", "b"), true)).toEqual([]);
    });
});

describe("countryMarketMerges", () => {
    test("merges the market cell over its alphabetically sorted countries", () => {
        expect(countryMarketMerges(markets("РФ", "РФ", "БЗ", "ДЗ", "ДЗ"), false)).toEqual([
            { row: 0, column: 0, rowSpan: 2 },
            { row: 3, column: 0, rowSpan: 2 },
        ]);
    });

    test("a filter or a user sorting disables the merge", () => {
        expect(countryMarketMerges(markets("РФ", "РФ"), true)).toEqual([]);
    });
});
