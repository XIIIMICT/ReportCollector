/**
 * The column filter's data contract, shared by EVERY table in the app.
 *
 * The popover (`column-filter-popover.tsx`) only ever asks its source for «the
 * distinct values of column X, window [offset, limit)» — and, when the search
 * narrows the list, «only those matching this query/range» — it never learns
 * where they come from. Two kinds of source implement the contract:
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
    /** Distinct values matching the current search (the loaded list length). */
    total: number;
    /**
     * Distinct values in the WHOLE column, IGNORING the search — the column's
     * real size. When nothing is searched it equals `total`.
     *
     * The popover's «does this list need a search control» question is answered
     * by THIS number: a filter that narrowed a long column down to a handful of
     * matches must not take the control away, or the user could never widen the
     * range again without resetting everything.
     */
    totalWithoutSearch?: number | undefined;
    hasNulls: boolean;
    nullCount?: number | undefined;
    rowCount?: number | undefined;
};

/**
 * What a source is asked for: a window of the column's distinct values, and —
 * when the search narrows the list — only the values that SEARCH matches.
 *
 * `search` is part of the REQUEST, not something the popover applies to the
 * loaded slice, so a narrowed list can be paged properly: a text query over a
 * column of 248k distinct values would otherwise only ever search the first
 * window. Two shapes, decided by the column's type:
 * - `text` — a case-insensitive substring of the display value;
 * - `range` — an inclusive min/max over the NUMERIC value behind the display,
 *   either bound optional. A source that cannot see numbers simply returns an
 *   empty window.
 */
export type FacetSearch =
    | { kind: "text"; query: string }
    | { kind: "range"; min: number | null; max: number | null }
    | { kind: "datetime"; min: number | null; max: number | null };

/** An inclusive numeric window, either bound optional. */
export type FacetRange = { min: number | null; max: number | null };

/**
 * The same window for a timestamp column. Both bounds are EPOCH MILLISECONDS —
 * one comparable number for every zone, so a source can compare them against a
 * parsed ISO value without agreeing on a display format first.
 */
export type FacetDateRange = { min: number | null; max: number | null };

