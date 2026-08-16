import { z } from "zod";

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
        key: z.string(),
        name: z.string().nullable(),
        theme: z.enum(["system", "light", "dark"]).optional(),
    })
    .transform((value) => ({
        access: value.access,
        email: value.email ?? undefined,
        key: value.key,
        name: value.name ?? undefined,
        theme: (value.theme ?? "system") as "system" | "light" | "dark",
    }));

export type CurrentUser = z.output<typeof currentUserSchema>;

export type ProjectAccess = NonNullable<CurrentUser["access"]>[number];

export function isProjectAdmin(project: ProjectAccess | undefined): boolean {
    return project?.roles.includes("admin") ?? false;
}

export type ThemePreference = "system" | "light" | "dark";

export type UpdateProfileRequest = {
    theme?: ThemePreference;
    password?: string;
};

export async function updateProfile(body: UpdateProfileRequest): Promise<CurrentUser> {
    const response = await fetch("/api/me", {
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" },
        method: "PATCH",
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

    return currentUserSchema.parse(await response.json());
}

export class ReportCollectorApiError extends Error {
    constructor(
        message: string,
        readonly status?: number,
    ) {
        super(message);
        this.name = "ReportCollectorApiError";
    }
}
