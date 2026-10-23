import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { ToolbarFilters } from "../toolbar-filters";

afterEach(cleanup);

test("an inline filter collapses its width through the shared toolbar class", () => {
    const view = render(
        <ToolbarFilters placement="inline" visible>
            <span>Настройка акциза</span>
        </ToolbarFilters>,
    );
    const node = view.container.firstElementChild as HTMLElement;
    expect(node.className).toContain("toolbar-collapse");
    expect(node.className).not.toContain("toolbar-collapse--hidden");
    expect(node.getAttribute("aria-hidden")).toBeNull();
});

test("a hidden inline filter keeps the transition classes AND hides itself", () => {
    const view = render(
        <ToolbarFilters placement="inline" visible={false}>
            <span>Настройка акциза</span>
        </ToolbarFilters>,
    );
    const node = view.container.firstElementChild as HTMLElement;
    // Both classes: the transition lives in the base one, the hidden one only
    // sets the collapsed values — dropping the base class would snap.
    expect(node.className).toContain("toolbar-collapse");
    expect(node.className).toContain("toolbar-collapse--hidden");
    expect(node.getAttribute("aria-hidden")).toBe("true");
});

test("a filter below the tabs collapses its height instead", () => {
    const visible = render(
        <ToolbarFilters placement="below" visible>
            <span>Срез</span>
        </ToolbarFilters>,
    );
    const shown = visible.container.firstElementChild as HTMLElement;
    expect(shown.className).toContain("toolbar-filters__row");
    expect(shown.className).not.toContain("toolbar-filters__row--hidden");
    cleanup();

    const hidden = render(
        <ToolbarFilters placement="below" visible={false}>
            <span>Срез</span>
        </ToolbarFilters>,
    );
    const collapsed = hidden.container.firstElementChild as HTMLElement;
    expect(collapsed.className).toContain("toolbar-filters__row");
    expect(collapsed.className).toContain("toolbar-filters__row--hidden");
    expect(collapsed.getAttribute("aria-hidden")).toBe("true");
});
