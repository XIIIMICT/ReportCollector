/**
 * Shared Excel export download: handles the dual-mode API.
 *
 * Small tables arrive as a direct attachment (200 + bytes); big ones return
 * 202 + a job description whose file URL streams the growing workbook — the
 * browser's native download then shows progress from the first byte. Both
 * paths end with the file saved locally; the blob path keeps the site toast
 * the old behaviour had, the job path hands the URL to the browser.
 */

import {
    apiErrorFromResponse,
    fetchFailure,
    readJsonBody,
    ReportCollectorApiError,
} from "./api-error";

export type ExportJobDescriptor = {
    id: string;
    status: "pending" | "running" | "done" | "failed";
    progress: number;
    total: number;
    fileName: string;
    error: string | null;
};

function parseJob(payload: unknown, response: Response): ExportJobDescriptor {
    const job = payload as Partial<ExportJobDescriptor>;
    if (
        typeof job.id !== "string" ||
        typeof job.progress !== "number" ||
        typeof job.total !== "number" ||
        typeof job.fileName !== "string" ||
        !["pending", "running", "done", "failed"].includes(String(job.status))
    ) {
        throw new ReportCollectorApiError(
            "Сервер вернул неожиданное состояние задания.",
            response.status,
            response.headers.get("X-Request-ID") ?? undefined,
        );
    }
    return {
        id: job.id,
        status: job.status as ExportJobDescriptor["status"],
        progress: job.progress,
        total: job.total,
        fileName: job.fileName,
        error: typeof job.error === "string" ? job.error : null,
    };
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
    let response: Response;
    try {
        response = await fetch(url, {
            cache: "no-store",
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
            signal: signal ?? null,
        });
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось выгрузить таблицу.");
    }
    if (response.status === 202) {
        const job = parseJob(await readJsonBody<unknown>(response), response);
        if (job.status === "failed") {
            throw new ReportCollectorApiError(job.error ?? "Не удалось сформировать файл.", 500);
        }
        // /api/<project>/export → /api/<project>/export/jobs
        const base = url.replace(/\/export$/, "/export/jobs");
        return onJob(job, base);
    }
    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
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
    let response: Response;
    try {
        response = await fetch(`${jobsBaseUrl}/${encodeURIComponent(jobId)}`, {
            cache: "no-store",
            signal: signal ?? null,
        });
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось получить состояние задания.");
    }
    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
    }
    return parseJob(await readJsonBody<unknown>(response), response);
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