/** Where a column filter gets its options from. */
export type FacetSource = (
    column: string,
    window: { offset: number; limit: number; search?: FacetSearch | null },
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
    /**
     * The COLUMN's full size, measured once per popover and reused afterwards.
     *
     * A range or a query narrows the list, and the popover must keep offering
     * its search controls while it does — but the narrowed answer cannot tell it
     * how big the column really is. One unfiltered request per column answers
     * that, and it is what the returned `totalWithoutSearch` reports.
     */
    const sizes = new Map<string, Promise<number>>();
    const columnSize = (column: string): Promise<number> => {
        let pending = sizes.get(column);
        if (!pending) {
            pending = load(column, { offset: 0, limit: 1 })
                .then((facets) => facets.total)
                .catch(() => 0);
            sizes.set(column, pending);
        }
        return pending;
    };

    return async (column, { offset, limit, search }) => {
        // The plan's endpoint narrows for neither shape, so the search is applied
        // HERE, over one window: enough for the first 200 values, and the caller
        // pages through the rest when needed.
        const facets = await load(column, { offset, limit });
        const values = facets.values
            .filter((entry): entry is { value: string; count: number } => entry.value !== null)
            .map((entry) => ({ value: entry.value, count: entry.count }));
        const narrowed =
            search === null || search === undefined
                ? values
                : search.kind === "text"
                  ? values.filter((entry) =>
                        entry.value
                            .toLocaleLowerCase("ru-RU")
                            .includes(search.query.trim().toLocaleLowerCase("ru-RU")),
                    )
                  : values.filter((entry) => {
                        // A `datetime` range compares INSTANTS, a numeric one
                        // compares numbers. Reading a date as a number returned
                        // `null` for every value, so the whole list vanished the
                        // moment a bound was typed (the client tables parse dates
                        // through `parseFacetInstant`; this branch did not).
                        const comparable =
                            search.kind === "datetime"
                                ? parseFacetInstant(entry.value)
                                : parseFacetNumber(entry.value);
                        if (comparable === null) return false;
                        if (search.min !== null && comparable < search.min) return false;
                        if (search.max !== null && comparable > search.max) return false;
                        return true;
                    });
        return {
            values: narrowed,
            total: narrowed.length,
            totalWithoutSearch: await columnSize(column),
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
    /** The numeric value behind each display string (range searches only). */
    numbers: Map<string, number>;
    /** Display strings that are not numbers — never matched by a range. */
    nonNumeric: Set<string>;
};

/**
 * Distinct values of one column across the given rows — the in-memory twin of
 * the backend's `read_plan_facets`.
 *
 * `text` maps a row to the string the TABLE shows for the column, resolved per
 * row: a column whose text comes from another field (a `displayKey`) must offer
 * what the user actually reads. A resolved `null`/`""` is the «Пустые» bucket.
 *
 * `number` maps a row to the value a RANGE compares against, when that has to
 * be the raw one. Without it the source parses the display string, which only
 * works while the formatter's separators are the plain ones — «3 361» becomes
 * 3361, but a column printing «1 234,5» is a guess. Reporting the value removes
 * the guess entirely.
 */
export function computeRowFacets(
    rows: Iterable<Record<string, unknown>>,
    text: (row: Record<string, unknown>) => unknown,
    order: FacetOrder = "text",
    number?: (row: Record<string, unknown>) => unknown,
): RowFacets {
    const counts = new Map<string, number>();
    const numbers = new Map<string, number>();
    const nonNumeric = new Set<string>();
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
        // A DATE column has no numeric value to report: its buckets are compared
        // as instants, and marking them `nonNumeric` here made `searchRowFacets`
        // drop every one of them before the date branch could look. The two
        // concepts are different — «not a number» vs «not comparable at all».
        if (order === "date") continue;
        if (!number || numbers.has(display) || nonNumeric.has(display)) continue;
        const raw = number(row);
        const numeric = typeof raw === "number" ? raw : Number(raw);
        if (raw === null || raw === undefined || raw === "" || !Number.isFinite(numeric)) {
            nonNumeric.add(display);
        } else {
            numbers.set(display, numeric);
        }
    }
    return {
        values: [...counts.keys()].sort((first, second) =>
            compareFacetValues(first, second, order),
        ),
        counts,
        hasNulls,
        nullCount,
        rowCount,
        numbers,
        nonNumeric,
    };
}

/**
 * The number behind a numeric bucket's DISPLAY string.
 *
 * Buckets are keyed by the display text («3 361», «1,5»), but a range search has
 * to compare numbers. Parsing the text would turn «3 361» into 3361 and «1,5»
 * into 1.5 — right only while the formatter's separator guesses hold, and wrong
 * the moment a column prints its own decimals or a non-breaking separator.
 * A source that KNOWS its rows therefore reports the value directly, in the
 * same window as `values`.
 */
export type FacetNumberWindow = {
    /** Display string → the numeric value it stands for. */
    numbers: Map<string, number>;
    /** Display strings whose value is not a number (never range-matched). */
    nonNumeric: Set<string>;
};

/** Cut one window out of the computed facets. */
export function facetWindow(
    facets: RowFacets,
    offset: number,
    limit: number,
    /**
     * The set's size BEFORE any search narrowed it — the popover decides whether
     * a list needs a search control from this, not from the matches.
     */
    totalWithoutSearch?: number,
): FacetWindow {
    return {
        values: facets.values.slice(offset, offset + limit).map((value) => ({
            value,
            count: facets.counts.get(value) ?? 0,
        })),
        total: facets.values.length,
        // The size BEFORE the search narrowed it — the popover decides whether a
        // list needs a search control from this, not from the matches.
        ...(totalWithoutSearch !== undefined && { totalWithoutSearch }),
        hasNulls: facets.hasNulls,
        nullCount: facets.nullCount,
        rowCount: facets.rowCount,
    };
}

/**
 * Parse a value the facet list carries as a NUMBER. Display strings come from
 * the cells' own formatter, so they may carry thousands spaces and either a
 * decimal point or a comma. `null` for anything that is not a finite number —
 * callers then treat the value as unmatched rather than silently keeping it.
 *
 * Lives here (not in the popover) because BOTH sides parse: `searchRowFacets`
 * and any source that filters by range.
 */
export function parseFacetNumber(display: string): number | null {
    const compact = display.replace(/[\s\u00a0\u202f]/g, "").replace(",", ".");
    if (compact === "") return null;
    const parsed = Number(compact);
    return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The instant behind a timestamp bucket's DISPLAY string, in epoch ms.
 *
 * A timestamp cell carries an ISO value, but the bucket KEY is what the cell
 * prints («03.02.2026, 08:45:00»). A source that knows the raw value reports it
 * (see `FacetNumberWindow`'s doc for the numeric twin); one that can only ship
 * strings falls back to `Date.parse`, which reads the locale formats the app
 * prints and returns `null` for anything else.
 */
export function parseFacetInstant(display: string): number | null {
    // The app prints «03.02.2026, 08:45:00» (ru-RU, day first, 24-hour). V8's
    // `Date.parse` cannot read that — it wants the US month-first order — and
    // silently returned `null`, so every timestamp bucket fell out of a range.
    // The parts are read explicitly instead, in the SAME order the formatter
    // prints them; anything else falls back to the native parser.
    const parts = /^(\d{2})\.(\d{2})\.(\d{4})(?:,\s*(\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(
        display.trim(),
    );
    if (parts) {
        const [, day, month, year, hour, minute, second] = parts;
        const instant = new Date(
            Number(year),
            Number(month) - 1,
            Number(day),
            Number(hour ?? 0),
            Number(minute ?? 0),
            Number(second ?? 0),
        ).getTime();
        return Number.isFinite(instant) ? instant : null;
    }
    const parsed = Date.parse(display);
    return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Keep only the buckets a search matches. Runs on the WHOLE computed set, so
 * «Выбрать найденные» and paging see every match rather than the loaded slice.
 */
export function searchRowFacets(
    facets: RowFacets,
    search: FacetSearch | null | undefined,
): RowFacets {
    if (!search) return facets;
    const needle = search.kind === "text" ? search.query.trim().toLocaleLowerCase("ru-RU") : "";
    if (search.kind === "text" && needle === "") return facets;
    // Fixed OUTSIDE the filter: narrowing the union inside the callback makes the
    // compiler forget it on every iteration.
    const byDate = search.kind === "datetime";
    const values = facets.values.filter((value) => {
        if (search.kind === "text") return value.toLocaleLowerCase("ru-RU").includes(needle);
        // The source's own value wins. A MISSING entry means the source does
        // not resolve numbers at all (it only ships display strings) — an entry
        // under `nonNumeric` means this particular bucket is not a number. The
        // two must not be confused, or every bucket of every column would fall
        // through to the string parser.
        // A timestamp bucket carries no numeric value of its own, so the two
        // kinds resolve their comparable number differently.
        const known = facets.numbers.get(value);
        const fallback = byDate ? parseFacetInstant(value) : parseFacetNumber(value);
        const comparable = known ?? (facets.nonNumeric.has(value) ? null : fallback);
        if (comparable === null) return false;
        if (search.min !== null && comparable < search.min) return false;
        if (search.max !== null && comparable > search.max) return false;
        return true;
    });
    return { ...facets, values };
}

/**
 * A `FacetSource` over rows already in memory. It keeps the paged contract —
 * a big dictionary pages instead of rendering thousands of options at once —
 * and computes the buckets ONCE, so paging itself is free. A search narrows the
 * computed set before the window is cut, so every match stays reachable.
 */
export function rowFacetSource(
    rows: readonly Record<string, unknown>[],
    text: (row: Record<string, unknown>) => unknown,
    order: FacetOrder = "text",
    number?: (row: Record<string, unknown>) => unknown,
): FacetSource {
    const facets = computeRowFacets(rows, text, order, number);
    return async (_column, { offset, limit, search }) =>
        facetWindow(searchRowFacets(facets, search), offset, limit, facets.values.length);
}
