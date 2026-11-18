import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createHmac } from "node:crypto";

import {
    decodeSession,
    encodeSession,
    getSessionConfig,
    safeReturnTo,
    sessionCookieOptions,
    sessionMaxAgeSeconds,
} from "../session";

const saved = { ...process.env };
beforeEach(() => {
    process.env.AUTH_SECRET = "test-only-secret-at-least-32-characters";
    process.env.AUTH_APP_ORIGIN = "https://app.example.ru/";
});
afterEach(() => {
    for (const key of ["AUTH_SECRET", "AUTH_APP_ORIGIN", "AUTH_SESSION_MAX_AGE_SECONDS"]) {
        if (saved[key] === undefined) delete process.env[key];
        else process.env[key] = saved[key];
    }
});

describe("signed sessions", () => {
    const session = () => ({ sub: "user", issuer: "local", expiresAt: Date.now() + 60000 });
    test("roundtrips and rejects tampering or a different secret", () => {
        const value = session();
        const token = encodeSession(value);
        expect(decodeSession(token)).toEqual(value);
        expect(decodeSession(`x${token}`)).toBeNull();
        process.env.AUTH_SECRET = "another-secret";
        expect(decodeSession(token)).toBeNull();
    });
    test.each(["", "payload", ".signature", "payload.x"])("rejects malformed token %s", (token) => {
        expect(decodeSession(token)).toBeNull();
    });
    test("rejects expired and structurally invalid signed payloads", () => {
        expect(
            decodeSession(encodeSession({ ...session(), expiresAt: Date.now() - 1 })),
        ).toBeNull();
        for (const invalid of [
            { sub: "user" },
            { ...session(), expiresAt: "tomorrow" },
            null,
            [],
        ]) {
            const payload = Buffer.from(JSON.stringify(invalid)).toString("base64url");
            const signature = createHmac("sha256", process.env.AUTH_SECRET!)
                .update(payload)
                .digest("base64url");
            expect(decodeSession(`${payload}.${signature}`)).toBeNull();
        }
        expect(decodeSession(`${encodeSession(session())}.extra`)).toBeNull();
    });
    test("uses secure HttpOnly cookies and normalizes the origin", () => {
        const config = getSessionConfig();
        expect(config.appOrigin).toBe("https://app.example.ru");
        expect(sessionCookieOptions(config, 60)).toEqual({
            httpOnly: true,
            secure: true,
            sameSite: "lax",
            path: "/",
            maxAge: 60,
        });
    });
    test.each(["", "0", "-1", "NaN", "Infinity"])("invalid TTL %s uses the default", (ttl) => {
        process.env.AUTH_SESSION_MAX_AGE_SECONDS = ttl;
        // The default is the practical maximum a browser keeps a cookie: the
        // session outlives stand restarts and the Keycloak SSO session.
        expect(sessionMaxAgeSeconds()).toBe(400 * 24 * 60 * 60);
    });
});

describe("return URL", () => {
    test.each(["https://evil.ru", "//evil.ru", "/\\evil.ru", "/\nLocation: evil", "/\rheader", ""])(
        "rejects external or unsafe destination %s",
        (path) => {
            expect(safeReturnTo(path)).toBeUndefined();
        },
    );
    test("preserves a relative project deep link", () => {
        expect(safeReturnTo("/raw-materials?month=2026-08")).toBe("/raw-materials?month=2026-08");
    });
});
