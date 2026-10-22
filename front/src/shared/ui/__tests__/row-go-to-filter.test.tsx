import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";

import { RowGoToFilter } from "../row-go-to-filter";

afterEach(cleanup);

async function openMenu() {
    const rows: number[] = [];
    const columns: number[] = [];
    let closes = 0;
    function Harness() {
        const [open, setOpen] = useState(true);
        return open ? (
            <RowGoToFilter
                anchor={{ left: 0, top: 0, width: 100 } as DOMRect}
                total={100}
                maxColumn={216}
                onGoTo={(position) => rows.push(position)}
                onGoToColumn={(column) => columns.push(column)}
                onClose={() => {
                    closes++;
                    setOpen(false);
                }}
            />
        ) : null;
    }
    render(<Harness />);
    await waitFor(() => expect(screen.getByLabelText("Номер строки")).toBeTruthy());
    return { rows, columns, closes: () => closes };
}

function typeCoordinates(row = "17", column = "23") {
    fireEvent.change(screen.getByLabelText("Номер строки"), {
        target: { value: row },
    });
    fireEvent.change(screen.getByLabelText("Номер столбца"), {
        target: { value: column },
    });
}

test("footer uses the shared expanding buttons in one group without reset", async () => {
    await openMenu();
    const cancel = screen.getByRole("button", { name: "Отменить" });
    const apply = screen.getByRole("button", { name: "Применить" });
    expect(cancel.className).toContain("button--tertiary");
    expect(apply.className).toContain("button--primary");
    expect(apply.parentElement).toBe(cancel.parentElement);
    expect(apply.parentElement?.getAttribute("data-slot")).toBe("button-group");
    expect(cancel.querySelector(".expandable-button__label")).toBeTruthy();
    expect(apply.querySelector(".expandable-button__label")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /По умолчанию/ })).toBeNull();
    const input = screen.getByLabelText("Номер столбца");
    expect(input.compareDocumentPosition(apply) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test.each(["button", "Enter"])("%s applies typed row and column", async (mode) => {
    const { rows, columns, closes } = await openMenu();
    typeCoordinates();
    if (mode === "button") fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    else
        fireEvent.keyDown(screen.getByLabelText("Номер столбца"), {
            key: "Enter",
        });
    expect(rows).toEqual([17]);
    expect(columns).toEqual([23]);
    await waitFor(() => expect(closes()).toBe(1));
});

test.each(["button", "Escape", "outside"])("%s cancels without jumping", async (mode) => {
    const { rows, columns, closes } = await openMenu();
    typeCoordinates();
    if (mode === "button") fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    else if (mode === "Escape")
        fireEvent.keyDown(screen.getByLabelText("Номер строки"), {
            key: "Escape",
        });
    else {
        const outside = document.querySelector('[data-testid="underlay"]')!;
        fireEvent.mouseDown(outside, { button: 0 });
        fireEvent.mouseUp(outside, { button: 0 });
        fireEvent.click(outside);
    }
    await waitFor(() => expect(closes()).toBe(1));
    expect(rows).toEqual([]);
    expect(columns).toEqual([]);
});

test("apply reads the latest text, clamps rows and ignores empty coordinates", async () => {
    const { rows, columns } = await openMenu();
    typeCoordinates("12", "23");
    fireEvent.blur(screen.getByLabelText("Номер строки"));
    typeCoordinates("120", "");
    fireEvent.keyDown(screen.getByLabelText("Номер строки"), { key: "Enter" });
    expect(rows).toEqual([100]);
    expect(columns).toEqual([]);
});
