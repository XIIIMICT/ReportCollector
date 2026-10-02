import { requestJson, requestWrite } from "@/shared/api/api-error";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { getCollabSessionId } from "@/shared/collab/ws-client";

export const RENOVATION_SECTION = "renovationPlan";

export type RenovationAsset = {
    id: string;
    code: string;
    repairCode: string;
    name: string;
};

export type RenovationCatalogOption = {
    valueId: string;
    label: string;
};

export type RenovationCatalog = {
    id: string;
    label: string;
    options: RenovationCatalogOption[];
};

export type RenovationField = {
    dbName: string;
    label: string;
    cellType: string;
    catalog?: string | null;
    isCalculated: boolean;
    isReadOnly: boolean;
    decimals?: number | null;
    position: number;
    ordinal: number;
    responsibilityBlock?: string | null;
};

export type RenovationHeaderCell = {
    id: string;
    text: string;
    startPosition: number;
    colSpan: number;
    rowSpan: number;
    level: number;
    excelRange: string;
};

export type RenovationHeaderRow = {
    level: number;
    cells: RenovationHeaderCell[];
};

export type RenovationMeta = {
    assets: RenovationAsset[];
    catalogs: RenovationCatalog[];
    fields: RenovationField[];
    headerCells: RenovationHeaderCell[];
    headerRows: RenovationHeaderRow[];
    workingPlanId: string | null;
    pendingPlanId: string | null;
    /** The current user's responsibility block (null for central roles). */
    userBlock: string | null;
    /** The user's renovation roles — source of truth for plan edit rights. */
    userRoles: string[];
};

export type RenovationPlanVersion = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    rowCount: number;
    createdAt: string;
    createdBy: string;
    updatedAt: string;
    updatedBy: string;
};

export type RenovationPlanRow = {
    id: string;
    assetId: string | null;
    assetCode: string | null;
    assetName: string | null;
    assetRepairCode: string | null;
    position: number;
    revision: number;
    updatedAt: string;
    updatedBy: string;
    values: Record<string, string | number | boolean | null>;
    renovationId: string | null;
    missingComponents: string[];
};

export type RenovationPlan = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    baselineBoundary: number | null;
};

export type RenovationPlanRows = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    total: number;
    baselineBoundary: number | null;
    /** Whole-plan row count (ignoring the asset filter) for the «всего» counter. */
    totalAll: number | null;
};

export type RenovationDiffCell = {
    field: string;
    oldValue?: string | number | boolean | null;
    newValue?: string | number | boolean | null;
};

export type RenovationDiffRow = {
    rowId: string;
    position: number;
    assetName: string;
    isNew: boolean;
    cells: RenovationDiffCell[];
};

export type RenovationDiff = {
    planId: string;
    comparedToPlanId: string | null;
    rows: RenovationDiffRow[];
    /** Full previous-version rows for the rows deleted in the target. */
    deletedRows: RenovationPlanRow[];
};

export type RenovationUpdate = {
    applied: number;
    calculated: Array<{ rowId: string; field: string; value: unknown }>;
    renovationIds: Array<{ rowId: string; value: string; missingComponents: string[] }>;
};

export type RenovationApprove = {
    archived: RenovationPlanVersion;
    working: RenovationPlanVersion;
};

export type RenovationPlanExport = {
    blob: Blob;
    fileName: string;
};

export async function getRenovationMeta(): Promise<RenovationMeta> {
    return requestJson("/api/renovation/meta");
}

export async function getRenovationPlan(planId?: string): Promise<RenovationPlan> {
    const query = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
    return requestJson(`/api/renovation/plan${query}`);
}

/** One active header filter: kept display values + the «Пустые» bucket. */
export type RenovationColumnFilter = {
    column: string;
    values: string[];
    includeNulls: boolean;
};

export async function getRenovationPlanRows(
    offset: number,
    limit: number,
    planId?: string,
    asset?: string | null,
    filters?: RenovationColumnFilter[] | null,
    positionFrom?: number | null,
    positionTo?: number | null,
): Promise<RenovationPlanRows> {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (planId) params.set("plan_id", planId);
    if (asset) params.set("asset", asset);
    if (filters && filters.length > 0) params.set("filters", JSON.stringify(filters));
    if (positionFrom != null) params.set("position_from", String(positionFrom));
    if (positionTo != null) params.set("position_to", String(positionTo));
    return requestJson(`/api/renovation/plan/rows?${params.toString()}`);
}

