import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import { getSession } from "@/shared/auth/session";
import { collabWsPath } from "@/shared/collab/ws-client";

export const dynamic = "force-dynamic";

const TICKET_TTL_SECONDS = 60;

export async function GET(request: Request) {
    const session = await getSession();
    if (!session) {
        return Response.json({ detail: "Не авторизован." }, { status: 401 });
    }
    const url = new URL(request.url);
    const project = url.searchParams.get("project") ?? "";
    const scope = url.searchParams.get("scope") ?? "";
    const headers = buildIdentityHeaders(session);
    const ticket = {
        h: Object.fromEntries(headers.entries()),
    };
    const baseWsUrl = process.env.COLLAB_WS_PUBLIC_URL?.trim() ?? "";
    const wsUrl = baseWsUrl ? `${baseWsUrl}${collabWsPath(project)}` : "";
    return Response.json({
        ticket: Buffer.from(JSON.stringify(ticket)).toString("base64url"),
        expiresIn: TICKET_TTL_SECONDS,
        wsUrl,
        project,
        scope,
    });
}
