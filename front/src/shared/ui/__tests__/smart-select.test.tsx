import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { hasOpenSmartSelect, SmartSelect } from "../smart-select";

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

test.each([{ options: shortOptions }, { options: longOptions }])(
    "Escape closes an open select without clearing the page selection",
    async ({ options }) => {
        // Simulate the grid's window-capture shortcut, registered before the select.
        let selectionCleared = 0;
        const gridEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !hasOpenSmartSelect()) selectionCleared += 1;
        };
        let bubbled = 0;
        const pageEscape = () => {
            bubbled += 1;
        };
        window.addEventListener("keydown", gridEscape, true);
        window.addEventListener("keydown", pageEscape);
        const { calls } = renderSelect({ options, defaultOpen: true });
        try {
            await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(0));
            fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
            await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
            expect(selectionCleared).toBe(0);
            expect(bubbled).toBe(0);
            expect(calls).toEqual([]);
            fireEvent.keyDown(document.body, { key: "Escape" });
            expect(selectionCleared).toBe(1);
        } finally {
            window.removeEventListener("keydown", gridEscape, true);
            window.removeEventListener("keydown", pageEscape);
        }
    },
);

test("unhover closes a select by default", async () => {
    renderSelect({ defaultOpen: true });
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(true));
    fireEvent.mouseMove(document.body, { clientX: 900, clientY: 900 });
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
});

test("outside pointer clicks close a persistent select", async () => {
    renderSelect({ defaultOpen: true, closeOnUnhover: false });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    const outside = document.querySelector('[data-testid="underlay"]')!;
    fireEvent.mouseDown(outside, { button: 0 });
    fireEvent.mouseUp(outside, { button: 0 });
    fireEvent.click(outside);
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
});

test("embedded editor still dismisses on Escape", async () => {
    let dismissed = 0;
    renderSelect({
        embedded: true,
        onDismiss: () => {
            dismissed += 1;
        },
    });
    const trigger = document.querySelector('[data-slot="select"]');
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    fireEvent.keyDown(trigger!, { key: "Escape" });
    expect(dismissed).toBe(1);
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
