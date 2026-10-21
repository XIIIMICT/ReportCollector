import { afterEach, expect, test } from "bun:test";

import { cleanup, render } from "@testing-library/react";

import { renovationRowStats } from "@/features/renovation/renovation-stats";

import { FormStatus } from "../form-status";

afterEach(cleanup);

test.each([
    { found: 12, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 0, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 7, total: 100, asset: false, columns: new Set(["col_011"]), filtered: true },
    { found: 100, total: 100, asset: true, columns: new Set<string>(), filtered: true },
    { found: 100, total: 100, asset: false, columns: undefined, filtered: false },
])("plan status separates found from total (%j)", ({ found, total, asset, columns, filtered }) => {
    const rows = renovationRowStats(found, total, asset, columns);
    expect(rows).toEqual({ filteredRows: found, totalRows: total, filtersApplied: filtered });
    const view = render(
        <FormStatus
            room={null}
            selectionLabel={null}
            gridStats={{
                ...rows,
                totalCols: 216,
                immutableCols: 0,
                calculatedCols: 0,
                editableCols: 216,
            }}
        />,
    );
    const inline = view.container.querySelector(".form-status__rows");
    if (filtered) expect(inline?.textContent).toBe(`Строки: ${found} найдено, ${total} всего`);
    else expect(inline).toBeTruthy();
});
