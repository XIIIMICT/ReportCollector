import { Button, Card, Typography } from "@heroui/react";
import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { Brand } from "@/features/raw-materials/raw-materials-workspace";
import { ReportWorkspace } from "@/features/report-workspace/report-workspace";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import {
    getCurrentUserServer,
    requestProjectAccessServer,
} from "@/shared/api/report-collector-server";
import { getSession, getSupportMail } from "@/shared/auth/session";
import { projectKeyFromSlug, projectNameFromKey } from "@/shared/projects";
import { SupportButton } from "@/shared/ui/support-button";

export const dynamic = "force-dynamic";

/** The site's name in the browser tab — the brand in Russian, as on screen. */
const SITE_TITLE = "РепортКоллектор";

/**
 * The tab reads «РепортКоллектор <Проект>», so three open tabs are told apart at
 * a glance. A slug that names no project (or a user without access to any) falls
 * back to the bare site name: the title is derived from the URL, never from the
 * session, so it does not depend on who is signed in.
 */
export async function generateMetadata({
    params,
}: {
    params: Promise<{ project: string }>;
}): Promise<Metadata> {
    const { project: slug } = await params;
    const key = projectKeyFromSlug(slug);
    if (key === null) return { title: SITE_TITLE };
    return { title: `${SITE_TITLE} ${projectNameFromKey(key)}` };
}

export default async function ProjectPage({
    params,
    searchParams,
}: {
    params: Promise<{ project: string }>;
    searchParams: Promise<{ section?: string }>;
}) {
    const { project: slug } = await params;
    const { section } = await searchParams;
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
            <main className="app-shell">
                <header className="app-header">
                    <Brand
                        compact
                        projectName={projectNameFromKey(projectKey)}
                        projects={[]}
                        activeProjectKey={projectKey}
                        onProjectChange={() => {}}
                    />
                    <span aria-hidden="true" />
                    <div className="app-header__actions">
                        {/* Nobody can be denied access and still lack a way to
                            ask for it: on this screen the developer contact is
                            expanded from the start. */}
                        <SupportButton
                            projectName={projectNameFromKey(projectKey)}
                            supportMail={getSupportMail()}
                            pinned
                        />
                    </div>
                </header>
                <div className="app-body">
                    <div className="flex items-center justify-center p-6">
                        <Card className="w-full max-w-lg">
                            <Card.Content className="space-y-4 p-8">
                                <Typography.Heading level={1}>
                                    Нет доступа к проекту
                                </Typography.Heading>
                                <Typography>
                                    У вашей учётной записи нет доступа к этому проекту. Мы
                                    зафиксировали ваш запрос — обратитесь к администратору, чтобы он
                                    включил вам доступ. После этого обновите страницу.
                                </Typography>
                                <div className="flex gap-3">
                                    <a href="/">
                                        <Button variant="tertiary">На главную</Button>
                                    </a>
                                </div>
                            </Card.Content>
                        </Card>
                    </div>
                </div>
            </main>
        );
    }

    return (
        <ReportWorkspace
            key={projectKey}
            currentUser={currentUser}
            projectKey={projectKey}
            initialSection={section ?? null}
            supportMail={getSupportMail()}
        />
    );
}
