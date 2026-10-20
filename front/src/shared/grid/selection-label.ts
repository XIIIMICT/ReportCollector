import type { ColumnDescriptor } from "@/shared/grid/grid-config";

/**
 * The footer selection text of the shared grid — the same shape the renovation
 * plan reports: the column is named by its NUMBER in the form
 * («столбец 2, строка 3»), and the full header chain travels separately as
 * `detail` for the status tooltip.
 *
 * Kept in its own module (no revo-grid import) so it stays testable in
 * happy-dom: `raw-materials-grid.tsx` pulls in the revogrid loader, which
 * touches `HTMLElement` at import time.
 */

export type SelectionRange = { x: number; y: number; x1: number; y1: number };

type ColumnLike = { prop?: unknown; name?: unknown };

/** Quote the whole header hierarchy, leaving coordinates outside the quotes. */
export function selectionDetailForColumn(
    chain: readonly string[],
    rowText: string,
    columnNumber?: number | null,
): string | null {
    if (chain.length === 0) return null;
    const suffix = columnNumber != null ? ` (столбец ${columnNumber})` : "";
    return `«${chain.join(" → ")}»${suffix}, ${rowText}`;
}

function columnLabel(
    column: ColumnLike | undefined,
    descriptorByKey: Map<string, ColumnDescriptor>,
): string | null {
    if (!column) return null;
    const descriptor = descriptorByKey.get(String(column.prop));
    if (descriptor) {
        return `«${[...(descriptor.path ?? []), descriptor.label].join(" / ")}»`;
    }
    const fallback = String(column.name ?? column.prop ?? "");
    return fallback ? `«${fallback}»` : null;
}

/**
 * Describe the selected range for the footer.
 *
 * `descriptorByKey` is the visible-descriptor map in view order, so the number
 * is the ordinal the user sees — hidden or permission-filtered columns don't
 * shift the rest. A column without a descriptor (a service column) has no
 * number and falls back to its quoted label. null — no selection.
 */
export function describeSelection(
    range: SelectionRange | null,
    columns: ReadonlyArray<ColumnLike | undefined>,
    descriptorByKey: Map<string, ColumnDescriptor>,
    rowCount: number,
): { label: string; detail: string | null } | null {
    if (!range || range.x1 < range.x || range.y1 < range.y) return null;
    const order = [...descriptorByKey.keys()];
    const infoAt = (
        index: number,
    ): { number: number | null; label: string; chain: string | null } => {
        const prop = columns[index]?.prop;
        const key = prop === undefined ? null : String(prop);
        const descriptor = key === null ? undefined : descriptorByKey.get(key);
        const position = key === null ? -1 : order.indexOf(key);
        return {
            number: position >= 0 ? position + 1 : null,
            label: columnLabel(columns[index], descriptorByKey) ?? "",
            chain:
                descriptor && key !== null
                    ? [...(descriptor.path ?? []), descriptor.label].join(" → ")
                    : null,
        };
    };
    const firstColumn = infoAt(range.x);
    const lastColumn = infoAt(range.x1);
    const columnText =
        range.x === range.x1
            ? firstColumn.number === null
                ? `столбец ${firstColumn.label}`
                : `столбец ${firstColumn.number}`
            : `столбцы ${firstColumn.number ?? firstColumn.label}–${lastColumn.number ?? lastColumn.label}`;
    const startRow = range.y + 1;
    const endRow = Math.min(range.y1 + 1, rowCount);
    const rowText = startRow === endRow ? `строка ${startRow}` : `строки ${startRow}–${endRow}`;
    const detail =
        range.x === range.x1 && firstColumn.chain
            ? selectionDetailForColumn([firstColumn.chain], rowText)
            : null;
    return { label: `${columnText}, ${rowText}`, detail };
}

/** The footer label only (presence and the status line keep the short form). */
export function selectionLabelForRange(
    range: SelectionRange | null,
    columns: ReadonlyArray<ColumnLike | undefined>,
    descriptorByKey: Map<string, ColumnDescriptor>,
    rowCount: number,
): string | null {
    return describeSelection(range, columns, descriptorByKey, rowCount)?.label ?? null;
}
