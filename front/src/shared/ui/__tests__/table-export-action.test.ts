import { expect, test } from "bun:test";

import { runTableExport } from "../table-export-action";

test("pending and unmounted grids do not start export requests", async () => {
    let requests = 0;
    const states: boolean[] = [];
    const args = {
        pending: true,
        gridRef: { current: null },
        setPending: (next: boolean) => {
            states.push(next);
        },
        fileName: () => "table.xlsx",
        request: async () => {
            requests++;
            return new Blob();
        },
    };
    await runTableExport(args);
    expect(states).toEqual([]);
    await runTableExport({ ...args, pending: false });
    expect(states).toEqual([true, false]);
    expect(requests).toBe(0);
});

test("grid read failures release pending state", async () => {
    const states: boolean[] = [];
    await runTableExport({
        pending: false,
        gridRef: {
            current: {
                getExportState: async () => {
                    throw new Error("grid read failed");
                },
            },
        },
        setPending: (next) => {
            states.push(next);
        },
        fileName: () => "table.xlsx",
        request: async () => new Blob(),
    });
    expect(states).toEqual([true, false]);
});
