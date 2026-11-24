import { afterEach, expect, test } from "bun:test";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ColumnFilter, FacetSource, FacetWindow } from "../column-filter-facets";
import type { FacetSearch } from "../column-filter-facets";

/**
 * The popover is generic: it is handed a `FacetSource` and nothing else — no
 * project API, no project columns — so the tests drive it through the very seam
 * every table uses.
 */
let facetHandler:
    | ((
          column: string,
          params?: { offset?: number; limit?: number; search?: FacetSearch | null | undefined },
      ) => Promise<FacetWindow>)
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

async function openFilter(column = "technical_location_name", firstLabel = "Насос") {
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
 * The master row. It is an ACTION whose caption says what a press will do:
 * «Снять выбор» (everything kept), «Выбрать найденные» (a search narrows the
 * list) or «Выбрать все» (something was taken away).
 */
function master() {
    return screen.getByRole("option", { name: /Снять выбор|Выбрать все|Выбрать найденные/ });
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

test("date field keeps its facet query deferred while segments are edited", async () => {
    const searches: Array<FacetSearch | null | undefined> = [];
    facetHandler = (_column, params = {}) => {
        searches.push(params.search);
        return Promise.resolve({
            values: [{ value: "01.01.2024", count: 1 }],
            total: 20,
            hasNulls: false,
            rowCount: 20,
        });
    };
    render(
        <ColumnFilterPopover
            column="date"
            columnLabel="Дата"
            anchor={anchor}
            facets={defaultFacets}
            initial={null}
            order="date"
            dateOnly
            onApply={() => {}}
            onClose={() => {}}
        />,
    );
    await waitFor(() => expect(document.querySelector(".date-time-picker__field")).toBeTruthy());
    const day = document.querySelector('.column-filter__popover [data-type="day"]') as HTMLElement;
    const month = document.querySelector(
        '.column-filter__popover [data-type="month"]',
    ) as HTMLElement;
    const year = document.querySelector(
        '.column-filter__popover [data-type="year"]',
    ) as HTMLElement;
    fireEvent.click(day);
    fireEvent.focus(day);
    fireEvent.keyDown(day, { key: "1" });
    fireEvent.keyDown(day, { key: "5" });
    fireEvent.keyDown(month, { key: "2" });
    fireEvent.keyDown(month, { key: "0" });
    expect(searches.every((search) => search === null)).toBe(true);
    // The segmented editor itself owns the partial value. The list loader must
    // remain on the unconstrained query until the field is actually complete.
    fireEvent.blur(year, { relatedTarget: document.body });
    await waitFor(() => expect(searches.length).toBeGreaterThan(1));
});

async function openRecorded(
    initial: { column?: string; values?: string[]; includeNulls?: boolean } | null,
    defaultFilter: ColumnFilter | null = null,
) {
    const applied: unknown[] = [];
    let closes = 0;
    render(
        <ColumnFilterPopover
            column="technical_location_name"
            columnLabel="technical_location_name"
            anchor={anchor}
            facets={defaultFacets}
            initial={
                initial && {
                    column: "technical_location_name",
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
    expect(applied[0]).toEqual({
        column: "technical_location_name",
        values: ["Шкаф"],
        includeNulls: true,
    });
});

test("«Отменить» restores the filter that was in force before opening", async () => {
    const initial = { values: ["Насос"], includeNulls: false };
    const { applied, closes } = await openRecorded(initial);
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([
        { column: "technical_location_name", values: ["Насос"], includeNulls: false },
    ]);
});

test("Escape cancels the pending filter", async () => {
    const { applied, closes } = await openRecorded({ values: ["Насос"], includeNulls: false });
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([
        { column: "technical_location_name", values: ["Насос"], includeNulls: false },
    ]);
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
    fireEvent.click(screen.getByRole("button", { name: "По умолчанию: technical_location_name" }));
    await waitFor(() => expect(closes()).toBe(1));
    // The workspace default is "no constraint" — unlike «Отменить», which keeps
    // the filter that was in force before opening.
    expect(applied).toEqual([null]);
});

test("Enter applies the pending filter", async () => {
    const { applied, closes } = await openRecorded({ values: ["Насос"], includeNulls: false });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await waitFor(() => expect(closes()).toBe(1));
    expect(applied).toEqual([
        { column: "technical_location_name", values: ["Насос"], includeNulls: false },
    ]);
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

test("the master toggle renames itself between «Снять выбор» and «Выбрать все»", async () => {
    // It reads as an ACTION, not a checkbox: while everything is kept it offers
    // to take the selection away, and once nothing is kept it offers to put it
    // back. Clicking it twice returns to the starting caption.
    await openFilter();
    expect(masterCaption()).toContain("Снять выбор");

    fireEvent.click(master());
    await waitFor(() => expect(masterCaption()).toContain("Выбрать все"));

    fireEvent.click(master());
    await waitFor(() => expect(masterCaption()).toContain("Снять выбор"));
});

test("a search turns the master into «Выбрать найденные»", async () => {
    await openFilter();
    const search = document.querySelector<HTMLInputElement>('input[name="column-filter-search"]');

    expect(masterCaption()).toContain("Снять выбор");

    // «Something is taken away» is the state «Выбрать найденные» answers: with
    // everything still kept the caption keeps offering «Снять выбор».
    fireEvent.click(screen.getByRole("option", { name: /^Шкаф/ }));
    fireEvent.click(screen.getByRole("option", { name: "Пустые" }));
    await waitFor(() => expect(masterCaption()).toContain("Выбрать все"));

    fireEvent.change(search!, { target: { value: "Нас" } });
    await waitFor(() => expect(masterCaption()).toContain("Выбрать найденные"));

    // The press keeps what the search found, and the caption says so from then
    // on — the selection IS exactly the found set.
    fireEvent.click(master());
    await waitFor(() => expect(masterCaption()).toContain("Снять выбор"));
    expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
        "true",
    );
});

test("«Снять выбор» acts on the FIRST press after «Выбрать найденные»", async () => {
    // react-aria does not hand the master's own key back reliably: after the
    // found set is applied it reports "the visible values are selected, the
    // master is not" — the very shape it uses for an individual value. Reading
    // that as "a value was checked" made the first press on «Снять выбор» a
    // no-op and forced a second one.
    await openFilter();
    const search = document.querySelector<HTMLInputElement>('input[name="column-filter-search"]');

    fireEvent.click(screen.getByRole("option", { name: "Пустые" }));
    await waitFor(() => expect(masterCaption()).toContain("Выбрать все"));

    fireEvent.change(search!, { target: { value: "Нас" } });
    await waitFor(() => expect(masterCaption()).toContain("Выбрать найденные"));

    fireEvent.click(master());
    await waitFor(() => expect(masterCaption()).toContain("Снять выбор"));

    // ONE press must clear the found selection.
    fireEvent.click(master());
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
            "false",
        ),
    );

    // And the next press is «Выбрать найденные» — it brings them back.
    await waitFor(() => expect(masterCaption()).toContain("Выбрать найденные"));
    fireEvent.click(master());
    await waitFor(() =>
        expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
            "true",
        ),
    );
});

test("unchecking «Выбрать все»'s counterpart clears every option, «Пустые» included", async () => {
    await openFilter();
    expect(masterCaption()).toContain("Снять выбор");
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "true",
    );

    // «Снять выбор» — the master while everything is kept.
    fireEvent.click(master());

    await waitFor(() => expect(masterCaption()).toContain("Выбрать все"));
    expect(screen.getByRole("option", { name: "Пустые" }).getAttribute("aria-selected")).toBe(
        "false",
    );
    expect(screen.getByRole("option", { name: /^Насос/ }).getAttribute("aria-selected")).toBe(
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
    expect(masterCaption()).toBe("Снять выбор12");
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

async function openCodes(initial: ColumnFilter | null = null) {
    const applied: Array<ColumnFilter | null> = [];
    const presets = [
        { value: "Корректные", count: 2 },
        { value: "Некорректные", count: 1 },
    ];
    const codes = ["2027_A", "2027_B", "Корректные"];
    const source: FacetSource = async (_column, { offset, limit, search }) => {
        const matching = codes.filter(
            (value) => search?.kind !== "text" || value.includes(search.query),
        );
        return {
            values: matching.slice(offset, offset + limit).map((value) => ({ value, count: 1 })),
            presets,
            total: matching.length,
            totalWithoutSearch: codes.length,
            rowCount: matching.length,
            hasNulls: false,
        };
    };
    render(
        <ColumnFilterPopover
            column="work_or_repair_code"
            columnLabel="Код"
            anchor={anchor}
            facets={source}
            initial={initial}
            forceSearchable
            onApply={(filter) => applied.push(filter)}
            onClose={() => {}}
        />,
    );
    await waitFor(() => expect(screen.getByRole("option", { name: /^2027_A/ })).toBeTruthy());
    return applied;
}

test("code search keeps group presets first and applies exact found codes only", async () => {
    const applied = await openCodes();
    const labels = screen
        .getAllByRole("option")
        .map((node) => node.querySelector("span")?.textContent);
    expect(labels.slice(1, 5)).toEqual(["Корректные", "Некорректные", "2027_A", "2027_B"]);
    const search = screen.getByRole("searchbox", { name: "Поиск: Код" });
    fireEvent.change(search, { target: { value: "2027_B" } });
    await waitFor(() => expect(screen.queryByRole("option", { name: /^2027_A/ })).toBeNull());
    expect(screen.getByRole("option", { name: /^Некорректные/ })).toBeTruthy();
    fireEvent.click(master());
    fireEvent.click(master());
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(applied[0]).toEqual({
        column: "work_or_repair_code",
        values: ["2027_B"],
        includeNulls: false,
        valueMode: "exact",
    });
});

test("group preset replaces code selection without selecting the visible code page", async () => {
    const applied = await openCodes();
    fireEvent.click(screen.getByRole("option", { name: /^Некорректные/ }));
    expect(screen.getByRole("option", { name: /^2027_A/ }).getAttribute("aria-selected")).toBe(
        "false",
    );
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(applied[0]).toEqual({
        column: "work_or_repair_code",
        values: ["Некорректные"],
        includeNulls: false,
    });
});

test("saved group can switch to a literal code even when its label matches a preset", async () => {
    const applied = await openCodes({
        column: "work_or_repair_code",
        values: ["Некорректные"],
        includeNulls: false,
    });
    const literal = screen.getAllByRole("option", { name: /^Корректные/ }).at(-1)!;
    fireEvent.click(literal);
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(applied[0]).toEqual({
        column: "work_or_repair_code",
        values: ["Корректные"],
        includeNulls: false,
        valueMode: "exact",
    });
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

    await openFilter("work_executor_name", "Значение 000");
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

test("a late response from an older search cannot append to the current list", async () => {
    let resolveOld!: (value: FacetWindow) => void;
    let resolveNew!: (value: FacetWindow) => void;
    const oldPage = new Promise<FacetWindow>((resolve) => {
        resolveOld = resolve;
    });
    const newPage = new Promise<FacetWindow>((resolve) => {
        resolveNew = resolve;
    });
    facetHandler = (_column, params = {}) => {
        const search = params.search;
        if (search?.kind === "text" && search.query === "old") return oldPage;
        if (search?.kind === "text" && search.query === "new") return newPage;
        return Promise.resolve({
            values: Array.from({ length: 12 }, (_, index) => ({
                value: `base-${index}`,
                count: 1,
            })),
            total: 12,
            hasNulls: false,
        });
    };
    await openFilter("work_description", "base-0");
    const search = document.querySelector<HTMLInputElement>('input[name="column-filter-search"]')!;
    fireEvent.change(search, { target: { value: "old" } });
    fireEvent.change(search, { target: { value: "new" } });
    resolveOld({ values: [{ value: "old-result", count: 1 }], total: 1, hasNulls: false });
    resolveNew({ values: [{ value: "new-result", count: 1 }], total: 1, hasNulls: false });
    await waitFor(() => expect(screen.getByRole("option", { name: /^new-result/ })).toBeTruthy());
    expect(screen.queryByRole("option", { name: /^old-result/ })).toBeNull();
});

test("an initial facet failure is visible and retryable", async () => {
    let failed = true;
    facetHandler = () => {
        if (failed) {
            failed = false;
            return Promise.reject(new Error("offline"));
        }
        return Promise.resolve({
            values: [{ value: "recovered", count: 1 }],
            total: 1,
            hasNulls: false,
        });
    };
    render(
        <ColumnFilterPopover
            column="defect_list_or_bill_of_quantities_number"
            columnLabel="defect_list_or_bill_of_quantities_number"
            anchor={anchor}
            facets={defaultFacets}
            initial={null}
            onApply={() => {}}
            onClose={() => {}}
        />,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(screen.getByRole("option", { name: /^recovered/ })).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
});
