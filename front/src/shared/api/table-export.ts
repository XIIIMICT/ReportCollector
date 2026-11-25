/**
 * Shared Excel export download: handles the dual-mode API.
 *
 * Small tables arrive as a direct attachment (200 + bytes); big ones return
 * 202 + a job description whose file URL streams the growing workbook — the
 * browser's native download then shows progress from the first byte. Both
 * paths end with the file saved locally; the blob path keeps the site toast
 * the old behaviour had, the job path hands the URL to the browser.
 */

import { readApiDetail } from "./api-error";
import { ReportCollectorApiError } from "./report-collector";

export type ExportJobDescriptor = {
    id: string;
    status: "pending" | "running" | "done" | "failed";
    progress: number;
    total: number;
    fileName: string;
    error: string | null;
};

async function readErrorDetail(response: Response): Promise<string> {
    const payload: unknown = await response.json().catch(() => null);
    return readApiDetail(payload) ?? `Запрос завершился ошибкой ${response.status}.`;
}

/**
 * POST an export request and download the result.
 *
 * 200 — the workbook is the response body (returned as a Blob).
 * 202 — the table exceeded the inline limit; ``onJob`` maps the descriptor
 * to the caller's result (typically a native-download descriptor with the
 * streaming file URL built from ``jobsBaseUrl``).
 */
export async function downloadTableExport<TJob>(
    url: string,
    request: unknown,
    onJob: (job: ExportJobDescriptor, jobsBaseUrl: string) => TJob,
    signal?: AbortSignal,
): Promise<Blob | TJob> {
    const response = await fetch(url, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: signal ?? null,
    });
    if (response.status === 202) {
        const job = (await response.json()) as ExportJobDescriptor;
        if (job.status === "failed") {
            throw new ReportCollectorApiError(job.error ?? "Не удалось сформировать файл.", 500);
        }
        // /api/<project>/export → /api/<project>/export/jobs
        const base = url.replace(/\/export$/, "/export/jobs");
        return onJob(job, base);
    }
    if (!response.ok) {
        throw new ReportCollectorApiError(await readErrorDetail(response), response.status);
    }
    return response.blob();
}

/** URL of a table export job's file (streams the growing workbook). */
export function tableExportJobFileUrl(jobsBaseUrl: string, jobId: string): string {
    return `${jobsBaseUrl}/${encodeURIComponent(jobId)}/file`;
}

/** Poll one durable job. Aborted when the user cancels or the hook unmounts. */
export async function getTableExportJob(
    jobsBaseUrl: string,
    jobId: string,
    signal?: AbortSignal,
): Promise<ExportJobDescriptor> {
    const response = await fetch(`${jobsBaseUrl}/${encodeURIComponent(jobId)}`, {
        cache: "no-store",
        signal: signal ?? null,
    });
    if (!response.ok) {
        throw new ReportCollectorApiError(await readErrorDetail(response), response.status);
    }
    return (await response.json()) as ExportJobDescriptor;
}

/** Best-effort server-side cancel, matching DocMasker's job cancellation. */
export async function cancelTableExportJob(jobsBaseUrl: string, jobId: string): Promise<void> {
    try {
        await fetch(`${jobsBaseUrl}/${encodeURIComponent(jobId)}`, {
            method: "DELETE",
            cache: "no-store",
        });
    } catch {
        // The job will expire on its own; the UI must still return to idle.
    }
}
