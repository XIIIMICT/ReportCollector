import type { HiddenColumnEntry } from "./hidden-columns-list";

export type GridViewStats = {
    totalRows: number;
    /** Rows matching the current filters (non-placeholder, non-deleted). */
    filteredRows: number;
    totalCols: number;
    /** Whether any column filter is currently applied. */
    filtersApplied: boolean;
    /**
     * The columns an applied filter narrows, each as its full header CHAIN —
     * group first, leaf last (a grouped column reads «Иерархия → Лист»), which
     * is what the status tooltip lists. Empty when nothing is filtered.
     */
    filteredColumns: string[][];
    /**
     * The columns the table is SORTED by, in the order RevoGrid applies them —
     * each as its header chain (like `filteredColumns`) plus the direction.
     * Empty when the table is unsorted.
     */
    sortedColumns: Array<{ path: string[]; order: "asc" | "desc" }>;
    /** Fixed (non-calculated) columns — not editable. */
    immutableCols: number;
    /** Calculated columns. */
    calculatedCols: number;
    /** Editable columns. */
    editableCols: number;
    /**
     * Columns hidden by the user, in table order — the same numbered list the
     * expand button's tooltip shows. Service columns appear too while their
     * project-wide toggle is off.
     */
    hiddenColumns?: HiddenColumnEntry[];
};
