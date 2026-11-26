import { describe, expect, test } from "bun:test";

import type { RenovationField, RenovationFieldRule } from "@/shared/api/renovation";

import {
    checkRenovationFieldRules,
    normalizeRenovationValue,
} from "../renovation-grid-value-normalization";

const field = (overrides: Partial<RenovationField> = {}): RenovationField => ({
    dbName: "col_x",
    label: "Проверка",
    cellType: "text",
    isCalculated: false,
    isReadOnly: false,
    position: 1,
    ordinal: 2,
    ...overrides,
});

// Keyed by field.dbName, exactly like the component's `catalogLabelsByField`.
const catalogs = new Map<string, readonly string[]>([["col_x", ["", "Собственные силы", "ОКР"]]]);

describe("normalizeRenovationValue", () => {
    test("number: empty input is a legal null and locale text parses fully", () => {
        const numeric = field({ cellType: "number" });
        for (const empty of [null, undefined, "", "   "]) {
            expect(normalizeRenovationValue(empty, numeric, catalogs)).toEqual({
                ok: true,
                value: null,
            });
        }
        expect(normalizeRenovationValue("1 234,50", numeric, catalogs)).toEqual({
            ok: true,
            value: 1234.5,
        });
        expect(normalizeRenovationValue("-3.5", numeric, catalogs)).toEqual({
            ok: true,
            value: -3.5,
        });
    });

    test("number: a partial numeric prefix is rejected with the field label", () => {
        const result = normalizeRenovationValue("123abc", field({ cellType: "number" }), catalogs);
        expect(result).toEqual({ ok: false, message: "Введите число в ячейку «Проверка»." });
    });

    test("date: dotted and ISO dates normalize, invalid calendar values fail", () => {
        const date = field({ cellType: "date" });
        expect(normalizeRenovationValue("05.11.2003", date, catalogs)).toEqual({
            ok: true,
            value: "2003-11-05",
        });
        expect(normalizeRenovationValue("2024-02-29", date, catalogs)).toEqual({
            ok: true,
            value: "2024-02-29",
        });
        expect(normalizeRenovationValue(null, date, catalogs)).toEqual({ ok: true, value: null });
        const invalid = normalizeRenovationValue("not-a-date", date, catalogs);
        expect(invalid.ok).toBe(false);
        if (!invalid.ok) expect(invalid.message).toContain("«Проверка»");
        expect(normalizeRenovationValue("32.13.2003", date, catalogs).ok).toBe(false);
    });

    test("boolean: Yes/No/1/0, the Russian layout and empty all map deterministically", () => {
        const bool = field({ cellType: "boolean" });
        for (const yes of ["да", "ДА", "true", "1"]) {
            expect(normalizeRenovationValue(yes, bool, catalogs)).toEqual({
                ok: true,
                value: true,
            });
        }
        for (const no of ["нет", "НЕТ", "false", "0"]) {
            expect(normalizeRenovationValue(no, bool, catalogs)).toEqual({
                ok: true,
                value: false,
            });
        }
        expect(normalizeRenovationValue("", bool, catalogs)).toEqual({ ok: true, value: null });
        expect(normalizeRenovationValue("maybe", bool, catalogs)).toEqual({
            ok: false,
            message: "Выберите «Да» или «Нет» в ячейке «Проверка».",
        });
    });

    test("reference: a catalog label is returned in its canonical spelling", () => {
        const reference = field({ cellType: "reference", catalog: "ref" });
        expect(normalizeRenovationValue("  Собственные силы ", reference, catalogs)).toEqual({
            ok: true,
            value: "Собственные силы",
        });
        expect(normalizeRenovationValue("", reference, catalogs)).toEqual({
            ok: true,
            value: "",
        });
        const unknown = normalizeRenovationValue("Other", reference, catalogs);
        expect(unknown.ok).toBe(false);
        if (!unknown.ok) expect(unknown.message).toContain("отсутствует в справочнике");
    });

    test("reference: lookup folds case without depending on locale-specific ICU data", () => {
        // ASCII labels keep the case-fold assertion deterministic across ICU
        // builds while still exercising the case-insensitive lookup itself.
        const latinCatalogs = new Map<string, readonly string[]>([["col_x", ["", "Alpha"]]]);
        const reference = field({ cellType: "reference", catalog: "latin" });
        expect(normalizeRenovationValue(" alpha ", reference, latinCatalogs)).toEqual({
            ok: true,
            value: "Alpha",
        });
    });

    test("reference: a missing catalog rejects everything but an empty value", () => {
        const reference = field({ cellType: "reference", catalog: "missing" });
        expect(normalizeRenovationValue("X", reference, catalogs).ok).toBe(false);
        expect(normalizeRenovationValue("  ", reference, catalogs)).toEqual({
            ok: true,
            value: "",
        });
    });

    test("text: trims and turns nullish into null, without touching the catalog", () => {
        expect(normalizeRenovationValue("  value  ", field(), catalogs)).toEqual({
            ok: true,
            value: "value",
        });
        expect(normalizeRenovationValue(undefined, field(), catalogs)).toEqual({
            ok: true,
            value: null,
        });
    });
});

