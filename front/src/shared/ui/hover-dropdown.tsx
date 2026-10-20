"use client";

import { Dropdown } from "@heroui/react";
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";

function pointInRect(point: { x: number; y: number }, element: HTMLElement | null): boolean {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    return (
        point.x >= rect.left &&
        point.x <= rect.right &&
        point.y >= rect.top &&
        point.y <= rect.bottom
    );
}

type HoverDropdownTriggerProps = {
    /** Forward to the trigger element — used to keep the pointer-position check honest. */
    ref: (element: HTMLElement | null) => void;
    onMouseEnter: () => void;
    /** Open on press (click / Enter / Space) — keyboard and touch access. */
    onPress: () => void;
    /** The dropdown is open — the trigger may keep its "expanded" look (hover is lost while open). */
    isOpen: boolean;
};

/**
 * A Dropdown that opens on hover (pointer over the trigger) and closes when the
 * pointer leaves both the trigger and the popover for a sustained period.
 *
 * Closing is driven by a window-level pointer-position check, not by
 * mouseleave: react-aria hides the rest of the page (`inert`) while the modal
 * popover is open, and the browser then fires a phantom `pointerout` on the
 * trigger (it is no longer hit-testable). A mouseleave-based close would loop
 * forever: open → inert → phantom leave → close → not inert → re-enter → open.
 * `getBoundingClientRect` still works on inert elements, so the trigger rect is
 * always readable.
 *
 * The trigger also receives `onPress`: a press (click / Enter / Space) opens
 * the menu for keyboard and touch users. The pointer-out close only arms
 * after a real pointermove, so a keyboard-opened menu is never killed by a
 * stale pointer position. Dismissal: Escape, outside press, pointer out.
 */
export function HoverDropdown({
    trigger,
    content,
    className,
    onOpenChange,
}: {
    trigger: (props: HoverDropdownTriggerProps) => ReactElement;
    content: ReactNode;
    className?: string;
    /** Notifies the owner whether the dropdown is open (used e.g. to hold the
        fullscreen toolbar panels open while a hover menu is expanded). */
    onOpenChange?: (open: boolean) => void;
}) {
    const [open, setOpenState] = useState(false);
    const onOpenChangeRef = useRef(onOpenChange);
    onOpenChangeRef.current = onOpenChange;

    const setOpen = (next: boolean) => {
        setOpenState(next);
        onOpenChangeRef.current?.(next);
    };
    const triggerRef = useRef<HTMLElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);
    const lastPointRef = useRef<{ x: number; y: number } | null>(null);
    const suppressHoverRef = useRef(false);
    /** Whether a real pointermove happened since the menu opened: the
        pointer-out close only applies to pointer-opened menus. */
    const openedByPointerRef = useRef(false);

    // Track the pointer at all times; once it leaves the trigger, hover is
    // allowed again (used after an explicit Escape close).
    useEffect(() => {
        const onPointerMove = (event: PointerEvent) => {
            lastPointRef.current = { x: event.clientX, y: event.clientY };
            openedByPointerRef.current = true;
            if (
                suppressHoverRef.current &&
                !pointInRect(lastPointRef.current, triggerRef.current)
            ) {
                suppressHoverRef.current = false;
            }
        };
        window.addEventListener("pointermove", onPointerMove);
        return () => window.removeEventListener("pointermove", onPointerMove);
    }, []);

    // While open: close once the pointer has been outside both the trigger and
    // the popover for a sustained period.
    useEffect(() => {
        if (!open) return;
        let outsideSince = 0;
        const timer = setInterval(() => {
            if (!openedByPointerRef.current) return;
            const point = lastPointRef.current;
            if (!point) return;
            if (pointInRect(point, triggerRef.current) || pointInRect(point, popoverRef.current)) {
                outsideSince = 0;
                return;
            }
            const now = performance.now();
            if (outsideSince === 0) {
                outsideSince = now;
            } else if (now - outsideSince > 250) {
                setOpen(false);
            }
        }, 60);
        return () => clearInterval(timer);
    }, [open]);

    // Escape closes the menu. Capture phase: runs before anything that could
    // stop the event. The hover reopen is suppressed until the pointer leaves
    // the trigger (see the pointermove effect above).
    useEffect(() => {
        if (!open) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                suppressHoverRef.current = true;
                setOpen(false);
            }
        };
        window.addEventListener("keydown", onKeyDown, true);
        return () => window.removeEventListener("keydown", onKeyDown, true);
    }, [open]);

    const triggerElement = trigger({
        ref: (element) => {
            triggerRef.current = element;
        },
        isOpen: open,
        onMouseEnter: () => {
            if (suppressHoverRef.current) return;
            openedByPointerRef.current = true;
            setOpen(true);
        },
        onPress: () => {
            // Open only, never toggle: on touch the synthetic mouseenter has
            // already opened the menu before the press lands, and a toggle
            // would close it instantly.
            openedByPointerRef.current = false;
            setOpen(true);
        },
    });

    return (
        <Dropdown isOpen={open} onOpenChange={setOpen}>
            {triggerElement}
            <Dropdown.Popover ref={popoverRef} {...(className && { className })}>
                {content}
            </Dropdown.Popover>
        </Dropdown>
    );
}
