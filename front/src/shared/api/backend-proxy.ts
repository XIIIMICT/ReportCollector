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
    const contentType = request.headers.get("Content-Type") ?? "";
    const headers = buildIdentityHeaders(session);

    // A multipart body must be forwarded BYTE-FOR-BYTE with its original
    // Content-Type: that header carries the boundary the backend parser needs,
    // and `request.text()` would re-encode the binary file into garbage.
    // Re-streaming `request.body` through undici also loses the parts in the
    // Next dev server, so the upload is buffered once and sent as a buffer.
    const isMultipart = contentType.toLowerCase().startsWith("multipart/form-data");
    if (contentType && !isMultipart) {
        headers.set("Content-Type", contentType);
    }

    const init: RequestInit = {
        cache: "no-store",
        headers,
        method,
    };
    if (method !== "GET" && method !== "HEAD") {
        if (isMultipart) {
            init.body = Buffer.from(await request.arrayBuffer());
            headers.set("Content-Type", contentType);
        } else {
            init.body = await request.text();
        }
    }

    const response = await fetch(targetUrl, init);
    const responseContentType = response.headers.get("Content-Type") ?? "application/json";

    // Forward the headers a browser cares about, for both JSON and binary
    // responses. Keep the body as a stream: buffering a large XLSX here delays
    // the first byte until the whole backend response has arrived and makes the
    // outer nginx proxy return 504 even though the backend export succeeds.
    //
    // Compression is NOT done here. A route handler cannot declare one: Next's
    // `sendResponse` drops `Content-Encoding` (FORBIDDEN_HEADERS — it assumes a
    // fetch-decompressed body), so gzipping in this function produced bytes the
    // browser could not read. In prod nginx already compresses application/json
    // and strips the request's `Accept-Encoding` before proxying here; in dev the
    // JSON arrives uncompressed.
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
