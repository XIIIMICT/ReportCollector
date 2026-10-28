import { afterEach, expect, test } from "bun:test";

import type { RangeArea } from "@revolist/react-datagrid";

import {
    createGridEpoch,
    createRevoGridAdapter,
    missingRangeIndexes,
    type GridRef,
} from "../revo-grid-adapter";

const mounted: HTMLElement[] = [];

function makeGrid(overrides: Record<string, unknown> = {}) {
    const grid = document.createElement("revo-grid") as unknown as HTMLRevoGridElement;
    Object.assign(grid, {
        getVisibleSource: async () => [{ id: "visible" }],
        getSource: async () => [{ id: "source" }],
        getSelectedRange: async () => null,
        setCellsFocus: async () => {},
        clearFocus: async () => {},
        refresh: async () => {},
        scrollToCoordinate: async () => {},
        ...overrides,
    });
    document.body.appendChild(grid);
    mounted.push(grid);
    return grid;
}

function refOf(grid: HTMLRevoGridElement | null): GridRef {
    return { current: grid };
}

afterEach(() => {
    for (const element of mounted.splice(0)) element.remove();
});

test("adapter commands keep selection refresh and private focus cleanup together", async () => {
    const calls: string[] = [];
    const grid = makeGrid({
        setCellsFocus: async (start: unknown, end: unknown) => {
            calls.push(`set:${JSON.stringify([start, end])}`);
        },
        clearFocus: async () => {
            calls.push("clear");
        },
        refresh: async () => {
            calls.push("refresh");
        },
    });
    (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore = {
        x: 2,
    };
    const adapter = createRevoGridAdapter(refOf(grid));

    await adapter.setRange({ x: 1, y: 2 }, { x: 3, y: 4 });
    await adapter.clearRange();

    expect(calls).toEqual(['set:[{"x":1,"y":2},{"x":3,"y":4}]', "refresh", "clear", "refresh"]);
    expect(
        (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore,
    ).toBe(undefined);
});

test("adapter reads visible/source rows and falls back after a disposed grid", async () => {
    const grid = makeGrid({
        getVisibleSource: async () => {
            throw new Error("disposed");
        },
        getSource: async () => {
            throw new Error("disposed");
        },
    });
    const adapter = createRevoGridAdapter<{ id: string }>(refOf(grid));

    await expect(adapter.getVisibleRows([{ id: "fallback" }])).resolves.toEqual([
        { id: "fallback" },
    ]);
    await expect(adapter.getSourceRows()).resolves.toEqual([]);
});

test("range edit dispatch is a composed event owned by the adapter", () => {
    const grid = makeGrid();
    let received: unknown;
    grid.addEventListener("rangeeditapply", (event) => {
        received = (event as CustomEvent).detail;
    });
    const adapter = createRevoGridAdapter(refOf(grid));
    const range = { x: 0, y: 0, x1: 0, y1: 0 } as RangeArea;
    const detail = { data: {}, models: [], type: "rgRow", oldRange: range, newRange: range };

    expect(adapter.dispatchRangeEdit(detail)).toBe(true);
    expect(received).toEqual(detail);
});

test("temporary drag range and shadow-dom target checks stay behind the adapter", () => {
    const grid = makeGrid();
    const temp = document.createElement("revogr-temp-range");
    let listener: ((value: RangeArea | null) => void) | undefined;
    const initial = { x: 4, y: 5, x1: 6, y1: 7 } as RangeArea;
    Object.assign(temp, {
        selectionStore: {
            onChange: (_key: string, callback: (value: RangeArea | null) => void) => {
                listener = callback;
                return () => {
                    listener = undefined;
                };
            },
            get: () => initial,
        },
    });
    grid.appendChild(temp);
    const child = document.createElement("button");
    grid.appendChild(child);
    const adapter = createRevoGridAdapter(refOf(grid));
    const ranges: Array<RangeArea | null> = [];
    const unsubscribe = adapter.subscribeTemporaryRange((range) => ranges.push(range));

    let event: Event | undefined;
    grid.addEventListener("click", (candidate) => {
        event = candidate;
    });
    const MouseEventConstructor = grid.ownerDocument.defaultView?.MouseEvent;
    child.dispatchEvent(
        new (MouseEventConstructor ?? Event)("click", { bubbles: true, composed: true }),
    );
    expect(adapter.isTarget(event as Event)).toBe(true);
    expect(ranges).toEqual([initial]);
    listener?.(null);
    expect(ranges).toEqual([initial, null]);
    unsubscribe();
    expect(listener).toBeUndefined();
});

test("semantic event subscriptions translate and tear down vendor event names", () => {
    const grid = makeGrid();
    const adapter = createRevoGridAdapter(refOf(grid));
    let calls = 0;
    const remove = adapter.on("setRange", () => {
        calls += 1;
    });
    const EventConstructor = grid.ownerDocument.defaultView?.Event ?? Event;
    grid.dispatchEvent(new EventConstructor("setrange"));
    grid.dispatchEvent(new EventConstructor("focuscell"));
    expect(calls).toBe(1);
    remove();
    grid.dispatchEvent(new EventConstructor("setrange"));
    expect(calls).toBe(1);
});

test("sparse range coverage reports every missing row", () => {
    expect(
        missingRangeIndexes(
            2,
            6,
            new Map([
                [2, {}],
                [4, {}],
                [6, {}],
            ]),
        ),
    ).toEqual([3, 5]);
});

test("grid epochs invalidate work from the previous scope", () => {
    const epoch = createGridEpoch();
    const first = epoch.next();
    expect(epoch.isCurrent(first)).toBe(true);
    const second = epoch.next();
    expect(epoch.isCurrent(first)).toBe(false);
    expect(epoch.isCurrent(second)).toBe(true);
    expect(epoch.current()).toBe(second);
});
