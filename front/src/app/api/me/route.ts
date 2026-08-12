import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

const BACKEND_URL = getBackendInternalUrl();

export const dynamic = "force-dynamic";

export async function GET() {
    const session = await getSession();

    if (!session) {
        return Response.json({ detail: "Пользователь не авторизован." }, { status: 401 });
    }

    const response = await fetch(new URL("/api/me", BACKEND_URL), {
        cache: "no-store",
        headers: buildIdentityHeaders(session),
    });

    return Response.json(await response.json(), { status: response.status });
}

export async function PATCH(request: Request) {
    const session = await getSession();

    if (!session) {
        return Response.json({ detail: "Пользователь не авторизован." }, { status: 401 });
    }

    let body: unknown;
    try {
        body = await request.json();
    } catch {
        return Response.json({ detail: "Некорректный JSON." }, { status: 400 });
    }

    const headers = buildIdentityHeaders(session);
    headers.set("Content-Type", "application/json");
    const response = await fetch(new URL("/api/me", BACKEND_URL), {
        body: JSON.stringify(body),
        cache: "no-store",
        headers,
        method: "PATCH",
    });

    return Response.json(await response.json(), { status: response.status });
}
