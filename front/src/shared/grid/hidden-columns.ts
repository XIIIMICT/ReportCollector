"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Hidden data columns of one table.
 *
 * The value of a prop is the HEADER LEVEL the column was hidden from and the
 * HIDE ACTION it belongs to. The level is where the expand button sits (the
 * clicked header cell's top edge), `0` being the top level; storing the level
 * rather than pixels keeps the button in place when header heights are dragged.
 * The action id keeps neighbouring columns hidden by DIFFERENT right-clicks as
 * separate buttons, with the later action drawn above the earlier one.
 */
export type HiddenColumnAnchor = {
    level: number;
    /** Monotonic hide-action id; a bigger id was hidden later. */
    group: number;
};

export type HiddenColumnAnchors = Record<string, HiddenColumnAnchor>;

export type HiddenColumnBlock = {
    /** The hidden props of ONE hide action, in table order. */
    props: string[];
    /** The HIGHEST header level in the block (its expand button's top). */
    anchor: number;
    /** Hide-action id: the later the action, the higher the button's z-index. */
    priority: number;
};

const STORAGE_PREFIX = "rc:hiddenColumns:";
const EMPTY: HiddenColumnAnchors = {};

/** `number` values are the legacy `{prop: level}` entries, read as separate actions. */
export function anchorOf(value: HiddenColumnAnchor | number | undefined): HiddenColumnAnchor {
    if (typeof value === "number" && Number.isFinite(value)) {
        return { level: Math.max(0, Math.floor(value)), group: -1 };
    }
    if (value && typeof value === "object" && typeof value.level === "number") {
        return {
            level: Math.max(0, Math.floor(value.level)),
            group: typeof value.group === "number" ? value.group : -1,
        };
    }
    return { level: 0, group: -1 };
}

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
        let group = 0;
        for (const [prop, value] of Object.entries(parsed)) {
            if (prop === "") continue;
            const parsedAnchor = value as HiddenColumnAnchor | number | undefined;
            const anchor = anchorOf(parsedAnchor);
            // A legacy number carries no action id: give each entry its own, so
            // columns hidden before the upgrade do not all merge into one button.
            result[prop] = {
                level: anchor.level,
                group: anchor.group >= 0 ? anchor.group : group,
            };
            group += 1;
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

/**
 * Maximal runs of hidden props hidden by the SAME action, in table order.
 *
 * Two adjacent columns hidden by two separate right-clicks stay TWO buttons —
 * the later one is stacked above the earlier one so it wins the pointer.
 */
export function hiddenColumnBlocks(
    orderedProps: readonly string[],
    hidden: HiddenColumnAnchors,
): HiddenColumnBlock[] {
    const blocks: HiddenColumnBlock[] = [];
    let current: string[] = [];
    let group: number | null = null;
    let level = 0;
    const flush = () => {
        if (current.length === 0) return;
        blocks.push({ props: current, anchor: level, priority: group ?? 0 });
        current = [];
    };
    for (const prop of orderedProps) {
        const value = hidden[prop];
        if (value === undefined) {
            flush();
            group = null;
            continue;
        }
        const anchor = anchorOf(value);
        if (current.length > 0 && anchor.group !== group) flush();
        if (current.length === 0) {
            group = anchor.group;
            level = anchor.level;
        } else {
            level = Math.min(level, anchor.level);
        }
        current.push(prop);
    }
    flush();
    return blocks;
}

/** Hide `props` as ONE action from `level`; the new action gets the top priority. */
export function hideColumns(
    hidden: HiddenColumnAnchors,
    props: readonly string[],
    level: number,
): HiddenColumnAnchors {
    if (props.length === 0) return hidden;
    let group = 0;
    for (const anchor of Object.values(hidden)) group = Math.max(group, anchor.group + 1);
    const next = { ...hidden };
    for (const prop of props) next[prop] = { level: Math.max(0, Math.floor(level)), group };
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
