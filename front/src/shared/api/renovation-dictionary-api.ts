/**
 * Renovation API — admin dictionary CRUD and dictionary export.
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import { requestJson, requestWrite } from "@/shared/api/api-error";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { downloadTableExport, tableExportJobFileUrl } from "@/shared/api/table-export";
import { getCollabSessionId } from "@/shared/collab/ws-client";

import type {
    RenovationAssetRow,
    RenovationCatalogOptionRow,
    RenovationExportColumn,
    RenovationExportJobDownload,
    RenovationUserRow,
} from "./renovation-shared";

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

/** Remove a user from «Ремонт» (only possible once their access is revoked). */
export async function deleteRenovationUser(userId: string): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/users/${encodeURIComponent(userId)}?session_id=${getCollabSessionId()}`,
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

export async function replaceRenovationAsset(
    assetId: string,
    targetId: string | null,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/assets/${encodeURIComponent(assetId)}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        { targetId },
    );
}

export async function replaceRenovationCatalogOption(
    catalogId: string,
    valueId: string,
    targetId: string | null,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options/${encodeURIComponent(valueId)}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        { targetId },
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

export async function exportRenovationDictionary(
    request: {
        section: string;
        headerPinned: boolean;
        columns: RenovationExportColumn[];
        rowIds: string[] | null;
        valuesOnly?: boolean;
        fileName: string;
    },
    signal?: AbortSignal,
): Promise<Blob | RenovationExportJobDownload> {
    return downloadTableExport(
        "/api/renovation/dictionary/export",
        { ...request, valuesOnly: request.valuesOnly ?? true },
        (job, base) => ({ job, fileUrl: tableExportJobFileUrl(base, job.id), jobsBaseUrl: base }),
        signal,
    );
}

// ─── Section-scoped undo/redo (plan and dictionaries) ─────────────────────
