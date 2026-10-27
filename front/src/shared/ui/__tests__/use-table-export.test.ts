import { afterEach, expect, test } from "bun:test";

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import type { ExportColumn } from "@/shared/grid/grid-config";

import { useTableExport } from "../use-table-export";

afterEach(cleanup);

const COLUMNS: ExportColumn[] = [
    { key: "product", headerPath: ["Продукция"], width: 40, numberFormat: null },
];
// The grid reports the rows it SHOWS — already filtered and sorted by it.
const VISIBLE = ["row-c", "row-a"];

function gridRef() {
    return {
        current: {
            getExportState: async () => ({ columns: COLUMNS, rowIds: VISIBLE }),
        },
    };
}

/**
 * The guard for the bug this hook was extracted for: «Реализация» quietly
 * exported the WHOLE view in «видимая» mode, because its call site threw the
 * mode away. Every grid shares this one implementation, so the rule is tested
 * once and cannot silently drift again.
 */
test("«видимая» exports the grid's rows in the grid's own order", async () => {
    const seen: Array<{ columns: unknown; rowIds: string[] | null }> = [];
    const { result } = renderHook(() =>
        useTableExport({
            gridRef: gridRef(),
            fileName: (mode) => `${mode}.xlsx`,
            request: async (view) => {
                seen.push({ columns: view.columns, rowIds: view.rowIds });
                return new Blob();
            },
        }),
    );

    act(() => {
        result.current.exportTable("visible");
    });

    await waitFor(() => expect(seen).toHaveLength(1));
    // The exact set AND the exact order the grid reported — not re-sorted, not
    // re-read from the server.
    expect(seen[0]!.rowIds).toEqual(VISIBLE);
});

test("«вся таблица» sends no row ids, and never the visible ones", async () => {
    const seen: Array<string[] | null> = [];
    const { result } = renderHook(() =>
        useTableExport({
            gridRef: gridRef(),
            fileName: () => "all.xlsx",
            request: async (view) => {
                seen.push(view.rowIds);
                return new Blob();
            },
        }),
    );

    act(() => {
        result.current.exportTable("all");
    });

    await waitFor(() => expect(seen).toHaveLength(1));
    // `null`, NOT the visible list: the server falls back to the whole view.
    // An empty array would mean "no rows".
    expect(seen[0]).toBeNull();
});

test("the file name is built for the mode that was asked for", async () => {
    const names: string[] = [];
    const { result } = renderHook(() =>
        useTableExport({
            gridRef: gridRef(),
            fileName: (mode) => `table-${mode}.xlsx`,
            request: async (_view, fileName) => {
                names.push(fileName);
                return new Blob();
            },
        }),
    );

    act(() => {
        result.current.exportTable("visible");
    });
    await waitFor(() => expect(names).toHaveLength(1));

    act(() => {
        result.current.exportTable("all");
    });
    await waitFor(() => expect(names).toHaveLength(2));

    expect(names).toEqual(["table-visible.xlsx", "table-all.xlsx"]);
});

test("a request failure still releases the pending state", async () => {
    const { result } = renderHook(() =>
        useTableExport({
            gridRef: gridRef(),
            fileName: () => "t.xlsx",
            request: async () => {
                throw new Error("boom");
            },
        }),
    );

    act(() => {
        result.current.exportTable("visible");
    });

    // The error is surfaced by `runTableExport` (a toast); the hook's part of
    // the contract is that the button does not stay stuck in "exporting".
    await waitFor(() => expect(result.current.pending).toBe(false));
});
