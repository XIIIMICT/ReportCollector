/**
 * Pure value normalization and workbook-rule validation for the renovation
 * plan grid. Split out of `renovation-grid.tsx`: neither helper touches a grid
 * instance, a ref, the DOM or React state — all inputs are values/maps passed
 * in by the caller, so the edit handlers can stay imperative while the rules
 * they apply stay testable.
 */

import { normalizeDate } from "@/features/renovation/renovation-grid-format";
import type { RenovationField } from "@/shared/api/renovation";
import { parseNumericText } from "@/shared/grid/grid-edit";

export type NormalizeResult = { ok: true; value: unknown } | { ok: false; message: string };

/** Catalog options by form field, keyed by `field.dbName` (empty string first). */
export type CatalogLabelsByField = ReadonlyMap<string, readonly string[]>;

/**
 * Coerce a raw editor value into the value committed to the server. A failure
 * carries the Russian message shown in the validation toast; an empty cell is
 * a legal `null` (or `""` for a reference), never an error.
 */
export function normalizeRenovationValue(
    value: unknown,
    field: RenovationField,
    catalogLabelsByField: CatalogLabelsByField,
): NormalizeResult {
    if (field.cellType === "number") {
        if (value === null || value === undefined || String(value).trim() === "") {
            return { ok: true, value: null };
        }
        const parsed = parseNumericText(value);
        if (parsed === null || !Number.isFinite(parsed)) {
            return { ok: false, message: `Введите число в ячейку «${field.label}».` };
        }
        return { ok: true, value: parsed };
    }
    if (field.cellType === "date") {
        if (value === null || value === undefined || String(value).trim() === "") {
            return { ok: true, value: null };
        }
        const iso = normalizeDate(String(value).trim());
        if (!iso) {
            return {
                ok: false,
                message: `Введите дату в ячейку «${field.label}» в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.`,
            };
        }
        return { ok: true, value: iso };
    }
    if (field.cellType === "boolean") {
        const text = String(value).trim().toLocaleLowerCase("ru-RU");
        if (text === "да" || text === "true" || text === "1") return { ok: true, value: true };
        if (text === "нет" || text === "false" || text === "0" || text === "") {
            return { ok: true, value: text === "" ? null : false };
        }
        return { ok: false, message: `Выберите «Да» или «Нет» в ячейке «${field.label}».` };
    }
    if (field.cellType === "reference") {
        const requested = String(value ?? "").trim();
        if (!requested) return { ok: true, value: "" };
        const options = catalogLabelsByField.get(field.dbName) ?? [];
        const byLabel = new Map(
            options
                .filter((option) => Boolean(option))
                .map((option) => [option.toLocaleLowerCase("ru-RU"), option]),
        );
        const canonical = byLabel.get(requested.toLocaleLowerCase("ru-RU"));
        if (!canonical) {
            return {
                ok: false,
                message: `Значение «${requested}» отсутствует в справочнике поля «${field.label}».`,
            };
        }
        return { ok: true, value: canonical };
    }
    return {
        ok: true,
        value: value === null || value === undefined ? null : String(value).trim(),
    };
}

/**
 * Rules from the source workbook that need the whole row. The local ones
 * (minimum/integer/percentage/allowed_values) are also enforced by the backend;
 * checking them here gives immediate feedback. `field_compare` is frontend-only.
 * Returns an error message, or `null` when the value is valid.
 */
export function checkRenovationFieldRules(
    field: RenovationField,
    value: unknown,
    row: Readonly<Record<string, unknown>>,
): string | null {
    if (value === null || value === undefined || String(value).trim() === "") return null;
    const rules = field.rules ?? [];
    for (const rule of rules) {
        if (rule.kind === "minimum" && rule.value !== undefined) {
            const numeric = Number(value);
            if (Number.isFinite(numeric) && numeric < Number(rule.value)) {
                return `Значение в ячейке «${field.label}» не может быть отрицательным.`;
            }
        } else if (rule.kind === "integer") {
            if (!Number.isInteger(Number(value))) {
                return `Значение в ячейке «${field.label}» должно быть целым числом.`;
            }
        } else if (rule.kind === "percentage") {
            const numeric = Number(value);
            const minimum = rule.minimum ?? 0;
            const maximum = rule.maximum ?? 100;
            if (Number.isFinite(numeric) && (numeric < minimum || numeric > maximum)) {
                return `Значение в ячейке «${field.label}» должно быть от ${minimum} до ${maximum}%.`;
            }
        } else if (rule.kind === "allowed_values" && rule.values) {
            if (!rule.values.includes(String(value).trim())) {
                return `Значение в ячейке «${field.label}» не входит в список допустимых.`;
            }
        } else if (rule.kind === "field_compare" && rule.field && rule.operator === "gte") {
            // ISO date strings compare lexicographically; a missing counterpart
            // skips the check.
            const other = row[rule.field];
            const otherText = other === null || other === undefined ? "" : String(other);
            if (otherText && String(value) < otherText) {
                return `Значение в ячейке «${field.label}» не должно быть меньше «${otherText}».`;
            }
        }
    }
    return null;
}
