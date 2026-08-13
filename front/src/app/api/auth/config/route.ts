import { NextResponse } from "next/server";

import { isKeycloakEnabled } from "@/shared/auth/session";

export const dynamic = "force-dynamic";

export function GET() {
    return NextResponse.json({ keycloakEnabled: isKeycloakEnabled() });
}
