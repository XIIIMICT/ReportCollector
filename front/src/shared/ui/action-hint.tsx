"use client";

import { Kbd } from "@heroui/react";
import { createPortal } from "react-dom";

type Modifier = "ctrl" | "shift" | "alt" | "meta";

/** HeroUI `KbdKey`-compatible labels for our modifiers. */
const KBD_KEY: Record<Modifier, "ctrl" | "shift" | "alt" | "command"> = {
    ctrl: "ctrl",
    shift: "shift",
    alt: "alt",
    meta: "command",
};

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
 * `modifiers` are rendered as `Kbd.Abbr` keys (`ctrl`, `shift`) and `key` as the
 * final `Kbd.Content`; both are omitted when the action has no shortcut.
 */
export function ActionHint({
    left,
    top,
    label,
    keyLabel,
    modifiers = [],
}: {
    left: number;
    top: number;
    label: string;
    keyLabel?: string;
    modifiers?: Modifier[];
}) {
    if (typeof document === "undefined") return null;
    return createPortal(
        <div className="shortcut-tooltip action-bar__hint" role="tooltip" style={{ left, top }}>
            <span>{label}</span>
            {keyLabel ? (
                <Kbd variant="light">
                    {modifiers.map((modifier) => (
                        <Kbd.Abbr key={modifier} keyValue={KBD_KEY[modifier]} />
                    ))}
                    <Kbd.Content>{keyLabel}</Kbd.Content>
                </Kbd>
            ) : null}
        </div>,
        document.body,
    );
}
