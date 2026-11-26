import { afterEach, describe, expect, mock, test } from "bun:test";

import {
    copyToClipboard,
    parsePastedText,
    pasteTargetRange,
    readFromClipboard,
} from "@/shared/grid/grid-clipboard";
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
descriptorByKey.set("assets", { key: "assets", label: "Активы", width: 100, kind: "assetsMulti" });
descriptorByKey.set("enabled", { key: "enabled", label: "Включено", width: 100, kind: "boolean" });
descriptorByKey.set("when", { key: "when", label: "Дата", width: 100, kind: "datetime" });

function row(id: string, values: Record<string, unknown> = {}): GridRow {
    return { id, rowState: "unchanged", changedFields: [], isDeleted: false, ...values };
}

describe("extractEdits (range paste)", () => {
    test("text equality does not coerce numeric-looking labels", () => {
        const models = [row("row-1")];
        const previous = new Map([["row-1", { note: "001" }]]);
        const result = extractEdits(
            { data: { 0: { note: "1" } }, models, type: "rgRow" },
            new Set(["note"]),
            descriptorByKey,
            previous,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "note", value: "1" }]);
    });

    test("text equality does not accept a numeric prefix", () => {
        const models = [row("row-1")];
        const previous = new Map([["row-1", { note: "123abc" }]]);
        const result = extractEdits(
            { data: { 0: { note: "123xyz" } }, models, type: "rgRow" },
            new Set(["note"]),
            descriptorByKey,
            previous,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "note", value: "123xyz" }]);
    });

    test("number equality still accepts alternate numeric spelling", () => {
        const models = [row("row-1")];
        const previous = new Map([["row-1", { quantity: "001" }]]);
        const result = extractEdits(
            { data: { 0: { quantity: "1" } }, models, type: "rgRow" },
            new Set(["quantity"]),
            descriptorByKey,
            previous,
        );
        expect(result.updates).toEqual([]);
    });

    test("arrays, booleans and equivalent date instants compare by their field kind", () => {
        const models = [row("row-1")];
        const previous = new Map([
            [
                "row-1",
                {
                    assets: ["A", "B"],
                    enabled: true,
                    when: "2026-01-01T00:00:00Z",
                },
            ],
        ]);
        const result = extractEdits(
            {
                data: {
                    0: {
                        assets: ["A", "B"],
                        enabled: false,
                        when: "2026-01-01",
                    },
                },
                models,
                type: "rgRow",
            },
            new Set(["assets", "enabled", "when"]),
            descriptorByKey,
            previous,
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "enabled", value: false }]);
    });
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

    test("single-cell edit uses descriptor-aware equality", () => {
        const result = extractEdits(
            { model: row("row-1"), prop: "note", val: "1" },
            new Set(["note"]),
            descriptorByKey,
            new Map([["row-1", { note: "001" }]]),
        );
        expect(result.updates).toEqual([{ rowId: "row-1", field: "note", value: "1" }]);
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

describe("parsePastedText (Excel buffer)", () => {
    test("таблица разбивается по табам и переводам строк", () => {
        expect(parsePastedText("1\t2\n3\t4")).toEqual([
            ["1", "2"],
            ["3", "4"],
        ]);
    });

    test("хвостовой перевод строки не добавляет пустую строку (ловушка 4)", () => {
        // Excel always terminates the last row: without the trim the empty
        // tail row is pasted and overwrites the cell below the selection.
        expect(parsePastedText("1\t2\n3\t4\n")).toEqual([
            ["1", "2"],
            ["3", "4"],
        ]);
    });

    test("Windows и старый Mac переводы строк", () => {
        expect(parsePastedText("1\t2\r\n3\t4\r\n")).toEqual([
            ["1", "2"],
            ["3", "4"],
        ]);
        expect(parsePastedText("1\t2\r3\t4")).toEqual([
            ["1", "2"],
            ["3", "4"],
        ]);
    });

    test("хвостовые пустые колонки обрезаются, внутренние — нет", () => {
        expect(parsePastedText("1\t\t\n2\t5\t\n")).toEqual([
            ["1", ""],
            ["2", "5"],
        ]);
    });

    test("пробельная строка в конце считается пустой", () => {
        expect(parsePastedText("1\n   \n\t\n")).toEqual([["1"]]);
    });

    test("пустой и пробельный буфер дают пустой результат", () => {
        expect(parsePastedText("")).toEqual([]);
        expect(parsePastedText("\n\n")).toEqual([]);
    });
});

describe("pasteTargetRange (выделение после вставки)", () => {
    const bounds = { rows: 100, columns: 10 };

    test("одиночная ячейка растягивается на весь буфер", () => {
        const result = pasteTargetRange(
            { x: 2, y: 5, x1: 2, y1: 5 },
            { height: 2, width: 3 },
            bounds,
        );
        expect(result).toEqual({
            range: { x: 2, y: 5, x1: 4, y1: 6 },
            truncated: false,
        });
    });

    test("буфер обрезается границей таблицы", () => {
        const result = pasteTargetRange(
            { x: 8, y: 98, x1: 8, y1: 98 },
            { height: 3, width: 3 },
            bounds,
        );
        expect(result).toEqual({
            range: { x: 8, y: 98, x1: 9, y1: 99 },
            truncated: false,
        });
    });

    test("выделенный диапазон остаётся целью вставки", () => {
        const selection = { x: 1, y: 1, x1: 2, y1: 2 };
        const result = pasteTargetRange(selection, { height: 2, width: 2 }, bounds);
        expect(result).toEqual({ range: selection, truncated: false });
    });

    test("больший буфер обрезается выделением и сообщает об этом", () => {
        const selection = { x: 1, y: 1, x1: 2, y1: 2 };
        const result = pasteTargetRange(selection, { height: 3, width: 4 }, bounds);
        expect(result.range).toEqual(selection);
        expect(result.truncated).toBe(true);
    });

    test("диапазон не расширяется меньшим буфером", () => {
        const selection = { x: 1, y: 1, x1: 2, y1: 2 };
        const result = pasteTargetRange(selection, { height: 1, width: 1 }, bounds);
        expect(result).toEqual({ range: selection, truncated: false });
    });
});

type ClipboardStub = {
    writeText?: (text: string) => Promise<void>;
    readText?: () => Promise<string>;
};

// test-setup.ts installs happy-dom's navigator/document; own-property stubs
// shadow its clipboard and execCommand and are removed again after each test.
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const execCommandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");

function installClipboard(stub: ClipboardStub | undefined): void {
    Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        writable: true,
        value: stub,
    });
}

