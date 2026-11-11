import { afterEach, expect, test } from "bun:test";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { GridHeaderMenu, GridUnhideButton, type GridHideRequest } from "../grid-header-menu";

afterEach(cleanup);

test("the header context menu is a HeroUI menu with the Hide item", async () => {
    const request: GridHideRequest = { x: 10, y: 10, props: ["demandPlanTons"], level: 1 };
    const handled: GridHideRequest[] = [];
    render(
        <GridHeaderMenu
            request={request}
            isOpen
            onOpenChange={() => {}}
            onHide={(value) => handled.push(value)}
        />,
    );

    const item = await screen.findByRole("menuitem", { name: "Скрыть" });
    expect(item.closest('[role="menu"]')).toBeTruthy();
    fireEvent.click(item);
    expect(handled).toEqual([request]);
});

test("the expand control is an icon-only HeroUI button", async () => {
    const pressed: number[] = [];
    render(
        <GridUnhideButton
            label="Показать скрытые столбцы (2)"
            entries={[
                { number: 3, path: ["Потребность", "План, т"] },
                { number: 4, path: ["Потребность", "Факт, т"] },
            ]}
            onPress={() => pressed.push(1)}
            style={{ left: 40, top: 0 }}
        />,
    );

    const button = screen.getByRole("button", { name: "Показать скрытые столбцы (2)" });
    expect(button.className).toContain("button--icon-only");
    fireEvent.click(button);
    await waitFor(() => expect(pressed).toEqual([1]));
});

test("the expand control's tooltip lists the hidden columns by table number", async () => {
    render(
        <GridUnhideButton
            label="Показать скрытые столбцы (2)"
            entries={[
                { number: 3, path: ["Потребность", "План, т"] },
                { number: 4, path: ["Потребность", "Факт, т"] },
            ]}
            onPress={() => {}}
        />,
    );

    const button = screen.getByRole("button", { name: "Показать скрытые столбцы (2)" });
    await act(async () => {
        fireEvent.mouseEnter(button);
        button.focus();
    });
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain("3. Потребность → План, т");
    expect(tooltip.textContent).toContain("4. Потребность → Факт, т");
});
