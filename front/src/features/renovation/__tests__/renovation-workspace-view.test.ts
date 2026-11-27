import { describe, expect, test } from "bun:test";

import type { RenovationPlanRow } from "@/shared/api/renovation";

import {
    BLOCK_LABELS,
    BLOCK_ORDER,
    formatVersionStamp,
    remoteCellPatchesByRow,
    renovationGridScopeKey,
    toRenovationGridRow,
    validationColumn,
    VERSION_STATE_LABELS,
} from "../renovation-workspace-view";

const emptyScope = { planId: null, asset: null, filters: [], sorting: [] };

describe("renovation workspace view helpers", () => {
    test("the grid scope changes with the plan, asset, filters and sorting", () => {
        const base = renovationGridScopeKey(emptyScope);
        expect(renovationGridScopeKey(emptyScope)).toBe(base);
        expect(renovationGridScopeKey({ ...emptyScope, planId: "v1" })).not.toBe(base);
        expect(renovationGridScopeKey({ ...emptyScope, asset: "a1" })).not.toBe(base);
        expect(
            renovationGridScopeKey({
                ...emptyScope,
                filters: [{ key: "f", values: ["x"] }] as typeof emptyScope.filters,
            }),
        ).not.toBe(base);
        expect(
            renovationGridScopeKey({
                ...emptyScope,
                sorting: [{ column: "c", direction: "asc" }] as typeof emptyScope.sorting,
            }),
        ).not.toBe(base);
    });

    test("validationColumn extracts the Russian column name from either message form", () => {
        expect(validationColumn("Неизвестный столбец «qty»")).toBe("qty");
        expect(validationColumn("Недопустимое значение столбца «date_from»")).toBe("date_from");
        expect(validationColumn("Ошибка запроса")).toBeNull();
    });

    test("toRenovationGridRow stamps diff state and keeps server values", () => {
        const row: RenovationPlanRow = {
            id: "00000000-0000-4000-8000-000000000001",
            position: 7,
            renovationId: "R-7",
            assetId: null,
            assetName: null,
            assetCode: "",
            assetRepairCode: "",
            values: { qty: 3 },
            missingComponents: [],
            invalidFields: [],
            revision: 1,
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-01T00:00:00Z",
            updatedBy: "",
        };
        const gridRow = toRenovationGridRow(row, new Map());
        expect(gridRow.__id).toBe(row.id);
        expect(gridRow.__rowState).toBe("unchanged");
        expect(gridRow.qty).toBe(3);
        expect(gridRow.__assetName).toBe("");
        const changed = toRenovationGridRow(
            row,
            new Map([
                [row.id, { isNew: false, cells: [{ field: "qty", before: 1, after: 3 }] } as never],
            ]),
        );
        expect(changed.__rowState).toBe("changed");
        expect(changed.__changedFields).toEqual(["qty"]);
    });

    test("formatVersionStamp keeps unparsable input and labels stay stable", () => {
        expect(formatVersionStamp("not-a-date")).toBe("not-a-date");
        expect(formatVersionStamp("2026-01-02T03:04:05Z")).toContain("2026");
        expect(Object.values(VERSION_STATE_LABELS)).toContain("рабочая");
        expect(BLOCK_ORDER.map((key) => BLOCK_LABELS[key])).toEqual([
            "1) Техническая дирекция",
            "2) Дирекция КСиР",
            "3) Дирекция МТО",
        ]);
    });
});

describe("remoteCellPatchesByRow (keyed remote-cell apply)", () => {
    const mergeable = new Set(["qty", "work_or_repair_code", "note"]);

    test("empty values and no mergeable fields give no patches", () => {
        expect(remoteCellPatchesByRow(new Map(), mergeable).size).toBe(0);
        expect(
            remoteCellPatchesByRow(new Map([["r_plan_rows/r1/readonly", 5]]), mergeable).size,
        ).toBe(0);
    });

    test("values are grouped per row in arrival order, including null", () => {
        const values = new Map<string, unknown>([
            ["r_plan_rows/r1/qty", 3],
            ["r_plan_rows/r2/qty", null],
            ["r_plan_rows/r1/note", "готово"],
        ]);
        expect(remoteCellPatchesByRow(values, mergeable)).toEqual(
            new Map([
                ["r1", { qty: 3, note: "готово" }],
                ["r2", { qty: null }],
            ]),
        );
    });

    test("work id derives the incomplete-tint companions from the remote value", () => {
        const withCode = remoteCellPatchesByRow(
            new Map([["r_plan_rows/r1/work_or_repair_code", "<NULL>-1"]]),
            mergeable,
        );
        expect(withCode.get("r1")).toEqual({
            work_or_repair_code: "<NULL>-1",
            __renovationId: "<NULL>-1",
            __missingComponents: ["missing"],
        });
        const cleared = remoteCellPatchesByRow(
            new Map([["r_plan_rows/r1/work_or_repair_code", null]]),
            mergeable,
        );
        expect(cleared.get("r1")).toEqual({
            work_or_repair_code: null,
            __renovationId: null,
            __missingComponents: [],
        });
    });

    test("dictionary-table keys and malformed or stale keys are ignored", () => {
        const values = new Map<string, unknown>([
            ["r_assets/a1/name", "Не должен попасть"],
            ["r_plan_rows/r1/qty", 1],
            ["r_plan_rows/r1", 2],
            ["r_plan_rows//qty", 3],
            ["r_plan_rows/r1/qty/extra", 4],
            ["r_plan_rows/r1/qty", undefined],
        ]);
        // The later undefined key overwrites the earlier value in the source
        // map, so it is skipped exactly like the previous full scan did.
        expect(remoteCellPatchesByRow(values, mergeable)).toEqual(new Map());
    });

    test("explicit table argument keeps a caller's key space isolated", () => {
        const values = new Map<string, unknown>([["other/r1/qty", 7]]);
        expect(remoteCellPatchesByRow(values, mergeable, "other")).toEqual(
            new Map([["r1", { qty: 7 }]]),
        );
        expect(remoteCellPatchesByRow(values, mergeable).size).toBe(0);
    });

    test("a large accumulated map yields one patch per row, not per field", () => {
        const values = new Map<string, unknown>();
        for (let index = 0; index < 500; index += 1) {
            values.set(`r_plan_rows/r${index % 25}/qty`, index);
            values.set(`r_plan_rows/r${index % 25}/note`, `n${index}`);
        }
        const patches = remoteCellPatchesByRow(values, mergeable);
        expect(patches.size).toBe(25);
        expect(patches.get("r24")).toEqual({ qty: 499, note: "n499" });
    });
});
