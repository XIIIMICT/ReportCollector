"use client";

import type { ReactNode } from "react";

/**
 * The toolbar's filter container — the ONE place a filter control appears and
 * disappears with animation.
 *
 * Every project puts its filters either BESIDE the tabs (in the tabs row) or
 * BELOW them (their own row). Both cases animate the same way when a filter
 * becomes relevant or irrelevant:
 *
 * - `placement="inline"` (beside the tabs): the control collapses its WIDTH —
 *   `toolbar-collapse` / `toolbar-collapse--hidden`;
 * - `placement="below"` (its own row under the tabs): the row collapses its
 *   HEIGHT — `toolbar-filters__row--hidden`.
 *
 * The rule exists so that a new filter never ships "popping" in and out: adding
 * a control means wrapping it here, not re-deriving the animation.
 */
export function ToolbarFilters({
    placement,
    visible,
    children,
}: {
    /** `inline` — inside the tabs row; `below` — its own row under it. */
    placement: "inline" | "below";
    /** When false the container collapses and stops taking pointer events. */
    visible: boolean;
    children: ReactNode;
}) {
    if (placement === "inline") {
        return (
            <div
                className={
                    visible
                        ? "toolbar-filters__inline toolbar-collapse"
                        : "toolbar-filters__inline toolbar-collapse toolbar-collapse--hidden"
                }
                aria-hidden={visible ? undefined : true}
            >
                {children}
            </div>
        );
    }
    return (
        <div
            className={
                visible
                    ? "toolbar-filters__row"
                    : "toolbar-filters__row toolbar-filters__row--hidden"
            }
            aria-hidden={visible ? undefined : true}
        >
            {children}
        </div>
    );
}
