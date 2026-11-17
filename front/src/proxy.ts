import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE = "rc_session";

export function proxy(request: NextRequest) {
    if (!request.cookies.get(SESSION_COOKIE)?.value) {
        const appOrigin = process.env.AUTH_APP_ORIGIN || request.nextUrl.origin;
        // Keycloak/callback always land on the root — carry the requested path
        // along so the auth flow can return to it after the session is set.
        const returnTo = encodeURIComponent(request.nextUrl.pathname);
        return NextResponse.redirect(new URL(`/api/auth/login?return_to=${returnTo}`, appOrigin));
    }

    return NextResponse.next();
}

export const config = {
    // Everything addressable without an API prefix: the root redirects to the
    // first accessible project, the project URLs deep-link into one project,
    // and /blocked is only meaningful with a session.
    matcher: ["/", "/blocked", "/raw-materials", "/renovation", "/kppk", "/logistics"],
};
