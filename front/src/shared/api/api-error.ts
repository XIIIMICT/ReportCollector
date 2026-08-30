import { ReportCollectorApiError } from "./report-collector";

/**
 * Shared API plumbing: JSON/body request helpers and pydantic error parsing.
 * Both the raw-materials and renovation API layers forward through the same
 * Next.js proxy and share the error shape, so the fetch/error logic lives here
 * to avoid copy-paste drift.
 */

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
export function readApiDetail(payload: unknown): string | null {
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

export async function requestJson<T>(path: string): Promise<T> {
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

export async function requestWrite<T>(
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
