import { Card } from "@heroui/react";
import type { ReactNode } from "react";

export function GridLoadError({ error }: { error: string }) {
    return (
        <div className="grid-empty">
            <Card>
                <Card.Content className="text-danger p-4 text-sm">{error}</Card.Content>
            </Card>
        </div>
    );
}

export function WorkspaceLoadError({
    error,
    title = "Не удалось открыть рабочее пространство",
    header,
}: {
    error: string;
    title?: string;
    header?: ReactNode;
}) {
    return (
        <div className="app-shell">
            {header}
            <div className="app-body">
                <Card className="startup-error-card">
                    <Card.Content className="p-8">
                        <h2 className="mb-2 text-lg font-semibold">{title}</h2>
                        <p className="text-danger text-sm">{error}</p>
                    </Card.Content>
                </Card>
            </div>
        </div>
    );
}
