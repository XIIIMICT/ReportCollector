"use client";

import { useLayoutEffect, useRef, type ReactNode } from "react";

/** Animate the panel's natural content height, including changes to its tallest control. */
export function AnimatedPanel({ children }: { children: ReactNode }) {
    const panelRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);

    useLayoutEffect(() => {
        const panel = panelRef.current;
        const content = contentRef.current;
        if (!panel || !content) return;
        let height = content.getBoundingClientRect().height;
        let animation: Animation | undefined;
        panel.style.height = `${height}px`;
        const observer = new ResizeObserver(() => {
            const next = content.getBoundingClientRect().height;
            if (Math.abs(next - height) < 0.1) return;
            const previous = panel.getBoundingClientRect().height;
            animation?.cancel();
            panel.style.height = `${next}px`;
            height = next;
            if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
                animation = panel.animate([{ height: `${previous}px` }, { height: `${next}px` }], {
                    duration: 260,
                    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
                });
            }
        });
        observer.observe(content);
        return () => {
            observer.disconnect();
            animation?.cancel();
        };
    }, []);

    return (
        <div ref={panelRef} className="workspace-panel">
            <div ref={contentRef} className="workspace-panel__content">
                {children}
            </div>
        </div>
    );
}
