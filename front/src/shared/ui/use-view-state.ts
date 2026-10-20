"use client";

import { useCallback, useState } from "react";

import { readWorkspaceQuery, writeWorkspaceQuery } from "./query-state";

/**
 * The view state every project shares: pinned header and service columns.
 *
 * Both flags live in one hook (they are always used together by the view strip)
 * and are remembered between sessions in `localStorage`, so a user who prefers
 * a pinned header keeps it after a reload and across projects. The storage key
 * is per project: switching from «Сырьё» to «Ремонт» shouldn't force one
 * project's look onto the other, and the renovation plan/dictionaries keep a
 * single pair inside their project (they used to hold two separate ones).
 */

export type ViewState = {
    headerPinned: boolean;
    showServiceColumns: boolean;
};

const STORAGE_PREFIX = "reportcollector:view:";

/** Pinned header on, service columns hidden — the default everywhere. */
export const DEFAULT_VIEW_STATE: ViewState = {
    headerPinned: true,
    showServiceColumns: false,
};

export const DEFAULT_ROW_HEIGHT = 36;
export const MIN_ROW_HEIGHT = DEFAULT_ROW_HEIGHT / 2;
export const MAX_ROW_HEIGHT = DEFAULT_ROW_HEIGHT * 2;
const TABLE_DIMENSIONS_PREFIX = "reportcollector:table-dimensions:";

function validRowHeight(value: unknown): value is number {
    return (
        typeof value === "number" &&
        Number.isFinite(value) &&
        value >= MIN_ROW_HEIGHT &&
        value <= MAX_ROW_HEIGHT
    );
}

function isBoolean(value: unknown): value is boolean {
    return typeof value === "boolean";
}

/**
 * Read the stored view state of a project. A missing or malformed entry falls
 * back to the defaults field by field, so a partially written object (an older
 * version of the app) cannot leave the UI in an undefined state.
 */
export function readStoredViewState(projectKey: string): ViewState {
    if (typeof window === "undefined") return DEFAULT_VIEW_STATE;
    try {
        const raw = window.localStorage.getItem(STORAGE_PREFIX + projectKey);
        if (!raw) return DEFAULT_VIEW_STATE;
        const parsed: unknown = JSON.parse(raw);
        if (typeof parsed !== "object" || parsed === null) return DEFAULT_VIEW_STATE;
        const record = parsed as Record<string, unknown>;
        return {
            headerPinned:
                readWorkspaceQuery("header") === "0"
                    ? false
                    : readWorkspaceQuery("header") === "1"
                      ? true
                      : isBoolean(record.headerPinned)
                        ? record.headerPinned
                        : DEFAULT_VIEW_STATE.headerPinned,
            showServiceColumns:
                readWorkspaceQuery("service") === "1"
                    ? true
                    : readWorkspaceQuery("service") === "0"
                      ? false
                      : isBoolean(record.showServiceColumns)
                        ? record.showServiceColumns
                        : DEFAULT_VIEW_STATE.showServiceColumns,
        };
    } catch {
        // Private mode / corrupted value: the defaults are still usable.
        return DEFAULT_VIEW_STATE;
    }
}

function writeStoredViewState(projectKey: string, state: ViewState): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(STORAGE_PREFIX + projectKey, JSON.stringify(state));
    } catch {
        // Storage full or disabled — the state still works for this session.
    }
}

/** Clear the remembered view of a project (tests, «сброс настроек»). */
export function clearStoredViewState(projectKey: string): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.removeItem(STORAGE_PREFIX + projectKey);
    } catch {
        // ignore
    }
}

export type ViewStateControls = ViewState & {
    rowHeight: number;
    setHeaderPinned: (value: boolean) => void;
    setShowServiceColumns: (value: boolean) => void;
    setRowHeight: (value: number) => void;
    resetRowHeight: () => void;
};

function readRowHeight(projectKey: string): number {
    if (typeof window === "undefined") return DEFAULT_ROW_HEIGHT;
    try {
        const raw = window.localStorage.getItem(TABLE_DIMENSIONS_PREFIX + projectKey);
        if (!raw) return DEFAULT_ROW_HEIGHT;
        const parsed = JSON.parse(raw) as { rowHeight?: unknown };
        return validRowHeight(parsed.rowHeight) ? parsed.rowHeight : DEFAULT_ROW_HEIGHT;
    } catch {
        return DEFAULT_ROW_HEIGHT;
    }
}

function writeRowHeight(projectKey: string, value: number): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(
            TABLE_DIMENSIONS_PREFIX + projectKey,
            JSON.stringify({ rowHeight: value }),
        );
    } catch {
        // Storage is an enhancement; the in-memory value remains usable.
    }
}

/**
 * The project's view state as React state, persisted on every change.
 *
 * `useState(readStored...)` initialises lazily on the client; SSR renders the
 * defaults and the first client render adopts the stored value before paint
 * (the switch is hidden behind the toolbar that mounts together with it, and
 * the grid reads its width from the DOM, so there is no visible jump).
 */
export function useViewState(projectKey: string): ViewStateControls {
    const [state, setState] = useState<ViewState>(() => readStoredViewState(projectKey));
    const [rowHeight, setRowHeightState] = useState(() => readRowHeight(projectKey));

    const setHeaderPinned = useCallback(
        (value: boolean) => {
            setState((current) => {
                const next = { ...current, headerPinned: value };
                writeStoredViewState(projectKey, next);
                writeWorkspaceQuery("header", value ? "1" : "0");
                return next;
            });
        },
        [projectKey],
    );

    const setShowServiceColumns = useCallback(
        (value: boolean) => {
            setState((current) => {
                const next = { ...current, showServiceColumns: value };
                writeStoredViewState(projectKey, next);
                writeWorkspaceQuery("service", value ? "1" : "0");
                return next;
            });
        },
        [projectKey],
    );

    const setRowHeight = useCallback(
        (value: number) => {
            if (!validRowHeight(value)) return;
            setRowHeightState(value);
            writeRowHeight(projectKey, value);
        },
        [projectKey],
    );

    const resetRowHeight = useCallback(() => {
        setRowHeight(DEFAULT_ROW_HEIGHT);
    }, [setRowHeight]);

    return {
        ...state,
        rowHeight,
        setHeaderPinned,
        setShowServiceColumns,
        setRowHeight,
        resetRowHeight,
    };
}
