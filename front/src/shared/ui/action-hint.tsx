"use client";

import { Kbd } from "@heroui/react";
import { createPortal } from "react-dom";

type Modifier = "ctrl" | "shift" | "alt" | "meta";

/** Written-out modifier labels — clearer than the ⌃⌥⇧ glyphs. */
const MODIFIER_LABEL: Record<Modifier, string> = {
    ctrl: "Ctrl",
    shift: "Shift",
    alt: "Alt",
    meta: "⌘",
};

/** Stable empty default: an inline `[]` would be a new array on every render. */
const NO_MODIFIERS: Modifier[] = [];

/** Parse an `aria-keyshortcuts` value («Control+Shift+Z») into Kbd parts. */
export function parseShortcut(aria: string): { modifiers: Modifier[]; keyLabel: string } {
    const parts = aria.split("+").filter(Boolean);
    const modifiers: Modifier[] = [];
    let keyLabel = "";
    for (const part of parts) {
        const normalized = part.toLowerCase();
        if (normalized === "control" || normalized === "ctrl") modifiers.push("ctrl");
        else if (normalized === "shift") modifiers.push("shift");
        else if (normalized === "alt") modifiers.push("alt");
        else if (normalized === "meta") modifiers.push("meta");
        else keyLabel = part;
    }
    return { modifiers, keyLabel };
}

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
 *
 * Two kinds of hints share this box:
 *  • a shortcut — only the keys («Ctrl Z»), no words: the label is already on
 *    the button and a repetition only adds noise;
 *  • a blocked action — the reason in words (there are no keys to show).
 *
 * Modifiers are written out (`Ctrl`, `Shift`, `Alt`) instead of HeroUI's Kbd
 * glyphs («⌃», «⇧»): the glyphs are not universally understood, and mixing a
 * glyph with a letter reads worse than «Ctrl Z».
 */
export function ActionHint({
    left,
    top,
    label,
    keyLabel,
    modifiers = NO_MODIFIERS,
}: {
    left: number;
    top: number;
    label: string;
    keyLabel?: string;
    modifiers?: Modifier[];
}) {
    if (typeof document === "undefined") return null;
    // The hint is centered over the hovered button, which pushes half of it
    // off-screen for the rightmost actions (delete sits at the bar's end).
    // Clamp the whole box to the viewport: the anchor is corrected by the
    // measured overflow, so a long reason never gets cut by the screen edge.
    const margin = 12;
    const viewport = window.innerWidth;
    return createPortal(
        <div
            ref={(node) => {
                if (!node) return;
                const width = node.getBoundingClientRect().width;
                const half = width / 2;
                const minLeft = half + margin;
                const maxLeft = viewport - half - margin;
                const clamped = Math.min(Math.max(left, minLeft), Math.max(minLeft, maxLeft));
                if (Math.abs(clamped - left) > 0.5) {
                    node.style.left = `${clamped}px`;
                }
            }}
            className="shortcut-tooltip action-bar__hint"
            role="tooltip"
            style={{ left, top }}
        >
            {keyLabel ? (
                <span className="action-bar__hint-keys">
                    {modifiers.map((modifier) => (
                        <Kbd key={modifier}>{MODIFIER_LABEL[modifier]}</Kbd>
                    ))}
                    <Kbd>{keyLabel}</Kbd>
                </span>
            ) : (
                <span>{label}</span>
            )}
        </div>,
        document.body,
    );
}
