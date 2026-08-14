import { Buffer } from "node:buffer";

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
    AUTH_NONCE_COOKIE,
    AUTH_STATE_COOKIE,
    AUTH_VERIFIER_COOKIE,
    SESSION_COOKIE,
    clearCookieOptions,
    encodeSession,
    getAuthConfig,
    sessionCookieOptions,
    sessionMaxAgeSeconds,
    stringClaim,
    type AuthConfig,
    type AuthSession,
} from "@/shared/auth/session";
import { logServerEvent } from "@/shared/logging/server";

type TokenResponse = {
    access_token?: string;
    error?: string;
    error_description?: string;
    expires_in?: number;
    id_token?: string;
    token_type?: string;
};

type UserInfo = Record<string, unknown> & { sub?: string };

const jwksByUrl = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
    const config = getAuthConfig();
    const requestUrl = new URL(request.url);
    const oidcErrorCode = requestUrl.searchParams.get("error");
    if (oidcErrorCode) {
        return oidcError(
            oidcErrorCode,
            requestUrl.searchParams.get("error_description") || "Keycloak отклонил авторизацию.",
        );
    }

    const code = requestUrl.searchParams.get("code");
    const state = requestUrl.searchParams.get("state");
    const cookieStore = await cookies();
    const expectedState = cookieStore.get(AUTH_STATE_COOKIE)?.value;
    const codeVerifier = cookieStore.get(AUTH_VERIFIER_COOKIE)?.value;
    const expectedNonce = cookieStore.get(AUTH_NONCE_COOKIE)?.value;

    if (
        !code ||
        !state ||
        !expectedState ||
        state !== expectedState ||
        !codeVerifier ||
        !expectedNonce
    ) {
        return restartLogin(config);
    }

    try {
        const redirectUri = `${config.appOrigin}/api/auth/callback`;
        const tokenResponse = await exchangeCode(config, code, codeVerifier, redirectUri);
        if (!tokenResponse.id_token || !tokenResponse.access_token) {
            throw new Error(
                tokenResponse.error_description ||
                    tokenResponse.error ||
                    "Keycloak не вернул ID token или access token.",
            );
        }

        const idTokenPayload = await verifyIdToken(config, tokenResponse.id_token, expectedNonce);
        const userInfo = await readUserInfo(config, tokenResponse.access_token);
        if (!userInfo.sub || userInfo.sub !== idTokenPayload.sub) {
            throw new Error("Идентификатор пользователя в UserInfo не совпадает с ID token.");
        }

        const session = buildSession(idTokenPayload, userInfo);
        const sessionMaxAge = Math.max(1, Math.floor((session.expiresAt - Date.now()) / 1000));
        const response = NextResponse.redirect(config.appOrigin);
        const clearOptions = clearCookieOptions(config);
        response.cookies.set(AUTH_STATE_COOKIE, "", clearOptions);
        response.cookies.set(AUTH_VERIFIER_COOKIE, "", clearOptions);
        response.cookies.set(AUTH_NONCE_COOKIE, "", clearOptions);
        response.cookies.set(
            SESSION_COOKIE,
            encodeSession(session),
            sessionCookieOptions(config, sessionMaxAge),
        );
        await logAuthEvent("oidc_authentication_succeeded", "Keycloak подтвердил пользователя.", {
            email: session.email,
            email_verified: session.emailVerified,
            family_name: session.familyName,
            given_name: session.givenName,
            issuer: session.issuer,
            name: session.name,
            preferred_username: session.preferredUsername,
            sub: session.sub,
        });
        return response;
    } catch (error) {
        const message = error instanceof Error ? error.message : "Неизвестная ошибка OIDC.";
        await logAuthEvent(
            "oidc_authentication_failed",
            "Не удалось завершить авторизацию через Keycloak.",
            {
                error: message,
            },
        );
        return oidcError("authentication_failed", message);
    }
}

async function exchangeCode(
    config: AuthConfig,
    code: string,
    codeVerifier: string,
    redirectUri: string,
): Promise<TokenResponse> {
    const body = new URLSearchParams({
        client_id: config.clientId,
        code,
        code_verifier: codeVerifier,
        grant_type: "authorization_code",
        redirect_uri: redirectUri,
    });
    const headers = new Headers({ "content-type": "application/x-www-form-urlencoded" });
    if (config.tokenEndpointAuthMethod === "client_secret_basic") {
        const credentials = Buffer.from(
            `${config.clientId}:${config.clientSecret}`,
            "utf8",
        ).toString("base64");
        headers.set("authorization", `Basic ${credentials}`);
    } else {
        body.set("client_secret", config.clientSecret);
    }

    const response = await fetch(`${config.internalIssuer}/protocol/openid-connect/token`, {
        body,
        headers,
        method: "POST",
    });
    const payload = (await response.json()) as TokenResponse;
    if (!response.ok) {
        throw new Error(
            payload.error_description ||
                payload.error ||
                `Keycloak вернул HTTP ${response.status}.`,
        );
    }
    return payload;
}