export async function getRenovationPlanSummary(
    params: {
        planId?: string | undefined;
        asset?: string | null | undefined;
        filters?: RenovationColumnFilter[] | null | undefined;
        positionFrom?: number | null | undefined;
        positionTo?: number | null | undefined;
    } = {},
): Promise<Record<string, number>> {
    const query = new URLSearchParams();
    if (params.planId) query.set("plan_id", params.planId);
    if (params.asset) query.set("asset", params.asset);
    if (params.filters && params.filters.length > 0)
        query.set("filters", JSON.stringify(params.filters));
    if (params.positionFrom != null) query.set("position_from", String(params.positionFrom));
    if (params.positionTo != null) query.set("position_to", String(params.positionTo));
    const data = await requestJson<{ values: Record<string, number> }>(
        `/api/renovation/plan/summary?${query.toString()}`,
    );
    return data.values;
}

export type RenovationFacetValue = {
    value: string | null;
    count: number;
};

export type RenovationFacets = {
    column: string;
    values: RenovationFacetValue[];
    /** Distinct non-empty values across the whole filtered plan. */
    total: number;
    /** Whether the column has a NULL/empty bucket (the «Пустые» option). */
    hasNulls: boolean;
};

export async function getRenovationPlanFacets(
    column: string,
    params: {
        planId?: string | undefined;
        asset?: string | null | undefined;
        offset?: number | undefined;
        limit?: number | undefined;
    } = {},
): Promise<RenovationFacets> {
    const query = new URLSearchParams({ column });
    if (params.planId) query.set("plan_id", params.planId);
    if (params.asset) query.set("asset", params.asset);
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    return requestJson(`/api/renovation/plan/facets?${query.toString()}`);
}

export async function getRenovationPlans(): Promise<RenovationPlanVersion[]> {
    return requestJson("/api/renovation/plans");
}

export async function updateRenovationCells(updates: CellUpdate[]): Promise<RenovationUpdate> {
    return requestWrite(`/api/renovation/plan?session_id=${getCollabSessionId()}`, "PATCH", {
        updates,
    });
}

export async function addRenovationRow(assetId: string): Promise<RenovationPlanRow> {
    return requestWrite("/api/renovation/plan/rows", "POST", { assetId });
}

export async function deleteRenovationRow(rowId: string): Promise<void> {
    return requestWrite(`/api/renovation/plan/rows/${encodeURIComponent(rowId)}`, "DELETE");
}

export async function getRenovationDiff(planId: string): Promise<RenovationDiff> {
    return requestJson(`/api/renovation/plan/diff?plan_id=${encodeURIComponent(planId)}`);
}

export type RenovationPlanExportParams = {
    planId?: string | undefined;
    rowIds?: string[] | null | undefined;
    asset?: string | null | undefined;
    block?: string | null | undefined;
};

export async function exportRenovationPlan(
    params: RenovationPlanExportParams = {},
): Promise<RenovationPlanExport> {
    const response = await fetch("/api/renovation/plan/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            planId: params.planId ?? null,
            rowIds: params.rowIds ?? null,
            asset: params.asset ?? null,
            block: params.block ?? null,
        }),
        cache: "no-store",
    });
    if (!response.ok) {
        let detail = `Запрос завершился ошибкой ${response.status}.`;
        try {
            const payload = (await response.json()) as { detail?: string };
            if (typeof payload.detail === "string") {
                detail = payload.detail;
            }
        } catch {
            // For a non-JSON response keep the generic message prepared above.
        }
        throw new ReportCollectorApiError(detail, response.status);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    // Prefer the RFC 5987 UTF-8 form — the plan name is Cyrillic and the bare
    // `filename` carries only a percent-encoded copy the browser would save raw.
    const star = /filename\*\s*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="?([^";]+)"?/.exec(disposition);
    let fileName = "";
    if (star?.[1]) {
        try {
            fileName = decodeURIComponent(star[1]);
        } catch {
            fileName = star[1];
        }
    } else if (plain?.[1]) {
        fileName = plain[1];
    }
    return {
        blob: await response.blob(),
        fileName: fileName || "Пообъектный план — рабочая версия.xlsx",
    };
}

export async function submitRenovationPlan(): Promise<RenovationPlanVersion> {
    return requestWrite("/api/renovation/plan/submit", "POST");
}

export async function approveRenovationPlan(): Promise<RenovationApprove> {
    return requestWrite("/api/renovation/plan/approve", "POST");
}

