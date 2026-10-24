/**
 * The column filter's data contract, shared by EVERY table in the app.
 *
 * The popover (`column-filter-popover.tsx`) only ever asks its source for «the
 * distinct values of column X, window [offset, limit)» — it never learns where
 * they come from. Two kinds of source implement the contract:
 *
 * - `serverFacetSource` — the renovation plan. Hundreds of thousands of
 *   distinct values live in PostgreSQL, so the list is paged over the wire
 *   (`GET /plan/facets`, imported by the owner and injected here — this module
 *   deliberately knows nothing about the plan's API or its columns).
 * - `rowFacetSource` — everything else. Those grids already hold their rows in
 *   memory, so the same buckets are computed here.
 *
 * The contract mirrors the backend's `read_plan_facets`
 * (`back/src/renovation/service.py`) on purpose — the UI must not be able to
 * tell the two apart:
 * - `values` are DISPLAY strings, deduplicated and sorted by `casefold`, then
 *   by the value itself (so consecutive windows concatenate) — numeric and
 *   date columns order NUMERICALLY, so «9» precedes «10»;
 * - NULL and an empty string collapse into one «Пустые» bucket, reported via
 *   `hasNulls` and excluded from the value window;
 * - `total` is the distinct non-empty value count of the WHOLE scope (not the
 *   window) and `rowCount` counts every row represented by the buckets.
 */

/** One active header filter: the kept display values plus the «Пустые» bucket. */
export type ColumnFilter = {
    column: string;
    values: string[];
    includeNulls: boolean;
};

/** Does one active filter keep this row? Shared by the grid and the facets. */
export function rowMatchesOne(
    filter: ColumnFilter,
    text: (row: Record<string, unknown>) => unknown,
    row: Record<string, unknown>,
): boolean {
    const display = facetDisplay(text(row));
    if (display === null) return filter.includeNulls;
    return filter.values.includes(display);
}

/** Does this row survive every active filter? Columns combine with AND. */
export function rowMatchesFilters(
    row: Record<string, unknown>,
    filters: readonly ColumnFilter[],
    text: (column: string, row: Record<string, unknown>) => unknown,
): boolean {
    for (const filter of filters) {
        if (!rowMatchesOne(filter, (target) => text(filter.column, target), row)) return false;
    }
    return true;
}

/**
 * The funnel's active state, applied to columns RevoGrid has already rendered.
 * `updateColumns` is the public way to change a column after render; only the
 * ones whose state actually moved are returned.
 */
export function withFilterMarkers<TColumn extends { prop: string | number; hasFilter?: boolean }>(
    columns: readonly TColumn[],
    active: Record<string, ColumnFilter>,
): TColumn[] {
    const changed: TColumn[] = [];
    for (const column of columns) {
        const next = active[String(column.prop)] !== undefined;
        if (Boolean(column.hasFilter) === next) continue;
        column.hasFilter = next;
        changed.push(column);
    }
    return changed;
}

/** One window of a column's distinct values, plus the counts behind them. */
export type FacetWindow = {
    values: Array<{ value: string; count: number }>;
    total: number;
    hasNulls: boolean;
    nullCount?: number | undefined;
    rowCount?: number | undefined;
};

/** Where a column filter gets its options from. */
export type FacetSource = (
    column: string,
    window: { offset: number; limit: number },
) => Promise<FacetWindow>;

/** The popover's own page size — matches the backend default. */
export const FACET_PAGE = 200;

/**
 * Adapt any paged loader to a `FacetSource`. The loader returns `null`-labelled
 * entries for its «Пустые» bucket; they never reach the value list.
 */
export function serverFacetSource(
    load: (
        column: string,
        params: { offset: number; limit: number },
    ) => Promise<{
        values: Array<{ value: string | null; count: number }>;
        total: number;
        hasNulls: boolean;
        nullCount?: number | undefined;
        rowCount?: number | undefined;
    }>,
): FacetSource {
    return async (column, { offset, limit }) => {
        const facets = await load(column, { offset, limit });
        return {
            values: facets.values
                .filter((entry): entry is { value: string; count: number } => entry.value !== null)
                .map((entry) => ({ value: entry.value, count: entry.count })),
            total: facets.total,
            hasNulls: facets.hasNulls,
            nullCount: facets.nullCount,
            rowCount: facets.rowCount,
        };
    };
}

/** Case-insensitive ordering, not gap-insensitive — the client twin of `casefold`. */
const FACET_COLLATOR = new Intl.Collator("ru-RU", { sensitivity: "variant" });

/**
 * How a column's values are ordered in the list.
 *
 * - `text` — casefold, then the value itself (the backend's ordering);
 * - `number`, `date` — NUMERIC order. A string sort would put «10» before «9»
 *   and «01.02.2024» before «31.01.2024», which reads as broken to anyone
 *   scanning a list of figures or dates.
 *
 * The formatted text is ordered by the value behind it, so the list keeps
 * showing exactly what the cells show.
 */
export type FacetOrder = "text" | "number" | "date";

/** Extracts the sortable number behind a display string. */
const NUMERIC_TOKEN = /-?\d+(?:[.,]\d+)?/;

