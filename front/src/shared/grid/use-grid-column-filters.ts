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

/** Drop anything that is not a well-formed `ColumnFilter`. */
function reviveFilters(raw: string): Record<string, ColumnFilter> {
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
 * Pass the result straight to `RawMaterialsGrid`:
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
            setFilters(next);
            if (key !== null) write(key, next);
        },
        [key],
    );

    /** «Сбросить все фильтры и сортировки» — the strip's reset entry. */
    const clear = useCallback(() => update({}), [update]);

    return { filters, setFilters: update, clearFilters: clear };
}
