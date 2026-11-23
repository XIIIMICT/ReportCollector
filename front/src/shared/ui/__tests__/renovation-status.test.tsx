import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { renovationRowStats } from "@/features/renovation/renovation-stats";

import { FilterSection, FormStatus, SortSection } from "../form-status";

afterEach(cleanup);

test.each([
    { found: 12, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 0, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 7, total: 100, asset: false, columns: new Set(["col_011"]), filtered: true },
    { found: 100, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 100, total: 100, asset: false, columns: undefined, filtered: false },
])("plan status separates found from total (%j)", ({ found, total, asset, columns, filtered }) => {
    const rows = renovationRowStats(found, total, asset, columns);
    expect(rows).toEqual({
        filteredRows: found,
        totalRows: total,
        filtersApplied: filtered,
        filteredColumns: [],
    });
    const view = render(
        <FormStatus
            room={null}
            selectionLabel={null}
            gridStats={{
                ...rows,
                sortedColumns: [],
                totalCols: 217,
                immutableCols: 0,
                calculatedCols: 0,
                editableCols: 217,
            }}
        />,
    );
    const inline = view.container.querySelector(".form-status__rows");
    if (filtered) expect(inline?.textContent).toBe(`Строки: ${found} найдено, ${total} всего`);
    else expect(inline).toBeTruthy();
});

test("the filter section renders the header chains it is given", () => {
    // The section is a pure render of `gridStats.filteredColumns` (group first,
    // leaf last — the chains the grid reports). The tooltip itself mounts its
    // content on hover only, so the markup is checked through the shared helper
    // the tooltip uses.
    const view = render(
        <FilterSection chains={[["Техническая дирекция", "Ремонтный код"], ["Наименование"]]} />,
    );
    const items = [...view.container.querySelectorAll(".form-status-detail__filters-item")].map(
        (node) => node.textContent,
    );
    expect(items).toEqual(["Техническая дирекция → Ремонтный код", "Наименование"]);
    expect(view.container.querySelector(".form-status-detail__filters")?.textContent).toContain(
        "Фильтры:",
    );
});

test("no applied filters -> no filter section", () => {
    const view = render(<FilterSection chains={[]} />);
    expect(view.container.querySelector(".form-status-detail__filters")).toBeNull();
});

test("the sorting reaches the tooltip content through the shared section", () => {
    // The status tooltip mounts its content only while open, so the grid's
    // payload is checked through the section it feeds — the same one
    // `FormStatus` renders with `gridStats.sortedColumns`.
    const view = render(<SortSection entries={[{ path: ["Наименование"], order: "desc" }]} />);
    const items = [...view.container.querySelectorAll(".form-status-detail__filters-item")].map(
        (node) => node.textContent,
    );
    expect(items).toEqual(["Наименование ↓"]);
});

test("the status lists the active sorting with its direction", () => {
    const view = render(
        <SortSection
            entries={[
                { path: ["Техническая дирекция", "Код работы"], order: "asc" },
                { path: ["Наименование"], order: "desc" },
            ]}
        />,
    );
    const items = [...view.container.querySelectorAll(".form-status-detail__filters-item")].map(
        (node) => node.textContent,
    );
    expect(items).toEqual(["Техническая дирекция → Код работы ↑", "Наименование ↓"]);
    expect(view.container.textContent).toContain("Сортировка:");
});

test("an unsorted table renders no sorting section", () => {
    const view = render(<SortSection entries={[]} />);
    expect(view.container.querySelector(".form-status-detail__filters")).toBeNull();
});
