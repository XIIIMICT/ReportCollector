"use client";

import { useCallback, useEffect, useState } from "react";

import { hasOpenKeyboardOverlay } from "@/shared/ui/modal-presence";

type KeyboardLock = { lock: (keys: string[]) => Promise<void>; unlock: () => void };
function keyboardLock(): KeyboardLock | undefined {
    return (navigator as Navigator & { keyboard?: KeyboardLock }).keyboard;
}

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
                    // Native fullscreen is always the source of truth. Keyboard
                    // Lock is only an optional enhancement that lets the app
                    // consume Escape before the browser exits fullscreen; a
                    // denied or unavailable lock must never prevent fullscreen.
                    await document.documentElement.requestFullscreen();
                    const lock = keyboardLock();
                    if (lock) {
                        // Lock permission can wait for a browser prompt. Do not
                        // hold the visible fullscreen state on that optional
                        // enhancement; native fullscreen remains usable either
                        // way and the browser handles Escape when the lock is
                        // denied.
                        void lock.lock(["Escape"]).catch(() => undefined);
                    }
                } else if (!next && document.fullscreenElement) {
                    keyboardLock()?.unlock();
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
                keyboardLock()?.unlock();
                setIsFullscreen(false);
            }
        };
        document.addEventListener("fullscreenchange", onFullscreenChange);
        return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
    }, [isFullscreen]);

    useEffect(() => {
        if (!isFullscreen) return;
        // Bubble phase: a grid with a live selection consumes Escape in capture;
        // React Aria consumes it in an open portal. Only the unhandled key exits.
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Escape" || event.defaultPrevented || hasOpenKeyboardOverlay())
                return;
            event.preventDefault();
            void setFullscreenMode(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [isFullscreen, setFullscreenMode]);

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
