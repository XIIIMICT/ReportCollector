import { requestJson, requestWrite } from "@/shared/api/api-error";
import type {
    AssetOptionRead,
    AssetRowRead,
    DeliveryBasisRowRead,
    MovementMetaRead,
    MovementRowRead,
    MovementSnapshotRead,
    PriceRowRead,
    RawMaterialRowRead,
    RawMaterialsMetaRead,
    SnapshotMovementRowRead,
    SnapshotViewRead,
    UserRowRead,
} from "@/shared/api/generated/openapi";
import { downloadTableExport, tableExportJobFileUrl } from "@/shared/api/table-export";
import type { ExportJobDescriptor } from "@/shared/api/table-export";
import { getCollabSessionId } from "@/shared/collab/ws-client";
import type { ExportColumn, SectionId } from "@/shared/grid/grid-config";

// HTTP read DTOs are generated from the live backend contract. The local names
// stay stable for callers; only their definitions come from the generated file.
export type AssetOption = AssetOptionRead;
export type RawMaterialsMeta = RawMaterialsMetaRead;
export type MovementSnapshot = MovementSnapshotRead;
export type MovementMeta = MovementMetaRead;
export type SnapshotMovementRow = SnapshotMovementRowRead;
export type SnapshotView = SnapshotViewRead;
export type MovementRow = MovementRowRead;
export type PriceRow = PriceRowRead;
export type RawMaterialRow = RawMaterialRowRead;
export type AssetRow = AssetRowRead;
export type DeliveryBasisRow = DeliveryBasisRowRead;
export type UserRow = UserRowRead;

export async function getRawMaterialsMeta(): Promise<RawMaterialsMeta> {
    return requestJson("/api/raw-materials/meta");
}

export async function getRawMaterialsMovement(reportMonth: string): Promise<MovementRow[]> {
    return requestJson(
        `/api/raw-materials/movement?report_month=${encodeURIComponent(reportMonth)}`,
    );
}

export async function getMovementMeta(reportMonth: string): Promise<MovementMeta> {
    return requestJson(
        `/api/raw-materials/movement/meta?report_month=${encodeURIComponent(reportMonth)}`,
    );
}

export async function getMovementVersions(): Promise<MovementSnapshot[]> {
    return requestJson("/api/raw-materials/movement/versions");
}

export async function fixMovement(reportMonth: string): Promise<MovementSnapshot> {
    return requestWrite("/api/raw-materials/movement/fix", "POST", { reportMonth });
}

export async function getMovementSnapshot(snapshotId: string): Promise<SnapshotView> {
    return requestJson(`/api/raw-materials/movement/snapshot/${encodeURIComponent(snapshotId)}`);
}

export async function getRawMaterialsPrices(): Promise<PriceRow[]> {
    return requestJson("/api/raw-materials/prices");
}

export async function getRawMaterialsList(): Promise<RawMaterialRow[]> {
    return requestJson("/api/raw-materials/raw-materials");
}

export async function getAssetList(): Promise<AssetRow[]> {
    return requestJson("/api/raw-materials/assets");
}

export async function getDeliveryBasisList(): Promise<DeliveryBasisRow[]> {
    return requestJson("/api/raw-materials/delivery-bases");
}

export async function getUserList(): Promise<UserRow[]> {
    return requestJson("/api/raw-materials/users");
}

export type CellValue = string | number | boolean | string[] | null;

export type CellUpdate = {
    rowId: string;
    field: string;
    value: CellValue;
};

export async function updateMovementCells(
    reportMonth: string,
    updates: CellUpdate[],
): Promise<number> {
    return requestWrite(`/api/raw-materials/movement?session_id=${getCollabSessionId()}`, "PATCH", {
        reportMonth,
        updates,
    });
}

export async function updatePriceCells(updates: CellUpdate[]): Promise<number> {
    return requestWrite(`/api/raw-materials/prices?session_id=${getCollabSessionId()}`, "PATCH", {
        updates,
    });
}

export async function updateDictionaryCells(
    section: "rawMaterials" | "assets" | "deliveryBases",
    updates: CellUpdate[],
): Promise<unknown> {
    // One batch request per save: the server applies every cell and journals
    // them as a single undo action (no per-cell fan-out).
    return requestWrite(
        `/api/raw-materials/dictionary?session_id=${getCollabSessionId()}`,
        "PATCH",
        {
            section,
            updates,
        },
    );
}

export async function updateUserCells(updates: CellUpdate[]): Promise<unknown> {
    return requestWrite(
        `/api/raw-materials/dictionary?session_id=${getCollabSessionId()}`,
        "PATCH",
        {
            section: "users",
            updates,
        },
    );
}

