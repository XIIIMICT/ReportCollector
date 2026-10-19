import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SmartSelect } from "../smart-select";

afterEach(cleanup);

const shortOptions = [
    { id: "a", name: "Альфа" },
    { id: "b", name: "Бета" },
    { id: "c", name: "Гамма" },
];

const longOptions = Array.from({ length: 8 }, (_, index) => ({
    id: `opt-${index}`,
    name: `Значение ${index}`,
}));

function renderSelect(props: Partial<Parameters<typeof SmartSelect>[0]> = {}) {
    const onChange = (value: string | string[] | null) => {
        calls.push(value);
    };
    const calls: (string | string[] | null)[] = [];
    const view = render(
        <SmartSelect
            options={shortOptions}
            value={null}
            onChange={onChange}
            label="Поле"
            placeholder="Выберите значение"
            {...props}
        />,
    );
    return { view, calls };
}

test("a short list renders a Select (no search field)", async () => {
    renderSelect();
    const trigger = document.querySelector('[data-slot="select"]');
    expect(trigger).toBeTruthy();
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);
    // The options open, and no search input appears: it is a plain Select.
    await waitFor(() => expect(screen.getAllByText("Альфа").length).toBeGreaterThan(0));
    expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("Найти…")).toBeNull();
});

test("a long list renders a searchable Autocomplete", async () => {
    renderSelect({ options: longOptions });
    const trigger = screen.getByText("Выберите значение");
    fireEvent.mouseEnter(trigger.closest("div") ?? trigger);
    await waitFor(() => expect(screen.getByPlaceholderText("Найти…")).toBeTruthy());
});

test("the threshold is configurable", async () => {
    // Three options, but the owner asks for Select only up to 2.
    renderSelect({ threshold: 2 });
    const trigger = screen.getByText("Выберите значение");
    fireEvent.mouseEnter(trigger.closest("div") ?? trigger);
    await waitFor(() => expect(screen.getByPlaceholderText("Найти…")).toBeTruthy());
});

test("multiple is independent of the component choice", async () => {
    const { calls } = renderSelect({ multiple: true, value: [] });
    expect(calls).toEqual([]);
    // The trigger renders and, being a short list, it is a Select — multiple
    // selection is supported by both controls.
    expect(screen.getByText("Выберите значение")).toBeTruthy();
});

test("nullOption and allOption are pinned above the values, separated by a rule", async () => {
    renderSelect({
        nullOption: { label: "Пустые", present: true, count: 12 },
        allOption: { label: "Все варианты", present: true },
    });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);

    // Both pinned rows open first, with the count suffix on the null row.
    await waitFor(() => expect(screen.getByRole("option", { name: "Все варианты" })).toBeTruthy());
    expect(screen.getByRole("option", { name: "Пустые (12)" })).toBeTruthy();
    // A rule separates them from the real values.
    expect(document.querySelector(".smart-select__separator")).toBeTruthy();
    expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy();
});

test("an absent special option renders nothing (no stray rule)", async () => {
    renderSelect({ nullOption: { label: "Пустые", present: false } });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    expect(screen.queryByRole("option", { name: /Пустые/ })).toBeNull();
    expect(document.querySelector(".smart-select__separator")).toBeNull();
});

test("picking the null row reports null", async () => {
    const { calls } = renderSelect({
        value: "a",
        nullOption: { label: "Не задан", present: true },
    });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);
    const item = await screen.findByRole("option", { name: "Не задан" });
    fireEvent.click(item);
    await waitFor(() => expect(calls).toContain(null));
});
