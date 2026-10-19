import { requestJson, requestWrite } from "@/shared/api/api-error";
import { downloadTableExport, tableExportJobFileUrl } from "@/shared/api/table-export";
import type { ExportJobDescriptor } from "@/shared/api/table-export";
import { getCollabSessionId } from "@/shared/collab/ws-client";
import type { ExportColumn } from "@/shared/grid/grid-config";

/**
 * The «КППК» dictionaries of the source-copy phase: «Продуктовые позиции»
 * (a product with its ДЗО set), «Сырьевые позиции» (the raw list) and «ДЗО».
 * The «Периоды» dictionary has no UI in this phase — no tab, no client calls.
 * Every mutation is admin-only server-side; reads require project access.
 * Undo/redo run through the shared collaboration journal: every mutation
 * carries the page-lifetime session id, and the history is scoped per tab.
 */

export type KppkSectionId = "kppkPositions" | "kppkRaws" | "kppkOrgs";

/**
 * The journal/room section of a «Реализация» slice: Бизнес-план and Оперплан
 * share the plan copy (`kppkRealizationPlan`), Факт reads the fact copy
 * (`kppkRealizationFact`). The undo scope and the collaboration room are
 * named by them.
 */
export type KppkRealizationSectionId = "kppkRealizationPlan" | "kppkRealizationFact";
export type KppkJournalSectionId = KppkSectionId | KppkRealizationSectionId;

/** The «Реализация» slices (Таблицы.xlsx sheet «Реализация»). */
export type KppkRealizationSlice = "bp" | "op" | "fact";

/** The banner period of the «Реализация» view (fixed server-side for now). */
export type KppkRealizationPeriod = {
    id: string;
    monthName: string;
    yearNum: number;
    repDate: string | null;
};

/**
 * One row of a «Реализация» slice. The slice leaves its four columns filled;
 * the field names are the camelCase DB attributes (the PATCH field value).
 *
 * Column slots per the template: Продукция | Рынок сбыта | Выкуп{Объем,
 * Цена FCA} | Реализация{Объем, Цена FCA}.
 */
export type KppkRealizationRow = {
    id: string;
    product: string;
    region: string;
    buyoutBpPlan: number | null;
    fcaPriceBpPlan: number | null;
    monthBpPlan: number | null;
    fcaPriceMarketBpPlan: number | null;
    buyoutMaxPlan: number | null;
    fcaPriceMaxPlan: number | null;
    monthMaxPlan: number | null;
    fcaPriceMarketMaxPlan: number | null;
    buyoutFact: number | null;
    fcaPriceFact: number | null;
    fact: number | null;
    fcaPriceMarketFact: number | null;
};

export type KppkRealizationView = {
    period: KppkRealizationPeriod;
    markets: string[];
    rows: KppkRealizationRow[];
};

