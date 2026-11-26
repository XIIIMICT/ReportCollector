import { describe, expect, test } from "bun:test";

import { buildGridResetState, type GridResetState } from "../grid-reset-state";

type FakeGrid = {
    resetColumnWidths: () => void;
    resetFiltersSorting: () => Promise<void>;
};

const fakeGrid = () => {
    const calls: string[] = [];
    const grid: FakeGrid = {
        resetColumnWidths: () => calls.push("widths"),
        resetFiltersSorting: async () => {
            calls.push("filters");
        },
    };
    return { grid, calls };
};

describe("buildGridResetState", () => {
    test("width reset goes only to the mounted grid", () => {
        const { grid, calls } = fakeGrid();
        const ref: { current: FakeGrid | null } = { current: grid };
        const clears: string[] = [];
        const state: GridResetState = buildGridResetState(ref, () => clears.push("clear"));

        state.resetColumnWidths();
        expect(calls).toEqual(["widths"]);
        expect(clears).toEqual([]);
    });

    test("filter/sort reset clears the section filters first, then the grid", () => {
        const { grid, calls } = fakeGrid();
        const ref: { current: FakeGrid | null } = { current: grid };
        const order: string[] = [];
        const state = buildGridResetState(ref, () => order.push("clear"));

        state.resetFiltersSorting();
        // The grid call is fire-and-forget; its synchronous prefix still ran.
        expect(order).toEqual(["clear"]);
        expect(calls).toEqual(["filters"]);
    });

    test("a missing grid is a no-op, but filters are still cleared", () => {
        const ref: { current: FakeGrid | null } = { current: null };
        let cleared = 0;
        const state = buildGridResetState(ref, () => {
            cleared += 1;
        });

        expect(() => state.resetColumnWidths()).not.toThrow();
        expect(() => state.resetFiltersSorting()).not.toThrow();
        expect(cleared).toBe(1);
    });

    test("the handlers follow the grid the ref points at when invoked", () => {
        const first = fakeGrid();
        const second = fakeGrid();
        const ref: { current: FakeGrid | null } = { current: first.grid };
        const state = buildGridResetState(ref, () => {});

        state.resetColumnWidths();
        ref.current = second.grid;
        state.resetColumnWidths();

        expect(first.calls).toEqual(["widths"]);
        expect(second.calls).toEqual(["widths"]);
    });

    test("each builder call captures its own clearFilters", () => {
        const ref: { current: FakeGrid | null } = { current: null };
        let firstClears = 0;
        let secondClears = 0;
        const first = buildGridResetState(ref, () => {
            firstClears += 1;
        });
        const second = buildGridResetState(ref, () => {
            secondClears += 1;
        });

        first.resetFiltersSorting();
        second.resetFiltersSorting();
        expect([firstClears, secondClears]).toEqual([1, 1]);
    });
});
