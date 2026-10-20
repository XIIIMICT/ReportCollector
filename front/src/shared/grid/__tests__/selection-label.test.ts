import { describe, expect, test } from "bun:test";

import type { ColumnRegular } from "@revolist/react-datagrid";

import type { ColumnDescriptor } from "@/shared/grid/grid-config";
import {
    describeSelection,
    selectionDetailForColumn,
    selectionLabelForRange,
} from "@/shared/grid/selection-label";

const columns = [
    { prop: "name", name: "Сырьевая позиция" },
    { prop: "demandPlanTons", name: "План, т" },
    { prop: "consumptionFactTons", name: "Потребление, т" },
] as unknown as ColumnRegular[];

const descriptors = new Map<string, ColumnDescriptor>([
    ["name", { key: "name", label: "Сырьевая позиция", width: 200, kind: "text" }],
    [
        "demandPlanTons",
        {
            key: "demandPlanTons",
            label: "План, т",
            path: ["Потребность"],
            width: 120,
            kind: "number",
        },
    ],
    [
        "consumptionFactTons",
        {
            key: "consumptionFactTons",
            label: "Потребление, т",
            path: ["Потребность"],
            width: 120,
            kind: "number",
        },
    ],
]);

describe("describeSelection", () => {
    test("plan detail quotes the hierarchy, not the column and row coordinates", () => {
        expect(
            selectionDetailForColumn(["Идентификационный код работы/ремонта"], "строка 42", 2),
        ).toBe("«Идентификационный код работы/ремонта» (столбец 2), строка 42");
        expect(selectionDetailForColumn(["Дирекция", "Всего"], "строки 1–5", 3)).toBe(
            "«Дирекция → Всего» (столбец 3), строки 1–5",
        );
        expect(selectionDetailForColumn([], "строка 42", 2)).toBeNull();
    });
    test("names a single column by its number, the chain goes to the detail", () => {
        const result = describeSelection({ x: 1, y: 2, x1: 1, y1: 2 }, columns, descriptors, 10);
        expect(result?.label).toBe("столбец 2, строка 3");
        expect(result?.detail).toBe("«Потребность → План, т», строка 3");
    });

    test("a cell range names both column numbers and the row range", () => {
        const result = describeSelection({ x: 1, y: 0, x1: 2, y1: 4 }, columns, descriptors, 10);
        expect(result?.label).toBe("столбцы 2–3, строки 1–5");
        // A multi-column selection has no single header chain.
        expect(result?.detail).toBeNull();
    });

    test("a column without a descriptor falls back to its quoted label", () => {
        const result = describeSelection({ x: 2, y: 0, x1: 2, y1: 0 }, columns, new Map(), 10);
        expect(result?.label).toBe("столбец «Потребление, т», строка 1");
        expect(result?.detail).toBeNull();
    });

    test("no selection is null", () => {
        expect(describeSelection(null, columns, descriptors, 10)).toBeNull();
        expect(selectionLabelForRange(null, columns, descriptors, 10)).toBeNull();
    });
});
