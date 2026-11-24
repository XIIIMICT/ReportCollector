/**
 * Renovation API — plan export (inline + durable job).
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import { requestJson, requestWrite } from "@/shared/api/api-error";
import { ReportCollectorApiError } from "@/shared/api/report-collector";

import type { RenovationColumnFilter, RenovationSortColumn } from "./renovation-shared";

export type RenovationPlanExport = {
    blob: Blob;
    fileName: string;
};

export type RenovationPlanExportParams = {
    planId?: string | undefined;
    rowIds?: string[] | null | undefined;
    asset?: string | null | undefined;
    block?: string | null | undefined;
    /** Active header filters — the server-side «видимая таблица» scope. */
    filters?: RenovationColumnFilter[] | null | undefined;
    /** Active server-side row ordering, in priority order. */
    sorting?: RenovationSortColumn[] | null | undefined;
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
            filters: params.filters ?? null,
            sorting: params.sorting ?? null,
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

export type RenovationExportJobStatus = "pending" | "running" | "done" | "failed";

export type RenovationExportJob = {
    id: string;
    status: RenovationExportJobStatus;
    /** Rows written so far (progress bar numerator). */
    progress: number;
    /** Rows in the exported scope (progress bar denominator). */
    total: number;
    fileName: string;
    error: string | null;
};

/**
 * Start (or reuse) a background plan export.
 *
 * The full plan is minutes of CPU and hundreds of MB, so the server generates
 * the file in a single sequential worker; the call returns immediately and the
 * client polls `getRenovationExportJob` before downloading. An identical
 * request (same plan, filters and last-edit stamp) reuses the ready file.
 */
export async function createRenovationExportJob(
    params: RenovationPlanExportParams = {},
): Promise<RenovationExportJob> {
    return requestWrite<RenovationExportJob>("/api/renovation/plan/export/jobs", "POST", {
        planId: params.planId ?? null,
        rowIds: params.rowIds ?? null,
        asset: params.asset ?? null,
        block: params.block ?? null,
        filters: params.filters ?? null,
        sorting: params.sorting ?? null,
    });
}

export async function getRenovationExportJob(jobId: string): Promise<RenovationExportJob> {
    return requestJson<RenovationExportJob>(
        `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}`,
    );
}

/** URL of a ready export file (streamed into the chosen file by the client). */
export function renovationExportJobFileUrl(jobId: string): string {
    return `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}/file`;
}

/** Download the ready XLSX of a completed export job. */
export async function downloadRenovationExportJob(jobId: string): Promise<RenovationPlanExport> {
    const response = await fetch(
        `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}/file`,
        { cache: "no-store" },
    );
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
        fileName: fileName || "Пообъектный план.xlsx",
    };
}
