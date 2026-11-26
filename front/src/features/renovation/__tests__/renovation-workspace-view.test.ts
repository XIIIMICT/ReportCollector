import { describe, expect, test } from "bun:test";

import type { RenovationPlanRow } from "@/shared/api/renovation";

import {
    BLOCK_LABELS,
    BLOCK_ORDER,
    formatVersionStamp,
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
