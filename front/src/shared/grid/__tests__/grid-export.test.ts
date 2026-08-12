import { describe, expect, test } from "bun:test";

import { exportColumnsFromGrid, exportFileName } from "@/shared/grid/grid-config";

describe("exportFileName", () => {
    const now = new Date(2026, 7, 13, 1, 12, 5);

    test("движение: месяц прописью и дата ДД.ММ.ГГГГ, ЧЧ.ММ.СС", () => {
        expect(exportFileName("materialMovement", "2026-08-01", now)).toBe(
            "Движение сырья — август (13.08.2026, 01.12.05).xlsx",
        );
    });

    test("справочники: подпись секции без месяца", () => {
        expect(exportFileName("rawMaterialPrices", null, now)).toBe(
            "Цены (13.08.2026, 01.12.05).xlsx",
        );
    });
});

describe("exportColumnsFromGrid", () => {
    test("служебные столбцы попадают в экспорт при включённом показе", () => {
        const columns = exportColumnsFromGrid("materialMovement", true, true, []);
        const keys = columns.map((column) => column.key);
        expect(keys).toContain("createdAt");
        expect(keys).toContain("updatedAt");
        expect(keys).toContain("updatedBy");
    });

    test("служебные столбцы исключаются, если показ выключен", () => {
        const columns = exportColumnsFromGrid("materialMovement", true, false, []);
        const keys = columns.map((column) => column.key);
        expect(keys).not.toContain("createdAt");
        expect(keys).not.toContain("updatedAt");
        expect(keys).not.toContain("updatedBy");
    });

    test("числовые колонки не получают формат с десятичным разделителем", () => {
        const columns = exportColumnsFromGrid("materialMovement", true, false, []);
        expect(columns.every((column) => column.numberFormat === null)).toBe(true);
    });
});
