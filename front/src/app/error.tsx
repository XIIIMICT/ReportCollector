"use client";

import { Button, ButtonGroup, Card, Typography } from "@heroui/react";

/**
 * Route-level error boundary: renders instead of the default Next.js crash
 * screen when a client render or a server component throws unexpectedly.
 * The error text itself is never shown (it can be technical or sensitive);
 * only the digest is exposed for log correlation.
 */
export default function ErrorPage({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    return (
        <main className="bg-background text-foreground flex min-h-dvh items-center justify-center p-6">
            <Card className="w-full max-w-lg">
                <Card.Content className="space-y-4 p-8">
                    <Typography.Heading level={1}>Что-то пошло не так</Typography.Heading>
                    <Typography>
                        Произошла непредвиденная ошибка. Попробуйте повторить попытку или обновить
                        страницу.
                    </Typography>
                    {error.digest ? (
                        <Typography className="text-muted text-xs">
                            Код ошибки: {error.digest}
                        </Typography>
                    ) : null}
                    <ButtonGroup>
                        <Button variant="primary" onPress={reset}>
                            Обновить
                        </Button>
                        <Button variant="tertiary" onPress={() => window.location.assign("/")}>
                            <ButtonGroup.Separator />
                            На главную
                        </Button>
                    </ButtonGroup>
                </Card.Content>
            </Card>
        </main>
    );
}
