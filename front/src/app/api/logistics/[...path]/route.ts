import { proxyBackend } from "@/shared/api/backend-proxy";

export const dynamic = "force-dynamic";

/**
 * The logistics dictionaries are read-only; the users dictionary and the main
 * «Ставки» table are edited through this proxy (admin-only on the backend).
 */
export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/logistics/${path.join("/")}`);
}

export async function PATCH(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/logistics/${path.join("/")}`, "PATCH");
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/logistics/${path.join("/")}`, "POST");
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/logistics/${path.join("/")}`, "DELETE");
}