async function verifyIdToken(
    config: AuthConfig,
    idToken: string,
    expectedNonce: string,
): Promise<JWTPayload> {
    const jwksUrl = `${config.internalIssuer}/protocol/openid-connect/certs`;
    let jwks = jwksByUrl.get(jwksUrl);
    if (!jwks) {
        jwks = createRemoteJWKSet(new URL(jwksUrl));
        jwksByUrl.set(jwksUrl, jwks);
    }
    const { payload } = await jwtVerify(idToken, jwks, {
        algorithms: config.signingAlgorithms,
        audience: config.clientId,
        issuer: config.tokenIssuer,
    });
    if (stringClaim(payload, "nonce") !== expectedNonce) {
        throw new Error("Nonce в ответе Keycloak не совпадает с ожидаемым.");
    }
    if (!payload.sub || !payload.exp) {
        throw new Error("В подтверждённом ID token отсутствуют обязательные claims sub или exp.");
    }
    return payload;
}

async function readUserInfo(config: AuthConfig, accessToken: string): Promise<UserInfo> {
    const response = await fetch(`${config.internalIssuer}/protocol/openid-connect/userinfo`, {
        headers: { authorization: `Bearer ${accessToken}` },
    });
    if (!response.ok) {
        throw new Error(`Не удалось получить UserInfo: Keycloak вернул HTTP ${response.status}.`);
    }
    return (await response.json()) as UserInfo;
}

function buildSession(idTokenPayload: JWTPayload, userInfo: UserInfo): AuthSession {
    const subject = idTokenPayload.sub;
    if (!subject || !idTokenPayload.exp) {
        throw new Error("В ID token отсутствуют обязательные claims sub или exp.");
    }
    // Сессия живёт по своей долготе (AUTH_SESSION_MAX_AGE_SECONDS), а не до exp
    // ID token (у Keycloak это минуты) — иначе пользователя выкидывает "не авторизован".

    const givenName = claimFromUserInfo("given_name", userInfo, idTokenPayload);
    const familyName = claimFromUserInfo("family_name", userInfo, idTokenPayload);
    return {
        email: claimFromUserInfo("email", userInfo, idTokenPayload),
        emailVerified: booleanClaim(userInfo, idTokenPayload, "email_verified"),
        expiresAt: Date.now() + sessionMaxAgeSeconds() * 1000,
        familyName,
        givenName,
        issuer: idTokenPayload.iss || "",
        name:
            claimFromUserInfo("name", userInfo, idTokenPayload) ||
            [givenName, familyName].filter(Boolean).join(" ") ||
            undefined,
        preferredUsername: claimFromUserInfo("preferred_username", userInfo, idTokenPayload),
        sub: subject,
    };
}

function claimFromUserInfo(
    claim: string,
    userInfo: UserInfo,
    idTokenPayload: JWTPayload,
): string | undefined {
    return stringClaim(userInfo, claim) ?? stringClaim(idTokenPayload, claim);
}

function booleanClaim(
    userInfo: UserInfo,
    idTokenPayload: JWTPayload,
    claim: string,
): boolean | undefined {
    const value = userInfo[claim] ?? idTokenPayload[claim];
    return typeof value === "boolean" ? value : undefined;
}

function oidcError(error: string, description: string) {
    return NextResponse.json(
        {
            detail: description,
            error,
        },
        { status: 400 },
    );
}

function restartLogin(config: AuthConfig) {
    const response = NextResponse.redirect(`${config.appOrigin}/api/auth/login`);
    const clearOptions = clearCookieOptions(config);
    response.cookies.set(AUTH_STATE_COOKIE, "", clearOptions);
    response.cookies.set(AUTH_VERIFIER_COOKIE, "", clearOptions);
    response.cookies.set(AUTH_NONCE_COOKIE, "", clearOptions);
    return response;
}

async function logAuthEvent(event: string, message: string, details: Record<string, unknown>) {
    await logServerEvent(event, message, details, event.endsWith("failed") ? "ERROR" : "INFO");
}
