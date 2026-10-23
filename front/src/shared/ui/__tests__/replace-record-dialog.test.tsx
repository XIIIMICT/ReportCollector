import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render } from "@testing-library/react";

import { ReplaceRecordDialog } from "../replace-record-dialog";

afterEach(cleanup);

const options = [
    { id: "a", name: "Значение А" },
    { id: "b", name: "Значение Б" },
];

test("explains the replacement, offers no self-target and reports the choice", () => {
    const targets: Array<string | null> = [];
    const view = render(
        <ReplaceRecordDialog
            isOpen
            isPending={false}
            label="Старое значение"
            options={options}
            onOpenChange={() => {}}
            onConfirm={(targetId) => targets.push(targetId)}
        />,
    );

    expect(
        view.getByText(
            /Выберите значение справочника, на которое хотите заменить «Старое значение»/,
        ),
    ).toBeTruthy();
    expect(view.getByRole("button", { name: "Отмена" })).toBeTruthy();
    expect(view.queryByText("Старое значение")).toBeNull();

    // Cancelling never reports a target; «Заменить» does.
    fireEvent.click(view.getByRole("button", { name: "Заменить" }));
});

test("the empty option reports null", () => {
    const targets: Array<string | null> = [];
    const view = render(
        <ReplaceRecordDialog
            isOpen
            isPending={false}
            label="Старое"
            options={options}
            onOpenChange={() => {}}
            onConfirm={(targetId) => targets.push(targetId)}
        />,
    );
    fireEvent.click(view.getByRole("button", { name: "Заменить" }));
    expect(targets).toEqual([null]);
});
