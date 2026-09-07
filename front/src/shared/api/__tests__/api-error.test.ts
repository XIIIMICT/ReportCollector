import { afterEach, expect, test } from "bun:test";

import { readApiDetail, requestJson, requestWrite } from "../api-error";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
});

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
test("GET does not cache and parses JSON", async () => {
    globalThis.fetch = (async (path, init) => {
        expect(path).toBe("/api/test");
        expect(init?.cache).toBe("no-store");
        return Response.json({ rows: [1] });
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
test("non-JSON gateway errors retain their HTTP status", async () => {
    globalThis.fetch = Object.assign(async () => new Response("bad gateway", { status: 502 }), {
        preconnect: originalFetch.preconnect,
    });
    await expect(requestJson("/api/test")).rejects.toMatchObject({ status: 502 });
    await expect(requestWrite("/api/test", "PATCH", {})).rejects.toMatchObject({ status: 502 });
});
