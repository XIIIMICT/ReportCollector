import { NextResponse } from "next/server";

import {
    AUTH_NONCE_COOKIE,
    AUTH_STATE_COOKIE,
    AUTH_VERIFIER_COOKIE,
    SESSION_COOKIE,
    encodeSession,
    getAuthConfig,
    getSessionConfig,
    isKeycloakEnabled,
    randomBase64Url,
    sessionCookieOptions,
    sessionMaxAgeSeconds,
    sha256Base64Url,
    transientCookieOptions,
} from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

export const dynamic = "force-dynamic";

export function GET() {
    if (!isKeycloakEnabled()) {
        return new NextResponse(localLoginPage(), {
            headers: { "Content-Type": "text/html; charset=utf-8" },
        });
    }

    const config = getAuthConfig();
    const state = randomBase64Url();
    const nonce = randomBase64Url();
    const codeVerifier = randomBase64Url(48);
    const redirectUri = `${config.appOrigin}/api/auth/callback`;
    const authorizeUrl = new URL(`${config.publicIssuer}/protocol/openid-connect/auth`);

    authorizeUrl.searchParams.set("client_id", config.clientId);
    authorizeUrl.searchParams.set("code_challenge", sha256Base64Url(codeVerifier));
    authorizeUrl.searchParams.set("code_challenge_method", "S256");
    authorizeUrl.searchParams.set("nonce", nonce);
    authorizeUrl.searchParams.set("redirect_uri", redirectUri);
    authorizeUrl.searchParams.set("response_type", "code");
    authorizeUrl.searchParams.set("scope", config.scopes);
    authorizeUrl.searchParams.set("state", state);

    const response = NextResponse.redirect(authorizeUrl);
    const cookieOptions = transientCookieOptions(config);
    response.cookies.set(AUTH_STATE_COOKIE, state, cookieOptions);
    response.cookies.set(AUTH_VERIFIER_COOKIE, codeVerifier, cookieOptions);
    response.cookies.set(AUTH_NONCE_COOKIE, nonce, cookieOptions);
    return response;
}

export async function POST(request: Request) {
    if (isKeycloakEnabled()) {
        return NextResponse.json({ detail: "Локальный вход отключён." }, { status: 404 });
    }

    const form = await request.formData();
    const email = fieldValue(form, "email");
    const password = fieldValue(form, "password");
    if (!email || !password) {
        return loginFailure("Укажите логин и пароль.");
    }

    const response = await fetch(new URL("/api/auth/login", getBackendInternalUrl()), {
        body: JSON.stringify({ email, password }),
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        method: "POST",
    });
    if (!response.ok) {
        const detail = await readErrorDetail(response);
        return loginFailure(detail, response.status);
    }

    const identity = (await response.json()) as {
        email: string | null;
        issuer: string;
        key: string;
        name: string | null;
        sub: string;
    };
    const config = getSessionConfig();
    const sessionSeconds = sessionMaxAgeSeconds();
    const expiresAt = Date.now() + sessionSeconds * 1000;
    const redirect = NextResponse.redirect(new URL("/", config.appOrigin), 303);
    redirect.cookies.set(
        SESSION_COOKIE,
        encodeSession({
            email: identity.email ?? undefined,
            expiresAt,
            issuer: identity.issuer,
            name: identity.name ?? undefined,
            preferredUsername: identity.key,
            sub: identity.sub,
        }),
        sessionCookieOptions(config, sessionSeconds),
    );
    return redirect;
}

function fieldValue(form: FormData, name: string): string | undefined {
    const value = form.get(name);
    if (typeof value !== "string") return undefined;
    return value.trim() || undefined;
}

async function readErrorDetail(response: Response): Promise<string> {
    try {
        const parsed = (await response.json()) as { detail?: string };
        if (parsed.detail) {
            return parsed.detail;
        }
    } catch {
        // При ошибке разбора JSON возвращаем общее сообщение.
    }

    return "Не удалось выполнить вход.";
}

function loginFailure(message: string, status = 401): NextResponse {
    return new NextResponse(localLoginPage(message), {
        headers: { "Content-Type": "text/html; charset=utf-8" },
        status,
    });
}

function localLoginPage(error?: string): string {
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Вход в ReportCollector</title><style>body{font-family:Arial,sans-serif;background:#f4f5f7;color:#18202a;margin:0;display:grid;place-items:center;min-height:100vh}form{background:#fff;border:1px solid #d6dae0;padding:28px;width:min(360px,calc(100vw - 48px));box-shadow:0 8px 24px #0001}h1{font-size:22px;margin:0 0 22px}label{display:block;font-size:14px;margin:14px 0 5px}input{box-sizing:border-box;width:100%;padding:10px;border:1px solid #aeb5bf;font:inherit}button{width:100%;margin-top:22px;padding:11px;border:0;background:#1769aa;color:#fff;font:inherit;cursor:pointer}.error{color:#b42318;font-size:14px}</style></head><body><form method="post"><h1>Вход в ReportCollector</h1>${error ? `<p class="error">${error}</p>` : ""}<label for="email">Логин (e-mail)</label><input id="email" name="email" type="email" required autofocus><label for="password">Пароль</label><input id="password" name="password" type="password" required minlength="8"><button type="submit">Войти</button></form></body></html>`;
}
