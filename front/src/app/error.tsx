"use client";

import { Button, ButtonGroup, Card, Typography } from "@heroui/react";
import { Refresh } from "iconoir-react";

import { projectKeyFromSlug, projectNameFromKey } from "@/shared/projects";
import { SupportButton } from "@/shared/ui/support-button";

/**
 * The support mailbox for CLIENT components (`SUPPORT_MAIL`).
 *
 * Server-rendered pages pass the value down as a prop, but the error boundary is
 * client-only and receives none: `next.config.ts` inlines the variable as
 * `NEXT_PUBLIC_SUPPORT_MAIL` at build time, so there is still one setting in
 * `.env`, not two.
 */
function clientSupportMail(): string {
    return (process.env.NEXT_PUBLIC_SUPPORT_MAIL ?? "").trim();
}

/** The project of the current URL («/renovation» → renovation), or "". */
function projectKeyFromLocation(): string {
    if (typeof window === "undefined") return "";
    const segment = window.location.pathname.split("/").filter(Boolean)[0] ?? "";
    return projectKeyFromSlug(segment) ?? "";
}

/**
 * Route-level error boundary: renders instead of the default Next.js crash
 * screen when a client render or a server component throws unexpectedly.
 * The error text itself is never shown (it can be technical or sensitive);
 * only the digest is exposed for log correlation.
 *
 * The screen is built exactly like the «Нет доступа к проекту» page — the app
 * shell, the same HeroUI `Card` and the same button row — so a crash is still
 * ReportCollector, not a bare surface.
 */
export default function ErrorPage({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    const projectName = projectNameFromKey(projectKeyFromLocation());
    const supportMail = clientSupportMail();

    return (
        <main className="app-shell">
            <div className="app-body">
                <div className="flex w-full items-center justify-center p-6">
                    <Card className="w-full max-w-lg">
                        <Card.Content className="space-y-4 p-8">
                            <Typography.Heading level={1}>Что-то пошло не так</Typography.Heading>
                            <Typography>
                                Произошла непредвиденная ошибка. Попробуйте повторить попытку или
                                обновить страницу.
                            </Typography>
                            {error.digest ? (
                                <Typography className="text-muted text-xs">
                                    Код ошибки: {error.digest}
                                </Typography>
                            ) : null}
                            <ButtonGroup className="self-start">
                                <Button variant="tertiary" onPress={reset}>
                                    <Refresh aria-hidden="true" />
                                    Обновить
                                </Button>
                                {/* A crash is exactly when someone needs the
                                    developer: the second button is the same
                                    support link the header offers, styled as
                                    the primary way out. */}
                                <SupportButton
                                    projectName={projectName}
                                    supportMail={supportMail}
                                    appearance="button"
                                    primary
                                    withSeparator
                                />
                            </ButtonGroup>
                        </Card.Content>
                    </Card>
                </div>
            </div>
        </main>
    );
}
