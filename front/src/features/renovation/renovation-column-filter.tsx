"use client";

import { getRenovationPlanFacets, type RenovationColumnFilter } from "@/shared/api/renovation";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import {
    renderDateText,
    renderFixedDecimalsText,
    renderNumberText,
} from "@/shared/grid/grid-config";
import {
    serverFacetSource,
    type ColumnFilter,
    type FacetOrder,
} from "@/shared/ui/column-filter-facets";
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
 * 2. **«Идентификационный код» (`work_or_repair_code`).** The backend keeps the two
 *    validation buckets first, then serves every concrete code alphabetically.
 *    The shared text search narrows this list like any other text column.
 *
 * Both are additive: the selection mechanics, the master toggle, the «Пустые»
 * bucket, the paging and the commit footer all stay in the shared component.
 */
export const RENOVATION_ID_COLUMN = "work_or_repair_code";

export function RenovationColumnFilterPopover({
    column,
    columnLabel,
    anchor,
    planId,
    asset,
    filters,
    order = "text",
    dateOnly = false,
    initial,
    defaultFilter,
    storageScope,
    instant,
    decimals,
    onApply,
    onClose,
    onInvalidQuery,
}: {
    /** The plan field's db name (or the synthetic asset column prop). */
    column: string;
    columnLabel: string;
    anchor: DOMRect;
    /** The open version; omitted — the working plan. */
    planId?: string | undefined;
    /** The «Предприятие» panel filter the facets are counted within. */
    asset?: string | null | undefined;
    filters: RenovationColumnFilter[];
    /** How the column is searched: `number` gives the MIN–MAX pair. */
    order?: FacetOrder;
    /** When `order` is `date`: a calendar day, not a timestamp. */
    dateOnly?: boolean;
    initial: ColumnFilter | null;
    defaultFilter?: ColumnFilter | null;
    /** Where the search draft of this column is remembered (see the popover). */
    storageScope?: { project: string; section: string } | undefined;
    /** A `timestamptz` column: the popover asks for a window instead of values. */
    instant?: boolean | undefined;
    /**
     * Digits after the decimal mark of a `number` column (`decimals` of the
     * field): the option list then reads exactly like the cells («1,50»).
     */
    decimals?: number | undefined;
    onApply: (filter: ColumnFilter | null) => void;
    onClose: () => void;
    /** Remove a stale column state when the backend rejects a facet query. */
    onInvalidQuery?: (reason: unknown) => void;
}) {
    // The plan's facet values arrive RAW: the digits of a number column, the ISO
    // string of a date. The server never formats them — formatting there split
    // one number into several buckets, printed seven-digit values as
    // "1.00001e+06", and left dates as «2027-01-01» while the cells print
    // «01.01.2027». The client applies the cells' OWN rule, so the filter list
    // offers exactly what the column shows and what the applied filter is
    // compared against.
    const render =
        order === "number"
            ? decimals === undefined
                ? renderNumberText
                : (raw: string) => renderFixedDecimalsText(raw, decimals)
            : order === "date"
              ? (raw: string) => renderDateText(raw.slice(0, 10))
              : null;
    const facets = serverFacetSource(async (target, page) => {
        let window;
        try {
            window = await getRenovationPlanFacets(target, {
                planId,
                asset,
                filters,
                offset: page.offset,
                limit: page.limit,
                search: page.search,
            });
        } catch (reason) {
            if (reason instanceof ReportCollectorApiError && reason.status === 422) {
                onInvalidQuery?.(reason);
            }
            throw reason;
        }
        if (render === null) return window;
        return {
            ...window,
            values: window.values.map((entry) =>
                entry.value === null ? entry : { value: render(entry.value), count: entry.count },
            ),
        };
    });
    return (
        <ColumnFilterPopover
            column={column}
            columnLabel={columnLabel}
            anchor={anchor}
            facets={facets}
            order={order}
            dateOnly={dateOnly}
            forceSearchable={column === RENOVATION_ID_COLUMN}
            initial={initial}
            defaultFilter={defaultFilter ?? null}
            storageScope={storageScope}
            instant={instant}
            onApply={onApply}
            onClose={onClose}
        />
    );
}
