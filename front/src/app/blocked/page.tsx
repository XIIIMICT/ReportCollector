import { Button, Card, Typography } from "@heroui/react";

export default function BlockedPage() {
    return (
        <main className="bg-background text-foreground flex min-h-dvh items-center justify-center p-6">
            <Card className="w-full max-w-lg">
                <Card.Content className="space-y-4 p-8">
                    <Typography.Heading level={1}>Доступ заблокирован</Typography.Heading>
                    <Typography>
                        Внутренний доступ к ReportCollector для этой учетной записи отключен главным
                        администратором.
                    </Typography>
                    <div className="flex gap-3">
                        <a href="/api/auth/logout">
                            <Button variant="secondary">Выйти</Button>
                        </a>
                    </div>
                </Card.Content>
            </Card>
        </main>
    );
}
