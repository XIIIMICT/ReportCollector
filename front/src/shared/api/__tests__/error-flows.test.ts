import { afterEach, expect, test } from "bun:test";

import { ReportCollectorApiError } from "../api-error";
import { getAuthConfig } from "../auth-config";
import { downloadTableExport, getTableExportJob } from "../table-export";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
});

function mockFetch(response: Response | (() => never)): void {
    globalThis.fetch = Object.assign(
        async () => {
            if (response instanceof Response) return response;
            return response();
        },
        { preconnect: originalFetch.preconnect },
    ) as typeof fetch;
}

function json(body: unknown, status = 200, requestId?: string): Response {
    const init: ResponseInit = { status };
    if (requestId) init.headers = { "X-Request-ID": requestId };
    return Response.json(body, init);
}

test("a 202 with a malformed job body fails instead of returning empty data", async () => {
    mockFetch(
        new Response("{bad", { status: 202, headers: { "Content-Type": "application/json" } }),
    );
    await expect(
        downloadTableExport("/api/raw-materials/export", {}, (result) => result),
    ).rejects.toBeInstanceOf(ReportCollectorApiError);
});

test("a 202 job with an incomplete descriptor fails", async () => {
    mockFetch(json({ id: "job-1", status: "pending" }, 202));
    await expect(
        downloadTableExport("/api/raw-materials/export", {}, (result) => result),
    ).rejects.toMatchObject({ message: "Сервер вернул неожиданное состояние задания." });
});

test("polling preserves a typed job error and its request id", async () => {
    mockFetch(json({ detail: "Задание отменено." }, 409, "poll-id"));
    await expect(
        getTableExportJob("/api/raw-materials/export/jobs", "job-1"),
    ).rejects.toMatchObject({
        message: "Задание отменено.",
        status: 409,
        requestId: "poll-id",
    });
});

test("polling maps abort and network failures to fixed Russian messages", async () => {
    mockFetch(() => {
        throw new DOMException("aborted", "AbortError");
    });
    await expect(
        getTableExportJob("/api/raw-materials/export/jobs", "job-1"),
    ).rejects.toMatchObject({
        message: "Запрос прерван.",
    });
    mockFetch(() => {
        throw new TypeError("network");
    });
    await expect(
        getTableExportJob("/api/raw-materials/export/jobs", "job-1"),
    ).rejects.toMatchObject({
        message: "Не удалось связаться с сервером.",
    });
});

test("an invalid auth config body fails instead of disabling Keycloak silently", async () => {
    mockFetch(json({ keycloakEnabled: "yes" }));
    await expect(getAuthConfig()).rejects.toBeInstanceOf(ReportCollectorApiError);
    mockFetch(json({ keycloakEnabled: false }));
    await expect(getAuthConfig()).resolves.toEqual({ keycloakEnabled: false });
});
