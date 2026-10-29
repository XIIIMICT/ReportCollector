import { Card } from "@heroui/react";
import type { ReactNode } from "react";

import { AnimatedPanel } from "@/shared/ui/animated-panel";

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
                        <AnimatedPanel>{toolbar}</AnimatedPanel>
                        {children}
                        <AnimatedPanel>{footer}</AnimatedPanel>
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
                    <AnimatedPanel>{toolbar}</AnimatedPanel>
                </div>
            </div>
            <div className={revealClass("bottom")}>
                <div className="fullscreen-panel fullscreen-panel--bottom">
                    <AnimatedPanel>{footer}</AnimatedPanel>
                </div>
            </div>
        </>
    );
}
