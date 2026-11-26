/**
 * The two handlers «Сбросить все фильтры и сортировок» / «Сбросить ширину столбцов»
 * publish from a mounted grid to the workspace view strip.
 *
 * The client-grid sections wire the same pair inline in a `useEffect`. Only the
 * grid ref and the section's `clearFilters` differ — the reset contract is
 * identical, so this builder stays pure: each caller keeps its own effect and
 * its exact dependency list, only the object body moves here.
 */

/** The part of `DataGridHandle` the strip's reset needs. */
type ResettableGrid = {
    resetColumnWidths: () => void;
    resetFiltersSorting: () => Promise<void>;
};

export type GridResetState = {
    resetColumnWidths: () => void;
    resetFiltersSorting: () => void;
};

/**
 * Build the published handlers. Both close over the CURRENT refs/hooks; a null
 * grid (the section has not mounted one yet) is a safe no-op, as it was inline.
 */
export function buildGridResetState(
    gridRef: { current: ResettableGrid | null },
    clearFilters: () => void,
): GridResetState {
    return {
        resetColumnWidths: () => gridRef.current?.resetColumnWidths(),
        resetFiltersSorting: () => {
            clearFilters();
            void gridRef.current?.resetFiltersSorting();
        },
    };
}
