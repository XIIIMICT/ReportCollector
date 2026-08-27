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
    assetId: string;
    assetCode: string;
    assetName: string;
    assetRepairCode: string;
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
    state: "working" | "pendingApproval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    baselineBoundary: number | null;
};

export type RenovationPlanRows = {
    id: string;
    state: "working" | "pendingApproval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    total: number;
    baselineBoundary: number | null;
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

export type RenovationDeletedRow = {
    position: number;
    assetName: string;
};

export type RenovationDiff = {
    planId: string;
    comparedToPlanId: string | null;
    rows: RenovationDiffRow[];
    deletedRows: RenovationDeletedRow[];
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

async function requestJson<T>(path: string): Promise<T> {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) {
        throw new ReportCollectorApiError(
            readApiDetail(await readPayload(response)) ??
                `Запрос завершился ошибкой ${response.status}.`,
            response.status,
        );
    }

    return (await response.json()) as T;
}

async function requestWrite<T>(
    path: string,
    method: "POST" | "PATCH" | "DELETE",
    body?: unknown,
): Promise<T> {
    const init: RequestInit = { cache: "no-store", method };
    if (method !== "DELETE" && body !== undefined) {
        init.headers = { "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
    }
    const response = await fetch(path, init);
    if (!response.ok) {
        throw new ReportCollectorApiError(
            readApiDetail(await readPayload(response)) ??
                `Запрос завершился ошибкой ${response.status}.`,
            response.status,
        );
    }
    if (response.status === 204) {
        return undefined as T;
    }
    return (await response.json()) as T;
}

async function readPayload(response: Response): Promise<unknown> {
    try {
        return (await response.json()) as unknown;
    } catch {
        return null;
    }
}

/**
 * Human-readable API error description: the detail string, or the pydantic
 * validation errors list (422), or null.
 */
function readApiDetail(payload: unknown): string | null {
    if (!payload || typeof payload !== "object") return null;
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (!Array.isArray(detail)) return null;
    const messages = detail
        .map((item) => {
            if (
                item &&
                typeof item === "object" &&
                typeof (item as { msg?: unknown }).msg === "string"
            ) {
                return (item as { msg: string }).msg.replace(/^Value error,\s*/, "");
            }
            return null;
        })
        .filter((message): message is string => Boolean(message));
    return messages.length > 0 ? messages.join("; ") : null;
}

export async function getRenovationMeta(): Promise<RenovationMeta> {
    return requestJson("/api/renovation/meta");
}

export async function getRenovationPlan(planId?: string): Promise<RenovationPlan> {
    const query = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
    return requestJson(`/api/renovation/plan${query}`);
}

export async function getRenovationPlanRows(
    offset: number,
    limit: number,
    planId?: string,
    asset?: string | null,
): Promise<RenovationPlanRows> {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (planId) params.set("plan_id", planId);
    if (asset) params.set("asset", asset);
    return requestJson(`/api/renovation/plan/rows?${params.toString()}`);
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

export async function exportRenovationPlan(
    params: { planId?: string; rowIds?: string[] | null } = {},
): Promise<RenovationPlanExport> {
    const response = await fetch("/api/renovation/plan/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ planId: params.planId ?? null, rowIds: params.rowIds ?? null }),
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
    const match = /filename="?([^";]+)"?/.exec(disposition);
    return {
        blob: await response.blob(),
        fileName: match?.[1] ?? `remont_plan_v${params.planId ?? "working"}.xlsx`,
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
    return requestWrite("/api/renovation/dictionary/assets", "POST", { name, repairCode });
}

export async function updateRenovationAssetCells(updates: CellUpdate[]): Promise<unknown> {
    const sessionQuery = `?session_id=${getCollabSessionId()}`;
    return Promise.all(
        updates.map((update) =>
            requestWrite(
                `/api/renovation/dictionary/assets/${encodeURIComponent(update.rowId)}${sessionQuery}`,
                "PATCH",
                {
                    ...(update.field === "name" ? { name: update.value } : {}),
                    ...(update.field === "repairCode" ? { repairCode: update.value } : {}),
                },
            ),
        ),
    );
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
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options`,
        "POST",
        { label },
    );
}

export async function updateRenovationCatalogOptionCells(
    catalogId: string,
    updates: CellUpdate[],
): Promise<unknown> {
    const sessionQuery = `?session_id=${getCollabSessionId()}`;
    return Promise.all(
        updates.map((update) =>
            requestWrite(
                `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options/${encodeURIComponent(update.rowId)}${sessionQuery}`,
                "PATCH",
                update.field === "label" ? { label: update.value } : {},
            ),
        ),
    );
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
    const sessionQuery = `?session_id=${getCollabSessionId()}`;
    return Promise.all(
        updates.map((update) =>
            requestWrite(
                `/api/renovation/dictionary/users/${encodeURIComponent(update.rowId)}${sessionQuery}`,
                "PATCH",
                {
                    ...(update.field === "fullname" ? { fullname: update.value } : {}),
                    ...(update.field === "role" ? { role: update.value } : {}),
                    ...(update.field === "block" ? { block: update.value || null } : {}),
                    ...(update.field === "assetNames" ? { assetNames: update.value } : {}),
                    ...(update.field === "isActive" ? { isActive: update.value } : {}),
                },
            ),
        ),
    );
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
