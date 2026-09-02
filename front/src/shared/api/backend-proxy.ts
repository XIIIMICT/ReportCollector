import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

export async function proxyBackend(request: Request, path: string, method = request.method) {
    const session = await getSession();

    if (!session) {
        return Response.json({ detail: "Пользователь не авторизован." }, { status: 401 });
    }

    const requestUrl = new URL(request.url);
    const targetUrl = new URL(`${path}${requestUrl.search}`, getBackendInternalUrl());
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
    // Forward the headers a browser cares about, for both JSON and binary
    // responses. Keep the body as a stream: buffering a large XLSX here delays
    // the first byte until the whole backend response has arrived and makes the
    // outer nginx proxy return 504 even though the backend export succeeds.
    const forwardedHeaders = new Headers({ "Content-Type": responseContentType });
    for (const name of ["Content-Disposition", "Cache-Control", "Content-Length", "ETag"]) {
        const value = response.headers.get(name);
        if (value) forwardedHeaders.set(name, value);
    }
    return new Response(response.body, {
        headers: forwardedHeaders,
        status: response.status,
    });
}
