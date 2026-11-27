import { ReportCollectorApiError, requestJson } from "./api-error";

export type AuthConfig = { keycloakEnabled: boolean };

/** Read the browser's auth mode without turning failures into a false config. */
export async function getAuthConfig(): Promise<AuthConfig> {
    const payload = await requestJson<unknown>("/api/auth/config");
    if (
        !payload ||
        typeof payload !== "object" ||
        typeof (payload as { keycloakEnabled?: unknown }).keycloakEnabled !== "boolean"
    ) {
        throw new ReportCollectorApiError("Сервер вернул некорректную конфигурацию входа.");
    }
    return { keycloakEnabled: (payload as AuthConfig).keycloakEnabled };
}
