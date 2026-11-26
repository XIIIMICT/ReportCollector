/**
 * Grid selection utilities for range and row selection management
 */

import type { RangeArea } from "@revolist/revogrid";

/** A column source accepted by the shared key helpers: a bare prop or a column object. */
export type SelectionColumnProp = string | { prop?: unknown };

/** Resolve the persistence key of one cell; `undefined` means "no key, skip it". */
export type CellKeyAccessor<TRow> = (row: TRow, prop: string) => string | undefined;

function columnPropOf(column: SelectionColumnProp | undefined): string | undefined {
    if (column === undefined) return undefined;
    if (typeof column === "string") return column;
    return column.prop == null ? undefined : String(column.prop);
}

/** Legacy default: `${row.id}/${prop}` for rows that carry an id. */
function defaultCellKey<TRow extends { id: string }>(row: TRow, prop: string): string {
    return `${row.id}/${prop}`;
}

/**
 * Targets that must keep the grid selection on an outside pointerdown.
 *
 * Both grids drop the row/cell selection when the pointer goes to the page, but
 * the overlays the grid itself opens (popovers, dialogs) and the table action
 * bar all operate ON the selection. Clearing on their pointerdown would disable
 * the pressed action mid-press (React Aria cancels a button that becomes
 * disabled before pointerup), so «Заменить запись» and «Удалить строку» would
 * silently do nothing. The action bar marks itself with
 * `data-grid-selection-keep`.
 */
const SELECTION_KEEPING_SELECTOR =
    '[data-slot="popover-dialog"], [role="dialog"], [role="alertdialog"], [data-grid-selection-keep]';

/** Whether a pointerdown target leaves the current grid selection in place. */
export function keepsGridSelection(target: Element): boolean {
    return target.closest(SELECTION_KEEPING_SELECTOR) !== null;
}

/**
 * Check if an event target is within the grid element
 */
export function isGridTarget(event: Event, grid: HTMLElement): boolean {
    const target = event.target as HTMLElement | null;
    if (!target) return false;
    return grid.contains(target);
}

/**
 * Cell keys for a selection range, in row-major order.
 *
 * `getCellKey` resolves a cell's persistence key — pass a `canonicalCells`
 * accessor when keys must stay identical across different visible column sets.
 * Rows and columns are clamped to the source; a null or reversed range yields
 * no keys.
 */
export function selectionCellKeys<TRow extends { id: string }>(
    range: RangeArea | null,
    rows: TRow[],
    columns: ReadonlyArray<SelectionColumnProp>,
    getCellKey: CellKeyAccessor<TRow> = defaultCellKey,
): string[] {
    if (!range || range.x1 < range.x || range.y1 < range.y) return [];
    const keys: string[] = [];
    for (let y = range.y; y <= Math.min(range.y1, rows.length - 1); y++) {
        const row = rows[y];
        if (!row) continue;
        for (let x = range.x; x <= Math.min(range.x1, columns.length - 1); x++) {
            const prop = columnPropOf(columns[x]);
            if (!prop) continue;
            const key = getCellKey(row, prop);
            if (key !== undefined) keys.push(key);
        }
    }
    return keys;
}

/**
 * Cell keys for selected rows, across all columns in column order.
 */
export function selectionRowCellKeys<TRow extends { id: string }>(
    rows: TRow[],
    startRow: number,
    endRow: number,
    columns: ReadonlyArray<SelectionColumnProp>,
    getCellKey: CellKeyAccessor<TRow> = defaultCellKey,
): string[] {
    const keys: string[] = [];
    for (let y = startRow; y <= Math.min(endRow, rows.length - 1); y++) {
        const row = rows[y];
        if (!row) continue;
        for (const column of columns) {
            const prop = columnPropOf(column);
            if (!prop) continue;
            const key = getCellKey(row, prop);
            if (key !== undefined) keys.push(key);
        }
    }
    return keys;
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
