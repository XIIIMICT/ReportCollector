import { proxyBackend } from "@/shared/api/backend-proxy";

export const dynamic = "force-dynamic";

/** The «Логистика» dictionaries are read-only: only GET is proxied. */
export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
    const { path } = await context.params;
    return proxyBackend(request, `/api/logistics/${path.join("/")}`);
}
