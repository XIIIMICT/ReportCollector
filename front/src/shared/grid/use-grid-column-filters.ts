"use client";

import { useCallback, useState } from "react";

import type { ColumnFilter } from "@/shared/ui/column-filter-facets";
import { readWorkspaceQuery, writeWorkspaceQuery } from "@/shared/ui/query-state";

/**
 * Header column filters of a client-side grid, keyed by column prop.
 *
 * The filters live in the URL (`?filters=…`) for the same reason the plan's do:
 * a reload, a shared link or a section switch must not silently drop what the
 * user narrowed the table to. The shape is the plan's (`ColumnFilter`), so a
 * filter reads the same wherever it came from.
 *
 * Pass the result straight to `RawMaterialsGrid`:
 * `filters={filters} onFiltersChange={setFilters}`.
 */
export function useGridColumnFilters() {
    const [filters, setFilters] = useState<Record<string, ColumnFilter>>(() => {
        const raw = readWorkspaceQuery("filters");
        if (!raw) return {};
        try {
            const parsed: unknown = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
            // A hand-edited or stale URL must not crash the table: each entry is
            // checked, and anything malformed is simply dropped.
            const valid: Record<string, ColumnFilter> = {};
            for (const [column, value] of Object.entries(parsed as Record<string, unknown>)) {
                const filter = value as Partial<ColumnFilter> | null;
                if (!filter || typeof filter !== "object") continue;
                if (
                    !Array.isArray(filter.values) ||
                    !filter.values.every((v) => typeof v === "string")
                )
                    continue;
                valid[column] = {
                    column,
                    values: filter.values,
                    includeNulls: filter.includeNulls !== false,
                };
            }
            return valid;
        } catch {
            return {};
        }
    });

    const update = useCallback((next: Record<string, ColumnFilter>) => {
        setFilters(next);
        writeWorkspaceQuery("filters", Object.keys(next).length ? JSON.stringify(next) : null);
    }, []);

    /** «Сбросить все фильтры и сортировки» — the strip's reset entry. */
    const clear = useCallback(() => update({}), [update]);

    return { filters, setFilters: update, clearFilters: clear };
}
