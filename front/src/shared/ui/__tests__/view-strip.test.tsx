import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";

import { ViewStrip } from "../view-strip";

afterEach(cleanup);

test("table menu toggles row density without an input and resets it with dimensions", async () => {
    const changes: number[] = [];
    const resets: string[] = [];
    function Harness() {
        const [rowHeight, setRowHeight] = useState(36);
        return (
            <ViewStrip
                rowHeight={rowHeight}
                onRowHeightChange={(height) => {
                    changes.push(height);
                    setRowHeight(height);
                }}
                onResetRowHeight={() => setRowHeight(36)}
                onReset={(scope) => resets.push(scope)}
                isFullscreen={false}
                isFullscreenPending={false}
                onToggleFullscreen={() => {}}
            />
        );
    }
    render(<Harness />);
    const open = () => fireEvent.click(screen.getByRole("button", { name: "Вид таблицы" }));
    open();
    await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Сузить строки" })).toBeTruthy(),
    );
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(document.querySelector("input")).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Сузить строки" }));
    expect(changes).toEqual([18]);
    open();
    await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Расширить строки" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Расширить строки" }));
    expect(changes).toEqual([18, 36]);
    open();
    await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Сузить строки" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Сузить строки" }));
    open();
    await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Расширить строки" })).toBeTruthy(),
    );
    fireEvent.click(
        screen.getByRole("menuitem", { name: "Сбросить ширину столбцов и высоту строк" }),
    );
    expect(resets).toEqual(["columnWidths"]);
    open();
    await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Сузить строки" })).toBeTruthy(),
    );
});
