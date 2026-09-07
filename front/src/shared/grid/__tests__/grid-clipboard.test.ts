import { describe, expect, test } from "bun:test";

import type { ColumnDescriptor, GridRow } from "@/shared/grid/grid-config";
import { extractEdits } from "@/shared/grid/grid-edit";

const descriptorByKey = new Map<string, ColumnDescriptor>(
    ["quantity", "price", "asset", "amount", "note"].map((key) => [
        key,
        { key, label: key, width: 100, kind: "number" },
    ]),
);
descriptorByKey.set("asset", { key: "asset", label: "Актив", width: 100, kind: "asset" });
descriptorByKey.set("amount", {
    key: "amount",
    label: "Сумма",
    width: 100,
    kind: "calculated",
    fixed: true,
});
descriptorByKey.set("note", { key: "note", label: "Примечание", width: 100, kind: "text" });

function row(id: string, values: Record<string, unknown> = {}): GridRow {
    return { id, rowState: "unchanged", changedFields: [], isDeleted: false, ...values };
}

describe("extractEdits (range paste)", () => {
    test("патч с моделями строк превращается в обновления ячеек", () => {
        const models = [row("row-1"), row("row-2")];
        const result = extractEdits(
            {
                data: { 0: { quantity: "10", note: "новая" }, 1: { quantity: "20" } },
                models,
                type: "rgRow",
            },
            new Set(["quantity", "note"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([
            { rowId: "row-1", field: "quantity", value: "10" },
            { rowId: "row-1", field: "note", value: "новая" },
            { rowId: "row-2", field: "quantity", value: "20" },
        ]);
        expect(result.placeholderPatch).toEqual({});
    });

    test("патч без моделей не даёт обновлений (старое поведение)", () => {
        const result = extractEdits(
            {
                data: { 0: { quantity: "10" } },
                models: [],
                type: "rgRow",
            },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([]);
    });

    test("нередактируемые и расчётные поля пропускаются", () => {
        const models = [row("row-1")];
        const result = extractEdits(
            { data: { 0: { amount: "999", quantity: "5" } }, models, type: "rgRow" },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "quantity", value: "5" }]);
    });

    test("значения без изменений не попадают в обновления", () => {
        const models = [row("row-1", { quantity: "10" })];
        const previous = new Map([["row-1", { quantity: "10" }]]);
        const result = extractEdits(
            { data: { 0: { quantity: "10" } }, models, type: "rgRow" },
            new Set(["quantity"]),
            descriptorByKey,
            previous,
        );
        expect(result.updates).toEqual([]);
    });

    test("значения проходят через toCellValue (trim строк)", () => {
        const models = [row("row-1")];
        const result = extractEdits(
            { data: { 0: { note: "  заметка  " } }, models, type: "rgRow" },
            new Set(["note"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "note", value: "заметка" }]);
    });

    test("пустая строка в буфере очищает ячейку", () => {
        const models = [row("row-1")];
        const result = extractEdits(
            { data: { 0: { note: "" } }, models, type: "rgRow" },
            new Set(["note"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "note", value: "" }]);
    });

    test("удалённые строки пропускаются", () => {
        const models = [row("row-1", { isDeleted: true })];
        const result = extractEdits(
            { data: { 0: { quantity: "5" } }, models, type: "rgRow" },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([]);
    });

    test("строки-заглушки уходят в placeholderPatch", () => {
        const models = [row("row-1", { isPlaceholder: true })];
        const result = extractEdits(
            { data: { 0: { quantity: "5" } }, models, type: "rgRow" },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([]);
        expect(result.placeholderPatch).toEqual({ quantity: "5" });
    });

    test("одиночное редактирование ячейки (не диапазон)", () => {
        const result = extractEdits(
            { model: row("row-1"), prop: "quantity", val: "7" },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "quantity", value: "7" }]);
    });

    test("single-cell afteredit with a source snapshot still produces an update", () => {
        const model = row("row-1");
        const result = extractEdits(
            { model, prop: "quantity", val: "7", data: [model, row("row-2")] },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "quantity", value: "7" }]);
    });

    test("range patches use the supplied visible row order", () => {
        const result = extractEdits(
            { data: { 0: { quantity: "42" } }, models: [row("row-2"), row("row-1")] },
            new Set(["quantity"]),
            descriptorByKey,
        );
        expect(result.updates).toEqual([{ rowId: "row-2", field: "quantity", value: "42" }]);
    });
});
