"use client";

import { createPortal } from "react-dom";

/**
 * The action bar's floating keyboard-shortcut hint.
 *
 * Rendered through a portal into `document.body` and `position: fixed`: the
 * footer that hosts the action bar uses `contain: paint`, which turns it into
 * the containing block for `fixed` descendants — an in-place fixed element
 * would be positioned against the footer and drawn off-screen. The portal
 * escapes that containing block, so the viewport coordinates from the hovered
 * button's rect are correct. A plain element (not HeroUI's Tooltip) is used
 * deliberately: react-aria's tooltip portals an `absolute` box against the
 * body, which grows the document and flashes a stray page scrollbar.
 */
export function ActionHint({ left, top, text }: { left: number; top: number; text: string }) {
    if (typeof document === "undefined") return null;
    return createPortal(
        <div className="shortcut-tooltip action-bar__hint" role="tooltip" style={{ left, top }}>
            <span>{text}</span>
        </div>,
        document.body,
    );
}
