import { afterEach, expect, mock, test } from "bun:test";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SectionTabs, type WorkspaceTab } from "../section-tabs";

afterEach(cleanup);

test("workspace tab selection forwards the complete scope, including the month", async () => {
    const tabs: WorkspaceTab[] = [
        { id: "august", label: "Август", section: "materialMovement", reportMonth: "2026-08-01" },
        { id: "prices", label: "Цены", section: "rawMaterialPrices", reportMonth: null },
    ];
    const changed = mock();
    const view = render(<SectionTabs tabs={tabs} value="august" onChange={changed} />);
    expect(screen.getByRole("tablist", { name: "Таблицы и справочники" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Август" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Цены" }));
    expect(changed).toHaveBeenCalledWith(tabs[1]);
    view.rerender(<SectionTabs tabs={tabs} value="prices" onChange={changed} />);
    expect(screen.getByRole("tab", { name: "Цены" }).getAttribute("aria-selected")).toBe("true");
    await act(async () => {
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    });
});

test("a tab marked separatorBefore starts a new group", () => {
    const tabs: WorkspaceTab[] = [
        { id: "movement", label: "Движение", section: "materialMovement", reportMonth: null },
        {
            id: "rawMaterials",
            label: "Сырьевые позиции",
            section: "rawMaterials",
            reportMonth: null,
            separatorBefore: true,
        },
    ];
    const view = render(<SectionTabs tabs={tabs} value="movement" onChange={mock()} />);
    const separators = view.container.querySelectorAll('[data-slot="tabs-separator"]');
    expect(separators.length).toBe(1);
    expect(separators[0].closest('[role="tab"]')?.textContent).toContain("Сырьевые позиции");
});
