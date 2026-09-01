import { Button, Card, Typography } from "@heroui/react";

export default function BlockedPage() {
    return (
        <main className="bg-background text-foreground flex min-h-dvh items-center justify-center p-6">
            <Card className="w-full max-w-lg">
                <Card.Content className="space-y-4 p-8">
                    <Typography.Heading level={1}>Нет доступа к проектам</Typography.Heading>
                    <Typography>
                        У вашей учётной записи нет доступа ни к одному из проектов. Обратитесь к
                        администратору, чтобы он включил вам доступ, после чего обновите страницу.
                        Если вы знаете адрес нужного проекта — откройте его напрямую, и запрос
                        доступа будет зафиксирован.
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
