/* oxlint-disable no-await-in-loop -- each fake-clock tick must finish before the next one is scheduled. */

import { afterEach, beforeEach, expect, mock, spyOn, test } from "bun:test";

import { act, cleanup, renderHook } from "@testing-library/react";

import type { GridRow } from "../grid-config";
import type { GridRef, RevoGridAdapter } from "../revo-grid-adapter";
import { writeScrollPosition } from "../scroll-position";
import { useGridScrollRestore } from "../use-grid-scroll-restore";

const ROW_HEIGHT = 36;
const FORM = "form";
const SCOPE = "scope";

function row(id: string, extra: Partial<GridRow> = {}): GridRow {
    return { id, rowState: "unchanged", changedFields: [], isDeleted: false, ...extra };
}

type Timer = { id: number; at: number; fn: () => void; cleared: boolean };

// The hook uses `window.setTimeout`/`clearTimeout`, so a deterministic clock is
// installed ON THOSE (React's own scheduler keeps real timers and stays intact).
let timers: Timer[] = [];
let now = 0;
let nextId = 1;
let clockSpies: Array<{ mockRestore: () => void }> = [];

// happy-dom hands out a fresh localStorage wrapper on every access, so spying
// on one instance misses writes made through the next. Install one stable object.
let entries: Map<string, string>;
let setItem: ReturnType<typeof mock>;
let originalLocalStorage: PropertyDescriptor | undefined;

function installStorage(): void {
    originalLocalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    entries = new Map();
    setItem = mock((key: string, value: string) => {
        entries.set(key, value);
    });
    Object.defineProperty(window, "localStorage", {
        configurable: true,
        writable: true,
        value: {
            getItem: (key: string) => entries.get(key) ?? null,
            setItem,
            removeItem: (key: string) => entries.delete(key),
            key: (index: number) => [...entries.keys()][index] ?? null,
            get length() {
                return entries.size;
            },
        },
    });
}

function installClock(): void {
    timers = [];
    now = 0;
    nextId = 1;
    clockSpies = [
        spyOn(window, "setTimeout").mockImplementation(((handler: () => void, delay?: number) => {
            const id = nextId;
            nextId += 1;
            timers.push({ id, at: now + (delay ?? 0), fn: handler, cleared: false });
            return id;
        }) as typeof window.setTimeout),
        spyOn(window, "clearTimeout").mockImplementation(((id?: number) => {
            const timer = timers.find((entry) => entry.id === id);
            if (timer) timer.cleared = true;
        }) as typeof window.clearTimeout),
    ];
}

function pendingTimers(): Timer[] {
    return timers.filter((timer) => !timer.cleared);
}

async function flushMicrotasks(): Promise<void> {
    for (let index = 0; index < 6; index += 1) await Promise.resolve();
}

/** Advance the fake clock and run everything that became due. */
async function tick(ms: number): Promise<void> {
    await act(async () => {
        now += ms;
        const due = timers.filter((timer) => !timer.cleared && timer.at <= now);
        for (const timer of due) {
            timer.cleared = true;
            timer.fn();
        }
        await flushMicrotasks();
    });
}

function makeGrid(height = 0) {
    const grid = document.createElement("revo-grid") as unknown as HTMLRevoGridElement;
    let currentHeight = height;
    Object.defineProperty(grid, "clientHeight", {
        configurable: true,
        get: () => currentHeight,
    });
    const scroller = document.createElement("revogr-scroll-virtual");
    scroller.className = "vertical";
    grid.appendChild(scroller);
    const scrollToRow = mock(async (_index: number) => {});
    Object.assign(grid, { scrollToRow });
    document.body.appendChild(grid);
    return {
        grid,
        ref: { current: grid } as GridRef,
        scroller,
        scrollToRow,
        addSpy: spyOn(grid, "addEventListener"),
        removeSpy: spyOn(grid, "removeEventListener"),
        setHeight(value: number) {
            currentHeight = value;
        },
        scrollChange() {
            // happy-dom rejects an Event from another realm (Bun's global one).
            const EventConstructor = window.Event;
            grid.dispatchEvent(new EventConstructor("scrollchange"));
        },
        registrations() {
            return this.addSpy.mock.calls.filter((call) => call[0] === "scrollchange");
        },
        removals() {
            return this.removeSpy.mock.calls.filter((call) => call[0] === "scrollchange");
        },
    };
}

function makeAdapter(visible: GridRow[]): RevoGridAdapter<GridRow> {
    return {
        getVisibleRows: mock(async (_fallback?: GridRow[]) => visible),
    } as unknown as RevoGridAdapter<GridRow>;
}

function renderScrollRestore(
    gridRef: GridRef,
    gridAdapter: RevoGridAdapter<GridRow>,
    rows: GridRow[],
    scrollFormKey: string | null,
    scrollScope: string,
) {
    return renderHook(
        (props: { form: string | null; scope: string }) =>
            useGridScrollRestore({
                gridRef,
                gridAdapter,
                rows,
                rowHeight: ROW_HEIGHT,
                scrollFormKey: props.form,
                scrollScope: props.scope,
            }),
        { initialProps: { form: scrollFormKey, scope: scrollScope } },
    );
}

