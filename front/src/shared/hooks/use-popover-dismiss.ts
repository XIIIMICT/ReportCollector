"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/** Keep the owner mounted until the popover's real CSS exit animations finish. */
export function usePopoverDismiss(element: RefObject<HTMLElement | null>, onClose: () => void) {
    const [isOpen, setIsOpen] = useState(true);
    const closing = useRef(false);
    const onCloseRef = useRef(onClose);
    onCloseRef.current = onClose;

    useEffect(() => {
        if (isOpen) return;
        let cancelled = false;
        const frame = requestAnimationFrame(() => {
            const animations = element.current?.getAnimations?.() ?? [];
            void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
                if (!cancelled) onCloseRef.current();
            });
        });
        return () => {
            cancelled = true;
            cancelAnimationFrame(frame);
        };
    }, [element, isOpen]);

    return {
        isOpen,
        dismiss: () => {
            if (closing.current) return;
            closing.current = true;
            setIsOpen(false);
        },
    };
}
