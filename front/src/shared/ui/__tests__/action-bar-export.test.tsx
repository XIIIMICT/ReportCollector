import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Download } from "iconoir-react";
import { createElement } from "react";

import { ActionBar, type ActionItem } from "../action-bar";
import { buildExportAction } from "../action-items";

afterEach(cleanup);

/**
 * The export button is identical in every project: one press opens the shared
 * dialog (no hover menu), and while a durable job runs the SAME button becomes
 * an enabled «Отменить» that calls the cancel handler.
 */
test("the export press opens the dialog instead of exporting directly", () => {
    let opened = 0;
    const items: ActionItem[] = [
        buildExportAction({
            pending: false,
            disabled: false,
            rowCount: 5,
            onPress: () => (opened += 1),
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

    const trigger = screen.getByRole("button", { name: "Скачать в Excel" });
    const upload = screen.getByRole("button", { name: "Загрузить из Excel" });
    expect(trigger.closest('[data-slot="button-group"]')).toBeTruthy();
    expect(trigger.parentElement).toBe(upload.parentElement);
    fireEvent.click(trigger);
    expect(opened).toBe(1);
    expect(screen.queryByText("Скачать видимую таблицу")).toBeNull();
});

test("a running download turns the button into an enabled Cancel", () => {
    let cancelled = 0;
    render(
        <ActionBar
            items={[
                buildExportAction({
                    pending: true,
                    disabled: false,
                    rowCount: 0,
                    onPress: () => {},
                    onCancel: () => (cancelled += 1),
                }),
            ]}
        />,
    );
    const button = screen.getByRole("button", { name: "Отменить" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(cancelled).toBe(1);
});

void createElement;
void Download;
