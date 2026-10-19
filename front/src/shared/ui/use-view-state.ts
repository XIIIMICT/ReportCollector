"use client";

import { useCallback, useState } from "react";

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
            headerPinned: isBoolean(record.headerPinned)
                ? record.headerPinned
                : DEFAULT_VIEW_STATE.headerPinned,
            showServiceColumns: isBoolean(record.showServiceColumns)
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
    setHeaderPinned: (value: boolean) => void;
    setShowServiceColumns: (value: boolean) => void;
};

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

    const setHeaderPinned = useCallback(
        (value: boolean) => {
            setState((current) => {
                const next = { ...current, headerPinned: value };
                writeStoredViewState(projectKey, next);
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
                return next;
            });
        },
        [projectKey],
    );

    return { ...state, setHeaderPinned, setShowServiceColumns };
}