function storageKey(scope: string): string {
    return `reportcollector:scroll-position:${FORM}:${scope}`;
}

beforeEach(() => {
    installStorage();
});

afterEach(() => {
    cleanup();
    for (const spy of clockSpies) spy.mockRestore();
    clockSpies = [];
    document.body.innerHTML = "";
    if (originalLocalStorage) Object.defineProperty(window, "localStorage", originalLocalStorage);
});

test("no form key means no restore, no listener, no timer and no write", async () => {
    installClock();
    const fake = makeGrid(ROW_HEIGHT * 10);
    const view = renderScrollRestore(fake.ref, makeAdapter([row("a")]), [row("a")], null, "");

    await tick(1000);
    expect(fake.scrollToRow).not.toHaveBeenCalled();
    expect(pendingTimers()).toHaveLength(0);
    expect(fake.registrations()).toHaveLength(0);
    expect(setItem).not.toHaveBeenCalled();

    fake.scrollChange();
    await tick(1000);
    expect(setItem).not.toHaveBeenCalled();
    view.unmount();
});

test("a stored row waits for layout, scrolls once and ends on the settle check", async () => {
    installClock();
    const rows = [row("a"), row("b"), row("target"), row("d")];
    writeScrollPosition(FORM, { rowId: "target", scope: SCOPE });
    setItem.mockClear(); // only count writes made by the hook
    const fake = makeGrid(0); // below the 3-row layout threshold
    const view = renderScrollRestore(fake.ref, makeAdapter(rows), rows, FORM, SCOPE);

    // First attempt only waits for the grid to lay out: no scroll, 80 ms timer.
    expect(fake.scrollToRow).not.toHaveBeenCalled();
    expect(pendingTimers().map((timer) => timer.at)).toEqual([80]);
    await tick(80);
    expect(fake.scrollToRow).not.toHaveBeenCalled();
    expect(pendingTimers()).toHaveLength(1);

    fake.setHeight(ROW_HEIGHT * 10);
    await tick(80);
    expect(fake.scrollToRow).toHaveBeenCalledTimes(1);
    expect(fake.scrollToRow).toHaveBeenCalledWith(2);
    expect(pendingTimers().map((timer) => timer.at)).toEqual([280]); // 120 ms settle check

    // The settle check accepts |scrollTop - index * rowHeight| <= 2 rows.
    fake.scroller.scrollTop = 2 * ROW_HEIGHT + ROW_HEIGHT;
    await tick(120);
    expect(fake.scrollToRow).toHaveBeenCalledTimes(1);
    expect(pendingTimers()).toHaveLength(0);
    expect(setItem).not.toHaveBeenCalled();
    view.unmount();
});

test("a stuck restore stops after 20 attempts", async () => {
    installClock();
    // The target is far enough down that a scrollTop of 0 is OUTSIDE the
    // two-row settle tolerance, so every check retries.
    const rows = [row("a"), row("b"), row("c"), row("d"), row("target"), row("f")];
    writeScrollPosition(FORM, { rowId: "target", scope: SCOPE });
    setItem.mockClear();
    const fake = makeGrid(ROW_HEIGHT * 10);
    const view = renderScrollRestore(fake.ref, makeAdapter(rows), rows, FORM, SCOPE);

    // scrollTop never reaches the target: each settle check retries, until the
    // bounded attempt count ends the loop with no pending timer left behind.
    // The first mount effect is flushed by the first `act` tick; from then on
    // each settle check retries, until the 20-attempt bound ends the loop with
    // no timer left behind.
    for (let attempt = 0; attempt < 21; attempt += 1) await tick(120);
    expect(fake.scrollToRow).toHaveBeenCalledTimes(20);
    expect(pendingTimers().map((timer) => timer.at)).toEqual([]);
    view.unmount();
});

test("a stored row that is absent, a placeholder or deleted is never scrolled to", async () => {
    const cases: Array<{ label: string; rows: GridRow[]; id: string }> = [
        { label: "absent", rows: [row("a"), row("b")], id: "gone" },
        {
            label: "placeholder",
            rows: [row("a"), row("plus", { isPlaceholder: true }), row("b")],
            id: "plus",
        },
        {
            label: "deleted",
            rows: [row("a"), row("gone", { isDeleted: true }), row("b")],
            id: "gone",
        },
    ];
    for (const entry of cases) {
        cleanup();
        installClock();
        writeScrollPosition(FORM, { rowId: entry.id, scope: SCOPE });
        setItem.mockClear();
        const fake = makeGrid(ROW_HEIGHT * 10);
        const view = renderScrollRestore(
            fake.ref,
            makeAdapter(entry.rows),
            entry.rows,
            FORM,
            SCOPE,
        );

        await tick(1000);
        expect(fake.scrollToRow).not.toHaveBeenCalled();
        expect(setItem).not.toHaveBeenCalled();
        expect(pendingTimers()).toHaveLength(0);
        view.unmount();
        for (const spy of clockSpies) spy.mockRestore();
        clockSpies = [];
    }
});

