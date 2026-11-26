/**
 * Pure geometry of the renovation plan's overlay header. Split out of
 * `renovation-grid.tsx`: only numbers, maps and field descriptors go in, so
 * these helpers are independent of the grid instance, refs and the DOM.
 */

/** Top edge of every header level; the last entry is the whole header block. */
export function computeLevelOffsets(levelHeights: readonly number[], rowHeight: number): number[] {
    const offsets: number[] = [0];
    let acc = 0;
    for (const height of levelHeights) {
        acc += (height * rowHeight) / 36;
        offsets.push(acc);
    }
    return offsets;
}

export type ColumnOffsets = {
    /** Left edge of each column, keyed by its prop. */
    offsets: Map<string, number>;
    /** Left edge just past the last column (the total content width from `startX`). */
    totalWidth: number;
};

/**
 * Left edge of every visible column, starting at `startX` (the row-header
 * width plus the pinned synthetic asset column). `widthOf` is supplied by the
 * caller, which owns the live resize overrides; this function only walks the
 * fields in order and accumulates the widths.
 */
export function measureColumnOffsets<T extends { dbName: string }>(
    fields: readonly T[],
    widthOf: (field: T) => number,
    startX: number,
): ColumnOffsets {
    const offsets = new Map<string, number>();
    let acc = startX;
    for (const field of fields) {
        offsets.set(field.dbName, acc);
        acc += widthOf(field);
    }
    return { offsets, totalWidth: acc };
}
