import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";
import type { CSSProperties } from "react";

import { ExpandableButton } from "../expandable-button";
import { ExpandableButtonGroup } from "../expandable-button-group";

afterEach(cleanup);

function measuredButton(label: string, textWidth: number) {
    return (
        <ExpandableButton
            key={label}
            icon={<span>✓</span>}
            style={
                {
                    minWidth: "42px",
                    paddingLeft: "10px",
                    paddingRight: "10px",
                    borderLeftWidth: "1px",
                    borderRightWidth: "1px",
                    "--expandable-button-gap": "8px",
                } as CSSProperties
            }
            ref={(button) => {
                if (!button) return;
                const text = button.querySelector<HTMLElement>(".expandable-button__label")!;
                const icon = button.querySelector<HTMLElement>(".expandable-button__icon")!;
                Object.defineProperty(text, "scrollWidth", {
                    configurable: true,
                    value: textWidth,
                });
                icon.getBoundingClientRect = () => ({ width: 16 }) as DOMRect;
            }}
        >
            {label}
        </ExpandableButton>
    );
}

test("reserves all expanded widths, not just the currently hovered label", () => {
    const view = render(
        <ExpandableButtonGroup size="sm">
            {measuredButton("Отменить", 60)}
            {measuredButton("Применить", 80)}
        </ExpandableButtonGroup>,
    );
    const group = view.getByRole("group");
    // Text + icon + expanded gap + padding + borders, for BOTH buttons.
    expect(group.style.minWidth).toBe(`${60 + 80 + 2 * (16 + 8 + 20 + 2)}px`);
    expect(view.getByRole("button", { name: "Отменить" }).className).toContain("button--sm");

    view.rerender(
        <ExpandableButtonGroup>
            {measuredButton("По умолчанию", 100)}
            {measuredButton("Отменить", 60)}
            {measuredButton("Применить", 80)}
        </ExpandableButtonGroup>,
    );
    expect(group.style.minWidth).toBe(`${100 + 60 + 80 + 3 * (16 + 8 + 20 + 2)}px`);
});

test("can reserve only the final two actions for a reset/cancel/apply footer", () => {
    const view = render(
        <ExpandableButtonGroup reserveLast={2}>
            {measuredButton("По умолчанию", 100)}
            {measuredButton("Отменить", 60)}
            {measuredButton("Применить", 80)}
        </ExpandableButtonGroup>,
    );
    const group = view.getByRole("group");
    // Reset still occupies its compact minimum width, but reserves no label.
    expect(group.style.minWidth).toBe(`${42 + 60 + 80 + 2 * (16 + 8 + 20 + 2)}px`);
});
