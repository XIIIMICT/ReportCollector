"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Fullscreen mode + the top/bottom reveal panels that host the toolbar and the
 * action bar.
 *
 * The panels slide in on pointer proximity. While the pointer rests on a panel
 * they must STAY visible — but a `:hover`-driven rule breaks as soon as a
 * dropdown opened from the panel inerts the rest of the page (react-aria), which
 * drops `:hover`. So the panels are held open by explicit state:
 *  - `setToolbarControlOpen(true/false)` — wired to every dropdown/popover that
 *    opens from the panels (via `onOpenChange` / `onDropdownOpenChange`);
 *  - `pointerInPanels` — a point-coordinate check over the panel rects, because
 *    the popovers are portaled out of the panels and a structural hover can't
 *    see them.
 *
 * Both workspaces share this hook so the behaviour is identical everywhere.
 */
export function useFullscreenPanels() {
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isFullscreenPending, setIsFullscreenPending] = useState(false);
    // A control inside a panel has an open dropdown/popover.
    const [toolbarControlOpen, setToolbarControlOpen] = useState(false);
    // The pointer is over a panel (or its reveal strip).
    const [pointerInPanels, setPointerInPanels] = useState(false);
    const panelsOpen = toolbarControlOpen || pointerInPanels;

    const setFullscreenMode = useCallback(
        async (next: boolean) => {
            if (isFullscreenPending || next === isFullscreen) return;
            setIsFullscreenPending(true);
            try {
                if (next && !document.fullscreenElement) {
                    await document.documentElement.requestFullscreen();
                } else if (!next && document.fullscreenElement) {
                    await document.exitFullscreen();
                }
                setIsFullscreen(next);
            } catch {
                setIsFullscreen(!next);
            } finally {
                setIsFullscreenPending(false);
            }
        },
        [isFullscreen, isFullscreenPending],
    );

    useEffect(() => {
        if (!isFullscreen) return;
        const onFullscreenChange = () => {
            if (!document.fullscreenElement) {
                setIsFullscreen(false);
            }
        };
        document.addEventListener("fullscreenchange", onFullscreenChange);
        return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
    }, [isFullscreen]);

    useEffect(() => {
        if (!isFullscreen) return;
        const onPointerMove = (event: PointerEvent) => {
            const x = event.clientX;
            const y = event.clientY;
            let hit = false;
            for (const selector of [
                ".fullscreen-panel--top",
                ".fullscreen-panel--bottom",
                ".fullscreen-reveal--top",
                ".fullscreen-reveal--bottom",
            ]) {
                const element = document.querySelector<HTMLElement>(selector);
                if (!element) continue;
                const rect = element.getBoundingClientRect();
                if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
                    hit = true;
                    break;
                }
            }
            setPointerInPanels(hit);
        };
        document.addEventListener("pointermove", onPointerMove, { passive: true });
        return () => document.removeEventListener("pointermove", onPointerMove);
    }, [isFullscreen]);

    /** The className of a reveal container (top/bottom), open or closed. */
    const revealClass = useCallback(
        (side: "top" | "bottom") =>
            `fullscreen-reveal fullscreen-reveal--${side}${panelsOpen ? " fullscreen-reveal--open" : ""}`,
        [panelsOpen],
    );

    return {
        isFullscreen,
        isFullscreenPending,
        panelsOpen,
        setFullscreenMode,
        setToolbarControlOpen,
        revealClass,
    };
}