export type UndoConflict = {
    rowId: string;
    field: string;
    user: string;
    actionId: number;
    reason: "conflict" | "locked";
};

export type UndoResult = {
    applied: boolean;
    reason: "applied" | "empty" | "conflict" | "locked" | null;
    conflicts: UndoConflict[];
    section: string | null;
    reportMonth: string | null;
    rowId: string | null;
    field: string | null;
    value: unknown;
    revision: number | null;
};

export async function undoCells(
    skipActionId: number | null,
    section: string,
    reportMonth: string | null,
): Promise<UndoResult> {
    return requestWrite(`/api/raw-materials/undo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth,
    });
}

export async function redoCells(
    skipActionId: number | null,
    section: string,
    reportMonth: string | null,
): Promise<UndoResult> {
    return requestWrite(`/api/raw-materials/redo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth,
    });
}

export type UndoState = {
    canUndo: boolean;
    canRedo: boolean;
};

export async function getUndoState(
    section: string,
    reportMonth: string | null,
): Promise<UndoState> {
    const params = new URLSearchParams({ section, session_id: getCollabSessionId() });
    if (reportMonth) params.set("report_month", reportMonth);
    return requestJson(`/api/raw-materials/undo-state?${params.toString()}`);
}

export type CreateDictionaryPayload = {
    name?: string;
    assetIds?: string[];
    email?: string;
    fullname?: string;
    role?: string;
    isAdmin?: boolean;
    assetNames?: string[];
    isActive?: boolean;
    resetPassword?: boolean;
};

export async function createDictionaryRecord(
    section: "rawMaterials" | "assets" | "deliveryBases" | "users",
    payload: CreateDictionaryPayload,
): Promise<unknown> {
    const endpoint =
        section === "rawMaterials"
            ? "raw-materials"
            : section === "assets"
              ? "assets"
              : section === "deliveryBases"
                ? "delivery-bases"
                : "users";
    const body =
        section === "rawMaterials"
            ? { name: payload.name, assetIds: payload.assetIds ?? [] }
            : section === "users"
              ? {
                    email: payload.email,
                    fullname: payload.fullname,
                    role: payload.role ?? "blocked",
                    assetNames: payload.assetNames ?? [],
                }
              : { name: payload.name };
    // session_id makes the create an undoable action (server-side journal).
    return requestWrite(
        `/api/raw-materials/${endpoint}?session_id=${getCollabSessionId()}`,
        "POST",
        body,
    );
}

export async function deleteDictionaryRecord(
    section: "rawMaterials" | "assets" | "deliveryBases" | "users",
    rowId: string,
): Promise<void> {
    const endpoint =
        section === "rawMaterials"
            ? "raw-materials"
            : section === "assets"
              ? "assets"
              : section === "deliveryBases"
                ? "delivery-bases"
                : "users";
    // The session id makes the deletion undoable: the backend journals an
    // «__deleted» snapshot only when it knows the page-lifetime history scope.
    return requestWrite(
        `/api/raw-materials/${endpoint}/${rowId}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

export type DictionaryReplaceTarget = "rawMaterials" | "assets" | "deliveryBases";

export async function replaceDictionaryRecord(
    section: DictionaryReplaceTarget,
    rowId: string,
    targetId: string | null,
): Promise<void> {
    const endpoint =
        section === "rawMaterials"
            ? "raw-materials"
            : section === "assets"
              ? "assets"
              : "delivery-bases";
    return requestWrite(
        `/api/raw-materials/${endpoint}/${rowId}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        { targetId },
    );
}

export type ExportViewRequest = {
    section: SectionId;
    reportMonth: string | null;
    headerPinned: boolean;
    columns: ExportColumn[];
    rowIds: string[] | null;
    snapshotId?: string | null;
    valuesOnly?: boolean;
    fileName: string;
};

export type ExportJobDownload = {
    job: ExportJobDescriptor;
    fileUrl: string;
    jobsBaseUrl: string;
};

/**
 * Download an Excel export — inline (a Blob) for small tables, or a
 * background job (returns the job + its streaming file URL) past the inline
 * row limit; the caller triggers the browser's native download for jobs.
 */
export async function exportTableToExcel(
    request: ExportViewRequest,
    signal?: AbortSignal,
): Promise<Blob | ExportJobDownload> {
    return downloadTableExport(
        "/api/raw-materials/export",
        { ...request, valuesOnly: request.valuesOnly ?? true },
        (job, base) => ({ job, fileUrl: tableExportJobFileUrl(base, job.id), jobsBaseUrl: base }),
        signal,
    );
}
