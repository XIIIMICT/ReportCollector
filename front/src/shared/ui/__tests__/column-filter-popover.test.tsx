import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ColumnFilter, FacetSource, FacetWindow } from "../column-filter-facets";

/**
 * The popover is generic: it is handed a `FacetSource` and nothing else — no
 * project API, no project columns — so the tests drive it through the very seam
 * every table uses.
 */
let facetHandler:
    | ((column: string, params?: { offset?: number; limit?: number }) => Promise<FacetWindow>)
    | undefined;

const defaultFacets: FacetSource = (column, params) => {
    if (facetHandler) return facetHandler(column, params);
    return Promise.resolve({
        values: [
            { value: "Насос", count: 3 },
            { value: "Шкаф", count: 2 },
        ],
        total: 2,
        hasNulls: true,
        rowCount: 17,
    });
};

const { ColumnFilterPopover } = await import("../column-filter-popover");

afterEach(() => {
    cleanup();
    facetHandler = undefined;
});

const anchor = { left: 0, top: 0, width: 100 } as DOMRect;

async function openFilter(column = "col_010", firstLabel = "Насос") {
    const view = render(
        <ColumnFilterPopover
            column={column}
            columnLabel={column}
            anchor={anchor}
            facets={defaultFacets}
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

/**
 * The master toggle. It is TWO-STATE: «Снять выбор» while everything is kept,
 * «Выбрать все» once something was excluded — so the tests must not assume a
 * fixed caption.
 */
function master() {
    return screen.getByRole("option", { name: /Выбрать все|Выбрать найденные/ });
}

/** The same item, whichever caption it currently shows. */
function masterCaption() {
    return master().textContent ?? "";
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
    expect(masterCaption()).toContain("17 (2 уникальных)");
});

async function openRecorded(
    initial: { column?: string; values?: string[]; includeNulls?: boolean } | null,
    defaultFilter: ColumnFilter | null = null,
) {
    const applied: unknown[] = [];
    let closes = 0;
    render(
        <ColumnFilterPopover
            column="col_010"
            columnLabel="col_010"
            anchor={anchor}
            facets={defaultFacets}
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
    facetHandler = (_column, params = {}) =>
        Promise.resolve({
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

test("a query turns the master into «Выбрать найденные», and clearing it restores «Выбрать все»", async () => {
    // The caption follows the SEARCH, not the selection: «Выбрать найденные»
    // while a query narrows the list, «Выбрать все» otherwise. Each caption is
    // its own action, and the press is taken on `pointerdown` — before react-aria
    // moves the selection and reports a change that is already stale.
    await openFilter();
    const search = document.querySelector<HTMLInputElement>('input[name="column-filter-search"]');

    expect(masterCaption()).toContain("Выбрать все");

    fireEvent.change(search!, { target: { value: "Нас" } });
    await waitFor(() => expect(masterCaption()).toContain("Выбрать найденные"));

    // «Выбрать найденные» keeps exactly the matched values.
    fireEvent.pointerDown(master());
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
            "true",
        ),
    );

    // Clearing the query brings the whole column back under the caption.
    fireEvent.change(search!, { target: { value: "" } });
    await waitFor(() => expect(masterCaption()).toContain("Выбрать все"));
});

test("the master is an action: «Выбрать все» restores every option", async () => {
    // It reads as an ACTION, not a checkbox — the caption names what a press
    // does, and the press does exactly that. Unticking a value leaves the
    // caption on «Выбрать все» (that IS the way back), and pressing it keeps
    // every option again.
    await openFilter();
    expect(masterCaption()).toContain("Выбрать все");

    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Шкаф/ }).getAttribute("aria-selected")).toBe(
            "false",
        ),
    );
    expect(masterCaption()).toContain("Выбрать все");

    fireEvent.click(master());
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Шкаф/ }).getAttribute("aria-selected")).toBe(
            "true",
        ),
    );
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

/**
 * A two-bucket column (the plan's repair-code semantics: «Корректные» /
 * «Некорректные») needs no special case in the popover — it is just a source
 * that returns two values and no «Пустые» bucket. The one thing the shared
 * component can be asked for is hiding the unique-count suffix, since the row
 * count already says everything there.
 */
const idBuckets: FacetSource = () =>
    Promise.resolve({
        values: [
            { value: "Корректные", count: 10 },
            { value: "Некорректные", count: 2 },
        ],
        total: 2,
        hasNulls: false,
        rowCount: 12,
    });

async function openIdBuckets(showUniqueCount = true) {
    render(
        <ColumnFilterPopover
            column="repairCode"
            columnLabel="Идентификационный код"
            anchor={anchor}
            facets={idBuckets}
            initial={null}
            showUniqueCount={showUniqueCount}
            onApply={() => {}}
            onClose={() => {}}
        />,
    );
    await waitFor(() => expect(screen.getByRole("option", { name: /^Корректные/ })).toBeTruthy());
}

test("a two-bucket column: unchecking both buckets unchecks «Выбрать все»", async () => {
    await openIdBuckets();
    expect(master().getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByRole("option", { name: /^Корректные/ }));
    fireEvent.click(screen.getByRole("option", { name: /^Некорректные/ }));

    await waitFor(() => expect(master().getAttribute("aria-selected")).toBe("false"));
});

test("showUniqueCount={false} hides the unique-count suffix only", async () => {
    await openIdBuckets(false);
    expect(masterCaption()).toBe("Выбрать все12");
    expect(masterCaption()).not.toContain("уникальных");
});

test("a two-bucket column: checking both buckets back checks «Выбрать все»", async () => {
    await openIdBuckets();
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
