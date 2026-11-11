"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Hidden data columns of one table.
 *
 * The value of a prop is the HEADER LEVEL the column was hidden from: the
 * expand button sits at the top edge of that level, exactly where the clicked
 * header cell starts. `0` is the top level, `1` the next one down, and so on.
 * Storing the level rather than pixels keeps the button in place when the
 * header heights are dragged.
 */
export type HiddenColumnAnchors = Record<string, number>;

export type HiddenColumnBlock = {
    /** The contiguous hidden props, in table order. */
    props: string[];
    /** Header level of the leftmost hidden column (its expand button's top). */
    anchor: number;
};

const STORAGE_PREFIX = "rc:hiddenColumns:";
const EMPTY: HiddenColumnAnchors = {};

/** The `localStorage` key of one table's hidden columns. */
export function hiddenColumnsStorageKey(project: string, section: string): string {
    return `${STORAGE_PREFIX}${project}:${section}`;
}

/** Read one table's hidden columns; a malformed entry simply hides nothing. */
export function readStoredHiddenColumns(project: string, section: string): HiddenColumnAnchors {
    if (typeof window === "undefined") return EMPTY;
    try {
        const raw = window.localStorage.getItem(hiddenColumnsStorageKey(project, section));
        if (raw === null) return EMPTY;
        const parsed: unknown = JSON.parse(raw);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return EMPTY;
        const result: HiddenColumnAnchors = {};
        for (const [prop, value] of Object.entries(parsed)) {
            if (prop === "") continue;
            const level =
                typeof value === "number" && Number.isFinite(value) && value >= 0
                    ? Math.floor(value)
                    : 0;
            result[prop] = level;
        }
        return result;
    } catch {
        return EMPTY;
    }
}

/** Persist one table's hidden columns; an empty set removes the entry. */
export function writeStoredHiddenColumns(
    project: string,
    section: string,
    hidden: HiddenColumnAnchors,
): void {
    if (typeof window === "undefined") return;
    try {
        const key = hiddenColumnsStorageKey(project, section);
        if (Object.keys(hidden).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(hidden));
    } catch {
        // A quota error or a disabled store leaves the table working.
    }
}

/** Maximal runs of hidden props in the table's own order. */
export function hiddenColumnBlocks(
    orderedProps: readonly string[],
    hidden: HiddenColumnAnchors,
): HiddenColumnBlock[] {
    const blocks: HiddenColumnBlock[] = [];
    let current: string[] = [];
    const flush = () => {
        if (current.length === 0) return;
        // The button sits at the HIGHEST header level in the block: merging a
        // leaf-hidden column with a whole group to its left must not drag the
        // button down to the leaf row.
        const anchor = Math.min(...current.map((prop) => hidden[prop] ?? 0));
        blocks.push({ props: current, anchor });
        current = [];
    };
    for (const prop of orderedProps) {
        if (hidden[prop] !== undefined) {
            current.push(prop);
            continue;
        }
        flush();
    }
    flush();
    return blocks;
}

/** Hide `props` from `level`; already-hidden neighbours join the same block. */
export function hideColumns(
    hidden: HiddenColumnAnchors,
    props: readonly string[],
    level: number,
): HiddenColumnAnchors {
    if (props.length === 0) return hidden;
    const next = { ...hidden };
    for (const prop of props) next[prop] = Math.max(0, Math.floor(level));
    return next;
}

/** Reveal `props` (the whole block the expand button stands for). */
export function showColumns(
    hidden: HiddenColumnAnchors,
    props: readonly string[],
): HiddenColumnAnchors {
    const next = { ...hidden };
    for (const prop of props) delete next[prop];
    return next;
}

/**
 * One table's hidden columns, restored from `localStorage` after hydration and
 * written on every change — the same key scheme and best-effort rules as the
 * column filters.
 *
 * The restore happens in an effect (not in the render) so SSR and the first
 * hydrated frame agree; a stored table shows its hidden columns one frame later.
 */
export function useGridHiddenColumns(project: string, section: string) {
    const scope = `${project}:${section}`;
    const [byScope, setByScope] = useState<Record<string, HiddenColumnAnchors>>({});
    const restored = useRef(new Set<string>());

    useEffect(() => {
        if (restored.current.has(scope)) return;
        restored.current.add(scope);
        const stored = readStoredHiddenColumns(project, section);
        if (Object.keys(stored).length === 0) return;
        setByScope((previous) => ({ ...previous, [scope]: stored }));
    }, [project, scope, section]);

    const hidden = byScope[scope] ?? EMPTY;

    const commit = useCallback(
        (next: HiddenColumnAnchors) => {
            writeStoredHiddenColumns(project, section, next);
            setByScope((previous) => ({ ...previous, [scope]: next }));
        },
        [project, scope, section],
    );

    const hide = useCallback(
        (props: readonly string[], level: number) => {
            setByScope((previous) => {
                const current = previous[scope] ?? EMPTY;
                const next = hideColumns(current, props, level);
                writeStoredHiddenColumns(project, section, next);
                return next === current ? previous : { ...previous, [scope]: next };
            });
        },
        [project, scope, section],
    );

    const show = useCallback(
        (props: readonly string[]) => {
            setByScope((previous) => {
                const current = previous[scope] ?? EMPTY;
                const next = showColumns(current, props);
                writeStoredHiddenColumns(project, section, next);
                return { ...previous, [scope]: next };
            });
        },
        [project, scope, section],
    );

    const reset = useCallback(() => {
        commit(EMPTY);
    }, [commit]);

    return { hidden, hide, show, reset } as const;
}