function numberKey(text: string): number {
    // RU-formatted numbers use a non-breaking space for thousands and a comma
    // for decimals ("1 234,5").
    const token = NUMERIC_TOKEN.exec(text.replace(/\s/g, ""));
    if (!token) return Number.NaN;
    const parsed = Number(token[0].replace(",", "."));
    return Number.isFinite(parsed) ? parsed : Number.NaN;
}

/**
 * The sortable key of a displayed date. The grid renders dates as
 * `дд.мм.гггг, чч:мм:сс` (see `dateTimeCellTemplate`), so the leading number is
 * the DAY — sorting by it would put every 1st before every 2nd, whatever the
 * month. Rebuild the ISO order (year, month, day, time) instead.
 */
const RU_DATE = /^(\d{2})\.(\d{2})\.(\d{4})(?:,(?:\s*)(\d{2}):(\d{2})(?::(\d{2}))?)?/;

function dateKey(text: string): number | null {
    const match = RU_DATE.exec(text.trim());
    if (!match) {
        // An ISO timestamp is already sortable as-is.
        const parsed = Date.parse(text);
        return Number.isFinite(parsed) ? parsed : null;
    }
    const [, day, month, year, hours = "0", minutes = "0", seconds = "0"] = match;
    return new Date(
        Number(year),
        Number(month) - 1,
        Number(day),
        Number(hours),
        Number(minutes),
        Number(seconds),
    ).getTime();
}

function compareDates(first: string, second: string): number | null {
    const a = dateKey(first);
    const b = dateKey(second);
    if (a === null || b === null) return null;
    if (a === b) return null;
    return a < b ? -1 : 1;
}

function compareNumbers(first: string, second: string): number | null {
    const a = numberKey(first);
    const b = numberKey(second);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    if (a !== b) return a < b ? -1 : 1;
    // The same number spelled differently ("3" / "3,0") — fall back to text.
    return null;
}

function compareText(first: string, second: string): number {
    const a = first.toLocaleLowerCase("ru-RU");
    const b = second.toLocaleLowerCase("ru-RU");
    if (a !== b) return a < b ? -1 : 1;
    return FACET_COLLATOR.compare(first, second) || (first < second ? -1 : first > second ? 1 : 0);
}

function compareFacetValues(first: string, second: string, order: FacetOrder): number {
    if (order === "date") {
        const chronological = compareDates(first, second);
        if (chronological !== null) return chronological;
    } else if (order === "number") {
        const numeric = compareNumbers(first, second);
        if (numeric !== null) return numeric;
    }
    return compareText(first, second);
}

/** The display text of one cell in the facet list; `null` = the «Пустые» bucket. */
export function facetDisplay(value: unknown): string | null {
    if (value === null || value === undefined) return null;
    if (Array.isArray(value)) {
        // A multi-valued cell is filtered by the joined text the grid shows —
        // one option, one cell — not by its parts.
        const text = value.filter((part) => part !== null && part !== undefined).join(", ");
        return text === "" ? null : text;
    }
    if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
    if (typeof value === "boolean") return value ? "Да" : "Нет";
    const text = String(value);
    return text === "" ? null : text;
}

export type RowFacets = {
    /** Distinct values, sorted the way the backend sorts them. */
    values: string[];
    counts: Map<string, number>;
    hasNulls: boolean;
    nullCount: number;
    rowCount: number;
};

/**
 * Distinct values of one column across the given rows — the in-memory twin of
 * the backend's `read_plan_facets`.
 *
 * `text` maps a row to the string the TABLE shows for the column, resolved per
 * row: a column whose text comes from another field (a `displayKey`) must offer
 * what the user actually reads. A resolved `null`/`""` is the «Пустые» bucket.
 */
export function computeRowFacets(
    rows: Iterable<Record<string, unknown>>,
    text: (row: Record<string, unknown>) => unknown,
    order: FacetOrder = "text",
): RowFacets {
    const counts = new Map<string, number>();
    let hasNulls = false;
    let nullCount = 0;
    let rowCount = 0;
    for (const row of rows) {
        const display = facetDisplay(text(row));
        rowCount += 1;
        if (display === null) {
            hasNulls = true;
            nullCount += 1;
            continue;
        }
        counts.set(display, (counts.get(display) ?? 0) + 1);
    }
    return {
        values: [...counts.keys()].sort((first, second) =>
            compareFacetValues(first, second, order),
        ),
        counts,
        hasNulls,
        nullCount,
        rowCount,
    };
}

/** Cut one window out of the computed facets. */
export function facetWindow(facets: RowFacets, offset: number, limit: number): FacetWindow {
    return {
        values: facets.values.slice(offset, offset + limit).map((value) => ({
            value,
            count: facets.counts.get(value) ?? 0,
        })),
        total: facets.values.length,
        hasNulls: facets.hasNulls,
        nullCount: facets.nullCount,
        rowCount: facets.rowCount,
    };
}

/**
 * A `FacetSource` over rows already in memory. It keeps the paged contract —
 * a big dictionary pages instead of rendering thousands of options at once —
 * and computes the buckets ONCE, so paging itself is free.
 */
export function rowFacetSource(
    rows: readonly Record<string, unknown>[],
    text: (row: Record<string, unknown>) => unknown,
    order: FacetOrder = "text",
): FacetSource {
    const facets = computeRowFacets(rows, text, order);
    return async (_column, { offset, limit }) => facetWindow(facets, offset, limit);
}
