import { afterEach, expect, test } from "bun:test";

import {
    apiErrorFromResponse,
    readApiDetail,
    readJsonBody,
    ReportCollectorApiError,
    requestJson,
    requestWrite,
} from "../api-error";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
});

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
    return Response.json(body, init);
}

test.each([null, {}, { detail: 42 }, { detail: [null, {}] }])(
    "malformed error payload uses fallback",
    (payload) => {
        expect(readApiDetail(payload)).toBeNull();
    },
);

test("pydantic validation errors become readable messages", () => {
    expect(
        readApiDetail({
            detail: [{ msg: "Value error, Неверное значение" }, null, { msg: "Нет доступа" }],
        }),
    ).toBe("Неверное значение; Нет доступа");
});

test("typed detail objects and PII-bearing fields stay discard-safe", () => {
    expect(readApiDetail({ detail: { msg: "Нет доступа" } })).toBe("Нет доступа");
    const payload = {
        detail: [
            {
                type: "value_error",
                loc: ["body", "email"],
                msg: "Некорректный адрес",
                input: "private@example.ru",
            },
        ],
    };
    expect(readApiDetail(payload)).toBe("Некорректный адрес");
    expect(readApiDetail(payload)).not.toContain("private@example.ru");
});

test("GET does not cache and parses JSON", async () => {
    globalThis.fetch = (async (path, init) => {
        expect(path).toBe("/api/test");
        expect(init?.cache).toBe("no-store");
        return jsonResponse({ rows: [1] });
    }) as typeof fetch;
    expect(await requestJson<{ rows: number[] }>("/api/test")).toEqual({ rows: [1] });
});

test.each(["POST", "PATCH", "DELETE"] as const)(
    "%s respects the body/204 contract",
    async (method) => {
        globalThis.fetch = (async (_path, init) => {
            expect(init?.method).toBe(method);
            expect(init?.body).toBe(method === "DELETE" ? undefined : JSON.stringify({ value: 0 }));
            return new Response(null, { status: 204 });
        }) as typeof fetch;
        expect(await requestWrite("/api/test", method, { value: 0 })).toBeUndefined();
    },
);

test("count-returning writes accept a JSON scalar without loosening the strict default", async () => {
    globalThis.fetch = Object.assign(async () => jsonResponse(1), {
        preconnect: originalFetch.preconnect,
    });
    expect(await requestWrite<number>("/api/test", "PATCH", {}, { allowScalar: true })).toBe(1);
    await expect(requestWrite("/api/test", "PATCH", {})).rejects.toBeInstanceOf(
        ReportCollectorApiError,
    );

    // Opting in to scalars does not accept a wrong content type.
    globalThis.fetch = Object.assign(
        async () => new Response("1", { headers: { "Content-Type": "text/plain" } }),
        { preconnect: originalFetch.preconnect },
    );
    await expect(
        requestWrite("/api/test", "PATCH", {}, { allowScalar: true }),
    ).rejects.toBeInstanceOf(ReportCollectorApiError);
});

test("non-JSON gateway errors retain their HTTP status", async () => {
    globalThis.fetch = Object.assign(async () => new Response("bad gateway", { status: 502 }), {
        preconnect: originalFetch.preconnect,
    });
    await expect(requestJson("/api/test")).rejects.toMatchObject({ status: 502 });
    await expect(requestWrite("/api/test", "PATCH", {})).rejects.toMatchObject({ status: 502 });
});

test("typed error envelopes preserve detail, status and the request id", async () => {
    const error = await apiErrorFromResponse(
        jsonResponse(
            { detail: "Нет доступа.", request_id: "payload-id" },
            {
                status: 403,
                headers: { "X-Request-ID": "header-id" },
            },
        ),
        "fallback",
    );
    expect(error).toBeInstanceOf(ReportCollectorApiError);
    expect(error.status).toBe(403);
    expect(error.requestId).toBe("header-id");
    expect(error.message).toBe("Нет доступа.");
});

test("a typed request id in the payload is used when the header is absent", async () => {
    const error = await apiErrorFromResponse(
        jsonResponse({ detail: "Ошибка", request_id: "payload-id" }, { status: 422 }),
        "fallback",
    );
    expect(error.requestId).toBe("payload-id");
});

test("wrong content type and malformed error bodies fall back without leaking body text", async () => {
    const html = await apiErrorFromResponse(
        new Response("<html>private</html>", {
            status: 500,
            headers: { "Content-Type": "text/html" },
        }),
        "Некорректный ответ сервера.",
    );
    expect(html.message).toBe("Некорректный ответ сервера.");
    expect(html.message).not.toContain("private");

    const broken = await apiErrorFromResponse(
        new Response("{not json", {
            status: 500,
            headers: { "Content-Type": "application/json" },
        }),
        "Некорректный ответ сервера.",
    );
    expect(broken.message).toBe("Некорректный ответ сервера.");
});

test("success bodies with the wrong content type or invalid shape throw", async () => {
    await expect(
        readJsonBody(new Response("ok", { headers: { "Content-Type": "text/plain" } })),
    ).rejects.toBeInstanceOf(ReportCollectorApiError);
    await expect(
        readJsonBody(new Response("[]", { headers: { "Content-Type": "application/json" } })),
    ).resolves.toEqual([]);
    await expect(
        readJsonBody(new Response("42", { headers: { "Content-Type": "application/json" } })),
    ).rejects.toBeInstanceOf(ReportCollectorApiError);
    await expect(
        readJsonBody(new Response("null", { headers: { "Content-Type": "application/json" } })),
    ).rejects.toBeInstanceOf(ReportCollectorApiError);
    await expect(
        readJsonBody(
            new Response("null", { headers: { "Content-Type": "application/json" } }),
            true,
        ),
    ).resolves.toBeNull();
});

test("network failures never surface the raw browser message", async () => {
    globalThis.fetch = Object.assign(
        async () => {
            throw new TypeError("Failed to fetch http://internal.example/private");
        },
        { preconnect: originalFetch.preconnect },
    );
    await expect(requestJson("/api/test")).rejects.toMatchObject({
        message: "Не удалось связаться с сервером.",
    });
    await expect(requestWrite("/api/test", "PATCH", {})).rejects.toMatchObject({
        message: "Не удалось связаться с сервером.",
    });
});

test("abort and timeout are reported as interruption, not empty data", async () => {
    globalThis.fetch = Object.assign(
        async () => {
            throw new DOMException("The operation was aborted", "AbortError");
        },
        { preconnect: originalFetch.preconnect },
    );
    await expect(requestJson("/api/test")).rejects.toMatchObject({ message: "Запрос прерван." });

    globalThis.fetch = Object.assign(
        async () => {
            throw new DOMException("timeout", "TimeoutError");
        },
        { preconnect: originalFetch.preconnect },
    );
    await expect(requestWrite("/api/test", "POST", {})).rejects.toMatchObject({
        message: "Запрос прерван.",
    });
});

test("a successful response with an unexpected body shape does not become empty data", async () => {
    globalThis.fetch = Object.assign(async () => new Response("ok", { status: 200 }), {
        preconnect: originalFetch.preconnect,
    });
    await expect(requestJson("/api/test")).rejects.toBeInstanceOf(ReportCollectorApiError);
});
