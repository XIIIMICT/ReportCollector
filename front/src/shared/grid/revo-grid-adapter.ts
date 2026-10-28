/**
 * The only application-owned boundary around the installed RevoGrid runtime.
 *
 * Feature grids may keep their data source, sorting policy and presentation
 * outside this module. They must not reach into RevoGrid's private selection
 * stores or focus restoration fields, though: those details are version
 * specific and belong here with the adapter contract tests.
 */

import type { RangeArea } from "@revolist/react-datagrid";

export type GridCoordinate = { x: number; y: number };

/** Public range coordinates plus the optional dimension labels in RevoGrid's event payload. */
export type SelectedGridRange = RangeArea & {
    rowType?: string;
    colType?: string;
};

export type GridRef = { current: HTMLRevoGridElement | null };

type TemporaryRangeStore = {
    onChange: (key: string, callback: (value: RangeArea | null) => void) => unknown;
    get: (key: string) => RangeArea | null;
};

type PrivateGridState = HTMLRevoGridElement & {
    pendingColumnFocusRestore?: unknown;
};

export type RangeEditDetail = {
    data: Record<string, Record<string, unknown>>;
    models: unknown[];
    type: string;
    oldRange: RangeArea;
    newRange: RangeArea;
};

export type RevoGridEventName =
    | "afterFilterApply"
    | "afterFocus"
    | "afterSourcesSet"
    | "beforeHeaderClick"
    | "cancelEdit"
    | "focusCell"
    | "scrollChange"
    | "setRange";

const EVENT_NAMES: Record<RevoGridEventName, string> = {
    afterFilterApply: "afterfilterapply",
    afterFocus: "afterfocus",
    afterSourcesSet: "aftersourceset",
    beforeHeaderClick: "beforeheaderclick",
    cancelEdit: "canceledit",
    focusCell: "focuscell",
    scrollChange: "scrollchange",
    setRange: "setrange",
};

export type RevoGridAdapter<TRow = unknown> = {
    /** Return the current visible source, or the supplied snapshot if absent. */
    getVisibleRows: (fallback?: TRow[]) => Promise<TRow[]>;
    /** Return the sparse source, or an empty list if the element is unavailable. */
    getSourceRows: () => Promise<TRow[]>;
    getColumns: () => Promise<unknown[]>;
    getContentSize: () => Promise<{ x: number; y: number }>;
    getSelectedRange: () => Promise<SelectedGridRange | null>;
    setRange: (start: GridCoordinate, end: GridCoordinate) => Promise<void>;
    clearRange: () => Promise<void>;
    scrollToCell: (row: number, column: number) => Promise<void>;
    dispatchRangeEdit: (detail: RangeEditDetail) => boolean;
    isTarget: (event: Event) => boolean;
    on: (
        event: RevoGridEventName,
        listener: EventListener,
        options?: boolean | AddEventListenerOptions,
    ) => () => void;
    /** Subscribe to the temporary drag range used by the autofill overlay. */
    subscribeTemporaryRange: (onRange: (range: RangeArea | null) => void) => () => void;
};

/**
 * A monotonically increasing scope token for asynchronous grid work.
 * Callers capture `token` before starting a timer/request and check
 * `isCurrent(token)` before publishing its result.
 */
export type GridEpoch = {
    current: () => number;
    next: () => number;
    isCurrent: (token: number) => boolean;
};

export function createGridEpoch(): GridEpoch {
    let value = 0;
    return {
        current: () => value,
        next: () => {
            value += 1;
            return value;
        },
        isCurrent: (token) => token === value,
    };
}

/** Whether a browser event belongs to this grid, including its shadow tree. */
export function isRevoGridTarget(event: Event, grid: HTMLElement): boolean {
    const target = event.target as Node | null;
    return (
        event.composedPath().includes(grid) ||
        (target !== null && grid.contains(target)) ||
        (target !== null && grid.shadowRoot?.contains(target) === true)
    );
}

