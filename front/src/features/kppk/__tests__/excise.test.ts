import { describe, expect, test } from "bun:test";

import type { CellUpdate } from "@/shared/api/raw-materials";

import { expandExcise } from "../kppk-excise";

/**
 * The merged «Акциз» cell is one figure per product, stored on every market row:
 * an edit must reach the rows the user cannot see, or the hidden rows keep the
 * old rate and a sort (which un-merges the block) exposes the disagreement.
 */
const ROWS = [
    { id: "a1", product: "Сода" },
    { id: "a2", product: "Сода" },
    { id: "a3", product: "Сода" },
    { id: "b1", product: "Сульфат" },
    { id: "b2", product: "Сульфат" },
];

describe("expandExcise", () => {
    test("an excise edit reaches every market row of that product only", () => {
        const expanded = expandExcise(
            [{ rowId: "a1", field: "exciseBp", value: 12 }],
            ROWS,
            "exciseBp",
        );
        expect(expanded.map((update) => update.rowId).sort()).toEqual(["a1", "a2", "a3"]);
        expect(expanded.every((update) => update.value === 12)).toBe(true);
    });

    test("editing a covered row still updates the whole block", () => {
        const expanded = expandExcise(
            [{ rowId: "a3", field: "exciseBp", value: 7 }],
            ROWS,
            "exciseBp",
        );
        expect(expanded.map((update) => update.rowId).sort()).toEqual(["a1", "a2", "a3"]);
    });

    test("another product's block is untouched", () => {
        const expanded = expandExcise(
            [{ rowId: "b2", field: "exciseBp", value: 5 }],
            ROWS,
            "exciseBp",
        );
        expect(expanded.map((update) => update.rowId).sort()).toEqual(["b1", "b2"]);
    });

    test("non-excise edits pass through unchanged", () => {
        const updates: CellUpdate[] = [
            { rowId: "a1", field: "monthBpPlan", value: 10 },
            { rowId: "a2", field: "fcaPriceBpPlan", value: 3 },
        ];
        expect(expandExcise(updates, ROWS, "exciseBp")).toBe(updates);
    });

    test("an unknown row is dropped (its product scope is unknown)", () => {
        const updates: CellUpdate[] = [{ rowId: "zz", field: "exciseBp", value: 1 }];
        expect(expandExcise(updates, ROWS, "exciseBp")).toHaveLength(0);
    });

    test("an excise outside the product's scope is dropped", () => {
        // Only «Сода» levies an excise on this asset.
        const allowed = (product: string) => product === "Сода";
        const expanded = expandExcise(
            [{ rowId: "b1", field: "exciseBp", value: 5 }],
            ROWS,
            "exciseBp",
            allowed,
        );
        expect(expanded).toHaveLength(0);
    });

    test("a scoped product still fans out while another is rejected", () => {
        const allowed = (product: string) => product === "Сода";
        const expanded = expandExcise(
            [
                { rowId: "a1", field: "exciseBp", value: 5 },
                { rowId: "b1", field: "exciseBp", value: 9 },
            ],
            ROWS,
            "exciseBp",
            allowed,
        );
        expect(expanded.map((update) => update.rowId).sort()).toEqual(["a1", "a2", "a3"]);
    });

    test("each slice has its own excise field", () => {
        const plan = expandExcise(
            [{ rowId: "a1", field: "exciseMaxPlan", value: 2 }],
            ROWS,
            "exciseMaxPlan",
        );
        expect(plan).toHaveLength(3);
        // The fact slice's key is not the excise key here, so nothing expands.
        const fact = expandExcise(
            [{ rowId: "a1", field: "fact", value: 2 }],
            ROWS,
            "exciseMaxPlan",
        );
        expect(fact).toHaveLength(1);
    });
});
