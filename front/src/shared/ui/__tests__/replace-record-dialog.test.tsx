import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render } from "@testing-library/react";

import { ReplaceRecordDialog } from "../replace-record-dialog";

afterEach(cleanup);

const OPTIONS = [
    { id: "a", name: "ТО" },
    { id: "b", name: "ТР" },
];

test("nothing is chosen until the user picks a value, so confirm starts disabled", () => {
    const view = render(
        <ReplaceRecordDialog
            isOpen
            isPending={false}
            label="КР"
            sectionLabel="Вид работ"
            options={OPTIONS}
            onOpenChange={() => {}}
            onConfirm={() => {}}
        />,
    );
    const confirm = view.getByRole("button", { name: "Заменить запись" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    // The trigger shows the hint, not «Пустое значение»: an untouched control is
    // not the same thing as choosing to clear the value.
    expect(view.getByRole("button", { name: /Значение для замены/ }).textContent).toContain(
        "Выберите значение",
    );
});

test("«Пустое значение» is a real option that reports null", () => {
    const confirmed: Array<string | null> = [];
    const view = render(
        <ReplaceRecordDialog
            isOpen
            isPending={false}
            label="КР"
            sectionLabel="Вид работ"
            options={OPTIONS}
            onOpenChange={() => {}}
            onConfirm={(target) => confirmed.push(target)}
        />,
    );
    const trigger = view.getByRole("button", { name: /Значение для замены/ });
    fireEvent.mouseEnter(trigger);
    fireEvent.click(trigger);

    const empty = view.getByRole("option", { name: /Пустое значение/ });
    fireEvent.click(empty);
    fireEvent.click(view.getByRole("button", { name: "Заменить запись" }));

    expect(confirmed).toEqual([null]);
    void empty;
});
