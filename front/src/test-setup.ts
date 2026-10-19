// Registering the happy-dom DOM environment for unit tests (bun test)
import { Window } from "happy-dom";

const domWindow = new Window();

Object.defineProperties(globalThis, {
    window: { configurable: true, value: domWindow, writable: true },
    document: { configurable: true, value: domWindow.document, writable: true },
    navigator: { configurable: true, value: domWindow.navigator, writable: true },
    HTMLElement: { configurable: true, value: domWindow.HTMLElement, writable: true },
    HTMLInputElement: { configurable: true, value: domWindow.HTMLInputElement, writable: true },
    HTMLButtonElement: { configurable: true, value: domWindow.HTMLButtonElement, writable: true },
    HTMLTextAreaElement: {
        configurable: true,
        value: domWindow.HTMLTextAreaElement,
        writable: true,
    },
    HTMLSelectElement: { configurable: true, value: domWindow.HTMLSelectElement, writable: true },
    HTMLAnchorElement: { configurable: true, value: domWindow.HTMLAnchorElement, writable: true },
    customElements: { configurable: true, value: domWindow.customElements, writable: true },
    getComputedStyle: {
        configurable: true,
        value: domWindow.getComputedStyle.bind(domWindow),
        writable: true,
    },
    matchMedia: { configurable: true, value: domWindow.matchMedia.bind(domWindow), writable: true },
    MutationObserver: { configurable: true, value: domWindow.MutationObserver, writable: true },
    Element: { configurable: true, value: domWindow.Element, writable: true },
    Node: { configurable: true, value: domWindow.Node, writable: true },
    NodeFilter: { configurable: true, value: domWindow.NodeFilter, writable: true },
    CSS: { configurable: true, value: domWindow.CSS, writable: true },
    SVGElement: { configurable: true, value: domWindow.SVGElement, writable: true },
    ResizeObserver: { configurable: true, value: domWindow.ResizeObserver, writable: true },
    // react-aria's `focusSafely` constructs a FocusEvent when it moves the
    // virtual focus onto a list option; happy-dom has no such global.
    FocusEvent: { configurable: true, value: domWindow.FocusEvent, writable: true },
    requestAnimationFrame: {
        configurable: true,
        value: domWindow.requestAnimationFrame.bind(domWindow),
        writable: true,
    },
    cancelAnimationFrame: {
        configurable: true,
        value: domWindow.cancelAnimationFrame.bind(domWindow),
        writable: true,
    },
});
