/**
 * Renovation API — plan import inspect / apply / job polling.
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import { readApiDetail } from "@/shared/api/api-error";
import { ReportCollectorApiError } from "@/shared/api/report-collector";

export type RenovationPlanImportCellIssue = {
    location: string;
    value: string;
    /** Human-readable field label (no header hierarchy). */
    column_label?: string | null;
};
export type RenovationPlanImportResult = {
    status: "invalid_format" | "has_errors" | "ready";
    /** Required headings absent from a readable file (a content error). */
    missing_required_columns: string[];
    duplicate_codes: string[];
    invalid_calculated_codes: string[];
    invalid_data_types: RenovationPlanImportCellIssue[];
    /** Numeric cells breaking the field's integer/min/max/percentage rules. */
    invalid_values: RenovationPlanImportCellIssue[];
    /** Excel «#REF!» cached values — a broken formula reference. */
    formula_reference_errors: RenovationPlanImportCellIssue[];
    /** Required cells left empty in a row — an error of the file. */
    missing_required_values: RenovationPlanImportCellIssue[];
    missing_dictionary_values: RenovationPlanImportCellIssue[];
    unknown_assets: RenovationPlanImportCellIssue[];
    /** Rows the load will not write: the asset cell is empty. */
    skipped_rows: number[];
    /** Every data row lacked the asset — there is nothing to load. */
    all_rows_skipped: boolean;
    assets: string[];
    new_rows: number;
    rows_to_update: number;
};

export type PlanImportProgress = {
    /** The bytes are on their way to the server. */
    uploading: () => void;
    /** The upload finished; from here the server parses the file. */
    analyzing: () => void;
};

/**
 * Check a workbook before loading it, reporting the TWO waits apart.
 *
 * `fetch` cannot say when the request body has been handed over, so this uses
 * `XMLHttpRequest`, whose `upload.onloadend` marks exactly that point: before it
 * the browser is sending bytes (the user's connection), after it the server is
 * parsing (its own work). The modal shows a different caption for each.
 */
export function inspectRenovationPlanImport(
    file: File,
    progress?: PlanImportProgress,
): Promise<RenovationPlanImportResult> {
    const body = new FormData();
    body.append("file", file);
    return new Promise((resolvePromise, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", "/api/renovation/plan/import/inspect");
        request.responseType = "text";
        // No Content-Type header: the browser must set it itself to attach the
        // multipart boundary that the backend parser needs.
        request.upload.onloadend = () => progress?.analyzing();
        request.onload = () => {
            if (request.status < 200 || request.status >= 300) {
                let detail: string | null = null;
                try {
                    detail = readApiDetail(JSON.parse(request.responseText) as unknown);
                } catch {
                    detail = null;
                }
                // 413 comes from the proxy in front of the API (nginx's
                // `client_max_body_size`) and carries no JSON body of its own —
                // falling through to the generic message made a too-large file
                // read as a broken one.
                reject(
                    new ReportCollectorApiError(
                        request.status === 413
                            ? "Файл имеет слишком большой размер"
                            : (detail ?? `Не удалось проверить файл (${request.status}).`),
                        request.status,
                    ),
                );
                return;
            }
            try {
                resolvePromise(JSON.parse(request.responseText) as RenovationPlanImportResult);
            } catch (reason) {
                reject(reason instanceof Error ? reason : new Error(String(reason)));
            }
        };
        request.onerror = () => reject(new Error("Не удалось загрузить файл."));
        request.onabort = () => reject(new Error("Загрузка файла прервана."));
        progress?.uploading();
        request.send(body);
    });
}

export type RenovationPlanImportApplyResult = {
    id: string;
    status: "pending" | "running" | "done" | "failed";
    progress: number;
    total: number;
    file_name: string;
    user_name?: string;
    error: string | null;
    new_rows: number;
    updated_rows: number;
    /** `parse` | `write` | `recompute` | `done` — the server's current stage. */
    phase: string | null;
};

/** The slice of the import job the progress bar reads. */
export type PlanImportJobState = Pick<
    RenovationPlanImportApplyResult,
    "progress" | "total" | "phase"
>;

/**
 * Load a checked Excel file (`.xlsb` or `.xlsx`) into the working plan.
 *
 * The backend stages the workbook and returns a durable job. `expectedRows` is
 * the preflight's row count and lets the server size its progress bar to the
 * write and recompute halves; the caller may poll
 * `getRenovationPlanImportJob` while the worker runs.
 */
export async function applyRenovationPlanImport(
    file: File,
    expectedRows: number,
): Promise<RenovationPlanImportApplyResult> {
    const body = new FormData();
    body.append("file", file);
    body.append("expected_rows", String(Math.max(0, Math.trunc(expectedRows))));
    const response = await fetch("/api/renovation/plan/import/apply", {
        method: "POST",
        body,
        cache: "no-store",
    });
    if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as unknown;
        throw new ReportCollectorApiError(
            readApiDetail(payload) ?? `Не удалось загрузить файл (${response.status}).`,
            response.status,
        );
    }
    return (await response.json()) as RenovationPlanImportApplyResult;
}

export async function getRenovationPlanImportJob(
    jobId: string,
): Promise<RenovationPlanImportApplyResult> {
    const response = await fetch(`/api/renovation/plan/import/jobs/${encodeURIComponent(jobId)}`, {
        cache: "no-store",
    });
    if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as unknown;
        throw new ReportCollectorApiError(
            readApiDetail(payload) ??
                `Не удалось получить состояние загрузки (${response.status}).`,
            response.status,
        );
    }
    return (await response.json()) as RenovationPlanImportApplyResult;
}

/**
 * The active import that blocks the plan form (Gate A). Every member may read
 * it: the importer's FIO and live progress come from the durable job row.
 */
export async function getRenovationActivePlanImport(): Promise<RenovationPlanImportApplyResult | null> {
    const response = await fetch("/api/renovation/plan/import/active", { cache: "no-store" });
    if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as unknown;
        throw new ReportCollectorApiError(
            readApiDetail(payload) ??
                `Не удалось получить состояние загрузки (${response.status}).`,
            response.status,
        );
    }
    return (await response.json()) as RenovationPlanImportApplyResult | null;
}
