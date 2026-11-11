import { afterEach, expect, test } from "bun:test";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
    columnMenuItems,
    GridContextMenu,
    GridUnhideButton,
    type GridMenuRequest,
} from "../grid-header-menu";

afterEach(cleanup);

const noopMenu = {
    onHide: () => {},
    onPin: () => {},
    onUnpin: () => {},
    onHideService: () => {},
};

test("a pinned column offers only «Открепить» — it cannot be hidden", () => {
    const items = columnMenuItems({
        targets: ["demandPlanTons"],
        level: 1,
        pinnedKeys: new Set(["demandPlanTons"]),
        ...noopMenu,
    });
    expect(items.map((item) => item.id)).toEqual(["unpin"]);
    expect(items[0]?.label).toBe("Открепить");
});

test("a mixed group hides/pins its unpinned columns and unpins the pinned ones", () => {
    const items = columnMenuItems({
        targets: ["demandPlanTons", "consumptionFactTons"],
        level: 0,
        pinnedKeys: new Set(["demandPlanTons"]),
        ...noopMenu,
    });
    expect(items.map((item) => item.id)).toEqual(["hide", "pin", "unpin"]);
});

test("a service column is only hidden", () => {
    const items = columnMenuItems({
        targets: [],
        level: 0,
        pinnedKeys: new Set(),
        service: true,
        ...noopMenu,
    });
    expect(items.map((item) => item.id)).toEqual(["hide"]);
});

test("the grid context menu is a HeroUI menu and runs the chosen action", async () => {
    const handled: string[] = [];
    const request: GridMenuRequest = {
        x: 10,
        y: 10,
        items: [
            { id: "hide", label: "Скрыть", icon: "hide", onSelect: () => handled.push("hide") },
            { id: "pin", label: "Закрепить", icon: "pin", onSelect: () => handled.push("pin") },
        ],
    };
    render(<GridContextMenu request={request} isOpen onOpenChange={() => {}} />);

    const item = await screen.findByRole("menuitem", { name: "Скрыть" });
    expect(item.closest('[role="menu"]')).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Закрепить" })).toBeTruthy();
    fireEvent.click(item);
    expect(handled).toEqual(["hide"]);
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
