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

test("commit mode shows the footer, keeps hover, and cancels on Escape", async () => {
    const events: string[] = [];
    renderSelect({
        options: longOptions,
        defaultOpen: true,
        closeOnUnhover: false,
        onConfirm: () => events.push("confirm"),
        onCancel: () => events.push("cancel"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Значение 0" })).toBeTruthy());
    // The footer belongs to the panel, after the scrolling list.
    const footer = document.querySelector(".smart-select__footer");
    const list = screen.getByRole("listbox");
    expect(footer).toBeTruthy();
    expect(list.compareDocumentPosition(footer!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Применить" })).toBeTruthy();

    // Hover dismissal is off: leaving the panel keeps it open.
    fireEvent.mouseMove(document.body, { clientX: 900, clientY: 900 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(hasOpenSmartSelect()).toBe(true);

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
    expect(events).toEqual(["cancel"]);
});

test("commit mode: «Применить» confirms without cancelling", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        onConfirm: () => events.push("confirm"),
        onCancel: () => events.push("cancel"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    const confirm = screen.getByRole("button", { name: "Применить" });
    fireEvent.click(confirm);
    // The press reaches the callback: SmartSelect itself stays open, because
    // the owner's onConfirm is what closes it (here it only records).
    expect(events).toEqual(["confirm"]);
});

test("commit mode: «Отменить» cancels", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        onConfirm: () => events.push("confirm"),
        onCancel: () => events.push("cancel"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
    expect(events).toEqual(["cancel"]);
});

test("commit mode: the outside press cancels before react-aria closes", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        onCancel: () => events.push("cancel"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    const outside = document.querySelector('[data-testid="underlay"]')!;
    fireEvent.mouseDown(outside, { button: 0 });
    expect(events).toEqual(["cancel"]);
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
});

test("commit mode: «Применить» can be disabled by the owner", async () => {
    renderSelect({ defaultOpen: true, closeOnUnhover: false, canConfirm: false });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    expect(document.querySelector(".smart-select__footer")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Применить" }).hasAttribute("disabled")).toBe(true);
});

test("default mode has no commit footer", async () => {
    renderSelect({ defaultOpen: true });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    expect(document.querySelector(".smart-select__footer")).toBeNull();
});

test("Enter in commit mode applies the selection", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        onConfirm: () => events.push("confirm"),
        onCancel: () => events.push("cancel"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    // From anywhere in the panel: the list, the search field, the footer.
    fireEvent.keyDown(document.querySelector(".smart-select__footer")!, { key: "Enter" });
    expect(events).toEqual(["confirm"]);
});

test("Enter does not apply when «Применить» is disabled", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        canConfirm: false,
        onConfirm: () => events.push("confirm"),
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    expect(events).toEqual([]);
});

test("«По умолчанию» renders first, icon-only, and calls its handler", async () => {
    const events: string[] = [];
    renderSelect({
        defaultOpen: true,
        closeOnUnhover: false,
        onReset: () => events.push("reset"),
        resetLabel: "По умолчанию: Группа",
    });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    const reset = screen.getByRole("button", { name: "По умолчанию: Группа" });
    // The shared labels are collapsed by CSS, not omitted from the DOM.
    expect(reset.querySelector(".expandable-button__label")?.textContent).toBe("По умолчанию");
    const cells = [...document.querySelectorAll(".smart-select__footer .button")];
    expect(cells.map((cell) => cell.getAttribute("aria-label"))).toEqual([
        "По умолчанию: Группа",
        "Отменить",
        "Применить",
    ]);
    // Stock HeroUI semantics: destructive reset, dismissive cancel, forward apply.
    expect(cells[0]!.className).toContain("button--danger");
    expect(cells[1]!.className).toContain("button--tertiary");
    expect(cells[2]!.className).toContain("button--primary");
    for (const cell of cells) {
        expect(cell.className).toContain("expandable-button");
        expect(cell.className).not.toContain("button--icon-only");
        expect(cell.querySelector(".expandable-button__label")).toBeTruthy();
    }
    // Direct children of ONE ButtonGroup, as in the action bar: the group owns
    // the corners, the buttons carry no class of ours (no local overrides).
    for (const cell of cells) expect(cell.className).not.toContain("smart-select__");
    const groups = [...document.querySelectorAll(".smart-select__footer .button-group")];
    expect(groups.length).toBe(1);
    expect(
        [...groups[0]!.children].every((child) => child.getAttribute("data-slot") === "button"),
    ).toBe(true);
    fireEvent.click(reset);
    expect(events).toEqual(["reset"]);
});

test("commit mode has no «По умолчанию» without a handler", async () => {
    renderSelect({ defaultOpen: true, closeOnUnhover: false });
    await waitFor(() => expect(screen.getByRole("option", { name: "Альфа" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: /По умолчанию/ })).toBeNull();
});

test("unhover closes a select by default", async () => {
    renderSelect({ defaultOpen: true });
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(true));
    fireEvent.mouseMove(document.body, { clientX: 900, clientY: 900 });
    await waitFor(() => expect(hasOpenSmartSelect()).toBe(false));
});

test("outside pointer clicks close a persistent select", async () => {
    // Commit mode (no callbacks) still closes on an outside click.
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
