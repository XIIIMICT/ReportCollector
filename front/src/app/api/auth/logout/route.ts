import { NextResponse } from "next/server";

import {
    AUTH_NONCE_COOKIE,
    AUTH_STATE_COOKIE,
    AUTH_VERIFIER_COOKIE,
    SESSION_COOKIE,
    clearCookieOptions,
    getAuthConfig,
    getSessionConfig,
    isKeycloakEnabled,
} from "@/shared/auth/session";

export const dynamic = "force-dynamic";

export async function GET() {
    if (!isKeycloakEnabled()) {
        const config = getSessionConfig();
        const response = NextResponse.redirect(new URL("/api/auth/login", config.appOrigin));
        clearAuthCookies(response, config);
        return response;
    }

    const config = getAuthConfig();
    const logoutUrl = new URL(`${config.publicIssuer}/protocol/openid-connect/logout`);

    logoutUrl.searchParams.set("client_id", config.clientId);

    const postLogoutRedirectUrl = new URL("/api/auth/logged-out", config.appOrigin);
    logoutUrl.searchParams.set("post_logout_redirect_uri", postLogoutRedirectUrl.toString());

    const response = NextResponse.redirect(logoutUrl);
    clearAuthCookies(response, config);

    return response;
}

function clearAuthCookies(response: NextResponse, config = getSessionConfig()) {
    const options = clearCookieOptions(config);

    response.cookies.set(SESSION_COOKIE, "", options);
    response.cookies.set(AUTH_STATE_COOKIE, "", options);
    response.cookies.set(AUTH_VERIFIER_COOKIE, "", options);
    response.cookies.set(AUTH_NONCE_COOKIE, "", options);
}
