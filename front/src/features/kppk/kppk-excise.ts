import type { CellUpdate } from "@/shared/api/raw-materials";

/**
 * Fan an edit of the merged «Акциз» cell out to every market row of the product.
 *
 * The excise is ONE figure per product, but it is physically stored on each of
 * the product's rows (РФ / ДЗ / БЗ — the copies have no separate product-level
 * table). The grid shows it as a single merged cell in the block's first row,
 * so editing it must write the same value to the rows the user cannot see;
 * otherwise the hidden rows keep the old rate and sorting the table (which
 * un-merges the block) exposes the disagreement.
 *
 * Non-excise updates pass through untouched. Rows are matched by PRODUCT NAME
 * inside the same block the edited row belongs to — the view is ordered by
 * product, so a run of equal names is exactly the block the merge covers.
 */
export function expandExcise(
    updates: CellUpdate[],
    rows: ReadonlyArray<{ id: string; product: string }>,
    exciseKey: string,
    allowed: (product: string) => boolean = () => true,
): CellUpdate[] {
    if (!updates.some((update) => update.field === exciseKey)) return updates;
    const byId = new Map(rows.map((row) => [row.id, row]));
    const expanded: CellUpdate[] = [];
    for (const update of updates) {
        if (update.field !== exciseKey) {
            expanded.push(update);
            continue;
        }
        const row = byId.get(update.rowId);
        // An excise OUTSIDE the product's scope is dropped outright: the cell is
        // locked on screen, and this guards a stale page or a paste that
        // bypassed the lock. The server rejects it as well.
        if (!row || !allowed(row.product)) continue;
        for (const sibling of rows) {
            if (sibling.product !== row.product) continue;
            expanded.push({ ...update, rowId: sibling.id });
        }
    }
    return expanded;
}
