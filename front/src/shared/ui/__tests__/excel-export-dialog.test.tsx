import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExcelExportDialog } from "../excel-export-dialog";

afterEach(cleanup);

test("both export switches default to the fast/safe options", () => {
    render(<ExcelExportDialog isOpen onOpenChange={() => {}} onConfirm={() => {}} />);
    const switches = screen.getAllByRole("switch") as HTMLButtonElement[];
    expect(switches).toHaveLength(2);
    expect((switches[0] as unknown as HTMLInputElement).checked).toBe(true);
    expect((switches[1] as unknown as HTMLInputElement).checked).toBe(true);
});

test("switches have plain labels and no descriptions", () => {
    // The Modal portals into document.body, not into the render container.
    render(<ExcelExportDialog isOpen onOpenChange={() => {}} onConfirm={() => {}} />);
    const labels = document.querySelectorAll(".font-normal");
    expect(labels).toHaveLength(2);
    expect(labels[0]!.textContent).toBe("Учитывать фильтры и сортировки");
    expect(labels[1]!.textContent).toBe("Сохранять значения вместо формул");
    expect(document.querySelectorAll('[data-slot="description"]')).toHaveLength(0);
});

test("cancel and confirm share a button group", () => {
    render(<ExcelExportDialog isOpen onOpenChange={() => {}} onConfirm={() => {}} />);
    const group = document.querySelector('[data-slot="button-group"]');
    expect(group).not.toBeNull();
    expect(group!.querySelectorAll('[data-slot="button"]')).toHaveLength(2);
    expect(group!.textContent).toContain("Отменить");
    expect(group!.textContent).toContain("Скачать");
});

test("confirm sends the chosen options", () => {
    let chosen: { useFilters: boolean; valuesOnly: boolean } | null = null;
    render(
        <ExcelExportDialog
            isOpen
            onOpenChange={() => {}}
            onConfirm={(options) => {
                chosen = options;
            }}
        />,
    );
    const switches = screen.getAllByRole("switch") as HTMLButtonElement[];
    fireEvent.click(switches[0]!);
    fireEvent.click(switches[1]!);
    fireEvent.click(screen.getByRole("button", { name: "Скачать" }));
    expect(chosen as { useFilters: boolean; valuesOnly: boolean } | null).toEqual({
        useFilters: false,
        valuesOnly: false,
    });
});
