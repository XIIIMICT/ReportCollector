"use client";

import { Button } from "@heroui/react";
import type { ComponentProps, ReactNode } from "react";

export type ExpandableButtonProps = Omit<
    ComponentProps<typeof Button>,
    "children" | "className" | "isIconOnly" | "variant"
> & {
    icon: ReactNode;
    children: string;
    className?: string;
    isExpanded?: boolean;
    variant?: ComponentProps<typeof Button>["variant"];
};

/**
 * The shared compact-to-expanded action button used by toolbars and popover
 * footers. Pass all remaining Button props through, including refs and the
 * direct-child marker injected by HeroUI ButtonGroup. No extra DOM wrapper.
 */
export function ExpandableButton({
    icon,
    children,
    className,
    isExpanded = false,
    variant,
    ...props
}: ExpandableButtonProps) {
    return (
        <Button
            {...props}
            aria-label={props["aria-label"] ?? children}
            variant={variant}
            className={["expandable-button", isExpanded && "expandable-button--expanded", className]
                .filter(Boolean)
                .join(" ")}
        >
            <span className="expandable-button__icon">{icon}</span>
            <span className="expandable-button__label">{children}</span>
        </Button>
    );
}
