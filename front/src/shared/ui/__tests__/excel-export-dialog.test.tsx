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
