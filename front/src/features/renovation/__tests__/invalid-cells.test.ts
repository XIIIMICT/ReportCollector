import { describe, expect, test } from "bun:test";

import type { RenovationField } from "@/shared/api/renovation";

import { breaksValueRules } from "../renovation-rules";

/**
 * The red tint of a CALCULATED cell must follow its recomputed value, not the
 * last server read: a live recalculation carries only numbers, so the grid
 * derives the rule check itself.
 */
const duration: RenovationField = {
    dbName: "work_duration_days",
    label: "Продолжительность, дни",
    cellType: "number",
    ordinal: 26,
    position: 26,
    isCalculated: true,
    isReadOnly: true,
    rules: [{ kind: "minimum", value: 0 }, { kind: "integer" }],
};

describe("breaksValueRules", () => {
    test("a negative duration breaks the minimum rule", () => {
        expect(breaksValueRules(duration, -5)).toBe(true);
    });

    test("a zero or positive duration is valid", () => {
        expect(breaksValueRules(duration, 0)).toBe(false);
        expect(breaksValueRules(duration, 12)).toBe(false);
    });

    test("an empty cell is never a violation", () => {
        expect(breaksValueRules(duration, null)).toBe(false);
        expect(breaksValueRules(duration, "")).toBe(false);
    });

    test("a fractional value breaks the integer rule", () => {
        expect(breaksValueRules(duration, 1.5)).toBe(true);
    });

    test("a percentage outside its bounds is flagged", () => {
        const share: RenovationField = {
            ...duration,
            dbName: "em_contracting_cs_pct",
            label: "Централизованная поставка",
            rules: [{ kind: "percentage", minimum: 0, maximum: 100 }],
        };
        expect(breaksValueRules(share, 120)).toBe(true);
        expect(breaksValueRules(share, 40)).toBe(false);
    });

    test("a field with no rules never flags", () => {
        const { rules: _rules, ...bare } = duration;
        expect(breaksValueRules(bare as RenovationField, -1)).toBe(false);
    });
});
