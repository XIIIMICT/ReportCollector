import { afterEach, expect, test } from "bun:test";

import { ButtonGroup } from "@heroui/react";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { ActionBar } from "../action-bar";
import { ExpandableButton } from "../expandable-button";

afterEach(cleanup);

test("is a direct ButtonGroup child and owns the shared expandable classes", () => {
    const view = render(
        <ButtonGroup size="sm" variant="danger">
            <ExpandableButton icon={<span>↻</span>} aria-label="Сбросить">
                Сбросить
            </ExpandableButton>
        </ButtonGroup>,
    );
    const button = view.getByRole("button", { name: "Сбросить" });
    expect(button.className).toContain("expandable-button");
    expect(button.querySelector(".expandable-button__icon")).toBeTruthy();
    expect(button.querySelector(".expandable-button__label")?.textContent).toBe("Сбросить");
    expect(button.closest('[data-slot="button-group"]')).toBeTruthy();
    expect(button.parentElement?.getAttribute("data-slot")).toBe("button-group");
    // These defaults reach Button only when the injected group marker survives.
    expect(button.className).toContain("button--sm");
    expect(button.className).toContain("button--danger");
    expect(button.hasAttribute("__button_group_child")).toBe(false);
});

test("keeps its ref, overrides, expansion state and press handler", () => {
    const ref = { current: null as HTMLButtonElement | null };
    let presses = 0;
    const view = render(
        <ButtonGroup size="sm" variant="danger">
            <ExpandableButton
                ref={ref}
                icon={<span>✓</span>}
                variant="primary"
                isExpanded
                onPress={() => presses++}
            >
                Применить
            </ExpandableButton>
        </ButtonGroup>,
    );
    const button = view.getByRole("button", { name: "Применить" });
    expect(ref.current).toBe(button as HTMLButtonElement);
    expect(button.className).toContain("expandable-button--expanded");
    expect(button.className).toContain("button--primary");
    expect(button.className).not.toContain("button--danger");
    fireEvent.click(button);
    expect(presses).toBe(1);
});

test("inherits disabled state from ButtonGroup", () => {
    let presses = 0;
    const view = render(
        <ButtonGroup isDisabled>
            <ExpandableButton icon={<span>✓</span>} onPress={() => presses++}>
                Применить
            </ExpandableButton>
        </ButtonGroup>,
    );
    const button = view.getByRole("button", { name: "Применить" });
    expect(button.hasAttribute("disabled")).toBe(true);
    fireEvent.click(button);
    expect(presses).toBe(0);
});

test("action bar uses the same expandable markup for grouped and primary actions", () => {
    const view = render(
        <ActionBar
            items={[
                {
                    id: "undo",
                    label: "Отменить",
                    icon: <span>↶</span>,
                    group: "history",
                    onPress: () => {},
                },
                {
                    id: "redo",
                    label: "Вернуть",
                    icon: <span>↷</span>,
                    group: "history",
                    onPress: () => {},
                },
                {
                    id: "apply",
                    label: "Зафиксировать",
                    icon: <span>✓</span>,
                    variant: "primary",
                    onPress: () => {},
                },
            ]}
        />,
    );
    const undo = view.getByRole("button", { name: "Отменить" });
    const redo = view.getByRole("button", { name: "Вернуть" });
    expect(undo.parentElement).toBe(redo.parentElement);
    expect(undo.parentElement?.getAttribute("data-slot")).toBe("button-group");
    expect(undo.querySelector(".expandable-button__label")?.textContent).toBe("Отменить");
    expect(view.getByRole("button", { name: "Зафиксировать" }).className).toContain(
        "expandable-button--expanded",
    );
});
