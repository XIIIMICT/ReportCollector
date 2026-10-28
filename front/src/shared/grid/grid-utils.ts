/** Compatibility helpers for callers that still work with a raw grid ref. */

import { createRevoGridAdapter, type GridRef } from "./revo-grid-adapter";

/**
 * Get visible rows from grid after filtering/sorting
 * Falls back to original rows if grid is not mounted or throws
 */
export async function getVisibleRows<TRow>(
    gridRef: GridRef,
    fallbackRows: TRow[],
): Promise<TRow[]> {
    return createRevoGridAdapter<TRow>(gridRef).getVisibleRows(fallbackRows);
}

/**
 * Apply a cell range selection to the grid
 * Includes refresh to force overlay redraw (Stencil doesn't auto-repaint)
 */
export async function applyGridRange(
    gridRef: GridRef,
    start: { x: number; y: number },
    end: { x: number; y: number },
): Promise<void> {
    await createRevoGridAdapter(gridRef).setRange(start, end);
}

/**
 * Clear grid selection and force overlay redraw
 * Delegates to the adapter, which also clears RevoGrid's stale focus restoration
 * state before the next column rebuild.
 */
export async function clearGridSelection(gridRef: {
    current: HTMLRevoGridElement | null;
}): Promise<void> {
    await createRevoGridAdapter(gridRef).clearRange();
}

/**
 * Scroll grid to a specific cell
 */
export async function scrollToCell(
    gridRef: { current: HTMLRevoGridElement | null },
    row: number,
    column: number,
): Promise<void> {
    await createRevoGridAdapter(gridRef).scrollToCell(row, column);
}

/**
 * Get grid's current scroll position
 */
export function getGridScroll(gridRef: {
    current: HTMLRevoGridElement | null;
}): { left: number; top: number } | null {
    const grid = gridRef.current;
    if (!grid) return null;
    const viewport = grid.querySelector(".rgRow") as HTMLElement | null;
    if (!viewport) return null;
    return { left: viewport.scrollLeft, top: viewport.scrollTop };
}

/**
 * Set grid's scroll position
 */
export function setGridScroll(
    gridRef: { current: HTMLRevoGridElement | null },
    scroll: { left: number; top: number },
): void {
    const grid = gridRef.current;
    if (!grid) return;
    const viewport = grid.querySelector(".rgRow") as HTMLElement | null;
    if (!viewport) return;
    viewport.scrollLeft = scroll.left;
    viewport.scrollTop = scroll.top;
}
