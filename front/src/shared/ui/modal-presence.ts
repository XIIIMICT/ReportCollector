/**
 * Whether a modal-level overlay (AlertDialog / Dialog) is open anywhere in the
 * document.
 *
 * HeroUI portals these overlays into `document.body`, so they live OUTSIDE the
 * grid that opened them: a window-level Escape handler cannot tell "the dialog
 * closed" from "nobody handled it" and would also clear the grid selection
 * underneath the dialog. Global capture shortcuts use this to leave Escape to
 * the overlay, exactly like `hasOpenSmartSelect` does for popovers.
 */
export function hasOpenModal(): boolean {
    return document.querySelector('[role="alertdialog"], [role="dialog"]') !== null;
}

/** Portals and inline editors own Escape before grid selection or fullscreen. */
export function hasOpenKeyboardOverlay(): boolean {
    return (
        hasOpenModal() ||
        document.querySelector(
            '[data-smart-select-open="true"], [data-slot="dropdown-popover"], [data-slot="popover"], .column-filter__popover, .row-go-to-filter__popover, [data-grid-keyboard-overlay]',
        ) !== null
    );
}
