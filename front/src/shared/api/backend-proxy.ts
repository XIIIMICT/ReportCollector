import { Agent } from "undici";

import { ReportCollectorApiError } from "@/shared/api/api-error";
import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

/**
 * Node's fetch gives up waiting for response headers after five minutes
 * (`UND_ERR_HEADERS_TIMEOUT`). The Excel preflight parses the whole workbook
 * before it answers, so a large file outlives that default — nginx is already
 * told to wait 24 h, and this is the remaining hop. The dispatcher applies to
 * every proxied call: a long body stream (an export) must not be cut either.
 */
const BACKEND_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const backendDispatcher = new Agent({
    headersTimeout: BACKEND_TIMEOUT_MS,
    bodyTimeout: BACKEND_TIMEOUT_MS,
});

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

    // `dispatcher` is undici's extension of RequestInit; the DOM type does not
    // declare it. Next passes the init through untouched for `no-store`.
    const init = {
        cache: "no-store",
        headers,
        method,
        dispatcher: backendDispatcher,
    } as RequestInit & { dispatcher: Agent };
    if (method !== "GET" && method !== "HEAD") {
        if (isMultipart) {
            init.body = Buffer.from(await request.arrayBuffer());
            headers.set("Content-Type", contentType);
        } else {
            init.body = await request.text();
        }
    }

    let response: Response;
    try {
        response = await fetch(targetUrl, init);
    } catch (reason) {
        // A network/timeout failure must reach the browser as a typed JSON
        // error, not an empty success body or a Next.js HTML error page.
        const interrupted = request.signal.aborted;
        const error =
            reason instanceof ReportCollectorApiError
                ? reason
                : new ReportCollectorApiError(
                      interrupted ? "Запрос прерван." : "Не удалось связаться с сервером.",
                  );
        return Response.json(
            { detail: error.message },
            {
                headers: { "X-Request-ID": crypto.randomUUID() },
                status: interrupted ? 499 : 502,
            },
        );
    }
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
    for (const name of [
        "Content-Disposition",
        "Cache-Control",
        "Content-Length",
        "ETag",
        "X-Request-ID",
    ]) {
        const value = response.headers.get(name);
        if (value) forwardedHeaders.set(name, value);
    }
    return new Response(response.body, {
        headers: forwardedHeaders,
        status: response.status,
    });
}
