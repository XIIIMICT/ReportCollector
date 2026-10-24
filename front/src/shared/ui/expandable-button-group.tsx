"use client";

import { ButtonGroup } from "@heroui/react";
import { useLayoutEffect, useRef, useState, type ComponentProps } from "react";

/** Reserve expanded labels during hover transitions, keeping other buttons compact. */
export function ExpandableButtonGroup({
    children,
    style,
    className,
    reserveLast = Number.POSITIVE_INFINITY,
    ...props
}: ComponentProps<typeof ButtonGroup> & {
    /** Reserve only the final N labels; preceding actions stay compact. */
    reserveLast?: number;
}) {
    const ref = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState<number>();

    useLayoutEffect(() => {
        const group = ref.current;
        if (!group) return;
        const measure = () => {
            const buttons = [...group.querySelectorAll<HTMLButtonElement>(".expandable-button")];
            const firstReserved = Math.max(0, buttons.length - reserveLast);
            const total = buttons.reduce((sum, button, index) => {
                const label = button.querySelector<HTMLElement>(".expandable-button__label");
                const icon = button.querySelector<HTMLElement>(".expandable-button__icon");
                const css = getComputedStyle(button);
                const gapValue = css.getPropertyValue("--expandable-button-gap").trim();
                const gap =
                    (Number.parseFloat(gapValue) || 0) *
                    (gapValue.endsWith("rem")
                        ? Number.parseFloat(getComputedStyle(document.documentElement).fontSize) ||
                          16
                        : 1);
                return (
                    sum +
                    Math.max(
                        Number.parseFloat(css.minWidth) || 0,
                        (icon?.getBoundingClientRect().width ?? 0) +
                            (index >= firstReserved ? (label?.scrollWidth ?? 0) + gap : 0) +
                            (Number.parseFloat(css.paddingLeft) || 0) +
                            (Number.parseFloat(css.paddingRight) || 0) +
                            (Number.parseFloat(css.borderLeftWidth) || 0) +
                            (Number.parseFloat(css.borderRightWidth) || 0),
                    )
                );
            }, 0);
            setWidth(Math.ceil(total));
        };
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(group);
        for (const icon of group.querySelectorAll(".expandable-button__icon"))
            observer.observe(icon);
        let cancelled = false;
        void document.fonts?.ready.then(() => {
            // The resolved value is unused — only the "fonts are ready" moment
            // matters for re-measuring the group.
            if (!cancelled) measure();
        });
        return () => {
            cancelled = true;
            observer.disconnect();
        };
    }, [children, reserveLast]);

    return (
        <ButtonGroup
            {...props}
            ref={ref}
            className={["expandable-button-group", className].filter(Boolean).join(" ")}
            style={{ ...style, minWidth: width }}
        >
            {children}
        </ButtonGroup>
    );
}