describe("checkRenovationFieldRules", () => {
    const rule = (overrides: Partial<RenovationFieldRule>): RenovationFieldRule => ({
        kind: "unknown",
        ...overrides,
    });

    test("empty values skip every rule", () => {
        const checked = field({
            rules: [rule({ kind: "minimum", value: 0 }), rule({ kind: "integer" })],
        });
        expect(checkRenovationFieldRules(checked, "", {})).toBeNull();
        expect(checkRenovationFieldRules(checked, null, {})).toBeNull();
    });

    test("minimum, integer and percentage produce their own messages", () => {
        expect(
            checkRenovationFieldRules(
                field({ rules: [rule({ kind: "minimum", value: 0 })] }),
                -1,
                {},
            ),
        ).toBe("Значение в ячейке «Проверка» не может быть отрицательным.");
        expect(
            checkRenovationFieldRules(field({ rules: [rule({ kind: "integer" })] }), 1.5, {}),
        ).toBe("Значение в ячейке «Проверка» должно быть целым числом.");
        expect(
            checkRenovationFieldRules(field({ rules: [rule({ kind: "percentage" })] }), 101, {}),
        ).toBe("Значение в ячейке «Проверка» должно быть от 0 до 100%.");
        expect(
            checkRenovationFieldRules(
                field({ rules: [rule({ kind: "percentage", minimum: 5, maximum: 10 })] }),
                11,
                {},
            ),
        ).toBe("Значение в ячейке «Проверка» должно быть от 5 до 10%.");
    });

    test("allowed_values compares the trimmed text against the whitelist", () => {
        const checked = field({ rules: [rule({ kind: "allowed_values", values: ["A", "Б"] })] });
        expect(checkRenovationFieldRules(checked, " A ", {})).toBeNull();
        expect(checkRenovationFieldRules(checked, "C", {})).toBe(
            "Значение в ячейке «Проверка» не входит в список допустимых.",
        );
    });

    test("field_compare gte skips a missing counterpart and compares ISO dates", () => {
        const checked = field({
            rules: [rule({ kind: "field_compare", field: "start", operator: "gte" })],
        });
        expect(checkRenovationFieldRules(checked, "2024-01-02", {})).toBeNull();
        expect(checkRenovationFieldRules(checked, "2024-01-02", { start: null })).toBeNull();
        expect(checkRenovationFieldRules(checked, "2024-01-01", { start: "2024-01-02" })).toBe(
            "Значение в ячейке «Проверка» не должно быть меньше «2024-01-02».",
        );
        expect(
            checkRenovationFieldRules(checked, "2024-01-03", { start: "2024-01-02" }),
        ).toBeNull();
    });

    test("unknown rule kinds are ignored and the first violation wins", () => {
        const checked = field({
            rules: [
                rule({ kind: "calendar_year" }),
                rule({ kind: "integer" }),
                rule({ kind: "minimum", value: 0 }),
            ],
        });
        expect(checkRenovationFieldRules(checked, "1", {})).toBeNull();
        expect(checkRenovationFieldRules(field({ rules: [] }), -5, {})).toBeNull();
        expect(checkRenovationFieldRules(checked, "-1.2", {})).toBe(
            "Значение в ячейке «Проверка» должно быть целым числом.",
        );
    });
});
