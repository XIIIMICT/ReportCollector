"use client";

import { Button, Card, Typography } from "@heroui/react";
import { useEffect, useState } from "react";

import { RawMaterialsWorkspace } from "@/features/raw-materials/raw-materials-workspace";
import { RenovationWorkspace } from "@/features/renovation/renovation-workspace";
import type { CurrentUser } from "@/shared/api/report-collector";

export type ReportWorkspaceUser = CurrentUser;

type ReportWorkspaceProps = {
    currentUser?: CurrentUser;
};

const RENOVATION_PROJECT_KEY = "renovation";

export function ReportWorkspace({ currentUser }: ReportWorkspaceProps) {
    const [activeProjectKey, setActiveProjectKey] = useState<string | null>(null);

    useEffect(() => {
        if (!currentUser) {
            window.location.replace("/api/auth/login");
            return;
        }
        // Prefer renovation; fall back to the first project in the access list.
        const projects = currentUser.access ?? [];
        const firstKey =
            projects.find((project) => project.key === RENOVATION_PROJECT_KEY)?.key ??
            projects[0]?.key ??
            null;
        setActiveProjectKey((current) => current ?? firstKey);
    }, [currentUser]);

    if (!currentUser) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    // No projects the user is activated in — show a clear message.
    if (currentUser.access && currentUser.access.length === 0) {
        return (
            <main className="bg-background text-foreground flex min-h-dvh items-center justify-center p-6">
                <Card className="w-full max-w-lg">
                    <Card.Content className="space-y-4 p-8">
                        <Typography.Heading level={1}>Нет доступа к проектам</Typography.Heading>
                        <Typography>
                            Ваша учётная запись активирована, но администратор пока не предоставил
                            доступ ни к одному проекту. Обратитесь к администратору для назначения
                            проекта.
                        </Typography>
                        <div className="flex gap-3">
                            <a href="/api/auth/logout">
                                <Button variant="tertiary">Выйти</Button>
                            </a>
                        </div>
                    </Card.Content>
                </Card>
            </main>
        );
    }

    if (activeProjectKey === null) {
        return <div className="bg-background min-h-dvh" aria-hidden />;
    }

    return activeProjectKey === RENOVATION_PROJECT_KEY ? (
        <RenovationWorkspace
            currentUser={currentUser}
            activeProjectKey={activeProjectKey}
            onProjectChange={setActiveProjectKey}
        />
    ) : (
        <RawMaterialsWorkspace
            currentUser={currentUser}
            activeProjectKey={activeProjectKey}
            onProjectChange={setActiveProjectKey}
        />
    );
}
