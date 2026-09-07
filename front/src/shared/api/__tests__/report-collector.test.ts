import { afterEach, describe, expect, test } from "bun:test";

import { ReportCollectorApiError, updateProfile } from "../report-collector";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
});

describe("profile API", () => {
    test("sends a PATCH and normalizes the validated response", async () => {
        globalThis.fetch = (async (url, options) => {
            expect(url).toBe("/api/me");
            expect(options?.method).toBe("PATCH");
            expect(JSON.parse(String(options?.body))).toEqual({ theme: "dark" });
            return Response.json({ email: null, name: null, theme: "dark" });
        }) as typeof fetch;
        expect(await updateProfile({ theme: "dark" })).toEqual({
            access: undefined,
            email: undefined,
            name: undefined,
            theme: "dark",
        });
    });

    test("rejects an invalid server response", async () => {
        globalThis.fetch = Object.assign(async () => Response.json({ email: 123, name: null }), {
            preconnect: originalFetch.preconnect,
        });
        await expect(updateProfile({ theme: "dark" })).rejects.toThrow();
    });

    test("preserves API status and the user-facing error", async () => {
        globalThis.fetch = Object.assign(
            async () => Response.json({ detail: "Нет доступа." }, { status: 403 }),
            { preconnect: originalFetch.preconnect },
        );
        try {
            await updateProfile({ theme: "dark" });
            throw new Error("Expected the request to fail");
        } catch (error) {
            expect(error).toBeInstanceOf(ReportCollectorApiError);
            expect((error as ReportCollectorApiError).status).toBe(403);
            expect((error as Error).message).toBe("Нет доступа.");
        }
    });
});
