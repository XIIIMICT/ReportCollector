import { redirect } from "next/navigation";

import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { getCurrentUserServer } from "@/shared/api/report-collector-server";
import { getSession } from "@/shared/auth/session";
import { projectSlugFromKey } from "@/shared/projects";

export const dynamic = "force-dynamic";

export default async function Home() {
    const session = await getSession();
    if (!session) {
        redirect("/api/auth/login");
    }

    let currentUser: Awaited<ReturnType<typeof getCurrentUserServer>>;
    try {
        // create=False on the backend: the root URL never provisions anything.
        currentUser = await getCurrentUserServer(session);
    } catch (error) {
        if (error instanceof ReportCollectorApiError && error.status === 403) {
            redirect("/blocked");
        }

        throw error;
    }

    const access = currentUser.access ?? [];
    if (access.length === 0) {
        // Unknown or not-yet-activated user: no memberships are created here.
        redirect("/blocked");
    }

    // Prefer the renovation project; fall back to the first accessible one.
    const firstKey = access.find((project) => project.key === "renovation")?.key ?? access[0]!.key;
    const slug = projectSlugFromKey(firstKey);
    if (slug === null) {
        redirect("/blocked");
    }

    redirect(slug);
}
