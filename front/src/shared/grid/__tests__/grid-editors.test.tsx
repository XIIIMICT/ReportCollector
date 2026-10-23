import { afterEach, expect, test } from "bun:test";

import type { EditorType } from "@revolist/react-datagrid";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ReferenceEditor, SelectEditor } from "../grid-editors";

afterEach(cleanup);

test.each([2, 8])(
    "reference editor with %i options uses the same invisible cell anchor",
    async (count) => {
        const values = Array.from({ length: count }, (_, index) => `Вариант ${index}`);
        const saved: unknown[] = [];
        render(
            <ReferenceEditor
                column={
                    {
                        prop: "value",
                        model: { value: "" },
                        column: { name: "Справочник", source: values },
                    } as unknown as EditorType["column"]
                }
                save={(value) => saved.push(value)}
                close={() => {}}
            />,
        );
        const option = await screen.findByRole("option", { name: "Вариант 0" });
        fireEvent.pointerDown(option);
        fireEvent.pointerUp(option);
        fireEvent.click(option);
        console.log("DBGOPT", count, JSON.stringify(saved));
        const anchor = document.querySelector(".grid-autocomplete-editor__hidden-trigger");
        expect(anchor).toBeTruthy();
        expect(anchor?.getAttribute("data-slot")).toBe(
            count > 5 ? "autocomplete-trigger" : "select-trigger",
        );
        await waitFor(() => expect(saved).toEqual(["Вариант 0"]));
    },
);

test("fixed-option editor uses the same hidden anchor", async () => {
    render(
        <SelectEditor
            column={
                {
                    prop: "role",
                    model: { role: "" },
                    column: { name: "Роль", source: [{ value: "admin", label: "Администратор" }] },
                } as unknown as EditorType["column"]
            }
            save={() => {}}
            close={() => {}}
        />,
    );
    await screen.findByRole("option", { name: "Администратор" });
    expect(
        document
            .querySelector(".grid-autocomplete-editor__hidden-trigger")
            ?.getAttribute("data-slot"),
    ).toBe("select-trigger");
});
