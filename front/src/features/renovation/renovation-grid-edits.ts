import type { BeforeSaveDataDetails } from "@revolist/react-datagrid";

import type { CellUpdate } from "@/shared/api/raw-materials";
import type { RenovationField } from "@/shared/api/renovation";
import type { ColumnDescriptor } from "@/shared/grid/grid-config";
import { cellValuesEqual, deduplicateUpdates, toCellValue } from "@/shared/grid/grid-edit";

/**
 * The Renovation plan's edit-event adapter.
 *
 * RevoGrid 4.32 attaches a `data` snapshot to plain single-cell edits too, so a
 * range save is recognised by its keyed patch AND the absence of `prop`. The
 * plan grid uses its own `__id` row identity, so this cannot use the shared
 * grid-edit extractor verbatim; the shape check and value conversion are shared.
 */
export type RenovationEditRow = {
    __id?: string;
    __rowState?: string;
    [field: string]: unknown;
};

export type RenovationEditContext = {
    editableFieldKeys: ReadonlySet<string>;
    fieldByDbName: ReadonlyMap<string, RenovationField>;
    previousValues: ReadonlyMap<string, Readonly<Record<string, unknown>>>;
};

/** Map the form's cell type onto the shared equality checker's kind. */
export function renovationCellKind(field: RenovationField | undefined): ColumnDescriptor["kind"] {
    switch (field?.cellType) {
        case "number":
            return "number";
        case "date":
            return "date";
        case "boolean":
            return "boolean";
        case "datetime":
            return "datetime";
        default:
            return "text";
    }
}

export function extractRenovationEdits(
    detail: BeforeSaveDataDetails | Record<string, unknown>,
    context: RenovationEditContext,
): { updates: CellUpdate[] } {
    const updates: CellUpdate[] = [];
    const isRangeSave =
        "data" in detail &&
        !Array.isArray((detail as { data?: unknown }).data) &&
        (detail as { prop?: unknown }).prop === undefined;

    const pushUpdate = (row: RenovationEditRow, field: string, value: unknown): void => {
        if (!context.editableFieldKeys.has(field)) return;
        if (!row.__id || row.__rowState === "deleted") return;
        const meta = context.fieldByDbName.get(field);
        const known = context.previousValues.get(row.__id);
        if (known && meta && cellValuesEqual(renovationCellKind(meta), known[field], value)) return;
        updates.push({ rowId: row.__id, field, value: toCellValue(value) });
    };

    if (isRangeSave) {
        const models = (detail as { models?: RenovationEditRow[] }).models ?? [];
        for (const [rowIndex, patch] of Object.entries(
            detail.data as Record<string, Record<string, unknown>>,
        )) {
            const previous = models[Number(rowIndex)] as RenovationEditRow | undefined;
            // A range patch is partial: it must never become the row source.
            const row = (previous ?? patch) as RenovationEditRow;
            for (const [field, value] of Object.entries(patch)) pushUpdate(row, field, value);
        }
    } else {
        pushUpdate(detail.model as RenovationEditRow, String(detail.prop), detail.val);
    }

    return { updates: deduplicateUpdates(updates) };
}
