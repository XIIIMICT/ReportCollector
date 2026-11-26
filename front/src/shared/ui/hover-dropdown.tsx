"use client";

import { Dropdown } from "@heroui/react";
import {
    cloneElement,
    useCallback,
    useEffect,
    useRef,
    useState,
    type ReactElement,
    type ReactNode,
} from "react";

const CLOSE_GRACE_MS = 250;

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
    /** React Aria's hover event: opens for mouse/pen, never for touch. */
    onHoverStart: () => void;
    /** Open on press (click / Enter / Space) — keyboard and touch access. */
    onPress: () => void;
    /** The dropdown is open — the trigger may keep its "expanded" look (hover is lost while open). */
    isOpen: boolean;
};

/**
 * A Dropdown that opens on React Aria hover/press and closes when the pointer
 * leaves both the trigger and the popover for a sustained period.
 *
 * Closing is still decided by geometry, NOT by mouseleave: react-aria sets
 * `inert` on the rest of the page while the modal popover is open, so the
 * trigger stops being hit-testable and the browser fires a phantom pointerout.
 * A mouseleave-based close would loop forever (open → inert → phantom leave →
 * close → un-inert → re-enter → open). `getBoundingClientRect` keeps working on
 * inert elements.
 *
 * Unlike the old implementation, there is no 60 ms polling loop. Pointer
 * coordinates are recorded by `pointermove`; a single requestAnimationFrame
 * coalesces geometry checks, and one cancellable 250 ms timeout owns the
 * outside grace period. Nothing reads rectangles while the pointer is still.
 *
 * Press is open-only: a toggle would close the menu on touch, where a synthetic
 * hover may have opened it first. Pointer-based dismissal is armed only by a
 * real hover open, so a keyboard-opened menu is never killed by a stale point.
 */
export function HoverDropdown({
    trigger,
    content,
    className,
    onOpenChange,
    ...triggerProps
}: {
    trigger: (props: HoverDropdownTriggerProps) => ReactElement;
    content: ReactNode;
    className?: string;
    /** Notifies the owner whether the dropdown is open (used e.g. to hold the
        fullscreen toolbar panels open while a hover menu is expanded). */
    onOpenChange?: (open: boolean) => void;
    /** HeroUI ButtonGroup marks its direct child; forward that marker to the trigger. */
    __button_group_child?: boolean;
}) {
    const [open, setOpenState] = useState(false);
    const onOpenChangeRef = useRef(onOpenChange);
    onOpenChangeRef.current = onOpenChange;

    const openRef = useRef(false);
    /** Invalidates timers/frames created for a previous open interaction. */
    const generationRef = useRef(0);
    const triggerRef = useRef<HTMLElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);
    const lastPointRef = useRef<{ x: number; y: number } | null>(null);
    const suppressHoverRef = useRef(false);
    /** Only a genuine hover open arms pointer-based dismissal. */
    const openedByPointerRef = useRef(false);
    const closeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const rafRef = useRef<number | null>(null);

    const clearCloseTimeout = useCallback(() => {
        if (closeTimeoutRef.current !== null) {
            clearTimeout(closeTimeoutRef.current);
            closeTimeoutRef.current = null;
        }
    }, []);

    const cancelFrame = useCallback(() => {
        if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
        }
    }, []);

    const setOpen = useCallback(
        (next: boolean) => {
            // onOpenChange must not fire repeatedly for idle pointer movement.
            if (openRef.current === next) return;
            openRef.current = next;
            generationRef.current += 1;
            clearCloseTimeout();
            cancelFrame();
            if (!next) openedByPointerRef.current = false;
            setOpenState(next);
            onOpenChangeRef.current?.(next);
        },
        [cancelFrame, clearCloseTimeout],
    );

    const pointIsInside = useCallback((): boolean => {
        const point = lastPointRef.current;
        if (!point) return false;
        return pointInRect(point, triggerRef.current) || pointInRect(point, popoverRef.current);
    }, []);

    const schedulePointerCheck = useCallback(() => {
        if (rafRef.current !== null) return;
        const generation = generationRef.current;
        rafRef.current = requestAnimationFrame(() => {
            rafRef.current = null;
            if (
                !openRef.current ||
                generationRef.current !== generation ||
                !openedByPointerRef.current
            ) {
                return;
            }
            if (pointIsInside()) {
                clearCloseTimeout();
                return;
            }
            // One timeout per interaction: additional outside moves must not
            // restart the grace period.
            if (closeTimeoutRef.current !== null) return;
            closeTimeoutRef.current = setTimeout(() => {
                closeTimeoutRef.current = null;
                if (
                    !openRef.current ||
                    generationRef.current !== generation ||
                    !openedByPointerRef.current
                ) {
                    return;
                }
                // Re-read geometry when the timer fires: the pointer may have
                // come back without another frame being scheduled here.
                if (pointIsInside()) return;
                setOpen(false);
            }, CLOSE_GRACE_MS);
        });
    }, [clearCloseTimeout, pointIsInside, setOpen]);

    // Keep the latest scheduler available to the long-lived pointer listener.
    const schedulePointerCheckRef = useRef(schedulePointerCheck);
    schedulePointerCheckRef.current = schedulePointerCheck;

    // Track the pointer at all times. After Escape, hover stays suppressed
    // until the pointer physically leaves the trigger. Touch movement is not
    // hover: it must neither open nor dismiss a hover-opened menu here.
    useEffect(() => {
        const onPointerMove = (event: PointerEvent) => {
            if (event.pointerType === "touch") return;
            lastPointRef.current = { x: event.clientX, y: event.clientY };
            if (
                suppressHoverRef.current &&
                !pointInRect(lastPointRef.current, triggerRef.current)
            ) {
                suppressHoverRef.current = false;
            }
            if (openRef.current && openedByPointerRef.current) {
                schedulePointerCheckRef.current();
            }
        };
        window.addEventListener("pointermove", onPointerMove, { passive: true });
        return () => window.removeEventListener("pointermove", onPointerMove);
    }, []);

    // Escape closes in capture phase, before anything can stop the event. The
    // hover reopen stays suppressed until the pointer leaves the trigger.
    useEffect(() => {
        if (!open) return;
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                suppressHoverRef.current = true;
                setOpen(false);
            }
        };
        window.addEventListener("keydown", onKeyDown, true);
        return () => window.removeEventListener("keydown", onKeyDown, true);
    }, [open, setOpen]);

    // No pending timer/frame may outlive the component.
    useEffect(
        () => () => {
            clearCloseTimeout();
            cancelFrame();
        },
        [cancelFrame, clearCloseTimeout],
    );

    const triggerElement = trigger({
        ref: (element) => {
            triggerRef.current = element;
        },
        isOpen: open,
        onHoverStart: () => {
            if (suppressHoverRef.current) return;
            openedByPointerRef.current = true;
            clearCloseTimeout();
            setOpen(true);
        },
        onPress: () => {
            openedByPointerRef.current = false;
            clearCloseTimeout();
            setOpen(true);
        },
    });

    return (
        <Dropdown isOpen={open} onOpenChange={setOpen}>
            {cloneElement(triggerElement, triggerProps)}
            <Dropdown.Popover ref={popoverRef} {...(className && { className })}>
                {content}
            </Dropdown.Popover>
        </Dropdown>
    );
}
