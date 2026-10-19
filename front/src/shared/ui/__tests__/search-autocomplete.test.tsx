import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SearchAutocomplete } from "../search-autocomplete";

afterEach(cleanup);

const BLOCKS = [
    { id: "tech", name: "1) Техническая дирекция" },
    { id: "ksir", name: "2) Дирекция КСиР" },
    { id: "mto", name: "3) Дирекция МТО" },
];

function renderFilter(props: Partial<Parameters<typeof SearchAutocomplete>[0]> = {}) {
    const picked: (string | null)[] = [];
    render(
        <SearchAutocomplete
            placeholder="Все блоки"
            items={BLOCKS}
            selectedKey={null}
            onSelectionChange={(key) => picked.push(key)}
            {...props}
        />,
    );
    return picked;
}

test("the all option is a pinned row above the values, separated by a rule", async () => {
    renderFilter({ allOption: { label: "Все блоки", present: true } });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);

    // «Все блоки» is NOT part of the values list: it renders above the rule.
    await waitFor(() => expect(screen.getByRole("option", { name: "Все блоки" })).toBeTruthy());
    expect(document.querySelector(".smart-select__separator")).toBeTruthy();
    expect(screen.getByRole("option", { name: "1) Техническая дирекция" })).toBeTruthy();
});

test("picking the all option reports null (no constraint)", async () => {
    const picked = renderFilter({
        allOption: { label: "Все блоки", present: true },
        selectedKey: "tech",
    });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);
    fireEvent.click(await screen.findByRole("option", { name: "Все блоки" }));
    await waitFor(() => expect(picked).toContain(null));
});

test("without an all option there is no stray rule", async () => {
    renderFilter({ selectedKey: "tech" });
    const trigger = document.querySelector('[data-slot="select"]');
    fireEvent.mouseEnter(trigger!.parentElement ?? trigger!);
    await waitFor(() =>
        expect(screen.getByRole("option", { name: "1) Техническая дирекция" })).toBeTruthy(),
    );
    expect(document.querySelector(".smart-select__separator")).toBeNull();
});
