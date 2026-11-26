import { describe, expect, test } from "bun:test";

import type { RenovationField, RenovationHeaderCell } from "@/shared/api/renovation";

import {
    buildHeaderChainByProp,
    buildHeaderChainForPosition,
    buildHeaderLevelByPosition,
    type HeaderSpanCell,
} from "../renovation-grid-header-chain";

const headerCell = (overrides: Partial<RenovationHeaderCell> = {}): RenovationHeaderCell => ({
    id: "cell",
    text: "",
    startPosition: 0,
    colSpan: 1,
    rowSpan: 1,
    level: 0,
    excelRange: "A1",
    ...overrides,
});

const planField = (
    position: number,
    responsibilityBlock: string | null = null,
    dbName = `col_${position}`,
): RenovationField => ({
    dbName,
    label: dbName,
    cellType: "text",
    isCalculated: false,
    isReadOnly: false,
    position,
    ordinal: position + 1,
    responsibilityBlock,
});

const span = (
    text: string,
    level: number,
    fromPosition: number,
    toPosition: number,
): HeaderSpanCell => ({ text, level, fromPosition, toPosition });

describe("buildHeaderChainForPosition", () => {
    test("empty metadata has no chain", () => {
        expect(buildHeaderChainForPosition(1, [], [])).toEqual([]);
    });

    test("cells are ordered by level; only adjacent duplicate texts are dropped", () => {
        const cells = [
            headerCell({ id: "leaf", text: " Цех ", startPosition: 1, colSpan: 1, level: 1 }),
            headerCell({ id: "group", text: "Группа", startPosition: 1, colSpan: 1, level: 0 }),
            headerCell({ id: "repeat", text: " Группа ", startPosition: 1, colSpan: 1, level: 3 }),
        ];
        expect(buildHeaderChainForPosition(2, cells, [])).toEqual(["Группа", "Цех", "Группа"]);
    });

    test("a level-0 block header is replaced by its canonical directorate label", () => {
        const cells = [
            headerCell({ id: "block", text: "Блок (раздел 1)", startPosition: 0, colSpan: 2 }),
            headerCell({ id: "leaf", text: "Цех", startPosition: 1, colSpan: 1, level: 1 }),
        ];
        const fields = [planField(1, "read_only"), planField(2, "tech")];
        expect(buildHeaderChainForPosition(2, cells, fields)).toEqual([
            "1) Техническая дирекция",
            "Цех",
        ]);
    });

    test("a read-only-only block keeps its Excel text instead of a canonical label", () => {
        const cells = [headerCell({ text: "Блок (раздел 9)", startPosition: 0, colSpan: 2 })];
        const fields = [planField(1, "read_only"), planField(2, "read_only")];
        expect(buildHeaderChainForPosition(1, cells, fields)).toEqual(["Блок (раздел 9)"]);
    });

    test("blank cells are skipped and a position outside every cell yields no chain", () => {
        const cells = [
            headerCell({ text: "  ", startPosition: 0, colSpan: 1, level: 0 }),
            headerCell({ text: "Цех", startPosition: 2, colSpan: 1, level: 1 }),
        ];
        expect(buildHeaderChainForPosition(1, cells, [])).toEqual([]);
        expect(buildHeaderChainForPosition(99, cells, [])).toEqual([]);
    });
});

describe("buildHeaderChainByProp", () => {
    const cells = [
        span(" 1) Техническая дирекция ", 0, 1, 10),
        span("Цех", 1, 4, 5),
        span("   ", 2, 4, 5),
    ];

    test("the synthetic asset column and a leaf get their covering cells", () => {
        const chains = buildHeaderChainByProp(cells, 1, [planField(4, null, "workshop")]);
        expect(chains.get("__assetName")).toEqual(["1) Техническая дирекция"]);
        expect(chains.get("workshop")).toEqual(["1) Техническая дирекция", "Цех"]);
    });

    test("service columns get their own label and missing positions get no chain", () => {
        const chains = buildHeaderChainByProp(cells, null, [
            planField(4, null, "workshop"),
            { ...planField(0, null, "createdAt"), label: "Время добавления" },
        ]);
        expect(chains.has("__assetName")).toBe(false);
        expect(chains.get("createdAt")).toEqual(["Время добавления"]);
        expect(chains.get("workshop")).toEqual(["1) Техническая дирекция", "Цех"]);
    });

    test("empty overlay metadata yields only the service labels", () => {
        const chains = buildHeaderChainByProp([], 1, [
            { ...planField(0, null, "updatedBy"), label: "Автор последней редакции" },
        ]);
        expect(chains.size).toBe(1);
        expect(chains.get("updatedBy")).toEqual(["Автор последней редакции"]);
        expect(chains.size).toBe(1);
    });
});

describe("buildHeaderLevelByPosition", () => {
    test("empty overlay produces an empty map", () => {
        expect(buildHeaderLevelByPosition([]).size).toBe(0);
    });

    test("the highest level covering a position wins", () => {
        const levels = buildHeaderLevelByPosition([
            span("group", 0, 1, 4),
            span("leaf", 2, 2, 3),
            span("another", 1, 3, 5),
        ]);
        expect(levels.get(1)).toBe(0);
        expect(levels.get(2)).toBe(2);
        expect(levels.get(3)).toBe(1);
        expect(levels.get(4)).toBe(1);
        expect(levels.has(5)).toBe(false);
    });

    test("zero-span cells do not mark any position", () => {
        expect(buildHeaderLevelByPosition([span("none", 7, 3, 3)]).size).toBe(0);
    });
});
