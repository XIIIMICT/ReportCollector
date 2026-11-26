import { describe, expect, test } from "bun:test";

import * as gridSelection from "@/shared/grid/grid-selection";
import { selectionCellKeys, selectionRowCellKeys } from "@/shared/grid/grid-selection";
import * as selectionLabel from "@/shared/grid/selection-label";

type TestRow = { id: string; canonicalCells?: Record<string, string> };

/** The DataGrid call-boundary accessor: canonical keys only, empty keys skipped. */
const canonicalKey = (row: TestRow, prop: string): string | undefined =>
    row.canonicalCells?.[prop] || undefined;

const rows: TestRow[] = [
    { id: "r1", canonicalCells: { name: "ck:r1/name", plan: "ck:r1/plan" } },
    { id: "r2", canonicalCells: { name: "ck:r2/name" } },
    { id: "r3" },
];
const props = ["name", "plan"];
const columns = props.map((prop) => ({ prop }));

describe("selectionCellKeys", () => {
    test("default keys are ${row.id}/${prop}, for prop strings and column objects", () => {
        const expected = ["r1/name", "r1/plan", "r2/name", "r2/plan"];
        expect(selectionCellKeys({ x: 0, y: 0, x1: 1, y1: 1 }, rows, props)).toEqual(expected);
        expect(selectionCellKeys({ x: 0, y: 0, x1: 1, y1: 1 }, rows, columns)).toEqual(expected);
    });

    test("the accessor resolves canonical keys and skips rows without them", () => {
        expect(selectionCellKeys({ x: 0, y: 0, x1: 1, y1: 2 }, rows, props, canonicalKey)).toEqual([
            "ck:r1/name",
            "ck:r1/plan",
            "ck:r2/name",
        ]);
    });

    test("rows without canonicalCells and missing/empty keys are skipped", () => {
        const mixed: TestRow[] = [
            { id: "a", canonicalCells: { a: "ck:a" } },
            { id: "b", canonicalCells: { a: "", b: "ck:b/b" } },
            { id: "c" },
        ];
        expect(
            selectionCellKeys({ x: 0, y: 0, x1: 1, y1: 2 }, mixed, ["a", "b"], canonicalKey),
        ).toEqual(["ck:a", "ck:b/b"]);
    });

    test("null and reversed ranges produce no keys", () => {
        expect(selectionCellKeys(null, rows, props)).toEqual([]);
        expect(selectionCellKeys({ x: 1, y: 1, x1: 0, y1: 0 }, rows, props)).toEqual([]);
        expect(selectionCellKeys({ x: 0, y: 1, x1: 1, y1: 0 }, rows, props)).toEqual([]);
        expect(selectionCellKeys({ x: 1, y: 0, x1: 0, y1: 1 }, rows, props)).toEqual([]);
        expect(selectionCellKeys(null, rows, props, canonicalKey)).toEqual([]);
    });

    test("rows and columns are clamped to the source", () => {
        expect(selectionCellKeys({ x: 0, y: 0, x1: 99, y1: 99 }, rows, props)).toEqual([
            "r1/name",
            "r1/plan",
            "r2/name",
            "r2/plan",
            "r3/name",
            "r3/plan",
        ]);
        expect(
            selectionCellKeys({ x: 0, y: 0, x1: 99, y1: 99 }, rows, props, canonicalKey),
        ).toEqual(["ck:r1/name", "ck:r1/plan", "ck:r2/name"]);
        expect(selectionCellKeys({ x: -2, y: -2, x1: 0, y1: 0 }, rows, props)).toEqual(["r1/name"]);
    });
});

describe("selectionRowCellKeys", () => {
    test("default keys cover all columns in order, clamped to rows.length - 1", () => {
        expect(selectionRowCellKeys(rows, 0, 1, props)).toEqual([
            "r1/name",
            "r1/plan",
            "r2/name",
            "r2/plan",
        ]);
        expect(selectionRowCellKeys(rows, 0, 99, props)).toEqual([
            "r1/name",
            "r1/plan",
            "r2/name",
            "r2/plan",
            "r3/name",
            "r3/plan",
        ]);
        expect(selectionRowCellKeys(rows, 0, 1, columns)).toEqual([
            "r1/name",
            "r1/plan",
            "r2/name",
            "r2/plan",
        ]);
    });

    test("reversed row range yields no keys", () => {
        expect(selectionRowCellKeys(rows, 1, 0, props)).toEqual([]);
    });

    test("the accessor resolves canonical keys and skips missing ones", () => {
        expect(selectionRowCellKeys(rows, 0, 2, props, canonicalKey)).toEqual([
            "ck:r1/name",
            "ck:r1/plan",
            "ck:r2/name",
        ]);
    });
});

test("only selection-label exports the authoritative selectionLabelForRange", () => {
    expect(Object.keys(gridSelection)).not.toContain("selectionLabelForRange");
    expect(Object.keys(selectionLabel)).toContain("selectionLabelForRange");
    expect(typeof gridSelection.selectionCellKeys).toBe("function");
    expect(typeof gridSelection.selectionRowCellKeys).toBe("function");
});
