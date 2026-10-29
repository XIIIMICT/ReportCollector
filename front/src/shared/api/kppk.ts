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

export type KppkSectionId = "kppkPositions" | "kppkRaws" | "kppkOrgs" | "kppkUsers";

export type KppkUserRow = {
    id: string;
    email: string;
    fullname: string;
    isAdmin: boolean;
    isActive: boolean;
    orgNames: string[];
    lastAuthenticatedAt: string | null;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export async function getKppkUsers(): Promise<KppkUserRow[]> {
    return requestJson("/api/kppk/users");
}

export async function createKppkUser(payload: {
    email: string;
    fullname: string;
    isAdmin: boolean;
}): Promise<KppkUserRow> {
    return requestWrite(`/api/kppk/users?session_id=${getCollabSessionId()}`, "POST", payload);
}

export async function updateKppkUserCell(
    id: string,
    field: string,
    value: unknown,
): Promise<KppkUserRow> {
    return requestWrite(`/api/kppk/users/${id}?session_id=${getCollabSessionId()}`, "PATCH", {
        [field]: value,
    });
}

export async function deleteKppkUser(id: string): Promise<void> {
    await requestWrite(`/api/kppk/users/${id}?session_id=${getCollabSessionId()}`, "DELETE");
}

/**
 * The journal/room section of a «Реализация» slice: Бизнес-план and Оперплан
 * share the plan copy (`kppkRealizationPlan`), Факт reads the fact copy
 * (`kppkRealizationFact`). The undo scope and the collaboration room are
 * named by them.
 */
export type KppkRealizationSectionId = "kppkRealizationPlan" | "kppkRealizationFact" | "kppkCost";
export type KppkJournalSectionId = KppkSectionId | KppkRealizationSectionId;

/** The «Реализация» slices (Таблицы.xlsx sheet «Реализация»). */
export type KppkRealizationSlice = "bp" | "op" | "fact";

/** The banner period of the «Реализация» view (fixed server-side for now). */
export type KppkRealizationPeriod = {
    periodDate: string;
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
    /** The «№» of the product in «Продуктовые позиции» (by name); null when the dictionary lacks it. */
    productNum: number | null;
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
    exciseBp: number;
    exciseMaxPlan: number;
    exciseFact: number;
};

export type KppkRealizationView = {
    period: KppkRealizationPeriod;
    markets: string[];
    rows: KppkRealizationRow[];
    /**
     * Product names that carry an excise on THIS asset (the «Продуктовые
     * позиции» flag). Empty = the asset has no excise: the form hides the
     * column entirely. A product missing from the list keeps its cell locked.
     */
    exciseProducts?: string[];
};

export type KppkProductPositionRow = {
    /** The position (the product × ДЗО link) — what every row operation takes. */
    id: string;
    /** The product this position belongs to. */
    productId: string;
    /** Source dictionary number («№») - editable and unique; `null` when unnumbered. */
    productNum: number | null;
    name: string;
    /** The asset of this position. */
    orgName: string;
    /** The product's whole ДЗО set — filled in the grouped view only. */
    orgNames?: string[];
    /** «Заполняемый акциз» of this position. */
    hasExcise: boolean;
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
    /** «Заполняемый акциз»: this ДЗО levies an excise at all. */
    hasExcise: boolean;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

/** A row payload returned by every mutation (the grid refetches anyway). */
/**
 * Mutation result. `row` is `null` when the edit removed the row it echoed back
 * (a grouped «ДЗО» edit may drop the position or leave the product bare).
 */
export type KppkApplied<T> = { row: T | null };

// ─── Продуктовые позиции (a product with its ДЗО set) ──────────────────────────────────

/**
 * «Продуктовые позиции» in one of the tab's two shapes.
 *
 * `grouped` (default) — one row per product with its whole ДЗО set (`orgNames`),
 * the historical look; `positions` — one row per product × ДЗО, where
 * «Заполняемый акциз» is set per pair.
 */
export async function getProductPositions(
    view: "grouped" | "positions" = "grouped",
): Promise<KppkProductPositionRow[]> {
    return requestJson(`/api/kppk/product-positions?view=${view}`);
}

/**
 * Add a «Продуктовые позиции» entry. Two shapes of the same call: the position
 * view sends one `orgName`, the default (grouped) view sends the product's
 * whole `orgNames` set.
 */
export async function createProductPosition(payload: {
    name: string;
    orgName?: string;
    orgNames?: string[];
    hasExcise?: boolean;
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

export async function replaceRawRow(rawId: string, targetId: string | null): Promise<void> {
    await requestWrite(
        `/api/kppk/raws/${rawId}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        {
            targetId,
        },
    );
}

export async function replaceOrgRow(orgId: string, targetId: string | null): Promise<void> {
    await requestWrite(
        `/api/kppk/orgs/${orgId}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        {
            targetId,
        },
    );
}

/**
 * Replace the PRODUCT of a «Продуктовые позиции» row with another one.
 *
 * The row id is a product × ДЗО position, but the replacement works on the
 * product: every position of the replaced product moves to the target.
 */
export async function replaceProductPosition(
    positionId: string,
    targetId: string | null,
): Promise<void> {
    await requestWrite(
        `/api/kppk/product-positions/${positionId}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        {
            targetId,
        },
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
    bp: ["buyoutBpPlan", "fcaPriceBpPlan", "monthBpPlan", "fcaPriceMarketBpPlan", "exciseBp"],
    op: [
        "buyoutMaxPlan",
        "fcaPriceMaxPlan",
        "monthMaxPlan",
        "fcaPriceMarketMaxPlan",
        "exciseMaxPlan",
    ],
    fact: ["buyoutFact", "fcaPriceFact", "fact", "fcaPriceMarketFact", "exciseFact"],
};

export const KPPK_COST_SLICE_FIELDS: Record<KppkRealizationSlice, readonly string[]> = {
    bp: ["fullCostBp", "productionCostBp", "variableCostBp"],
    op: ["fullCostOp", "productionCostOp", "variableCostOp"],
    fact: ["fullCostFact", "productionCostFact", "variableCostFact"],
};

export type KppkCostRow = {
    id: string;
    product: string;
    productNum: number | null;
    fullCostBp: number | null;
    productionCostBp: number | null;
    variableCostBp: number | null;
    fullCostOp: number | null;
    productionCostOp: number | null;
    variableCostOp: number | null;
    fullCostFact: number | null;
    productionCostFact: number | null;
    variableCostFact: number | null;
};

export type KppkCostView = { period: KppkRealizationPeriod; rows: KppkCostRow[] };

export function getCostView(
    orgId: string,
    month: string | null,
    year: string | null,
): Promise<KppkCostView> {
    const params = new URLSearchParams({ orgId });
    if (month && year) {
        params.set("month", month);
        params.set("year", year);
    }
    return requestJson(`/api/kppk/cost?${params}`);
}

export function updateCostCell(
    rowId: string,
    field: string,
    value: unknown,
): Promise<{ applied: boolean }> {
    return requestWrite(`/api/kppk/cost/${rowId}?session_id=${getCollabSessionId()}`, "PATCH", {
        field,
        value,
    });
}

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

/**
 * The «Реализация» export replays the toolbar's slice × ДЗО × period selection.
 *
 * `rowIds` is the «видимая» mode: the rows the grid currently shows, in its own
 * order. The grid filters and sorts CLIENT-side, so this is the only way the
 * file can match the screen — `null` exports the whole view («вся таблица»).
 */
export type KppkRealizationExportRequest = {
    slice: KppkRealizationSlice;
    orgId: string;
    month: string | null;
    year: string | null;
    rowIds: string[] | null;
    mergeRows: boolean;
    headerPinned: boolean;
    columns: ExportColumn[];
    fileName: string;
};

/**
 * Download the «Реализация» view as one sheet.
 *
 * The form is a view, so the request carries the toolbar selection; «видимая»
 * additionally carries the grid's visible rows in the grid's order, because the
 * column filters and the sort never leave the client.
 */
export async function exportKppkRealization(
    request: KppkRealizationExportRequest,
): Promise<Blob | KppkExportJobDownload> {
    const result = await downloadTableExport(
        "/api/kppk/realization/export",
        {
            slice: request.slice,
            orgId: request.orgId,
            month: request.month === null ? null : Number(request.month),
            year: request.year === null ? null : Number(request.year),
            rowIds: request.rowIds,
            mergeRows: request.mergeRows,
            headerPinned: request.headerPinned,
            columns: request.columns,
            fileName: request.fileName,
        },
        (job, base) => ({ job, fileUrl: tableExportJobFileUrl(base, job.id) }),
    );
    return result;
}

/** Download a КППК dictionary export — inline, or as a background job past the inline row limit. */
export async function exportKppkTable(
    request: KppkExportViewRequest,
): Promise<Blob | KppkExportJobDownload> {
    return downloadTableExport("/api/kppk/export", request, (job, base) => ({
        job,
        fileUrl: tableExportJobFileUrl(base, job.id),
    }));
}

export async function exportKppkCost(
    request: KppkRealizationExportRequest & { rowIds: string[] | null },
): Promise<Blob | KppkExportJobDownload> {
    return downloadTableExport(
        "/api/kppk/cost/export",
        {
            ...request,
            month: request.month === null ? null : Number(request.month),
            year: request.year === null ? null : Number(request.year),
        },
        (job, base) => ({ job, fileUrl: tableExportJobFileUrl(base, job.id) }),
    );
}
