import { describe, expect, test } from "bun:test";

import { computeLevelOffsets, measureColumnOffsets } from "../renovation-grid-geometry";

describe("computeLevelOffsets", () => {
    test("no levels yields a single zero boundary", () => {
        expect(computeLevelOffsets([], 36)).toEqual([0]);
    });

    test("level heights scale with the row height", () => {
        expect(computeLevelOffsets([24, 24], 36)).toEqual([0, 24, 48]);
        expect(computeLevelOffsets([12], 18)).toEqual([0, 6]);
        expect(computeLevelOffsets([0, 0], 36)).toEqual([0, 0, 0]);
    });

    test("217 levels stay monotonic and end at the full header height", () => {
        const offsets = computeLevelOffsets(Array<number>(217).fill(24), 36);
        expect(offsets).toHaveLength(218);
        expect(offsets[0]).toBe(0);
        for (let index = 1; index < offsets.length; index += 1) {
            expect(offsets[index] ?? 0).toBeGreaterThanOrEqual(offsets[index - 1] ?? 0);
        }
        expect(offsets[offsets.length - 1]).toBe(217 * 24);
    });
});

describe("measureColumnOffsets", () => {
    test("no fields leaves only the starting offset", () => {
        expect(measureColumnOffsets([], () => 100, 52)).toEqual({
            offsets: new Map(),
            totalWidth: 52,
        });
    });

    test("each field starts where the previous one ended", () => {
        const widths = new Map([
            ["a", 10],
            ["b", 20],
            ["c", 30],
        ]);
        const result = measureColumnOffsets(
            [{ dbName: "a" }, { dbName: "b" }, { dbName: "c" }],
            (field) => widths.get(field.dbName) ?? 0,
            52,
        );
        expect([...result.offsets]).toEqual([
            ["a", 52],
            ["b", 62],
            ["c", 82],
        ]);
        expect(result.totalWidth).toBe(112);
    });

    test("217 columns keep every left edge and the caller's start offset", () => {
        const fields = Array.from({ length: 217 }, (_, index) => ({ dbName: `col_${index}` }));
        let calls = 0;
        const result = measureColumnOffsets(
            fields,
            () => {
                calls += 1;
                return 100;
            },
            272,
        );
        expect(result.offsets.size).toBe(217);
        expect(calls).toBe(217);
        expect(result.offsets.get("col_0")).toBe(272);
        expect(result.offsets.get("col_216")).toBe(272 + 216 * 100);
        expect(result.totalWidth).toBe(272 + 217 * 100);
    });
});
