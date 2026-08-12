import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE = "rc_session";

export function proxy(request: NextRequest) {
    if (!request.cookies.get(SESSION_COOKIE)?.value) {
        const appOrigin = process.env.AUTH_APP_ORIGIN || request.nextUrl.origin;
        return NextResponse.redirect(new URL("/api/auth/login", appOrigin));
    }

    return NextResponse.next();
}

export const config = {
    matcher: "/",
};