function privateState(grid: HTMLRevoGridElement): PrivateGridState {
    return grid as PrivateGridState;
}

function temporaryRangeStore(grid: HTMLRevoGridElement): TemporaryRangeStore | null {
    return (
        (
            grid.querySelector("revogr-temp-range") as unknown as {
                selectionStore?: TemporaryRangeStore;
            } | null
        )?.selectionStore ?? null
    );
}

export function createRevoGridAdapter<TRow = unknown>(ref: GridRef): RevoGridAdapter<TRow> {
    const getGrid = () => ref.current;

    return {
        async getVisibleRows(fallback = []): Promise<TRow[]> {
            const grid = getGrid();
            if (!grid) return fallback;
            try {
                return (await grid.getVisibleSource("rgRow")) as TRow[];
            } catch {
                return fallback;
            }
        },

        async getSourceRows(): Promise<TRow[]> {
            const grid = getGrid();
            if (!grid) return [];
            try {
                return (await grid.getSource("rgRow")) as TRow[];
            } catch {
                return [];
            }
        },

        async getColumns(): Promise<unknown[]> {
            const grid = getGrid();
            if (!grid) return [];
            try {
                return await grid.getColumns();
            } catch {
                return [];
            }
        },

        async getContentSize() {
            const grid = getGrid();
            if (!grid) return { x: 0, y: 0 };
            try {
                return await grid.getContentSize();
            } catch {
                return { x: 0, y: 0 };
            }
        },

        async getSelectedRange() {
            const grid = getGrid();
            if (!grid) return null;
            try {
                return (await grid.getSelectedRange()) as SelectedGridRange | null;
            } catch {
                return null;
            }
        },

        async setRange(start, end) {
            const grid = getGrid();
            if (!grid) return;
            await grid.setCellsFocus(start, end);
            await grid.refresh();
        },

        async clearRange() {
            const grid = getGrid();
            if (!grid) return;
            const state = privateState(grid);
            await grid.clearFocus();
            // RevoGrid can restore a focused cell after a column rebuild. The
            // field is private, so this is deliberately the only cast in the
            // application and is covered by the adapter contract tests.
            state.pendingColumnFocusRestore = undefined;
            await grid.refresh();
            state.pendingColumnFocusRestore = undefined;
        },

        async scrollToCell(row, column) {
            const grid = getGrid();
            if (!grid) return;
            await grid.scrollToCoordinate({ x: column, y: row });
        },

        dispatchRangeEdit(detail) {
            const grid = getGrid();
            if (!grid) return false;
            const EventConstructor = grid.ownerDocument.defaultView?.CustomEvent ?? CustomEvent;
            return grid.dispatchEvent(
                new EventConstructor("rangeeditapply", {
                    detail,
                    bubbles: true,
                    composed: true,
                }),
            );
        },

        isTarget(event) {
            const grid = getGrid();
            return grid ? isRevoGridTarget(event, grid) : false;
        },

        on(event, listener, options) {
            const grid = getGrid();
            if (!grid) return () => {};
            const name = EVENT_NAMES[event];
            grid.addEventListener(name, listener, options);
            return () => grid.removeEventListener(name, listener, options);
        },

        subscribeTemporaryRange(onRange) {
            const grid = getGrid();
            if (!grid) return () => {};
            const store = temporaryRangeStore(grid);
            if (!store) return () => {};
            const off = store.onChange("tempRange", onRange);
            onRange(store.get("tempRange"));
            return typeof off === "function" ? (off as () => void) : () => {};
        },
    };
}

/** Return indexes in a range that are not present in a sparse row map. */
export function missingRangeIndexes(
    start: number,
    end: number,
    rowsByIndex: ReadonlyMap<number, unknown>,
): number[] {
    const missing: number[] = [];
    for (let index = start; index <= end; index += 1) {
        if (!rowsByIndex.has(index)) missing.push(index);
    }
    return missing;
}
