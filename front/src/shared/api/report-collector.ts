import { z } from "zod";

import { apiErrorFromResponse, readJsonBody } from "./api-error";

const currentUserSchema = z
    .object({
        access: z
            .array(
                z.object({
                    assets: z.array(z.object({ name: z.string() })),
                    key: z.string(),
                    name: z.string(),
                    roles: z.array(z.string()),
                }),
            )
            .optional(),
        email: z.string().nullable(),
        name: z.string().nullable(),
        theme: z.enum(["system", "light", "dark"]).optional(),
    })
    .transform((value) => ({
        access: value.access,
        email: value.email ?? undefined,
        name: value.name ?? undefined,
        theme: (value.theme ?? "system") as "system" | "light" | "dark",
    }));

export type CurrentUser = z.output<typeof currentUserSchema>;

/** Validate an untrusted `/api/me` payload before it reaches React state. */
export function parseCurrentUser(payload: unknown): CurrentUser {
    return currentUserSchema.parse(payload);
}

export type ProjectAccess = NonNullable<CurrentUser["access"]>[number];

export function isProjectAdmin(project: ProjectAccess | undefined): boolean {
    return project?.roles.includes("admin") ?? false;
}

export type ThemePreference = "system" | "light" | "dark";

export type UpdateProfileRequest = {
    theme?: ThemePreference | undefined;
    password?: string | undefined;
};

export async function updateProfile(body: UpdateProfileRequest): Promise<CurrentUser> {
    const response = await fetch("/api/me", {
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" },
        method: "PATCH",
    });

    if (!response.ok) {
        throw await apiErrorFromResponse(response, `Запрос завершился ошибкой ${response.status}.`);
    }

    return parseCurrentUser(await readJsonBody<unknown>(response));
}

export { ReportCollectorApiError } from "./api-error";
