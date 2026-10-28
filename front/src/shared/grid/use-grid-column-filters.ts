"use client";

import { useCallback, useState } from "react";

import type { ColumnFilter } from "@/shared/ui/column-filter-facets";

/**
 * Where a table's header filters are remembered between visits.
 *
 * The URL is for the PANEL filters: few, they describe which slice of the data
 * is open, and a shared link should reproduce them. Header filters are working
 * state — one per column, each carrying the values it keeps (the plan's ID
 * column alone holds hundreds of thousands) — so they go to `localStorage`,
 * keyed by PROJECT and SECTION.
 *
 * The key is `rc:filters:<project>:<section>`, e.g. `rc:filters:renovation:assets`.
 * One project's filters can never reach another section (the bug that broke the
 * plan's load) and can never collide with another project's columns.
 *
 * Storage is best-effort in both directions: a full or disabled store must not
 * break the table, and a hand-edited entry must not crash it — anything that
 * fails to parse is dropped and the table simply opens unfiltered.
 */
const STORAGE_PREFIX = "rc:filters:";

/** The `localStorage` key of one table's filters. */
export function columnFiltersStorageKey(project: string, section: string): string {
    return `${STORAGE_PREFIX}${project}:${section}`;
}

/**
 * One remembered SORTING: which column, and in which direction.
 *
 * Deliberately only the column's `prop` and its order — never a comparator.
 * A comparator is a function, so it cannot be stored; the grid rebuilds it from
 * the column descriptor when the sorting is restored.
 */
export type StoredSortColumn = { prop: string; order: "asc" | "desc" };

/**
 * Does this filter constrain anything?
 *
 * A column filter with no kept values, no «Пустые» bucket and no window selects
 * NO rows — the server reads it as «nothing is kept». Persisting one emptied the
 * table on the next load while the popover itself looked untouched.
 */
export function isConstrainingFilter(filter: ColumnFilter): boolean {
    return (
        filter.values.length > 0 ||
        filter.includeNulls !== false ||
        (filter.from != null && filter.from !== "") ||
        (filter.to != null && filter.to !== "")
    );
}

/**
 * The narrowest storage for a table that keeps its filters OUTSIDE the shared
 * grid hook — the renovation plan has its own workspace state.
 *
 * Same key scheme and the same rules as the hook: best-effort in both
 * directions, a malformed entry is dropped rather than crashing the table, and
 * an empty set removes the entry.
 */
export function readStoredFilters(project: string, section: string): Record<string, ColumnFilter> {
    if (typeof window === "undefined") return {};
    try {
        const raw = window.localStorage.getItem(columnFiltersStorageKey(project, section));
        return raw === null ? {} : reviveFilters(raw);
    } catch {
        return {};
    }
}

/** Persist one table's filters; an empty set removes the entry. */
export function writeStoredFilters(
    project: string,
    section: string,
    filters: Record<string, ColumnFilter>,
): void {
    if (typeof window === "undefined") return;
    try {
        const key = columnFiltersStorageKey(project, section);
        if (Object.keys(filters).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(filters));
    } catch {
        // A quota error or a disabled store leaves the table working.
    }
}

/**
 * The SEARCH DRAFT of one table's header-filter popovers: what the user typed in
 * the search field / the MIN–MAX pair of a column, kept per column.
 *
 * It is working state exactly like the filters themselves, so it is remembered
 * the same way (`localStorage`, per project + section) and never persisted as a
 * filter: only «Применить» turns a search into a filter. Closing the popover used
 * to lose it, so a half-typed search had to be retyped.
 *
 * The key is `rc:filterDraft:<project>:<section>`.
 */
export type StoredFilterDraft = {
    /** The text search (a `text` column). */
    query?: string;
    /** The MIN–MAX pair (a `number` column). */
    min?: number | null;
    max?: number | null;
    /** The date range (a `date` column), as epoch milliseconds. */
    dateMin?: number | null;
    dateMax?: number | null;
};

/** The `localStorage` key of one table's filter-search drafts. */
export function filterDraftStorageKey(project: string, section: string): string {
    return `rc:filterDraft:${project}:${section}`;
}

/** A MIN–MAX bound of an instant range: a UTC instant, both ends inclusive. */
export type InstantWindow = { from?: string | null; to?: string | null };

/**
 * The remembered INSTANT WINDOW of one table's header-filter popovers.
 *
 * A `timestamptz` column offers no value list to pick from — every row has its
 * own timestamp, so a facet would be one option per row. The popover asks for a
 * window instead and the server matches the ROWS inside it. Kept per column
 * beside the other drafts, same key scheme.
 */
