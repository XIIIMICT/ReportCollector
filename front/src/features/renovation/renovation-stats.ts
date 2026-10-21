import type { GridViewStats } from "@/shared/grid/raw-materials-grid";

/** The loader size is filtered; the status denominator is the whole plan. */
export function renovationRowStats(
    filteredRows: number,
    totalRows: number,
    hasAssetFilter: boolean,
    activeFilterColumns?: ReadonlySet<string>,
): Pick<GridViewStats, "filteredRows" | "totalRows" | "filtersApplied"> {
    return {
        filteredRows,
        totalRows,
        filtersApplied: hasAssetFilter || (activeFilterColumns?.size ?? 0) > 0,
    };
}
