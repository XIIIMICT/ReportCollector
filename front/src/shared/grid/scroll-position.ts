"use client";

/**
 * Where the user last left each form, remembered per project and per form.
 *
 * The plan holds hundreds of thousands of rows and the dictionaries a handful;
 * a reload used to throw everyone back to the top either way. The remembered
 * value is the **stable row id** of the row at the top of the viewport, not an
 * ordinal: rows are inserted and deleted between visits, sorting and filtering
 * renumber the visible list, and an ordinal would silently land on a different
 * record. A missing id (the row was deleted, the filter hides it) simply means
 * "no position" and the form opens at its default.
 *
 * The filter part of the key is deliberate. A position is only meaningful
 * inside the scope it was taken in, so the entry is stored per filter signature;
 * coming back with the same filters returns the same row, and clearing them
 * returns to where the user was before filtering instead of jumping somewhere
 * arbitrary. `scope` carries filters and sorting only — the asset selector is a
 * scope of its own, already part of the form key.
 *
 * Storage: `localStorage`, so it survives reloads and sessions, unlike the
 * in-memory undo stacks. Malformed entries are ignored, never thrown on — a
 * remembered position is an enhancement and must not break a form.
 */

const STORAGE_PREFIX = "reportcollector:scroll-position:";

export type ScrollPosition = {
    /** Stable id of the row that sat at the top of the viewport. */
    rowId: string;
    /** Filters/sorting the position was taken in (empty = the plain form). */
    scope: string;
};

/** Normalise a scope description into a stable, storage-safe key part. */
export function scrollScopeKey(scope: unknown): string {
    if (scope === null || scope === undefined) return "";
    if (typeof scope === "string") return scope;
    try {
        return JSON.stringify(scope);
    } catch {
        return "";
    }
}

function storageKey(formKey: string, scope: string): string {
    return `${STORAGE_PREFIX}${formKey}:${scope}`;
}

/** The remembered row of a form in a given scope, or null. */
export function readScrollPosition(formKey: string, scope = ""): ScrollPosition | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(storageKey(formKey, scope));
        if (!raw) return null;
        const parsed: unknown = JSON.parse(raw);
        if (typeof parsed !== "object" || parsed === null) return null;
        const record = parsed as Record<string, unknown>;
        if (typeof record.rowId !== "string" || record.rowId === "") return null;
        return { rowId: record.rowId, scope };
    } catch {
        return null;
    }
}

/** Remember the row at the top of a form, replacing the previous one. */
export function writeScrollPosition(formKey: string, position: ScrollPosition): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(
            storageKey(formKey, position.scope),
            JSON.stringify({ rowId: position.rowId }),
        );
    } catch {
        // Storage full or disabled — the form still works, it just forgets.
    }
}

/** Forget a form's position in one scope (or in every scope when omitted). */
export function clearScrollPosition(formKey: string, scope?: string): void {
    if (typeof window === "undefined") return;
    try {
        if (scope !== undefined) {
            window.localStorage.removeItem(storageKey(formKey, scope));
            return;
        }
        const prefix = `${STORAGE_PREFIX}${formKey}:`;
        const stale: string[] = [];
        for (let index = 0; index < window.localStorage.length; index += 1) {
            const key = window.localStorage.key(index);
            if (key?.startsWith(prefix)) stale.push(key);
        }
        for (const key of stale) window.localStorage.removeItem(key);
    } catch {
        // ignore
    }
}
