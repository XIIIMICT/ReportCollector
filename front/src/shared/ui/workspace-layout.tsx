import { Card } from "@heroui/react";
import type { ReactNode } from "react";

/** Layout only: grids and project dialogs keep their state in the workspace. */
export function WorkspaceContent({
    toolbar,
    footer,
    children,
}: {
    toolbar: ReactNode;
    footer: ReactNode;
    children: ReactNode;
}) {
    return (
        <div className="app-body">
            <section className="workspace">
                <Card className="workspace-card">
                    <Card.Content className="workspace-card__content">
                        {toolbar}
                        {children}
                        {footer}
                    </Card.Content>
                </Card>
            </section>
        </div>
    );
}

export function WorkspaceFullscreenPanels({
    visible,
    revealClass,
    header,
    toolbar,
    footer,
}: {
    visible: boolean;
    revealClass: (side: "top" | "bottom") => string;
    header: ReactNode;
    toolbar: ReactNode;
    footer: ReactNode;
}) {
    if (!visible) return null;
    return (
        <>
            <div className={revealClass("top")}>
                <div className="fullscreen-panel fullscreen-panel--top">
                    {header}
                    {toolbar}
                </div>
            </div>
            <div className={revealClass("bottom")}>
                <div className="fullscreen-panel fullscreen-panel--bottom">{footer}</div>
            </div>
        </>
    );
}
