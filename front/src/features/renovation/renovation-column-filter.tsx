"use client";

import { getRenovationPlanFacets } from "@/shared/api/renovation";
import { serverFacetSource, type ColumnFilter } from "@/shared/ui/column-filter-facets";
import { ColumnFilterPopover } from "@/shared/ui/column-filter-popover";

/**
 * The renovation plan's header filter — the general `ColumnFilterPopover` plus
 * the two things only the plan knows.
 *
 * Every table in the app shares the popover and the `FacetSource` seam; a
 * project then EXTENDS it with its own column semantics instead of forking it.
 * Here:
 *
 * 1. **Where the values live.** The plan holds up to ~250k distinct values per
 *    column in PostgreSQL, so the source is the paged `/plan/facets` endpoint,
 *    narrowed by the open version and the «Предприятие» filter. The values
 *    arrive ALREADY in the shape the popover expects — that is why the server
 *    contract was mirrored in `column-filter-facets.ts` in the first place.
 * 2. **«Идентификационный код» (`col_002`) is not a value list.** Every ID is
 *    unique, so listing them would be useless: the backend serves exactly two
 *    buckets — «Корректные» / «Некорректные» — and the row count next to
 *    «Выбрать все» already says everything a unique count would, so the
 *    suffix is off for that column.
 *
 * Both are additive: the selection mechanics, the master toggle, the «Пустые»
 * bucket, the paging and the commit footer all stay in the shared component.
 */
export const RENOVATION_ID_COLUMN = "col_002";

export function RenovationColumnFilterPopover({
    column,
    columnLabel,
    anchor,
    planId,
    asset,
    initial,
    defaultFilter,
    onApply,
    onClose,
}: {
    /** The plan field's db name (or the synthetic asset column prop). */
    column: string;
    columnLabel: string;
    anchor: DOMRect;
    /** The open version; omitted — the working plan. */
    planId?: string | undefined;
    /** The «Предприятие» panel filter the facets are counted within. */
    asset?: string | null | undefined;
    initial: ColumnFilter | null;
    defaultFilter?: ColumnFilter | null;
    onApply: (filter: ColumnFilter | null) => void;
    onClose: () => void;
}) {
    const facets = serverFacetSource((target, page) =>
        getRenovationPlanFacets(target, {
            planId,
            asset,
            offset: page.offset,
            limit: page.limit,
        }),
    );
    return (
        <ColumnFilterPopover
            column={column}
            columnLabel={columnLabel}
            anchor={anchor}
            facets={facets}
            showUniqueCount={column !== RENOVATION_ID_COLUMN}
            initial={initial}
            defaultFilter={defaultFilter ?? null}
            onApply={onApply}
            onClose={onClose}
        />
    );
}
