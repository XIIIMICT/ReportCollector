import { useCallback, useMemo } from "react";

export interface RowSelectionState {
    [key: string]: boolean;
}

/**
 * Grid selection logic hook
 * Encapsulates row selection state and operations
 */
export function useGridSelection(
    selectedRows: RowSelectionState,
    onSelectionChange: (
        updater: RowSelectionState | ((prev: RowSelectionState) => RowSelectionState),
    ) => void,
    totalRows: number,
) {
    const selectedCount = useMemo(() => Object.keys(selectedRows).length, [selectedRows]);

    const isAllSelected = useMemo(
        () => totalRows > 0 && selectedCount === totalRows,
        [selectedCount, totalRows],
    );

    const isPartiallySelected = useMemo(
        () => selectedCount > 0 && selectedCount < totalRows,
        [selectedCount, totalRows],
    );

    const selectAll = useCallback(() => {
        const allSelected: RowSelectionState = {};
        for (let i = 0; i < totalRows; i++) {
            allSelected[i] = true;
        }
        onSelectionChange(allSelected);
    }, [totalRows, onSelectionChange]);

    const deselectAll = useCallback(() => {
        onSelectionChange({});
    }, [onSelectionChange]);

    const toggleAll = useCallback(() => {
        if (isAllSelected || isPartiallySelected) {
            deselectAll();
        } else {
            selectAll();
        }
    }, [isAllSelected, isPartiallySelected, selectAll, deselectAll]);

    const toggleRow = useCallback(
        (rowId: string) => {
            onSelectionChange((prev: RowSelectionState) => {
                const next = { ...prev };
                if (next[rowId]) {
                    delete next[rowId];
                } else {
                    next[rowId] = true;
                }
                return next;
            });
        },
        [onSelectionChange],
    );

    const selectRange = useCallback(
        (startId: string, endId: string) => {
            const start = Number.parseInt(startId, 10);
            const end = Number.parseInt(endId, 10);
            const [from, to] = start < end ? [start, end] : [end, start];

            onSelectionChange((prev: RowSelectionState) => {
                const next = { ...prev };
                for (let i = from; i <= to; i++) {
                    next[i] = true;
                }
                return next;
            });
        },
        [onSelectionChange],
    );

    const getSelectedRowIds = useCallback(() => {
        return Object.keys(selectedRows).filter((key) => selectedRows[key]);
    }, [selectedRows]);

    const isRowSelected = useCallback((rowId: string) => !!selectedRows[rowId], [selectedRows]);

    return {
        selectedCount,
        isAllSelected,
        isPartiallySelected,
        selectAll,
        deselectAll,
        toggleAll,
        toggleRow,
        selectRange,
        getSelectedRowIds,
        isRowSelected,
    };
}