function installExecCommand(impl: (command: string) => boolean): void {
    Object.defineProperty(document, "execCommand", {
        configurable: true,
        writable: true,
        value: impl,
    });
}

function restoreGlobal(
    target: object,
    key: string,
    descriptor: PropertyDescriptor | undefined,
): void {
    if (descriptor) Object.defineProperty(target, key, descriptor);
    else Reflect.deleteProperty(target, key);
}

afterEach(() => {
    restoreGlobal(navigator, "clipboard", clipboardDescriptor);
    restoreGlobal(document, "execCommand", execCommandDescriptor);
    document.body.innerHTML = "";
});

describe("copyToClipboard", () => {
    test("primary clipboard write succeeds without a fallback textarea", async () => {
        const writeText = mock((_text: string) => Promise.resolve());
        const execCommand = mock((_command: string) => true);
        installClipboard({ writeText });
        installExecCommand(execCommand);

        expect(await copyToClipboard("1\t2\n3\t4")).toBe(true);
        expect(writeText).toHaveBeenCalledTimes(1);
        expect(writeText).toHaveBeenCalledWith("1\t2\n3\t4");
        expect(execCommand).not.toHaveBeenCalled();
        expect(document.querySelectorAll("textarea")).toHaveLength(0);
    });

    test("primary write fails, fallback succeeds and removes the textarea", async () => {
        const writeText = mock((_text: string) => Promise.reject(new Error("denied")));
        let fallback: { value: string; position: string; opacity: string } | undefined;
        const execCommand = mock((command: string) => {
            const textarea = document.querySelector("textarea") as HTMLTextAreaElement | null;
            fallback = {
                value: textarea?.value ?? "",
                position: textarea?.style.position ?? "",
                opacity: textarea?.style.opacity ?? "",
            };
            return command === "copy";
        });
        installClipboard({ writeText });
        installExecCommand(execCommand);

        expect(await copyToClipboard("fallback text")).toBe(true);
        expect(execCommand).toHaveBeenCalledWith("copy");
        expect(fallback).toEqual({ value: "fallback text", position: "fixed", opacity: "0" });
        expect(document.querySelectorAll("textarea")).toHaveLength(0);
    });

    test("both writes fail → false and no leftover textarea", async () => {
        const writeText = mock((_text: string) => Promise.reject(new Error("denied")));
        const execCommand = mock((_command: string) => {
            throw new Error("execCommand unavailable");
        });
        installClipboard({ writeText });
        installExecCommand(execCommand);

        expect(await copyToClipboard("x")).toBe(false);
        expect(document.querySelectorAll("textarea")).toHaveLength(0);
    });

    test("a thrown execCommand is cleaned up in finally", async () => {
        const writeText = mock((_text: string) => Promise.reject(new Error("denied")));
        let presentDuringAttempt = false;
        installClipboard({ writeText });
        installExecCommand(() => {
            presentDuringAttempt = document.querySelectorAll("textarea").length === 1;
            throw new Error("execCommand exploded");
        });

        expect(await copyToClipboard("hidden")).toBe(false);
        expect(presentDuringAttempt).toBe(true);
        expect(document.querySelectorAll("textarea")).toHaveLength(0);
        expect(document.body.innerHTML).not.toContain("textarea");
    });
});

describe("readFromClipboard", () => {
    test("successful read returns the text", async () => {
        installClipboard({ readText: () => Promise.resolve("a\tb") });
        expect(await readFromClipboard()).toBe("a\tb");
    });

    test("failed read → null", async () => {
        installClipboard({ readText: () => Promise.reject(new Error("permission denied")) });
        expect(await readFromClipboard()).toBeNull();
    });

    test("missing clipboard API → null", async () => {
        installClipboard(undefined);
        expect(await readFromClipboard()).toBeNull();
    });

    test("empty read → null", async () => {
        installClipboard({ readText: () => Promise.resolve("") });
        expect(await readFromClipboard()).toBeNull();
    });
});
