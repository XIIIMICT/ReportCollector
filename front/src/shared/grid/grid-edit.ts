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
    if ("data" in detail) {
        const updates: CellUpdate[] = [];
        const models = (detail as { models?: GridRow[] }).models ?? [];
        for (const [rowIndex, patch] of Object.entries(
            detail.data as Record<string, Record<string, unknown>>,
        )) {
            const previous = models[Number(rowIndex)] as GridRow | undefined;
            const row = (previous ?? patch) as GridRow;
            if (!row?.id || row.isDeleted) continue;
            const known = previousValues.get(row.id);
            for (const [field, value] of Object.entries(patch)) {
                if (!editableKeys.has(field) || !descriptorByKey.has(field)) continue;
                if (known && sameCellValue(known[field], value)) continue;
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
    return {
        updates: [{ rowId: row.id, field, value: toCellValue(detail.val) }],
        placeholderPatch,
    };
}

function toCellValue(value: unknown): CellUpdate["value"] {
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

function sameCellValue(known: unknown, value: unknown): boolean {
    const previous = toCellValue(known);
    const next = toCellValue(value);
    if (previous === next) return true;
    if (
        previous === null ||
        next === null ||
        typeof previous === "boolean" ||
        typeof next === "boolean" ||
        Array.isArray(previous) ||
        Array.isArray(next)
    ) {
        return false;
    }
    const parse = (item: unknown): number | null => {
        const text = String(item).replace("\u00a0", " ").replace(/\s/g, "").replace(",", ".");
        if (!text) return null;
        const parsed = Number.parseFloat(text);
        return Number.isNaN(parsed) ? null : parsed;
    };
    const left = parse(previous);
    const right = parse(next);
    return left !== null && right !== null && left === right;
}

function deduplicateUpdates(updates: CellUpdate[]): CellUpdate[] {
    const result = new Map<string, CellUpdate>();
    for (const update of updates) result.set(`${update.rowId}\u0000${update.field}`, update);
    return [...result.values()];
}
