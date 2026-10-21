import { afterEach, expect, mock, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

let facetHandler: (
    column: string,
    params?: { offset?: number; limit?: number },
) => Promise<{
    column: string;
    values: { value: string; count: number }[];
    total: number;
    rowCount?: number;
    hasNulls: boolean;
    nullCount?: number;
}>;

mock.module("@/shared/api/renovation", () => ({
    getRenovationPlanFacets: (column: string, params?: { offset?: number; limit?: number }) => {
        if (facetHandler) return facetHandler(column, params);
        if (column === "col_002") {
            return Promise.resolve({
                column,
                // The two-bucket ID filter: no value list, no «Пустые».
                values: [
                    { value: "Корректные", count: 10 },
                    { value: "Некорректные", count: 2 },
                ],
                total: 2,
                hasNulls: false,
                rowCount: 12,
            });
        }
        return Promise.resolve({
            column,
            values: [
                { value: "Насос", count: 3 },
                { value: "Шкаф", count: 2 },
            ],
            total: 2,
            hasNulls: true,
            rowCount: 17,
        });
    },
}));

import type { RenovationColumnFilter } from "@/shared/api/renovation";

const { ColumnFilterPopover } = await import("../column-filter-popover");

afterEach(() => {
    cleanup();
    facetHandler = undefined as never;
});

const anchor = { left: 0, top: 0, width: 100 } as DOMRect;

async function openFilter(column = "col_010", firstLabel = "Насос") {
    const view = render(
        <ColumnFilterPopover
            column={column}
            columnLabel={column}
            anchor={anchor}
            initial={null}
            onApply={() => {}}
            onClose={() => {}}
        />,
    );
    await waitFor(() =>
        expect(screen.getByRole("option", { name: new RegExp(`^${firstLabel}`) })).toBeTruthy(),
    );
    return view;
}

function master() {
    return screen.getByRole("option", { name: /Выбрать все/ });
}

test("separator follows «Выбрать все» before «Пустые» and the values", async () => {
    await openFilter();
    const separator = document.querySelector(".smart-select__separator");
    expect(separator).toBeTruthy();
    const nullOption = screen.getByRole("option", { name: /^Пустые/ });
    const firstValue = screen.getByRole("option", { name: /^Насос/ });
    expect(
        master().compareDocumentPosition(separator!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
        separator!.compareDocumentPosition(nullOption) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
        nullOption.compareDocumentPosition(firstValue) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(master().textContent).toContain("17 (2 уникальных)");
});

async function openRecorded(
    initial: { column?: string; values?: string[]; includeNulls?: boolean } | null,
    defaultFilter: RenovationColumnFilter | null = null,
) {
    const applied: unknown[] = [];
    let closes = 0;
    render(
        <ColumnFilterPopover
            column="col_010"
            columnLabel="col_010"
            anchor={anchor}
            initial={
                initial && {
                    column: "col_010",
                    values: initial.values ?? [],
                    includeNulls: initial.includeNulls ?? true,
                }
            }
            defaultFilter={defaultFilter}
            onApply={(filter) => applied.push(filter)}
            onClose={() => {
                closes += 1;
            }}
        />,
    );
    await waitFor(() => expect(screen.getByRole("option", { name: /^Насос/ })).toBeTruthy());
    return { applied, closes: () => closes };
}

test("«Применить» hands the pending selection to the owner", async () => {
    const { applied, closes } = await openRecorded(null);
    // «Выбрать все» is on by default, so a filter that still keeps every value
    // carries no constraint at all. Unchecking «Насос» makes the selection
    // explicit: the master stays checked, but the request names the values.
    // (The click keeps the master «on» only after its own toggle pass, so the
    // master is unchecked first to get a positive list below.)
    fireEvent.click(screen.getByRole("option", { name: /^Насос/ }));
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
            "false",
        ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied[0]).toEqual({ column: "col_010", values: ["Шкаф"], includeNulls: true });
});

test("«Отменить» restores the filter that was in force before opening", async () => {
    const initial = { values: ["Насос"], includeNulls: false };
    const { applied, closes } = await openRecorded(initial);
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([{ column: "col_010", values: ["Насос"], includeNulls: false }]);
});

test("Escape cancels the pending filter", async () => {
    const { applied, closes } = await openRecorded({ values: ["Насос"], includeNulls: false });
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([{ column: "col_010", values: ["Насос"], includeNulls: false }]);
});

test("a click outside the filter also cancels it", async () => {
    const { applied, closes } = await openRecorded(null);
    const outside = document.querySelector('[data-testid="underlay"]')!;
    fireEvent.mouseDown(outside, { button: 0 });
    await waitFor(() => expect(closes()).toBe(1));
    // The filter was «everything» before, so «Отменить» keeps no constraint.
    expect(applied).toEqual([null]);
});

test("«По умолчанию» resets the column to the given default, not to the opening value", async () => {
    const { applied, closes } = await openRecorded({ values: ["Насос"], includeNulls: false });
    fireEvent.click(screen.getByRole("button", { name: "По умолчанию: col_010" }));
    await waitFor(() => expect(closes()).toBe(1));
    // The workspace default is "no constraint" — unlike «Отменить», which keeps
    // the filter that was in force before opening.
    expect(applied).toEqual([null]);
});

test("Enter applies the pending filter", async () => {
    const { applied, closes } = await openRecorded({ values: ["Насос"], includeNulls: false });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([{ column: "col_010", values: ["Насос"], includeNulls: false }]);
});

test("the commit footer is pinned after the scrolling list", async () => {
    await openFilter();
    const footer = document.querySelector(".smart-select__footer");
    const list = document.querySelector(".column-filter__list");
    expect(footer).toBeTruthy();
    expect(list!.compareDocumentPosition(footer!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Применить" })).toBeTruthy();
});

test("column filter stays open when the pointer leaves it", async () => {
    await openFilter();
    fireEvent.mouseMove(document.body, { clientX: 900, clientY: 900 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.getByRole("option", { name: /^Насос/ })).toBeTruthy();
});

test.each([undefined, Number.NaN])(
    "missing or invalid nullCount %s never renders NaN across pages",
    async (nullCount) => {
        const requested: number[] = [];
        facetHandler = (column, params = {}) => {
            const offset = params.offset ?? 0;
            requested.push(offset);
            return Promise.resolve({
                column,
                values: offset < 2 ? [{ value: offset === 0 ? "Насос" : "Шкаф", count: 2 }] : [],
                total: 2,
                hasNulls: true,
                ...(nullCount !== undefined && { nullCount }),
            });
        };
        await openFilter();
        await waitFor(() => expect(requested).toContain(1));
        await waitFor(() => expect(screen.getByRole("option", { name: /^Шкаф/ })).toBeTruthy());
        const empty = screen.getByRole("option", { name: "Пустые" });
        expect(empty.querySelector(".text-muted")).toBeNull();
        expect(screen.queryByText("NaN")).toBeNull();
    },
);

test("a valid empty count survives a legacy response on the next page", async () => {
    facetHandler = (column, params = {}) =>
        Promise.resolve({
            column,
            values: [{ value: params.offset ? "Шкаф" : "Насос", count: 2 }],
            total: 2,
            hasNulls: true,
            ...(!params.offset && { nullCount: 12 }),
        });
    await openFilter();
    await waitFor(() => expect(screen.getByRole("option", { name: /^Шкаф/ })).toBeTruthy());
    expect(screen.getByRole("option", { name: "Пустые 12" })).toBeTruthy();
    expect(screen.queryByText("NaN")).toBeNull();
});

test("unchecking the last option unchecks «Выбрать все»", async () => {
    await openFilter();
    expect(master().getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("option", { name: /^Насос/ }));
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.click(screen.getByRole("option", { name: "Пустые" }));

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
});

test("unchecking «Выбрать все» clears every option, «Пустые» included", async () => {
    await openFilter();
    expect(master().getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "true",
    );

    fireEvent.click(master());

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "false",
    );
    expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
        "false",
    );
    expect(screen.getByRole("option", { name: /^Шкаф/ }).getAttribute("aria-selected")).toBe(
        "false",
    );
});

test("ID column: unchecking both buckets unchecks «Выбрать все»", async () => {
    await openFilter("col_002", "Корректные");
    expect(master().getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("option", { name: /^Корректные/ }));
    fireEvent.click(screen.getByRole("option", { name: /^Некорректные/ }));

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
});

test("ID column: «Выбрать все» shows rows without unique-count suffix", async () => {
    await openFilter("col_002", "Корректные");
    expect(master().textContent).toBe("Выбрать все12");
    expect(master().textContent).not.toContain("уникальных");
});

test("ID column: checking both buckets back checks «Выбрать все»", async () => {
    await openFilter("col_002", "Корректные");
    const correct = () => screen.getByRole("option", { name: /^Корректные/ });
    const incorrect = () => screen.getByRole("option", { name: /^Некорректные/ });

    fireEvent.click(correct());
    fireEvent.click(incorrect());
    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));

    fireEvent.click(correct());
    fireEvent.click(incorrect());
    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("true"));
});

