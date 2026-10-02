import { useCallback, useMemo, useState } from "react";

export interface ColumnFilterConfig {
    id: string;
    type: "text" | "select" | "number" | "date" | "boolean";
    label: string;
    options?: Array<{ value: string; label: string }>;
    placeholder?: string;
}

export interface ColumnFilter {
    id: string;
    value: unknown;
}

export type ColumnFiltersState = ColumnFilter[];

/**
 * Grid column filter logic hook
 * Encapsulates column filtering state and operations
 */
export function useGridColumnFilter(
    columns: ColumnFilterConfig[],
    initialFilters: ColumnFiltersState = [],
) {
    const [filters, setFilters] = useState<ColumnFiltersState>(initialFilters);
    const [searchQuery, setSearchQuery] = useState("");

    const activeFilterCount = useMemo(() => filters.length, [filters]);

    const setColumnFilter = useCallback((columnId: string, value: unknown) => {
        setFilters((prev: ColumnFiltersState) => {
            const existing = prev.findIndex((f: ColumnFilter) => f.id === columnId);
            if (existing !== -1) {
                if (value === undefined || value === "" || value === null) {
                    return prev.filter((f: ColumnFilter) => f.id !== columnId);
                }
                const next = [...prev];
                next[existing] = { id: columnId, value };
                return next;
            }
            if (value !== undefined && value !== "" && value !== null) {
                return [...prev, { id: columnId, value }];
            }
            return prev;
        });
    }, []);

    const clearColumnFilter = useCallback((columnId: string) => {
        setFilters((prev: ColumnFiltersState) =>
            prev.filter((f: ColumnFilter) => f.id !== columnId),
        );
    }, []);

    const clearAllFilters = useCallback(() => {
        setFilters([]);
        setSearchQuery("");
    }, []);

    const getColumnFilter = useCallback(
        (columnId: string) => {
            const filter = filters.find((f: ColumnFilter) => f.id === columnId);
            return filter?.value;
        },
        [filters],
    );

    const hasActiveFilters = useMemo(
        () => activeFilterCount > 0 || searchQuery.length > 0,
        [activeFilterCount, searchQuery],
    );

    const filterByColumn = useCallback(
        <T extends Record<string, unknown>>(data: T[], columnId: string, value: unknown): T[] => {
            if (!value) return data;

            const column = columns.find((c) => c.id === columnId);
            if (!column) return data;

            switch (column.type) {
                case "text":
                    return data.filter((row) => {
                        const cellValue = String(row[columnId] ?? "").toLowerCase();
                        const filterValue = String(value).toLowerCase();
                        return cellValue.includes(filterValue);
                    });

                case "select":
                    return data.filter((row) => row[columnId] === value);

                case "number": {
                    const numValue = Number(value);
                    if (Number.isNaN(numValue)) return data;
                    return data.filter((row) => Number(row[columnId]) === numValue);
                }

                case "boolean":
                    return data.filter((row) => Boolean(row[columnId]) === Boolean(value));

                case "date": {
                    const dateValue = new Date(String(value));
                    if (Number.isNaN(dateValue.getTime())) return data;
                    return data.filter((row) => {
                        const cellDate = new Date(String(row[columnId] ?? ""));
                        return cellDate.toDateString() === dateValue.toDateString();
                    });
                }

                default:
                    return data;
            }
        },
        [columns],
    );

    const applyFilters = useCallback(
        <T extends Record<string, unknown>>(data: T[]): T[] => {
            let filtered = data;

            for (const filter of filters) {
                filtered = filterByColumn(filtered, filter.id, filter.value);
            }

            if (searchQuery) {
                const query = searchQuery.toLowerCase();
                filtered = filtered.filter((row) =>
                    Object.values(row).some((val) => String(val).toLowerCase().includes(query)),
                );
            }

            return filtered;
        },
        [filters, searchQuery, filterByColumn],
    );

    return {
        filters,
        searchQuery,
        activeFilterCount,
        hasActiveFilters,
        setColumnFilter,
        clearColumnFilter,
        clearAllFilters,
        getColumnFilter,
        setSearchQuery,
        applyFilters,
    };
}
