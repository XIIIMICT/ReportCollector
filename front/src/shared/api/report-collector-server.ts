import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import type { AuthSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

import { ReportCollectorApiError, type CurrentUser } from "./report-collector";

export async function getCurrentUserServer(session: AuthSession | null): Promise<CurrentUser> {
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
            // For a non-JSON response keep the generic message prepared above.
        }

        throw new ReportCollectorApiError(detail, response.status);
    }

    const raw = (await response.json()) as {
        access?: Array<{
            assets: Array<{ name: string }>;
            key: string;
            name: string;
            roles: string[];
        }>;
        email?: string | null;
        name?: string | null;
        theme?: string;
    };
    return {
        access: raw.access,
        email: raw.email ?? undefined,
        name: raw.name ?? undefined,
        theme: (raw.theme ?? "system") as CurrentUser["theme"],
    };
}

/**
 * Self-requested membership in one project: creates the user record and an
 * inactive project link (the admin grants access later). Returns whether the
 * project is already usable right now.
 */
export async function requestProjectAccessServer(
    session: AuthSession | null,
    projectKey: string,
): Promise<{ hasAccess: boolean }> {
    const response = await fetch(
        new URL(
            `/api/me/projects/${encodeURIComponent(projectKey)}/request-access`,
            getBackendInternalUrl(),
        ),
        {
            cache: "no-store",
            headers: buildIdentityHeaders(session),
            method: "POST",
        },
    );
    if (!response.ok) {
        throw new ReportCollectorApiError(
            `Запрос доступа завершился ошибкой ${response.status}.`,
            response.status,
        );
    }
    return (await response.json()) as { hasAccess: boolean };
}
