import type { GridViewStats } from "@/shared/grid/grid-types";

/** The loader size is filtered; the status denominator is the whole plan. */
export function renovationRowStats(
    filteredRows: number,
    totalRows: number,
    hasAssetFilter: boolean,
    activeFilterColumns?: ReadonlySet<string>,
    filteredColumns?: string[][],
): Pick<GridViewStats, "filteredRows" | "totalRows" | "filtersApplied" | "filteredColumns"> {
    return {
        filteredRows,
        totalRows,
        filtersApplied: hasAssetFilter || (activeFilterColumns?.size ?? 0) > 0,
        filteredColumns: filteredColumns ?? [],
    };
}
