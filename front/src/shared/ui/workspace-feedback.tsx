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

/**
 * The read-only banner a stale scope paints over a retained grid while its new
 * slice loads. The three client-grid forms (КППК dictionaries/realization and
 * «Логистика» rates) rendered the same styled `role="status"` div inline; only
 * the message differs, so it is a child here. The style is repeated nowhere
 * else — the notice must sit above the pinned viewport without taking layout.
 */
export function ScopeStaleNotice({ children }: { children: ReactNode }) {
    return (
        <div
            role="status"
            style={{
                position: "absolute",
                inset: "0.5rem 0.75rem auto",
                zIndex: 4,
                padding: "0.35rem 0.6rem",
                color: "var(--muted)",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "0.35rem",
            }}
        >
            {children}
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
