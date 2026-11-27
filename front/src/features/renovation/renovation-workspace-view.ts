import type {
    RenovationDiffRow,
    RenovationPlanRow,
    RenovationSortColumn,
} from "@/shared/api/renovation";
import type { ColumnFilter } from "@/shared/ui/column-filter-facets";

import type { RenovationGridRow } from "./renovation-grid";
import { RENOVATION_PLAN_ROWS_TABLE } from "./renovation-grid-constants";

/**
 * Pure view-model helpers of the Renovation workspace.
 *
 * They used to live at the top of the 2k-line workspace component. Keeping them
 * here makes the identity/validation rules testable without rendering React.
 */

/** Identity of the remote row scope: every server-side filter changes it. */
export function renovationGridScopeKey(params: {
    planId: string | null;
    asset: string | null;
    filters: ColumnFilter[];
    sorting: RenovationSortColumn[];
}): string {
    return JSON.stringify([
        params.planId ?? "working",
        params.asset ?? "",
        params.filters,
        params.sorting,
    ]);
}

/**
 * The column a plan-query 422 names, if any. The Russian text is the only
 * signal: the backend does not return a structured column field.
 */
export function validationColumn(message: string): string | null {
    return message.match(/столб(?:ец|ца|цу|цом|це)\s+«([^»]+)»/i)?.[1] ?? null;
}

/** A server plan row as the grid renders it, with diff state stamped on load. */
export function toRenovationGridRow(
    row: RenovationPlanRow,
    diffByRowId: ReadonlyMap<string, RenovationDiffRow>,
): RenovationGridRow {
    const diffRow = diffByRowId.get(row.id);
    return {
        ...row.values,
        work_or_repair_code: row.renovationId,
        id: row.id,
        position: row.position,
        revision: row.revision,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        updatedBy: row.updatedBy,
        __id: row.id,
        __position: row.position,
        __assetId: row.assetId,
        __assetName: row.assetName ?? "",
        __renovationId: row.renovationId,
        __rowState: diffRow ? (diffRow.isNew ? "new" : "changed") : "unchanged",
        __changedFields: diffRow?.cells.map((cell) => cell.field) ?? [],
        __missingComponents: row.missingComponents,
        __invalidFields: row.invalidFields ?? [],
    };
}

/** Locale-formatted version timestamp; an unparsable value stays untouched. */
export function formatVersionStamp(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}

export const VERSION_STATE_LABELS: Record<string, string> = {
    working: "рабочая",
    pending_approval: "на согласовании",
    archived: "утверждённая",
};

/** Canonical responsibility-block names, ordered as the form shows them. */
export const BLOCK_LABELS: Record<string, string> = {
    tech: "1) Техническая дирекция",
    ksir: "2) Дирекция КСиР",
    mto: "3) Дирекция МТО",
};

export const BLOCK_ORDER = ["tech", "ksir", "mto"];

/**
 * Invert accumulated `table/rowId/field` remote values into one field patch per
 * row id.
 *
 * The previous consumer scanned every loaded row × every mergeable field for
 * each `cell:changed` frame (O(loaded × fields)). This walks only the received
 * keys instead (O(values)), then the caller resolves them through the loaded
 * window's keyed mirror. All values are still returned, not just the newest
 * ones: a row loaded after a change must receive the pending value on the next
 * frame, exactly as with the full scan. Keys of other tables (dictionary cells
 * share the same state) and non-mergeable fields are ignored.
 */
export function remoteCellPatchesByRow(
    values: ReadonlyMap<string, unknown>,
    mergeableFieldKeys: ReadonlySet<string>,
    table: string = RENOVATION_PLAN_ROWS_TABLE,
): Map<string, Record<string, unknown>> {
    const byRow = new Map<string, Record<string, unknown>>();
    const prefix = `${table}/`;
    for (const [key, value] of values) {
        if (!key.startsWith(prefix) || value === undefined) continue;
        const parts = key.slice(prefix.length).split("/");
        if (parts.length !== 2) continue;
        const [rowId, field] = parts as [string, string];
        if (!rowId || !mergeableFieldKeys.has(field)) continue;
        let fields = byRow.get(rowId);
        if (!fields) {
            fields = {};
            byRow.set(rowId, fields);
        }
        fields[field] = value;
        // The work id drives the red «incomplete» tint. The old full scan
        // derived these two companion fields from the remote value itself;
        // keep that derivation here so a remote edit refreshes them too.
        if (field === "work_or_repair_code") {
            fields.__renovationId = value == null ? null : String(value);
            fields.__missingComponents = String(value).includes("<NULL>") ? ["missing"] : [];
        }
    }
    return byRow;
}