test("paged column: no «Загрузка…» caption, and new windows arrive checked", async () => {
    // A column with more values than one window, served with the real
    // offset/limit contract: the popover must NOT print a progress caption and
    // every value a later window brings has to render as CHECKED (the master
    // «Выбрать все» is on, so the whole column is kept).
    const all = Array.from(
        { length: 500 },
        (_, index) => `Значение ${String(index).padStart(3, "0")}`,
    );
    const requested: number[] = [];
    facetHandler = (column, params = {}) => {
        const offset = params.offset ?? 0;
        requested.push(offset);
        return Promise.resolve({
            column,
            values: all
                .slice(offset, offset + (params.limit ?? 200))
                .map((value) => ({ value, count: 1 })),
            total: all.length,
            hasNulls: false,
        });
    };

    await openFilter("col_021", "Значение 000");
    // The old implementation printed «Загрузка…» under the list and never
    // cleared it (a missed setLoadingMore(false) in the tail fetch), and the
    // caption is gone for good now.
    expect(document.querySelector(".column-filter__status")).toBeNull();
    expect(screen.queryByText(/Загрузка/)).toBeNull();

    // Windows keep arriving while the list fits (jsdom reports zero heights, so
    // the short-list effect pulls every page), and each tail value is checked.
    await waitFor(() => expect(requested.length).toBeGreaterThan(1), { timeout: 3000 });
    await waitFor(
        () => expect(screen.getByRole("option", { name: /^Значение 499/ })).toBeTruthy(),
        {
            timeout: 3000,
        },
    );

    expect(
        screen.getByRole("option", { name: /^Значение 200/ }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(
        screen.getByRole("option", { name: /^Значение 499/ }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(master().getAttribute("aria-selected")).toBe("true");
    // Every window was requested once, in order, and never re-fetched.
    expect(requested).toEqual([...new Set(requested)]);
    expect(screen.queryByText(/Загрузка/)).toBeNull();
    facetHandler = undefined as never;
});
