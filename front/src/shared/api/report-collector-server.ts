import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import type { AuthSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

import { ReportCollectorApiError } from "./report-collector";

export async function getCurrentUserServer(session: AuthSession | null) {
    const response = await fetch(new URL("/api/me", getBackendInternalUrl()), {
        cache: "no-store",
        headers: buildIdentityHeaders(session),
    });

    if (!response.ok) {
        let detail = `Запрос завершился ошибкой ${response.status}.`;
        try {
            const payload = (await response.json()) as { detail?: string };
            if (typeof payload.detail === "string") {
                detail = payload.detail;
            }
        } catch {
            // Для ответа не в JSON оставляем общее сообщение, подготовленное выше.
        }

        throw new ReportCollectorApiError(detail, response.status);
    }

    return (await response.json()) as {
        access?: Array<{
            assets: Array<{ name: string }>;
            key: string;
            name: string;
            roles: string[];
        }>;
        email?: string | null;
        key: string;
        name?: string | null;
        theme?: string;
    };
}