export type KppkProductPositionRow = {
    id: string;
    /** Source dictionary number («№») — editable and unique; `null` when unnumbered. */
    productNum: number | null;
    name: string;
    orgNames: string[];
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export type KppkRawRow = {
    id: string;
    /** Source dictionary number («№») — editable and unique; `null` when unnumbered. */
    rawNum: number | null;
    name: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export type KppkOrgRow = {
    id: string;
    code: string;
    name: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

/** A row payload returned by every mutation (the grid refetches anyway). */
export type KppkApplied<T> = { row: T };

// ─── Продуктовые позиции (a product with its ДЗО set) ──────────────────────────────────

export async function getProductPositions(): Promise<KppkProductPositionRow[]> {
    return requestJson("/api/kppk/product-positions");
}

export async function createProductPosition(payload: {
    name: string;
    orgNames: string[];
    productNum: number | null;
}): Promise<KppkApplied<KppkProductPositionRow>> {
    return requestWrite(
        `/api/kppk/product-positions?session_id=${getCollabSessionId()}`,
        "POST",
        payload,
    );
}

export async function updateProductPositionCell(
    productId: string,
    field: string,
    value: unknown,
): Promise<KppkApplied<KppkProductPositionRow>> {
    return requestWrite(
        `/api/kppk/product-positions/${productId}?session_id=${getCollabSessionId()}`,
        "PATCH",
        { field, value },
    );
}

export async function deleteProductPosition(productId: string): Promise<void> {
    await requestWrite(
        `/api/kppk/product-positions/${productId}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

// ─── Сырьевые позиции ─────────────────────────────────────────────────────

export async function getRawRows(): Promise<KppkRawRow[]> {
    return requestJson("/api/kppk/raws");
}

export async function createRaw(payload: {
    name: string;
    rawNum: number | null;
}): Promise<KppkApplied<KppkRawRow>> {
    return requestWrite(`/api/kppk/raws?session_id=${getCollabSessionId()}`, "POST", payload);
}

export async function updateRawCell(
    rawId: string,
    field: string,
    value: unknown,
): Promise<KppkApplied<KppkRawRow>> {
    return requestWrite(`/api/kppk/raws/${rawId}?session_id=${getCollabSessionId()}`, "PATCH", {
        field,
        value,
    });
}

export async function deleteRaw(rawId: string): Promise<void> {
    await requestWrite(`/api/kppk/raws/${rawId}?session_id=${getCollabSessionId()}`, "DELETE");
}

// ─── ДЗО ──────────────────────────────────────────────────────────────────

export async function getOrgRows(): Promise<KppkOrgRow[]> {
    return requestJson("/api/kppk/orgs");
}

export async function createOrg(payload: {
    code: string;
    name: string;
}): Promise<KppkApplied<KppkOrgRow>> {
    return requestWrite(`/api/kppk/orgs?session_id=${getCollabSessionId()}`, "POST", payload);
}

export async function updateOrgCell(
    orgId: string,
    field: string,
    value: unknown,
): Promise<KppkApplied<KppkOrgRow>> {
    return requestWrite(`/api/kppk/orgs/${orgId}?session_id=${getCollabSessionId()}`, "PATCH", {
        field,
        value,
    });
}

export async function deleteOrg(orgId: string): Promise<void> {
    await requestWrite(`/api/kppk/orgs/${orgId}?session_id=${getCollabSessionId()}`, "DELETE");
}

// ─── «Реализация» (Таблицы.xlsx sheet «Реализация») ───────────────────────

/** The slice's editable fields (camelCase DB attributes of its columns). */
export const KPPK_REALIZATION_SLICE_FIELDS: Record<KppkRealizationSlice, readonly string[]> = {
    bp: ["buyoutBpPlan", "fcaPriceBpPlan", "monthBpPlan", "fcaPriceMarketBpPlan"],
    op: ["buyoutMaxPlan", "fcaPriceMaxPlan", "monthMaxPlan", "fcaPriceMarketMaxPlan"],
    fact: ["buyoutFact", "fcaPriceFact", "fact", "fcaPriceMarketFact"],
};

/**
 * The «Реализация» view of one period. Without the period arguments the
 * server picks the data's newest period (the current behavior); with
 * `month` (1-based) + `year` the requested period is used.
 */
export async function getRealizationView(
    orgId: string,
    slice: KppkRealizationSlice,
    month?: string | null,
    year?: string | null,
): Promise<KppkRealizationView> {
    const params = new URLSearchParams({ orgId, slice });
    if (month && year) {
        params.set("month", month);
        params.set("year", year);
    }
    return requestJson(`/api/kppk/realization?${params.toString()}`);
}

export async function updateRealizationCell(
    rowId: string,
    slice: KppkRealizationSlice,
    field: string,
    value: unknown,
): Promise<KppkApplied<KppkRealizationRow>> {
    return requestWrite(
        `/api/kppk/realization/${rowId}?session_id=${getCollabSessionId()}&slice=${slice}`,
        "PATCH",
        { field, value },
    );
}

// ─── Undo/redo (the shared collaboration journal) ─────────────────────────

export type KppkUndoConflict = {
    rowId: string;
    field: string;
    user: string;
    actionId: number;
    reason: "conflict" | "locked";
};

export type KppkUndoResult = {
    applied: boolean;
    reason: "applied" | "empty" | "conflict" | "locked" | null;
    conflicts: KppkUndoConflict[];
    section: string | null;
    reportMonth: string | null;
    rowId: string | null;
    field: string | null;
    value: unknown;
    revision: number | null;
};

export type KppkUndoState = {
    canUndo: boolean;
    canRedo: boolean;
};

export async function undoKppkCells(
    skipActionId: number | null,
    section: KppkJournalSectionId,
): Promise<KppkUndoResult> {
    return requestWrite(`/api/kppk/undo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function redoKppkCells(
    skipActionId: number | null,
    section: KppkJournalSectionId,
): Promise<KppkUndoResult> {
    return requestWrite(`/api/kppk/redo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function getKppkUndoState(section: KppkJournalSectionId): Promise<KppkUndoState> {
    const params = new URLSearchParams({ section, session_id: getCollabSessionId() });
    return requestJson(`/api/kppk/undo-state?${params.toString()}`);
}

// ─── Excel export ─────────────────────────────────────────────────────────

export type KppkExportViewRequest = {
    section: KppkSectionId;
    headerPinned: boolean;
    columns: ExportColumn[];
    rowIds: string[] | null;
    fileName: string;
};

export type KppkExportJobDownload = {
    job: ExportJobDescriptor;
    fileUrl: string;
};

/** Download a КППК dictionary export — inline, or as a background job past the inline row limit. */
export async function exportKppkTable(
    request: KppkExportViewRequest,
): Promise<Blob | KppkExportJobDownload> {
    return downloadTableExport("/api/kppk/export", request, (job, base) => ({
        job,
        fileUrl: tableExportJobFileUrl(base, job.id),
    }));
}
