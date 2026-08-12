import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

const BACKEND_URL = getBackendInternalUrl();

export async function proxyBackend(request: Request, path: string, method = request.method) {
    const session = await getSession();

    if (!session) {
        return Response.json({ detail: "Пользователь не авторизован." }, { status: 401 });
    }

    const requestUrl = new URL(request.url);
    const targetUrl = new URL(`${path}${requestUrl.search}`, BACKEND_URL);
    const contentType = request.headers.get("Content-Type");
    const headers = buildIdentityHeaders(session);

    if (contentType) {
        headers.set("Content-Type", contentType);
    }

    const init: RequestInit = {
        cache: "no-store",
        headers,
        method,
    };
    if (method !== "GET" && method !== "HEAD") {
        init.body = await request.text();
    }

    const response = await fetch(targetUrl, init);
    const responseContentType = response.headers.get("Content-Type") ?? "application/json";
    if (responseContentType.includes("application/json")) {
        return Response.json(await response.json(), { status: response.status });
    }

    return new Response(await response.text(), {
        headers: { "Content-Type": responseContentType },
        status: response.status,
    });
}