export type RenovationUndoResult = {
    applied: boolean;
    reason: "applied" | "empty" | "conflict" | "locked" | null;
    conflicts: Array<{
        rowId: string;
        field: string;
        user: string;
        actionId: number;
        reason: "conflict" | "locked";
    }>;
    section: string | null;
    reportMonth: string | null;
    rowId: string | null;
    field: string | null;
    value: unknown;
    revision: number | null;
};

export type RenovationUndoState = {
    canUndo: boolean;
    canRedo: boolean;
};

// ─── Dictionary grid rows ───────────────────────────────────────────────────

export type RenovationAssetRow = {
    id: string;
    repairCode: string;
    name: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
    /** Computed: the working plan's row count for this asset. */
    repairCount: number;
    /** Computed: sum of «Стоимость итого» (col_031) over the plan rows. */
    totalCost: number;
};

export type RenovationCatalogOptionRow = {
    valueId: string;
    label: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export type RenovationUserRow = {
    id: string;
    email: string;
    fullname: string;
    role: string;
    block: string | null;
    assetNames: string[];
    isActive: boolean;
    lastAuthenticatedAt: string | null;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export const RENOVATION_ASSETS_SECTION = "renovationAssets";
export const RENOVATION_USERS_SECTION = "renovationUsers";

export function renovationCatalogSection(catalogId: string): `renovationCatalog:${string}` {
    return `renovationCatalog:${catalogId}`;
}

// ─── Dictionary CRUD ───────────────────────────────────────────────────────

export async function getRenovationDictionaryAssets(): Promise<RenovationAssetRow[]> {
    return requestJson("/api/renovation/dictionary/assets");
}

export async function createRenovationAsset(
    name: string,
    repairCode: string,
): Promise<RenovationAssetRow> {
    // session_id makes the create an undoable action (server-side journal).
    return requestWrite(
        `/api/renovation/dictionary/assets?session_id=${getCollabSessionId()}`,
        "POST",
        { name, repairCode },
    );
}

export async function updateRenovationAssetCells(updates: CellUpdate[]): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "assets",
        updates,
    });
}

export async function deleteRenovationAsset(assetId: string): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/assets/${encodeURIComponent(assetId)}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

export async function getRenovationCatalogOptions(
    catalogId: string,
): Promise<RenovationCatalogOptionRow[]> {
    return requestJson(`/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}`);
}

export async function createRenovationCatalogOption(
    catalogId: string,
    label: string,
): Promise<RenovationCatalogOptionRow> {
    // session_id makes the create an undoable action (server-side journal).
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options?session_id=${getCollabSessionId()}`,
        "POST",
        { label },
    );
}

export async function updateRenovationCatalogOptionCells(
    catalogId: string,
    updates: CellUpdate[],
): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "catalog",
        catalog: catalogId,
        updates,
    });
}

export async function deleteRenovationCatalogOption(
    catalogId: string,
    valueId: string,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options/${encodeURIComponent(valueId)}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

export async function getRenovationDictionaryUsers(): Promise<RenovationUserRow[]> {
    return requestJson("/api/renovation/dictionary/users");
}

export async function createRenovationUser(payload: {
    email: string;
    fullname: string;
    role: string;
    block: string | null;
    assetNames: string[];
}): Promise<RenovationUserRow> {
    return requestWrite("/api/renovation/dictionary/users", "POST", payload);
}

export async function updateRenovationUserCells(updates: CellUpdate[]): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "users",
        updates,
    });
}

export type RenovationExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
};

export async function exportRenovationDictionary(request: {
    section: string;
    headerPinned: boolean;
    columns: RenovationExportColumn[];
    rowIds: string[] | null;
    fileName: string;
}): Promise<Blob> {
    const response = await fetch("/api/renovation/dictionary/export", {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
    });
    if (!response.ok) {
        let detail = `Запрос завершился ошибкой ${response.status}.`;
        try {
            const payload = (await response.json()) as { detail?: string };
            if (typeof payload.detail === "string") {
                detail = payload.detail;
            }
        } catch {
            // For a non-JSON response keep the generic message prepared above.
        }
        throw new ReportCollectorApiError(detail, response.status);
    }
    return response.blob();
}

// ─── Section-scoped undo/redo (plan and dictionaries) ─────────────────────

export async function undoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/undo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function redoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/redo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function getUndoState(
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoState> {
    const params = new URLSearchParams({ section, session_id: getCollabSessionId() });
    return requestJson(`/api/renovation/undo-state?${params.toString()}`);
}
