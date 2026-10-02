/**
 * Grid utilities for working with revo-grid instances
 */

/**
 * Get visible rows from grid after filtering/sorting
 * Falls back to original rows if grid is not mounted or throws
 */
export async function getVisibleRows<TRow>(
    gridRef: { current: HTMLRevoGridElement | null },
    fallbackRows: TRow[],
): Promise<TRow[]> {
    const grid = gridRef.current;
    if (!grid) return fallbackRows;
    try {
        return (await grid.getVisibleSource("rgRow")) as TRow[];
    } catch {
        return fallbackRows;
    }
}

/**
 * Apply a cell range selection to the grid
 * Includes refresh to force overlay redraw (Stencil doesn't auto-repaint)
 */
export async function applyGridRange(
    gridRef: { current: HTMLRevoGridElement | null },
    start: { x: number; y: number },
    end: { x: number; y: number },
): Promise<void> {
    const grid = gridRef.current;
    if (!grid) return;
    await grid.setCellsFocus(start, end);
    await grid.refresh();
}

/**
 * Clear grid selection and force overlay redraw
 * Also clears pendingColumnFocusRestore to prevent stale selection resurrection
 */
export async function clearGridSelection(gridRef: {
    current: HTMLRevoGridElement | null;
}): Promise<void> {
    const grid = gridRef.current;
    if (!grid) return;
    await grid.clearFocus();
    await grid.refresh();
    // Clear the cached focus that revo-grid would restore on next column rebuild
    (grid as unknown as { pendingColumnFocusRestore?: unknown }).pendingColumnFocusRestore =
        undefined;
}

/**
 * Scroll grid to a specific cell
 */
export async function scrollToCell(
    gridRef: { current: HTMLRevoGridElement | null },
    row: number,
    column: number,
): Promise<void> {
    const grid = gridRef.current;
    if (!grid) return;
    await grid.scrollToCoordinate({ x: column, y: row });
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
