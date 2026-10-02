/**
 * Grid selection utilities for range and row selection management
 */

import type { RangeArea } from "@revolist/revogrid";

/**
 * Check if an event target is within the grid element
 */
export function isGridTarget(event: Event, grid: HTMLElement): boolean {
    const target = event.target as HTMLElement | null;
    if (!target) return false;
    return grid.contains(target);
}

/**
 * Get cell keys for a selection range
 */
export function selectionCellKeys<TRow extends { id: string }>(
    range: RangeArea,
    rows: TRow[],
    columnProps: string[],
): string[] {
    const keys: string[] = [];
    for (let y = range.y; y <= Math.min(range.y1, rows.length - 1); y++) {
        const row = rows[y];
        if (!row) continue;
        for (let x = range.x; x <= Math.min(range.x1, columnProps.length - 1); x++) {
            const prop = columnProps[x];
            if (!prop) continue;
            keys.push(`${row.id}/${prop}`);
        }
    }
    return keys;
}

/**
 * Get cell keys for selected rows
 */
export function selectionRowCellKeys<TRow extends { id: string }>(
    rows: TRow[],
    startRow: number,
    endRow: number,
    columnProps: string[],
): string[] {
    const keys: string[] = [];
    for (let y = startRow; y <= Math.min(endRow, rows.length - 1); y++) {
        const row = rows[y];
        if (!row) continue;
        for (const prop of columnProps) {
            keys.push(`${row.id}/${prop}`);
        }
    }
    return keys;
}

/**
 * Build a human-readable label for the current selection
 */
export function selectionLabelForRange<TDescriptor extends { key: string; label: string }>(
    range: RangeArea | null,
    columns: Array<{ prop: string }>,
    descriptorMap: Map<string, TDescriptor>,
    rowCount: number,
): string | null {
    if (!range) return null;

    const height = range.y1 - range.y + 1;
    const width = range.x1 - range.x + 1;

    if (height === 1 && width === 1) {
        const prop = columns[range.x]?.prop;
        if (!prop) return null;
        const descriptor = descriptorMap.get(String(prop));
        return descriptor ? descriptor.label : null;
    }

    if (height === rowCount) {
        // Full column(s) selected
        if (width === 1) {
            const prop = columns[range.x]?.prop;
            if (!prop) return null;
            const descriptor = descriptorMap.get(String(prop));
            return descriptor ? `${descriptor.label} (весь столбец)` : null;
        }
        return `${width} столбцов, ${height} строк`;
    }

    return `${width} × ${height}`;
}

/**
 * Check if the active element is a text input or editor
 */
export function isActiveElementTextEditor(): boolean {
    const active = document.activeElement as HTMLElement | null;
    if (!active) return false;
    return active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable;
}

/**
 * Handle shift-click range selection
 */
export function calculateShiftClickRange(
    anchorIndex: number | null,
    clickedIndex: number,
): { start: number; end: number } {
    const anchor = anchorIndex ?? clickedIndex;
    return {
        start: Math.min(anchor, clickedIndex),
        end: Math.max(anchor, clickedIndex),
    };
}

/**
 * Handle ctrl/cmd-click toggle selection
 */
export function toggleSelection<T>(
    currentSet: Set<T>,
    item: T,
): { next: Set<T>; wasRemoved: boolean } {
    const next = new Set(currentSet);
    const wasRemoved = next.has(item);
    if (wasRemoved) {
        next.delete(item);
    } else {
        next.add(item);
    }
    return { next, wasRemoved };
}
