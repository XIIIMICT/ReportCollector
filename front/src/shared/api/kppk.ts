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

/** The «Реализация» export replays the toolbar's slice × ДЗО × period selection. */
export type KppkRealizationExportRequest = {
    slice: KppkRealizationSlice;
    orgId: string;
    month: string | null;
    year: string | null;
    headerPinned: boolean;
    columns: ExportColumn[];
    fileName: string;
};

/**
 * Download the «Реализация» view as one sheet.
 *
 * The form is not a dictionary row list but a view, so the request carries the
 * toolbar selection and the server re-reads the same rows in the same order.
 */
export async function exportKppkRealization(request: KppkRealizationExportRequest): Promise<Blob> {
    const result = await downloadTableExport<never>(
        "/api/kppk/realization/export",
        {
            slice: request.slice,
            orgId: request.orgId,
            month: request.month === null ? null : Number(request.month),
            year: request.year === null ? null : Number(request.year),
            rowIds: null,
            headerPinned: request.headerPinned,
            columns: request.columns,
            fileName: request.fileName,
        },
        () => {
            // The view is bounded by one period and one ДЗО — it always renders
            // inline; a job response would mean the server changed that.
            throw new Error("Выгрузка «Реализации» не поддерживает фоновые задания.");
        },
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
): Promise<Blob> {
    return downloadTableExport<never>(
        "/api/kppk/cost/export",
        {
            ...request,
            month: request.month === null ? null : Number(request.month),
            year: request.year === null ? null : Number(request.year),
        },
        () => {
            throw new Error("Фоновая выгрузка себестоимости недоступна.");
        },
    );
}
