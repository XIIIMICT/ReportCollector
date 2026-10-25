"use client";

import { useCallback, useState } from "react";

import type { ColumnFilter } from "@/shared/ui/column-filter-facets";

/**
 * Header column filters of a client-side grid, keyed by column prop.
 *
 * They are NOT kept in the URL. Only the PANEL filters belong in the address:
 * they are few, they say which slice of the data the page is showing, and a
 * shared link should reproduce them. Header filters are many, each carrying the
 * values it keeps (a repair-code column alone can hold hundreds of IDs), so the
 * address is not a place for them — it would grow past what browsers and proxies
 * accept, and they are working state, not a property of the page.
 *
 * The state therefore lives for as long as the table is mounted: an in-page
 * section switch keeps it (each table owns its own hook instance), and a reload
 * starts clean — the same rule the selection and the undo history follow.
 *
 * Pass the result straight to `RawMaterialsGrid`:
 * `filters={filters} onFiltersChange={setFilters}`.
 */
export function useGridColumnFilters() {
    const [filters, setFilters] = useState<Record<string, ColumnFilter>>({});

    const update = useCallback((next: Record<string, ColumnFilter>) => {
        setFilters(next);
    }, []);

    /** «Сбросить все фильтры и сортировки» — the strip's reset entry. */
    const clear = useCallback(() => update({}), [update]);

    return { filters, setFilters: update, clearFilters: clear };
}
