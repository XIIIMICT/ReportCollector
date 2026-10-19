import type { RenovationField } from "@/shared/api/renovation";

/**
 * Does a field's CURRENT value break one of the form's value-local rules?
 *
 * A calculated cell is never edited directly, so a bad result (e.g. a negative
 * «Продолжительность, дни» when the end date precedes the start) is never
 * rejected by the edit path — it has to be reported after the fact. The backend
 * reports the same list on every read (`rows[].invalidFields`), but a live
 * recalculation carries only the new VALUES: neither the PATCH result nor a
 * `cell:changed` broadcast carries rule metadata. Deriving the flag from the
 * row's current value keeps the red tint in step with the recalculated number
 * on the very next render — for our own edits and for other users' edits.
 *
 * Only the value-local kinds are checked here (the same ones the backend can
 * verify from the value alone); anything needing the whole row stays with the
 * backend's list.
 */
export function breaksValueRules(field: RenovationField, value: unknown): boolean {
    if (value === null || value === undefined || String(value).trim() === "") return false;
    for (const rule of field.rules ?? []) {
        if (rule.kind === "minimum" && rule.value !== undefined) {
            const numeric = Number(value);
            if (Number.isFinite(numeric) && numeric < Number(rule.value)) return true;
        } else if (rule.kind === "integer") {
            if (!Number.isInteger(Number(value))) return true;
        } else if (rule.kind === "percentage") {
            const numeric = Number(value);
            const minimum = rule.minimum ?? 0;
            const maximum = rule.maximum ?? 100;
            if (Number.isFinite(numeric) && (numeric < minimum || numeric > maximum)) return true;
        } else if (rule.kind === "allowed_values" && rule.values) {
            if (!rule.values.includes(String(value).trim())) return true;
        }
    }
    return false;
}
