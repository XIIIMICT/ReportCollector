import { proxyBackend } from "@/shared/api/backend-proxy";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/raw-materials/${path.join("/")}`);
}

export async function PATCH(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/raw-materials/${path.join("/")}`, "PATCH");
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/raw-materials/${path.join("/")}`, "POST");
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/raw-materials/${path.join("/")}`, "DELETE");
}