test("a scope change re-arms the restore flags and the storage scope", async () => {
    installClock();
    const rows = [row("a"), row("b"), row("c"), row("d"), row("e"), row("f")];
    writeScrollPosition(FORM, { rowId: "b", scope: "one" });
    // Index 4 keeps scrollTop 0 outside the two-row settle tolerance, so the
    // restore is still travelling when the scroll event below fires.
    writeScrollPosition(FORM, { rowId: "e", scope: "two" });
    setItem.mockClear();
    const fake = makeGrid(ROW_HEIGHT * 10);
    const view = renderScrollRestore(fake.ref, makeAdapter(rows), rows, FORM, "one");

    fake.scroller.scrollTop = ROW_HEIGHT;
    await tick(120);
    expect(fake.scrollToRow).toHaveBeenCalledWith(1);
    fake.scrollChange();
    await tick(250);
    expect(setItem).toHaveBeenCalledWith(storageKey("one"), JSON.stringify({ rowId: "b" }));
    setItem.mockClear();

    // Switching scope re-arms: the new scope's row is restored, and a scroll
    // while that restore is travelling is NOT written (no transient overwrite).
    fake.scroller.scrollTop = 0;
    view.rerender({ form: FORM, scope: "two" });
    fake.scrollChange();
    await tick(250);
    expect(fake.scrollToRow).toHaveBeenCalledWith(4);
    expect(setItem).not.toHaveBeenCalled();

    fake.scroller.scrollTop = 4 * ROW_HEIGHT;
    await tick(480);
    fake.scrollChange();
    await tick(250);
    expect(setItem).toHaveBeenCalledWith(storageKey("two"), JSON.stringify({ rowId: "e" }));
    view.unmount();
});

test("scrollchange debounces into one write of the visible top row and its scope", async () => {
    installClock();
    const rows = [row("a"), row("b"), row("c")];
    const fake = makeGrid(ROW_HEIGHT * 10);
    fake.scroller.scrollTop = ROW_HEIGHT; // row "b" sits at the top
    const adapter = makeAdapter(rows);
    const view = renderScrollRestore(fake.ref, adapter, rows, FORM, SCOPE);

    for (let count = 0; count < 6; count += 1) fake.scrollChange();
    expect(fake.registrations()).toHaveLength(1);
    expect(pendingTimers().map((timer) => timer.at)).toEqual([250]);
    await tick(249);
    expect(setItem).not.toHaveBeenCalled();
    await tick(1);
    expect(adapter.getVisibleRows).toHaveBeenCalledTimes(1);
    expect(setItem).toHaveBeenCalledTimes(1);
    expect(setItem).toHaveBeenCalledWith(storageKey(SCOPE), JSON.stringify({ rowId: "b" }));
    view.unmount();
});

test("a placeholder or deleted row at the top keeps the last real position", async () => {
    installClock();
    const rows = [
        row("a"),
        row("b"),
        row("plus", { isPlaceholder: true }),
        row("gone", { isDeleted: true }),
    ];
    writeScrollPosition(FORM, { rowId: "b", scope: SCOPE });
    setItem.mockClear();
    const fake = makeGrid(ROW_HEIGHT * 10);
    fake.scroller.scrollTop = ROW_HEIGHT; // settle the initial restore at row "b"
    const view = renderScrollRestore(fake.ref, makeAdapter(rows), rows, FORM, SCOPE);
    await tick(120);

    fake.scroller.scrollTop = 2 * ROW_HEIGHT; // placeholder
    fake.scrollChange();
    await tick(250);
    fake.scroller.scrollTop = 3 * ROW_HEIGHT; // deleted
    fake.scrollChange();
    await tick(250);
    expect(setItem).not.toHaveBeenCalled();
    view.unmount();
});

test("unmount clears the pending debounce and removes the scrollchange listener", async () => {
    installClock();
    const fake = makeGrid(ROW_HEIGHT * 10);
    const view = renderScrollRestore(fake.ref, makeAdapter([row("a")]), [row("a")], FORM, SCOPE);

    const [registration] = fake.registrations();
    expect(registration).toBeDefined();
    fake.scrollChange();
    const [timer] = pendingTimers();
    expect(timer).toBeDefined();

    view.unmount();
    const [removal] = fake.removals();
    expect(removal).toBeDefined();
    expect(removal?.[1]).toBe(registration?.[1]);
    expect(timer?.cleared).toBe(true);

    fake.scrollChange();
    await tick(1000);
    expect(setItem).not.toHaveBeenCalled();
});
