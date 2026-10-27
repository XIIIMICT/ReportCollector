"use client";

import { Button, Card, Typography } from "@heroui/react";

import { projectKeyFromSlug, projectNameFromKey } from "@/shared/projects";

/**
 * The page for a URL that names no project («/что-то»), built like the
 * «Нет доступа к проекту» and error screens: the app shell and the same HeroUI
 * card. Without it Next renders its own English 404, which also replaces the tab
 * title with «404: This page could not be found.» — the brand is what a lost
 * user should see.
 */
function currentProjectName(): string {
    if (typeof window === "undefined") return "РепортКоллектор";
    const segment = window.location.pathname.split("/").filter(Boolean)[0] ?? "";
    const key = projectKeyFromSlug(segment);
    return key === null ? "РепортКоллектор" : `РепортКоллектор ${projectNameFromKey(key)}`;
}

export default function NotFound() {
    const name = currentProjectName();

    return (
        <main className="app-shell">
            <title>{name}</title>
            <div className="app-body">
                <div className="flex w-full items-center justify-center p-6">
                    <Card className="w-full max-w-lg">
                        <Card.Content className="space-y-4 p-8">
                            <Typography.Heading level={1}>Страница не найдена</Typography.Heading>
                            <Typography>
                                Такой страницы нет. Возможно, ссылка устарела или в адресе опечатка.
                            </Typography>
                            <Button variant="primary" onPress={() => window.location.assign("/")}>
                                На главную
                            </Button>
                        </Card.Content>
                    </Card>
                </div>
            </div>
        </main>
    );
}
