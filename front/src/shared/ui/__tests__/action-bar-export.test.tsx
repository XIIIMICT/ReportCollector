import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Download } from "iconoir-react";
import { createElement } from "react";

import { ActionBar, type ActionItem } from "../action-bar";
import { buildExportAction } from "../action-items";

afterEach(cleanup);

function exportBar(onExport: (mode: "all" | "visible") => void) {
    const items: ActionItem[] = [
        buildExportAction({ pending: false, disabled: false, rowCount: 5, onExport }),
    ];
    return <ActionBar items={items} />;
}

/**
 * The object-level plan puts «Загрузить из Excel» right after the export, in the
 * SAME `download` group. A ButtonGroup renders every child as a plain button and
 * cannot host a dropdown, so the export's «вся / видимая таблица» menu used to
 * vanish on that tab alone — a press exported everything and there was no way to
 * ask for the visible rows. The dictionary tabs have no import button, so their
 * export stayed alone and kept the menu.
 */
test("the export menu survives sharing the download group with an import button", async () => {
    const calls: string[] = [];
    const items: ActionItem[] = [
        buildExportAction({
            pending: false,
            disabled: false,
            rowCount: 5,
            onExport: (m) => calls.push(m),
        }),
        {
            id: "plan-excel-import",
            label: "Загрузить из Excel",
            icon: createElement(Download),
            onPress: () => {},
            group: "download",
        },
    ];
    render(<ActionBar items={items} />);

    // A plain press must NOT export: it opens the dropdown instead.
    const trigger = screen.getByRole("button", { name: "Скачать в Excel" });
    const upload = screen.getByRole("button", { name: "Загрузить из Excel" });
    expect(trigger.closest('[data-slot="button-group"]')).toBeTruthy();
    expect(trigger.parentElement).toBe(upload.parentElement);
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.queryByText("Скачать видимую таблицу")).toBeTruthy());
    expect(calls).toEqual([]);

    fireEvent.click(screen.getByText("Скачать видимую таблицу"));
    await waitFor(() => expect(calls).toEqual(["visible"]));
});

test("the export trigger exposes both download modes", async () => {
    const calls: string[] = [];
    render(exportBar((mode) => calls.push(mode)));

    const trigger = screen.getByRole("button", { name: "Скачать в Excel" });
    expect(trigger).toBeTruthy();

    fireEvent.mouseEnter(trigger);
    fireEvent.mouseMove(trigger);
    fireEvent.click(trigger);

    await waitFor(() => expect(screen.queryByText("Скачать видимую таблицу")).toBeTruthy());
    expect(screen.queryByText("Скачать всю таблицу")).toBeTruthy();

    fireEvent.click(screen.getByText("Скачать видимую таблицу"));
    await waitFor(() => expect(calls).toEqual(["visible"]));
});

void createElement;
void Download;
