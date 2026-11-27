import { buildIdentityHeaders } from "@/shared/auth/identity-headers";
import type { AuthSession } from "@/shared/auth/session";
import { getBackendInternalUrl } from "@/shared/config";

import { apiErrorFromResponse, fetchFailure, readJsonBody } from "./api-error";
import { parseCurrentUser, ReportCollectorApiError, type CurrentUser } from "./report-collector";

export async function getCurrentUserServer(session: AuthSession | null): Promise<CurrentUser> {
    let response: Response;
    try {
        response = await fetch(new URL("/api/me", getBackendInternalUrl()), {
            cache: "no-store",
            headers: buildIdentityHeaders(session),
        });
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось загрузить профиль.");
    }

    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
    }

    return parseCurrentUser(await readJsonBody<unknown>(response));
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
    let response: Response;
    try {
        response = await fetch(
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
    } catch (reason) {
        throw fetchFailure(reason, "Не удалось запросить доступ.");
    }
    if (!response.ok) {
        throw await apiErrorFromResponse(
            response,
            `Запрос доступа завершился ошибкой ${response.status}.`,
        );
    }
    const payload = await readJsonBody<unknown>(response);
    if (
        !payload ||
        typeof payload !== "object" ||
        typeof (payload as { hasAccess?: unknown }).hasAccess !== "boolean"
    ) {
        throw new ReportCollectorApiError("Сервер вернул неожиданный ответ.", response.status);
    }
    return { hasAccess: (payload as { hasAccess: boolean }).hasAccess };
}
