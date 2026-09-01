import { Button, Card, Typography } from "@heroui/react";
import { notFound, redirect } from "next/navigation";

import { ReportWorkspace } from "@/features/report-workspace/report-workspace";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import {
    getCurrentUserServer,
    requestProjectAccessServer,
} from "@/shared/api/report-collector-server";
import { getSession } from "@/shared/auth/session";
import { projectKeyFromSlug } from "@/shared/projects";

export const dynamic = "force-dynamic";

export default async function ProjectPage({ params }: { params: Promise<{ project: string }> }) {
    const { project: slug } = await params;
    const projectKey = projectKeyFromSlug(slug);
    if (projectKey === null) {
        notFound();
    }

    const session = await getSession();
    if (!session) {
        redirect("/api/auth/login");
    }

    let currentUser: Awaited<ReturnType<typeof getCurrentUserServer>>;
    try {
        currentUser = await getCurrentUserServer(session);
    } catch (error) {
        if (error instanceof ReportCollectorApiError && error.status === 403) {
            redirect("/blocked");
        }

        throw error;
    }

    const hasAccess = (currentUser.access ?? []).some((project) => project.key === projectKey);
    if (!hasAccess) {
        // Self-provisioning: the user record and an inactive project link are
        // created, so the project admin can grant access from «Пользователи».
        // The page stays on the same URL — a refresh re-checks the access.
        await requestProjectAccessServer(session, projectKey);

        return (
            <main className="bg-background text-foreground flex min-h-dvh items-center justify-center p-6">
                <Card className="w-full max-w-lg">
                    <Card.Content className="space-y-4 p-8">
                        <Typography.Heading level={1}>Нет доступа к проекту</Typography.Heading>
                        <Typography>
                            У вашей учётной записи нет доступа к этому проекту. Мы зафиксировали ваш
                            запрос — обратитесь к администратору, чтобы он включил вам доступ. После
                            этого обновите страницу.
                        </Typography>
                        <div className="flex gap-3">
                            <a href="/">
                                <Button variant="tertiary">На главную</Button>
                            </a>
                        </div>
                    </Card.Content>
                </Card>
            </main>
        );
    }

    return <ReportWorkspace key={projectKey} currentUser={currentUser} projectKey={projectKey} />;
}