export function readInstantWindow(
    project: string,
    section: string,
    column: string,
): InstantWindow | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(filterDraftStorageKey(project, section));
        if (raw === null) return null;
        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
        const entry = (parsed as Record<string, unknown>)[column];
        if (!entry || typeof entry !== "object") return null;
        const instant = (entry as { instant?: InstantWindow }).instant;
        return instant && typeof instant === "object" ? instant : null;
    } catch {
        return null;
    }
}

/** Remember one column's instant window; an empty one removes the entry. */
export function writeInstantWindow(
    project: string,
    section: string,
    column: string,
    range: InstantWindow,
): void {
    if (typeof globalThis.localStorage === "undefined") return;
    try {
        const key = filterDraftStorageKey(project, section);
        const raw = globalThis.localStorage.getItem(key);
        let all: Record<string, Record<string, unknown>> = {};
        if (raw !== null) {
            const parsed: unknown = JSON.parse(raw);
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                all = parsed as Record<string, Record<string, unknown>>;
            }
        }
        const empty = (range.from ?? null) === null && (range.to ?? null) === null;
        const entry = { ...(all[column] ?? {}) };
        if (empty) delete entry.instant;
        else entry.instant = range;
        if (Object.keys(entry).length === 0) delete all[column];
        else all[column] = entry;
        if (Object.keys(all).length === 0) globalThis.localStorage.removeItem(key);
        else globalThis.localStorage.setItem(key, JSON.stringify(all));
    } catch {
        // Best-effort, as everywhere else in this module.
    }
}

/** Read one table's drafts, dropping anything malformed. */
export function readFilterDraft<T extends StoredFilterDraft>(
    project: string,
    section: string,
    column: string,
): T | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(filterDraftStorageKey(project, section));
        if (raw === null) return null;
        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
        const entry = (parsed as Record<string, unknown>)[column];
        return entry && typeof entry === "object" ? (entry as T) : null;
    } catch {
        return null;
    }
}

/**
 * Forget EVERY draft of one table — the search strings, the MIN–MAX pairs and the
 * instant windows of all its columns.
 *
 * «Сбросить все фильтры и сортировки» must leave nothing behind: the drafts are
 * remembered in `localStorage`, so clearing only the applied filters wiped the
 * view while the popovers kept their values and the reload brought them back.
 */
export function clearFilterDrafts(project: string, section: string): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.removeItem(filterDraftStorageKey(project, section));
    } catch {
        // Best-effort, as everywhere else in this module.
    }
}

/** Remember one column's draft; an empty draft removes its entry. */
export function writeFilterDraft(
    project: string,
    section: string,
    column: string,
    draft: StoredFilterDraft,
): void {
    if (typeof window === "undefined") return;
    try {
        const key = filterDraftStorageKey(project, section);
        const raw = window.localStorage.getItem(key);
        let all: Record<string, StoredFilterDraft> = {};
        if (raw !== null) {
            const parsed: unknown = JSON.parse(raw);
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                all = parsed as Record<string, StoredFilterDraft>;
            }
        }
        const empty =
            (draft.query ?? "") === "" &&
            (draft.min ?? null) === null &&
            (draft.max ?? null) === null &&
            (draft.dateMin ?? null) === null &&
            (draft.dateMax ?? null) === null;
        if (empty) delete all[column];
        else all[column] = draft;
        if (Object.keys(all).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(all));
    } catch {
        // A quota error or a disabled store leaves the popover working; only
        // the memory across opens is lost.
    }
}

/**
 * The «Перейти к строке/столбцу» draft: the two numbers last entered, kept per
 * table so reopening the popover continues where the user stopped.
 *
 * The key is `rc:goto:<project>:<section>`.
 */
export function readStoredGoTo(
    project: string,
    section: string,
): { row: number | null; column: number | null } | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(`rc:goto:${project}:${section}`);
        if (raw === null) return null;
        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
        const entry = parsed as { row?: unknown; column?: unknown };
        const toNumber = (value: unknown) =>
            typeof value === "number" && Number.isFinite(value) ? value : null;
        return { row: toNumber(entry.row), column: toNumber(entry.column) };
    } catch {
        return null;
    }
}

/** Remember the «Перейти к» numbers; both empty removes the entry. */
export function writeStoredGoTo(
    project: string,
    section: string,
    draft: { row: number | null; column: number | null },
): void {
    if (typeof window === "undefined") return;
    try {
        const key = `rc:goto:${project}:${section}`;
        if (draft.row === null && draft.column === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(draft));
    } catch {
        // Best-effort, as everywhere else in this module.
    }
}

/** The `localStorage` key of one table's sorting. */
export function columnSortingStorageKey(project: string, section: string): string {
    return `rc:sorting:${project}:${section}`;
}

