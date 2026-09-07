import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";

export const SESSION_COOKIE = "rc_session";
export const AUTH_STATE_COOKIE = "rc_auth_state";
export const AUTH_VERIFIER_COOKIE = "rc_auth_verifier";
export const AUTH_NONCE_COOKIE = "rc_auth_nonce";
export const AUTH_RETURN_TO_COOKIE = "rc_auth_return_to";

export type AuthSession = {
    expiresAt: number;
    email?: string;
    emailVerified?: boolean;
    familyName?: string;
    givenName?: string;
    issuer: string;
    name?: string;
    preferredUsername?: string;
    sub: string;
};

export type TokenEndpointAuthMethod = "client_secret_basic" | "client_secret_post";

export type AuthConfig = {
    appOrigin: string;
    clientId: string;
    clientSecret: string;
    cookieSecure: boolean;
    internalIssuer: string;
    publicIssuer: string;
    scopes: string;
    secret: string;
    signingAlgorithms: string[];
    tokenEndpointAuthMethod: TokenEndpointAuthMethod;
    tokenIssuer: string;
};

export type SessionConfig = {
    appOrigin: string;
    cookieSecure: boolean;
    secret: string;
};

export function isKeycloakEnabled(): boolean {
    return process.env.KEYCLOAK_ENABLED !== "false";
}

/** Session lifetime (seconds). Independent of the Keycloak token TTL (5–10 min). */
export function sessionMaxAgeSeconds(): number {
    const raw = Number(process.env.AUTH_SESSION_MAX_AGE_SECONDS ?? "");
    return Number.isFinite(raw) && raw > 0 ? raw : 12 * 60 * 60;
}

export async function getSession(): Promise<AuthSession | null> {
    const cookieStore = await cookies();
    const cookieValue = cookieStore.get(SESSION_COOKIE)?.value;

    return cookieValue ? decodeSession(cookieValue) : null;
}

export function getAuthConfig(): AuthConfig {
    const sessionConfig = getSessionConfig();
    if (!isKeycloakEnabled()) {
        throw new Error("Конфигурация Keycloak запрошена при KEYCLOAK_ENABLED=false.");
    }
    const publicIssuer = getRequiredEnv("AUTH_KEYCLOAK_PUBLIC_ISSUER");
    const tokenEndpointAuthMethod =
        process.env.AUTH_KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD ?? "client_secret_basic";
    if (
        tokenEndpointAuthMethod !== "client_secret_basic" &&
        tokenEndpointAuthMethod !== "client_secret_post"
    ) {
        throw new Error(
            "AUTH_KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD должен быть client_secret_basic или client_secret_post.",
        );
    }

    return {
        appOrigin: sessionConfig.appOrigin,
        clientId: getRequiredEnv("AUTH_KEYCLOAK_CLIENT_ID"),
        clientSecret: getRequiredEnv("AUTH_KEYCLOAK_CLIENT_SECRET"),
        cookieSecure: sessionConfig.cookieSecure,
        internalIssuer: trimTrailingSlash(
            process.env.AUTH_KEYCLOAK_INTERNAL_ISSUER || publicIssuer,
        ),
        publicIssuer: trimTrailingSlash(publicIssuer),
        scopes: process.env.AUTH_KEYCLOAK_SCOPES?.trim() || "openid profile email",
        secret: sessionConfig.secret,
        signingAlgorithms: parseList(process.env.AUTH_KEYCLOAK_SIGNING_ALGORITHMS || "RS256"),
        tokenEndpointAuthMethod,
        tokenIssuer: trimTrailingSlash(process.env.AUTH_KEYCLOAK_TOKEN_ISSUER || publicIssuer),
    };
}

export function getSessionConfig(): SessionConfig {
    const appOrigin = trimTrailingSlash(getRequiredEnv("AUTH_APP_ORIGIN"));
    return {
        appOrigin,
        cookieSecure: appOrigin.startsWith("https://"),
        secret: getRequiredEnv("AUTH_SECRET"),
    };
}

export function transientCookieOptions(config: AuthConfig) {
    return {
        httpOnly: true,
        maxAge: 10 * 60,
        path: "/",
        sameSite: "lax" as const,
        secure: config.cookieSecure,
    };
}

export function sessionCookieOptions(config: SessionConfig, maxAge: number) {
    return {
        httpOnly: true,
        maxAge,
        path: "/",
        sameSite: "lax" as const,
        secure: config.cookieSecure,
    };
}

export function clearCookieOptions(config: SessionConfig) {
    return {
        httpOnly: true,
        maxAge: 0,
        path: "/",
        sameSite: "lax" as const,
        secure: config.cookieSecure,
    };
}

export function encodeSession(session: AuthSession): string {
    const payload = encodeBase64Url(Buffer.from(JSON.stringify(session), "utf8"));
    return `${payload}.${sign(payload)}`;
}

export function decodeSession(value: string): AuthSession | null {
    const [payload, signature, extra] = value.split(".");
    if (!payload || !signature || extra !== undefined || !verifySignature(payload, signature)) {
        return null;
    }

    try {
        const session = JSON.parse(decodeBase64Url(payload).toString("utf8")) as AuthSession;
        if (
            !session ||
            typeof session !== "object" ||
            typeof session.expiresAt !== "number" ||
            !Number.isFinite(session.expiresAt) ||
            session.expiresAt <= Date.now() ||
            typeof session.sub !== "string" ||
            !session.sub ||
            typeof session.issuer !== "string" ||
            !session.issuer
        ) {
            return null;
        }

        return session;
    } catch {
        return null;
    }
}

export function randomBase64Url(byteLength = 32): string {
    return encodeBase64Url(randomBytes(byteLength));
}

export function sha256Base64Url(value: string): string {
    return encodeBase64Url(createHash("sha256").update(value).digest());
}

export function stringClaim(payload: Record<string, unknown>, claim: string): string | undefined {
    const value = payload[claim];
    return typeof value === "string" ? value : undefined;
}

function sign(payload: string): string {
    return encodeBase64Url(
        createHmac("sha256", getSessionConfig().secret).update(payload).digest(),
    );
}

function verifySignature(payload: string, signature: string): boolean {
    const expected = sign(payload);
    const expectedBuffer = Buffer.from(expected);
    const signatureBuffer = Buffer.from(signature);

    return (
        expectedBuffer.length === signatureBuffer.length &&
        timingSafeEqual(expectedBuffer, signatureBuffer)
    );
}

function encodeBase64Url(value: Buffer): string {
    return value.toString("base64url");
}

function decodeBase64Url(value: string): Buffer {
    return Buffer.from(value, "base64url");
}

function getRequiredEnv(name: string): string {
    const value = process.env[name];
    if (!value) {
        throw new Error(
            `Переменная окружения ${name} обязательна для выбранного режима авторизации.`,
        );
    }

    return value;
}

function trimTrailingSlash(value: string): string {
    return value.replace(/\/+$/, "");
}

function parseList(value: string): string[] {
    const items = value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    if (!items.length) {
        throw new Error("Список допустимых алгоритмов подписи OIDC не может быть пустым.");
    }
    return items;
}

/**
 * A safe post-authentication redirect target: only relative in-app paths
 * ("/raw-materials"), never protocol-relative ("//host") or absolute URLs.
 */
export function safeReturnTo(value: string | null | undefined): string | undefined {
    if (!value) return undefined;
    if (!value.startsWith("/") || value.startsWith("//")) return undefined;
    if (value.includes("\\") || value.includes("\n") || value.includes("\r")) return undefined;
    return value;
}
