"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * User overrides of a column's pinned state.
 *
 * `true` — the user pinned a column that was not pinned by default; `false` —
 * the user unpinned an identity column that is pinned by default. Absent means
 * "follow the descriptor's `pinned` flag".
 */
export type PinnedColumnOverrides = Record<string, boolean>;

const STORAGE_PREFIX = "rc:pinnedColumns:";
const EMPTY: PinnedColumnOverrides = {};

export function pinnedColumnsStorageKey(project: string, section: string): string {
    return `${STORAGE_PREFIX}${project}:${section}`;
}

export function readStoredPinnedColumns(project: string, section: string): PinnedColumnOverrides {
    if (typeof window === "undefined") return EMPTY;
    try {
        const raw = window.localStorage.getItem(pinnedColumnsStorageKey(project, section));
        if (raw === null) return EMPTY;
        const parsed: unknown = JSON.parse(raw);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return EMPTY;
        const result: PinnedColumnOverrides = {};
        for (const [prop, value] of Object.entries(parsed)) {
            if (prop !== "" && typeof value === "boolean") result[prop] = value;
        }
        return result;
    } catch {
        return EMPTY;
    }
}

export function writeStoredPinnedColumns(
    project: string,
    section: string,
    overrides: PinnedColumnOverrides,
): void {
    if (typeof window === "undefined") return;
    try {
        const key = pinnedColumnsStorageKey(project, section);
        if (Object.keys(overrides).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(overrides));
    } catch {
        // Best-effort, like the hidden-column state.
    }
}

/** The effective pinned state of one column: user override, then the descriptor default. */
export function isColumnPinned(
    prop: string,
    overrides: PinnedColumnOverrides,
    descriptorPinned = false,
): boolean {
    return overrides[prop] ?? descriptorPinned;
}

/**
 * One table's user pin overrides, restored after hydration and written on every
 * change. Same key scheme and best-effort rules as the hidden columns.
 */
export function useGridPinnedColumns(project: string, section: string) {
    const scope = `${project}:${section}`;
    const [byScope, setByScope] = useState<Record<string, PinnedColumnOverrides>>({});
    const restored = useRef(new Set<string>());

    useEffect(() => {
        if (restored.current.has(scope)) return;
        restored.current.add(scope);
        const stored = readStoredPinnedColumns(project, section);
        if (Object.keys(stored).length === 0) return;
        setByScope((previous) => ({ ...previous, [scope]: stored }));
    }, [project, scope, section]);

    const overrides = byScope[scope] ?? EMPTY;

    const setPinned = useCallback(
        (prop: string, pinned: boolean) => {
            setByScope((previous) => {
                const current = previous[scope] ?? EMPTY;
                const next = { ...current, [prop]: pinned };
                writeStoredPinnedColumns(project, section, next);
                return { ...previous, [scope]: next };
            });
        },
        [project, scope, section],
    );

    const setMany = useCallback(
        (props: readonly string[], pinned: boolean) => {
            if (props.length === 0) return;
            setByScope((previous) => {
                const current = previous[scope] ?? EMPTY;
                const next = { ...current };
                for (const prop of props) next[prop] = pinned;
                writeStoredPinnedColumns(project, section, next);
                return { ...previous, [scope]: next };
            });
        },
        [project, scope, section],
    );

    const reset = useCallback(() => {
        writeStoredPinnedColumns(project, section, EMPTY);
        setByScope((previous) => ({ ...previous, [scope]: EMPTY }));
    }, [project, scope, section]);

    return { overrides, setPinned, setMany, reset } as const;
}
