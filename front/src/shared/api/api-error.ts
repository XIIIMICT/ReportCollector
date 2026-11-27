/**
 * Shared HTTP error plumbing for every browser API module and Next proxy.
 *
 * Rules that matter here:
 * - a malformed/non-JSON body is a FAILURE, never an empty success value;
 * - only curated Russian `detail` messages and pydantic `msg` strings are
 *   surfaced — validation `input`/`ctx`/`loc` never reach the UI;
 * - the opaque `X-Request-ID` (or a typed `request_id`) is carried on the error
 *   so a toast can be correlated with backend logs without exposing PII.
 */

const JSON_CONTENT_TYPE = /^application\/(?:[\w.+-]+\+)?json\b/i;
const SAFE_REQUEST_ID = /^[A-Za-z0-9._:-]{1,64}$/;

export class ReportCollectorApiError extends Error {
    constructor(
        message: string,
        readonly status?: number,
        readonly requestId?: string,
    ) {
        super(message);
        this.name = "ReportCollectorApiError";
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function safeRequestId(value: unknown): string | undefined {
    return typeof value === "string" && SAFE_REQUEST_ID.test(value) ? value : undefined;
}

export function isJsonContentType(contentType: string | null | undefined): boolean {
    return typeof contentType === "string" && JSON_CONTENT_TYPE.test(contentType);
}

/**
 * Human-readable API error description: the string detail, pydantic validation
 * messages (422), or a typed `{detail}` envelope — never submitted values.
 */
export function readApiDetail(payload: unknown): string | null {
    if (!isRecord(payload)) return null;
    const detail = payload.detail;
    if (typeof detail === "string") return detail.trim() || null;
    const items = Array.isArray(detail) ? detail : isRecord(detail) ? [detail] : [];
    const messages = items
        .map((item) => {
            if (!isRecord(item) || typeof item.msg !== "string") return null;
            const message = item.msg.replace(/^Value error,\s*/, "").trim();
            return message || null;
        })
        .filter((message): message is string => message !== null);
    return messages.length > 0 ? messages.join("; ") : null;
}

/** Build an error from an already-parsed payload (or null for an unusable body). */
export function apiErrorFromPayload(
    payload: unknown,
    status: number | undefined,
    requestId: string | null | undefined,
    fallback: string,
): ReportCollectorApiError {
    const id =
        safeRequestId(requestId) ??
        (isRecord(payload) ? safeRequestId(payload.request_id) : undefined);
    return new ReportCollectorApiError(readApiDetail(payload) ?? fallback, status, id);
}

async function readErrorPayload(response: Response): Promise<unknown> {
    if (!isJsonContentType(response.headers.get("Content-Type"))) return null;
    try {
        const value: unknown = JSON.parse(await response.text());
        return isRecord(value) ? value : null;
    } catch {
        return null;
    }
}

/** Convert an error response into a typed error, preserving status and id. */
export async function apiErrorFromResponse(
    response: Response,
    fallback: string,
): Promise<ReportCollectorApiError> {
    return apiErrorFromPayload(
        await readErrorPayload(response),
        response.status,
        response.headers.get("X-Request-ID"),
        fallback,
    );
}

/** Fixed Russian messages for failures that never reached a backend response. */
export function fetchFailure(reason: unknown, fallback: string): ReportCollectorApiError {
    if (reason instanceof ReportCollectorApiError) return reason;
    const name = isRecord(reason) && typeof reason.name === "string" ? reason.name : "";
    if (name === "AbortError" || name === "TimeoutError") {
        return new ReportCollectorApiError("Запрос прерван.");
    }
    if (reason instanceof TypeError) {
        return new ReportCollectorApiError("Не удалось связаться с сервером.");
    }
    return new ReportCollectorApiError(fallback);
}

/**
 * Parse a JSON response body once. A wrong content type, an empty/invalid body
 * or a non-object payload throws; no caller can mistake them for empty data.
 */
export function parseJsonPayload<T>(
    text: string,
    options: {
        contentType: string | null | undefined;
        status?: number;
        requestId?: string | null;
        fallback: string;
        allowNull?: boolean;
    },
): T {
    if (!isJsonContentType(options.contentType)) {
        throw new ReportCollectorApiError(
            options.fallback,
            options.status,
            safeRequestId(options.requestId),
        );
    }
    let value: unknown;
    try {
        value = JSON.parse(text);
    } catch {
        throw new ReportCollectorApiError(
            options.fallback,
            options.status,
            safeRequestId(options.requestId),
        );
    }
    if (value === null && options.allowNull) return value as T;
    if (value === null || typeof value !== "object") {
        throw new ReportCollectorApiError(
            options.fallback,
            options.status,
            safeRequestId(options.requestId),
        );
    }
    return value as T;
}

/** Read and validate a successful JSON response (204 is handled by the caller). */
export async function readJsonBody<T>(response: Response, allowNull = false): Promise<T> {
    let text: string;
    try {
        text = await response.text();
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось прочитать ответ сервера.");
    }
    return parseJsonPayload<T>(text, {
        contentType: response.headers.get("Content-Type"),
        status: response.status,
        requestId: response.headers.get("X-Request-ID"),
        fallback: `Некорректный ответ сервера (${response.status}).`,
        allowNull,
    });
}

async function fetchJson(path: string, init: RequestInit): Promise<Response> {
    try {
        return await fetch(path, init);
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось выполнить запрос.");
    }
}

export async function requestJson<T>(path: string): Promise<T> {
    const response = await fetchJson(path, { cache: "no-store" });
    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
    }
    return readJsonBody<T>(response);
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
    const response = await fetchJson(path, init);
    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
    }
    if (response.status === 204) {
        return undefined as T;
    }
    return readJsonBody<T>(response);
}
