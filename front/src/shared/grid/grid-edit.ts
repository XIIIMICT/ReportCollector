import type { BeforeSaveDataDetails } from "@revolist/react-datagrid";

import type { CellUpdate } from "@/shared/api/raw-materials";

import type { ColumnDescriptor, GridRow } from "./grid-config";

export type ExtractedEdits = {
    updates: CellUpdate[];
    placeholderPatch: Record<string, unknown>;
};

export function extractEdits(
    detail: BeforeSaveDataDetails | Record<string, unknown>,
    editableKeys: Set<string>,
    descriptorByKey: Map<string, ColumnDescriptor>,
    previousValues: ReadonlyMap<string, Readonly<Record<string, unknown>>> = new Map(),
): ExtractedEdits {
    const placeholderPatch: Record<string, unknown> = {};
    // revo-grid 4.27 attaches a `data` key to plain single-cell edits too —
    // the whole source snapshot as an ARRAY. A range save (our rangeeditapply
    // path) carries a keyed patch object plus the full row models, and has no
    // `prop`; without this check every cell edit silently produced no updates.
    const isRangeSave =
        "data" in detail &&
        !Array.isArray((detail as { data?: unknown }).data) &&
        (detail as { prop?: unknown }).prop === undefined;
    if (isRangeSave) {
        const updates: CellUpdate[] = [];
        const models = (detail as { models?: GridRow[] }).models ?? [];
        for (const [rowIndex, patch] of Object.entries(
            detail.data as Record<string, Record<string, unknown>>,
        )) {
            const previous = models[Number(rowIndex)] as GridRow | undefined;
            // A range save must carry the full row model: without it there is no
            // id/canonicalCells and the edit would be silently dropped. Patch
            // objects are partial, so they must never be used as the row source.
            if (!previous) continue;
            const row = previous;
            if (!row?.id || row.isDeleted) continue;
            const known = previousValues.get(row.id);
            for (const [field, value] of Object.entries(patch)) {
                if (!editableKeys.has(field) || !descriptorByKey.has(field)) continue;
                const descriptor = descriptorByKey.get(field);
                if (known && descriptor && cellValuesEqual(descriptor.kind, known[field], value))
                    continue;
                if (row.isPlaceholder) {
                    placeholderPatch[field] = toCellValue(value);
                    continue;
                }
                updates.push({ rowId: row.id, field, value: toCellValue(value) });
            }
        }
        return { updates: deduplicateUpdates(updates), placeholderPatch };
    }
    const field = String(detail.prop);
    const row = detail.model as GridRow;
    if (!editableKeys.has(field) || !descriptorByKey.has(field) || !row?.id || row.isDeleted) {
        return { updates: [], placeholderPatch };
    }
    if (row.isPlaceholder) {
        placeholderPatch[field] = toCellValue(detail.val);
        return { updates: [], placeholderPatch };
    }
    const descriptor = descriptorByKey.get(field);
    const known = previousValues.get(row.id);
    if (descriptor && known && cellValuesEqual(descriptor.kind, known[field], detail.val)) {
        return { updates: [], placeholderPatch };
    }
    return {
        updates: [{ rowId: row.id, field, value: toCellValue(detail.val) }],
        placeholderPatch,
    };
}

/** Parse a locale-formatted number ('1 234,5' with any Unicode spaces → 1234.5 | null). */
export function parseNumericText(item: unknown): number | null {
    const text = String(item)
        .replace(/[\s\u00a0\u202f]/g, "")
        .replace(",", ".");
    if (!text) return null;
    // Number.parseFloat accepts a numeric prefix (`123abc`). Grid values must
    // be fully numeric before they can participate in equality or normalization.
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return null;
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : null;
}

export function toCellValue(value: unknown): CellUpdate["value"] {
    if (Array.isArray(value)) {
        return value
            .filter((item): item is string => typeof item === "string")
            .map((item) => item.trim())
            .filter(Boolean);
    }
    if (value === null || typeof value === "number" || typeof value === "boolean") {
        return value;
    }
    if (typeof value === "string") return value.trim();
    return value === undefined ? null : String(value);
}

export function cellValuesEqual(
    kind: ColumnDescriptor["kind"],
    known: unknown,
    value: unknown,
): boolean {
    const previous = toCellValue(known);
    const next = toCellValue(value);
    if (previous === next) return true;
    if (Array.isArray(previous) || Array.isArray(next)) {
        return (
            Array.isArray(previous) &&
            Array.isArray(next) &&
            previous.length === next.length &&
            previous.every((item, index) => item === next[index])
        );
    }
    if (kind === "number" || kind === "calculated") {
        if (
            previous === null ||
            next === null ||
            typeof previous === "boolean" ||
            typeof next === "boolean"
        ) {
            return false;
        }
        const left = parseNumericText(previous);
        const right = parseNumericText(next);
        return left !== null && right !== null && left === right;
    }
    if (kind === "boolean") return typeof previous === "boolean" && previous === next;
    if (previous === null || next === null) return false;
    if (kind === "date" || kind === "datetime") {
        const left = typeof previous === "string" ? Date.parse(previous) : NaN;
        const right = typeof next === "string" ? Date.parse(next) : NaN;
        if (Number.isFinite(left) && Number.isFinite(right)) return left === right;
        return false;
    }
    // Text, identifiers, references and select values are intentionally
    // field-aware: "001" and "1" are different labels.
    return String(previous ?? "") === String(next ?? "");
}

export function deduplicateUpdates(updates: CellUpdate[]): CellUpdate[] {
    const result = new Map<string, CellUpdate>();
    for (const update of updates) result.set(`${update.rowId}\u0000${update.field}`, update);
    return [...result.values()];
}