/** Read one table's remembered sorting, dropping anything malformed. */
export function readStoredSorting(project: string, section: string): StoredSortColumn[] {
    if (typeof window === "undefined") return [];
    try {
        const raw = window.localStorage.getItem(columnSortingStorageKey(project, section));
        if (raw === null) return [];
        const parsed: unknown = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        const columns: StoredSortColumn[] = [];
        for (const entry of parsed) {
            const candidate = entry as Partial<StoredSortColumn> | null;
            if (!candidate || typeof candidate !== "object") continue;
            if (typeof candidate.prop !== "string" || candidate.prop === "") continue;
            if (candidate.order !== "asc" && candidate.order !== "desc") continue;
            columns.push({ prop: candidate.prop, order: candidate.order });
        }
        return columns;
    } catch {
        return [];
    }
}

/**
 * Remember one table's sorting. An EMPTY list removes the entry, so an unsorted
 * table leaves nothing behind — the same rule the filters follow.
 */
export function writeStoredSorting(
    project: string,
    section: string,
    columns: readonly StoredSortColumn[],
): void {
    if (typeof window === "undefined") return;
    try {
        const key = columnSortingStorageKey(project, section);
        if (columns.length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(columns));
    } catch {
        // A quota error or a disabled store leaves the table working; only the
        // memory across visits is lost.
    }
}

/**
 * Drop anything that is not a well-formed `ColumnFilter`.
 *
 * Storage is shared with the user's own edits and with older code versions, so
 * the input is untrusted: a hand-edited entry or a shape from a previous release
 * must leave the table working (unfiltered), not crash it on mount.
 */
export function reviveFilters(raw: string): Record<string, ColumnFilter> {
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return {};
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const valid: Record<string, ColumnFilter> = {};
    for (const [column, value] of Object.entries(parsed as Record<string, unknown>)) {
        const filter = value as Partial<ColumnFilter> | null;
        if (!filter || typeof filter !== "object") continue;
        if (!Array.isArray(filter.values) || !filter.values.every((v) => typeof v === "string")) {
            continue;
        }
        valid[column] = {
            column,
            values: filter.values,
            includeNulls: filter.includeNulls !== false,
        };
    }
    return valid;
}

function read(key: string): Record<string, ColumnFilter> {
    if (typeof window === "undefined") return {};
    try {
        const raw = window.localStorage.getItem(key);
        return raw === null ? {} : reviveFilters(raw);
    } catch {
        return {};
    }
}

function write(key: string, filters: Record<string, ColumnFilter>): void {
    if (typeof window === "undefined") return;
    try {
        if (Object.keys(filters).length === 0) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, JSON.stringify(filters));
    } catch {
        // A quota error or a disabled store leaves the table working for this
        // session — only the memory across visits is lost.
    }
}

/**
 * Header column filters of one table, keyed by column prop, remembered in
 * `localStorage` under `project` + `section`.
 *
 * The initializer reads storage LAZILY (never during SSR), and `section` may
 * change without unmounting the hook (`null` while the section is unknown, so
 * nothing is read or written until it resolves).
 *
 * Pass the result straight to `DataGrid`:
 * `filters={filters} onFiltersChange={setFilters}`.
 */
export function useGridColumnFilters(project: string, section: string | null) {
    const key = section === null ? null : columnFiltersStorageKey(project, section);
    const [filters, setFilters] = useState<Record<string, ColumnFilter>>(() => ({
        // Read on first render of this table. `key` is fixed for the lifetime of
        // the hook instance: a table is mounted per section.
        ...(key !== null ? read(key) : {}),
    }));

    // The table keeps its hook across an in-page section switch (the same table
    // component renders every dictionary), so the filters are RELOADED when the
    // section changes — otherwise «Категория работ» would open with the previous
    // section's filters, which describe columns it may not even have.
    //
    // The read happens DURING RENDER of the new key, not in an effect: an effect
    // would paint one frame of the OLD section's filters in the new table.
    const [loadedKey, setLoadedKey] = useState(key);
    if (key !== loadedKey) {
        setLoadedKey(key);
        setFilters(key !== null ? read(key) : {});
    }

    const update = useCallback(
        (next: Record<string, ColumnFilter>) => {
            // A filter that constrains nothing is NOT a filter (see
            // `isConstrainingFilter`): the server read it as «select no rows».
            const kept = Object.fromEntries(
                Object.entries(next).filter(([, filter]) => isConstrainingFilter(filter)),
            );
            setFilters(kept);
            if (key !== null) write(key, kept);
        },
        [key],
    );

    /** «Сбросить все фильтры и сортировки» — the strip's reset entry. */
    const clear = useCallback(() => {
        // The remembered DRAFTS go with the filters: a search string or a MIN–MAX
        // pair that survived the reset came back on every open (and a window that
        // had been applied came back on reload).
        clearFilterDrafts(project, section ?? "");
        update({});
    }, [project, section, update]);

    return { filters, setFilters: update, clearFilters: clear };
}
