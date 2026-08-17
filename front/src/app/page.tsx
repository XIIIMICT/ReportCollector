import { redirect } from "next/navigation";

import { ReportWorkspace } from "@/features/report-workspace/report-workspace";
import { ReportCollectorApiError, type CurrentUser } from "@/shared/api/report-collector";
import { getCurrentUserServer } from "@/shared/api/report-collector-server";
import { getSession } from "@/shared/auth/session";

export const dynamic = "force-dynamic";

export default async function Home() {
    const session = await getSession();
    if (!session) {
        redirect("/api/auth/login");
    }

    let currentUser: CurrentUser;
    try {
        const user = await getCurrentUserServer(session);
        currentUser = {
            access: user.access,
            email: user.email ?? undefined,
            name: user.name ?? undefined,
            theme: (user.theme ?? "system") as "system" | "light" | "dark",
        };
    } catch (error) {
        if (error instanceof ReportCollectorApiError && error.status === 403) {
            redirect("/blocked");
        }

        throw error;
    }

    return <ReportWorkspace currentUser={currentUser} />;
}
