import { describe, expect, test } from "bun:test";

import type { RenovationField } from "@/shared/api/renovation";

import {
    extractRenovationEdits,
    renovationCellKind,
    type RenovationEditContext,
} from "../renovation-grid-edits";

const field = (
    dbName: string,
    cellType: RenovationField["cellType"] = "text",
): RenovationField => ({
    dbName,
    label: dbName,
    cellType,
    ordinal: 1,
    position: 1,
    isCalculated: false,
    isReadOnly: false,
});

function context(): RenovationEditContext {
    return {
        editableFieldKeys: new Set(["name", "qty"]),
        fieldByDbName: new Map([
            ["name", field("name")],
            ["qty", field("qty", "number")],
        ]),
        previousValues: new Map([["r1", { name: "old", qty: 1 }]]),
    };
}

describe("extractRenovationEdits", () => {
    test("a plain single-cell edit with a data snapshot stays a single-cell edit", () => {
        const result = extractRenovationEdits(
            {
                prop: "name",
                val: "  new  ",
                model: { __id: "r1", __rowState: "unchanged", name: "old" },
                data: [{ __id: "r1", name: "old" }],
            },
            context(),
        );
        expect(result.updates).toEqual([{ rowId: "r1", field: "name", value: "new" }]);
    });

    test("a range save uses the keyed patch and the full row model", () => {
        const result = extractRenovationEdits(
            {
                data: { 0: { name: "new" }, 1: { qty: 2 } },
                models: [
                    { __id: "r1", __rowState: "unchanged", name: "old" },
                    { __id: "r2", __rowState: "changed", qty: 1 },
                ],
            },
            context(),
        );
        expect(result.updates).toEqual([
            { rowId: "r1", field: "name", value: "new" },
            { rowId: "r2", field: "qty", value: 2 },
        ]);
    });

    test("a range patch with a row id is used when the model is missing", () => {
        // The plan grid's pre-extraction behaviour: a range patch may carry the
        // row envelope itself; the shared extractor drops it, this one keeps it.
        const result = extractRenovationEdits(
            { data: { 0: { __id: "r1", __rowState: "unchanged", name: "new" } }, models: [] },
            context(),
        );
        expect(result.updates).toEqual([{ rowId: "r1", field: "name", value: "new" }]);
    });

    test("deleted rows, non-editable fields and unchanged values are ignored", () => {
        const result = extractRenovationEdits(
            {
                data: {
                    0: { name: "old", qty: 1, locked: "x" },
                    1: { name: "new" },
                },
                models: [
                    { __id: "r1", __rowState: "unchanged" },
                    { __id: "r2", __rowState: "deleted" },
                ],
            },
            context(),
        );
        expect(result.updates).toEqual([]);
    });

    test("duplicate updates are collapsed", () => {
        const result = extractRenovationEdits(
            {
                data: { 0: { name: "new" }, 1: { name: "new" } },
                models: [
                    { __id: "r1", __rowState: "unchanged" },
                    { __id: "r1", __rowState: "unchanged" },
                ],
            },
            context(),
        );
        expect(result.updates).toEqual([{ rowId: "r1", field: "name", value: "new" }]);
    });

    test("cell kinds map onto the shared numeric/date/boolean comparison", () => {
        expect(renovationCellKind(field("x", "number"))).toBe("number");
        expect(renovationCellKind(field("x", "date"))).toBe("date");
        expect(renovationCellKind(field("x", "datetime"))).toBe("datetime");
        expect(renovationCellKind(field("x", "boolean"))).toBe("boolean");
        expect(renovationCellKind(undefined)).toBe("text");
    });
});
