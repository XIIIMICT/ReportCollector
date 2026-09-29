import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

/** Bodies at least this large are worth compressing (small ones grow). */
const COMPRESS_MIN_BYTES = 1024;

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
    const contentLengthHeader = response.headers.get("Content-Length");
    const contentLength = contentLengthHeader === null ? null : Number(contentLengthHeader);

    // Forward the headers a browser cares about, for both JSON and binary
    // responses. Keep the body as a stream: buffering a large XLSX here delays
    // the first byte until the whole backend response has arrived and makes the
    // outer nginx proxy return 504 even though the backend export succeeds.
    const forwardedHeaders = new Headers({ "Content-Type": responseContentType });
    for (const name of ["Content-Disposition", "Cache-Control"]) {
        const value = response.headers.get(name);
        if (value) forwardedHeaders.set(name, value);
    }

    // JSON window payloads are large and highly repetitive (a 1000-row
    // renovation window is ~2 MB, ~30x smaller gzipped), and neither the backend
    // nor the Next dev server compresses them, so do it here. Binary responses
    // (the XLSX export) are already compressed and are passed through untouched.
    const acceptsGzip = (request.headers.get("accept-encoding") ?? "").includes("gzip");
    const largeEnough = contentLength === null || contentLength >= COMPRESS_MIN_BYTES;
    if (
        responseContentType.includes("json") &&
        acceptsGzip &&
        largeEnough &&
        response.body &&
        typeof CompressionStream !== "undefined"
    ) {
        forwardedHeaders.set("Content-Encoding", "gzip");
        forwardedHeaders.set("Vary", "Accept-Encoding");
        const compressed = response.body.pipeThrough(new CompressionStream("gzip"));
        return new Response(compressed, { headers: forwardedHeaders, status: response.status });
    }

    for (const name of ["Content-Length", "ETag"]) {
        const value = response.headers.get(name);
        if (value) forwardedHeaders.set(name, value);
    }
    return new Response(response.body, {
        headers: forwardedHeaders,
        status: response.status,
    });
}
