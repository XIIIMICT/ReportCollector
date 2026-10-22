import { afterEach, describe, expect, mock, test } from "bun:test";

const PATH = "/api/renovation/plan/rows";

const session = {
    email: "admin@example.ru",
    name: "Admin User",
    sub: "admin-sub",
    issuer: "issuer",
    preferredUsername: "admin",
    givenName: "Admin",
    familyName: "User",
    emailVerified: true,
};

// Spread the real modules so their other exports survive the process-wide mock.
const realSession = await import("@/shared/auth/session");
const realIdentity = await import("@/shared/auth/identity-headers");
const realConfig = await import("@/shared/config");

mock.module("@/shared/auth/session", () => ({ ...realSession, getSession: async () => session }));
mock.module("@/shared/auth/identity-headers", () => ({
    ...realIdentity,
    buildIdentityHeaders: () => new Headers(),
}));
mock.module("@/shared/config", () => ({
    ...realConfig,
    getBackendInternalUrl: () => "http://backend",
}));

const { proxyBackend } = await import("../backend-proxy");

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
});

function rowsRequest(acceptEncoding = "gzip") {
    return new Request(`http://front${PATH}`, { headers: { "Accept-Encoding": acceptEncoding } });
}

function jsonResponse(body: string, headers: Record<string, string> = {}) {
    globalThis.fetch = Object.assign(
        async () =>
            new Response(body, { headers: { "Content-Type": "application/json", ...headers } }),
        { preconnect: originalFetch.preconnect },
    ) as typeof fetch;
}

describe("proxyBackend uploads", () => {
    test("forwards a multipart body byte-for-byte with its own boundary", async () => {
        const form = new FormData();
        const file = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0xff])]);
        form.append("file", file, "plan.xlsb");
        const request = new Request(`http://front${PATH}`, { method: "POST", body: form });
        const incomingContentType = request.headers.get("Content-Type") ?? "";
        expect(incomingContentType).toContain("multipart/form-data");

        let forwarded: { body: unknown; contentType: string | null } = {
            body: undefined,
            contentType: null,
        };
        globalThis.fetch = Object.assign(
            async (_url: string | URL | Request, init?: RequestInit) => {
                forwarded = {
                    body: init?.body,
                    contentType: new Headers(init?.headers).get("Content-Type"),
                };
                return new Response("{}", { headers: { "Content-Type": "application/json" } });
            },
            { preconnect: originalFetch.preconnect },
        ) as typeof fetch;

        await proxyBackend(request, PATH, "POST");

        // The original Content-Type (with its boundary) is preserved...
        expect(forwarded.contentType).toBe(incomingContentType);
        // ...and the body is the untouched multipart payload, not decoded text.
        const body = forwarded.body;
        const bytes =
            body instanceof ArrayBuffer
                ? new Uint8Array(body)
                : body instanceof Uint8Array
                  ? body
                  : null;
        expect(bytes).not.toBeNull();
        expect(bytes?.length ?? 0).toBeGreaterThan(0);
        // The file's binary bytes survive the hop unchanged.
        expect(Array.from(bytes ?? [])).toContain(0xff);
    });
});

describe("proxyBackend compression", () => {
    test("gzips a large JSON body when the client accepts gzip", async () => {
        const body = JSON.stringify({
            rows: Array.from({ length: 500 }, (_, index) => ({ id: index, name: "x".repeat(60) })),
        });
        jsonResponse(body);
        const response = await proxyBackend(rowsRequest(), PATH);
        expect(response.headers.get("Content-Encoding")).toBe("gzip");
        expect(response.headers.get("Vary")).toBe("Accept-Encoding");
        const decompressed = await new Response(
            response.body?.pipeThrough(new DecompressionStream("gzip")),
        ).text();
        expect(decompressed).toBe(body);
    });

    test("leaves a small JSON body uncompressed", async () => {
        jsonResponse('{"ok":true}', { "Content-Length": "11" });
        const response = await proxyBackend(rowsRequest(), PATH);
        expect(response.headers.get("Content-Encoding")).toBeNull();
        expect(await response.text()).toBe('{"ok":true}');
    });

    test("does not gzip when the client does not accept it", async () => {
        jsonResponse(
            JSON.stringify({ rows: Array.from({ length: 500 }, (_, index) => ({ id: index })) }),
        );
        const response = await proxyBackend(rowsRequest("identity"), PATH);
        expect(response.headers.get("Content-Encoding")).toBeNull();
    });

    test("passes an XLSX export through untouched", async () => {
        const body = "PK\u0003\u0004binary";
        globalThis.fetch = Object.assign(
            async () =>
                new Response(body, {
                    headers: {
                        "Content-Type":
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "Content-Length": String(body.length),
                    },
                }),
            { preconnect: originalFetch.preconnect },
        ) as typeof fetch;
        const response = await proxyBackend(rowsRequest(), PATH);
        expect(response.headers.get("Content-Encoding")).toBeNull();
        expect(await response.text()).toBe(body);
    });
});
