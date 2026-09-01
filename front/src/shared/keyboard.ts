/**
 * Whether the event originates inside a text editor (input, textarea or
 * contentEditable). Sees through the shadow DOM via composedPath: revo-grid's
 * built-in cell editors are shadow inputs, and document.activeElement only
 * reports their host element. Works for keyboard and clipboard events.
 */
export function isFromTextEditor(event: Event): boolean {
    return (event.composedPath() as EventTarget[]).some(
        (node) =>
            node instanceof HTMLElement &&
            (node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.isContentEditable),
    );
}
