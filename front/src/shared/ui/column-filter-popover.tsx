"use client";

import { EmptyState, ListBox, SearchField, Separator, type Key } from "@heroui/react";
import { List, ListSelect, TaskList } from "iconoir-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { usePopoverDismiss } from "@/shared/hooks/use-popover-dismiss";
import {
    ColumnFilterDateRange,
    EMPTY_DATE_RANGE,
    isDateRangeEmpty,
} from "@/shared/ui/column-filter-date-range";
import {
    FACET_PAGE,
    type ColumnFilter,
    type FacetDateRange,
    type FacetOrder,
    type FacetRange,
    type FacetSearch,
    type FacetSource,
} from "@/shared/ui/column-filter-facets";
import { ColumnFilterRange, EMPTY_RANGE, isRangeEmpty } from "@/shared/ui/column-filter-range";
import { SmartSelect } from "@/shared/ui/smart-select";

/** The contract lives with the sources; re-exported for the popover's callers. */
export type { ColumnFilter, FacetSource } from "@/shared/ui/column-filter-facets";

/** Sentinel keys for the two pinned options (never column values). */
const ALL_KEY = "__all__";
const NULL_KEY = "__null__";
/** Below this many options the popover renders no search control at all. */
const SEARCH_MIN_OPTIONS = 10;
/** Start loading the next window this close (px) to the list's bottom. */
const FACET_LOAD_THRESHOLD = 120;

/**
 * A table header's column filter — THE one filter popover of the app.
 *
 * Every table shares it: the «Ремонт» object plan (whose facets come from
 * PostgreSQL through `/plan/facets`), and the dictionaries of every project
 * («Сырьё», «КППК», the renovation dictionaries) whose rows are already in
 * memory. The owner injects a `FacetSource` (see `column-filter-facets.ts`) and
 * gets back a plain `ColumnFilter` — the popover itself knows neither the
 * project nor where the numbers come from.
 *
 * Default state is «everything»: every value plus the «Пустые» bucket (when the
 * column has NULLs), i.e. no constraint. The list has «Выбрать все» pinned on
 * top — a master toggle that stays checked while some values are unchecked,
 * clears every value when unchecked itself and restores them when re-checked —
 * then «Пустые», a separator and the column's distinct values alphabetically
 * (lazy-loaded in windows). The master is stateful, but when the user
 * unchecks the last remaining option the master unchecks itself, so «nothing
 * selected» never keeps a checked «Выбрать все».
 *
 * The filter is opened by the header funnel (an outside trigger) and is a
 * commit-mode `SmartSelect`: hovering away keeps it open, and the pinned
 * «По умолчанию / Отменить / Применить» footer decides. «Применить» (also
 * Enter) hands `applied` to the owner; «Отменить», Escape and a click outside
 * the filter keep the filter that was in force before opening; «По умолчанию»
 * returns the column to its default («no constraint», like «Сбросить все
 * фильтры и сортировки»).
 */
export function ColumnFilterPopover({
    column,
    columnLabel,
    anchor,
    facets,
    initial,
    defaultFilter = null,
    showUniqueCount = true,
    order = "text",
    dateOnly = false,
    onApply,
    onClose,
}: {
    /** The column's key — opaque to the popover, handed back to the source. */
    column: string;
    /** The column's header text (the popover's aria label and reset caption). */
    columnLabel: string;
    /** The header cell's rect — the popover hangs off it. */
    anchor: DOMRect;
    /** Where the options come from (server paged or computed from memory). */
    facets: FacetSource;
    initial: ColumnFilter | null;
    /** The column's default filter («По умолчанию»); `null` = no constraint. */
    defaultFilter?: ColumnFilter | null;
    /**
     * The «Выбрать все» item also shows the distinct-value count. Off for a
     * column with two fixed buckets (the plan's ID column), where it would only
     * repeat the row count.
     */
    showUniqueCount?: boolean;
    /**
     * How the column is searched. `number` swaps the text search for the MIN–MAX
     * pair: for numbers a substring query matches 312 and 1200 for «12», which
     * is not what anyone means. Defaults to `text`.
     */
    order?: FacetOrder;
    /**
     * When `order` is `date`: does the column hold a plain calendar DAY rather
     * than a timestamp? The range pickers then drop their time segments тАФ a
     * time would be a fiction the cell's ┬л03.02.2026┬╗ text cannot match.
     */
    dateOnly?: boolean;
    onApply: (filter: ColumnFilter | null) => void;
    onClose: () => void;
}) {
    const [values, setValues] = useState<string[]>([]);
    const [counts, setCounts] = useState<Map<string, number>>(new Map());
    const [hasNulls, setHasNulls] = useState(false);
    const [nullCount, setNullCount] = useState<number | undefined>();
    // Distinct value count across the whole filtered scope — shown on the
    // master item as «Выбрать все (N)» and it doubles as the "load more" guard.
    const [total, setTotal] = useState(0);
    // The column's size IGNORING the search. Once a filter narrowed a long list
    // down to a few matches, the search controls must stay: hiding them would
    // remove the only way to widen the range again (short of resetting the filter).
    const [totalWithoutSearch, setTotalWithoutSearch] = useState(0);
    const [rowCount, setRowCount] = useState(0);
    const [allActive, setAllActive] = useState(true);
    // «Пустые» is on by default so the default state really is "everything".
    const [kept, setKept] = useState<Set<string>>(new Set(initial?.values ?? []));
    const [includeNulls, setIncludeNulls] = useState(initial?.includeNulls ?? true);
    // Until the user EXCLUDES something, every newly loaded window joins the
    // kept set: the master «Выбрать все» promises the whole column, not just
    // the window that happened to be loaded when the popover opened. It also
    // seeds the very first window (`initial === null` means "no filter").
    const allStillSelectedRef = useRef(initial === null);
    // The value list is paged: a column can hold tens of thousands of distinct
    // values (the plan's ID column ~248k, a «Сырьё» name column a few hundred),
    // so only the first window arrives with the popover and the rest follow as
    // the user scrolls to the bottom (the source pages by offset/limit). The
    // loading state is a ref only: the popover shows no progress text — new
    // windows simply appear as they arrive.
    const listRef = useRef<HTMLDivElement>(null);
    const loadingRef = useRef(false);
    const exhaustedRef = useRef(false);
    // Distinct values rendered so far; also the next page's offset.
    const loadedCountRef = useRef(0);
    // A ref mirror of `values` for the paging callbacks: reading the state
    // inside a callback memoized without it would see a stale window.
    const valuesRef = useRef<string[]>([]);
    valuesRef.current = values;

    // The source is looked up per request through a ref: a client-side source
    // is rebuilt whenever the rows change, and re-running the effect on every
    // rebuild would reset the user's selection under their hands.
    const facetSourceRef = useRef(facets);

    // «Выбрать найденные» replaces the whole selection, and react-aria re-emits
    // the previous one (still carrying ALL_KEY) right after. Ignore that echo for
    // a moment, or the master would flip straight back to «Снять выбор».
    const ignoreMasterEchoRef = useRef(false);
    // True while the selection IS what a search produced: the caption then reads
    // «Снять выбор» (its own action), not «Выбрать найденные» again.
    const foundAppliedRef = useRef(false);

    facetSourceRef.current = facets;

    /**
     * The master item is an ACTION with three states:
     * - `clear` — «Снять выбор» while everything is kept;
     * - `found` — «Выбрать найденные» while a search narrows the list and
     *   something is excluded: it selects only the values the search matched;
     * - `all` — «Выбрать все» once something is excluded and no search is active.
     *
     * Clearing the search keeps the selection as it is; only the caption moves
     * on (a kept-everything state stays `clear`, a partial one becomes `all`).
     *
     * The search goes to the SOURCE, not to the loaded slice: a client table
     * filters its whole computed set, and a paged source narrows inside its
     * query — so «найденные» means every match, not the first page of them.
     * A TEXT column gets a substring search; a NUMBER column gets the MIN–MAX
     * pair, because «12» as a substring also matches 312 and 1200.
     */
    const [query, setQuery] = useState("");
    const [range, setRange] = useState<FacetRange>(EMPTY_RANGE);
    const [dateRange, setDateRange] = useState<FacetDateRange>(EMPTY_DATE_RANGE);
    const isDate = order === "date";
    // A reversed range (`мин > макс`) is a typo, not «everything»: it matches
    // nothing. Without this guard the popover compared against it, found the
    // reversed window empty, and — because «nothing kept, nothing searched» also
    // reads as an EMPTY filter — sent "no constraint" to the grid, so the funnel
    // lit up while every row stayed.
    const activeRange = isDate ? dateRange : range;
    const rangeReversed =
        activeRange.min !== null && activeRange.max !== null && activeRange.min > activeRange.max;
    const rangeEmpty = isDate ? isDateRangeEmpty(dateRange) : isRangeEmpty(range);
    const search: FacetSearch | null =
        order === "text"
            ? query.trim() === ""
                ? null
                : { kind: "text", query }
            : rangeEmpty
              ? null
              : rangeReversed
                ? { kind: isDate ? "datetime" : "range", min: 1, max: 0 }
                : {
                      kind: isDate ? "datetime" : "range",
                      min: activeRange.min,
                      max: activeRange.max,
                  };
    // Identity of the search, so the reload effect fires on a real change only.
    const searchKey =
        search === null
            ? ""
            : search.kind === "text"
              ? `t:${search.query}`
              : `${search.kind[0]}:${search.min ?? ""}:${search.max ?? ""}`;

    // The live search travels to the source with every request. It is read
    // through a ref: the loaders are memoized without it, and a stale closure
    // would page the PREVIOUS query after the user typed.
    const searchRef = useRef<FacetSearch | null>(null);

    useEffect(() => {
        let cancelled = false;
        loadingRef.current = true;
        exhaustedRef.current = false;
        loadedCountRef.current = 0;
        // A new search REPLACES the list: the old window's values are not part
        // of the narrowed result and would make the count and the paging lie.
        setValues([]);
        valuesRef.current = [];
        void facetSourceRef
            .current(column, { offset: 0, limit: FACET_PAGE, search: searchRef.current })
            .then((facets) => {
                if (cancelled) return;
                const loaded = facets.values.map((entry) => entry.value);
                setCounts(new Map(facets.values.map((entry) => [entry.value, entry.count])));
                setValues(loaded);
                // Keep the paging refs in step with the FIRST window at once:
                // the short-list effect may fire before React commits the
                // state, and a stale (empty) window would ask for offset 0..n.
                valuesRef.current = loaded;
                setHasNulls(facets.hasNulls);
                setNullCount(
                    typeof facets.nullCount === "number" && Number.isFinite(facets.nullCount)
                        ? facets.nullCount
                        : undefined,
                );
                setTotal(facets.total);
                setTotalWithoutSearch(facets.totalWithoutSearch ?? facets.total);
                setRowCount(
                    typeof facets.rowCount === "number" && Number.isFinite(facets.rowCount)
                        ? facets.rowCount
                        : 0,
                );
                loadedCountRef.current = loaded.length;
                exhaustedRef.current = loaded.length >= facets.total;
                // While a search narrows the list, a new window is NOT a new
                // default: the user's found set must survive the reload that the
                // next keystroke triggers, or ???????? ?????????? switches itself
                // off (???????? ???? again) just by typing the upper bound.
                if (allStillSelectedRef.current && searchRef.current === null) {
                    setKept(new Set(loaded));
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setValues([]);
                    exhaustedRef.current = true;
                }
            })
            .finally(() => {
                if (cancelled) return;
                loadingRef.current = false;
            });
        return () => {
            cancelled = true;
            loadingRef.current = false;
        };
        // Not `facetSource`: a rebuilt client source must not restart the list
        // (see `facetSourceRef`) — only a different column or search does.
    }, [column, searchKey]);

    /**
     * Append the next window of distinct values.
     *
     * Ordering is the source's (casefold + value), so pages concatenate. A Set
     * dedupes values that moved between windows while the plan was edited.
     */
    const loadNextPage = useCallback(async () => {
        if (loadingRef.current || exhaustedRef.current) return;
        loadingRef.current = true;
        try {
            const facets = await facetSourceRef.current(column, {
                offset: loadedCountRef.current,
                limit: FACET_PAGE,
                search: searchRef.current,
            });
            const loaded = facets.values.map((entry) => entry.value);
            setCounts((previous) => {
                const next = new Map(previous);
                for (const entry of facets.values) next.set(entry.value, entry.count);
                return next;
            });
            setHasNulls((previous) => previous || facets.hasNulls);
            const nextNullCount = facets.nullCount;
            if (typeof nextNullCount === "number" && Number.isFinite(nextNullCount)) {
                setNullCount((previous) => Math.max(previous ?? 0, nextNullCount));
            }
            setTotal(facets.total);
            setRowCount(
                typeof facets.rowCount === "number" && Number.isFinite(facets.rowCount)
                    ? facets.rowCount
                    : 0,
            );
            if (loaded.length === 0) {
                exhaustedRef.current = true;
            }
            // The offset must advance HERE, synchronously: the state updater
            // below may run later (or twice under StrictMode), and a concurrent
            // tail request would otherwise re-read the stale offset.
            const seenBefore = new Set(valuesRef.current);
            const appended = loaded.filter((value) => !seenBefore.has(value));
            loadedCountRef.current = valuesRef.current.length + appended.length;
            setValues((previous) => {
                const seen = new Set(previous);
                const merged = [...previous];
                for (const value of loaded) {
                    if (!seen.has(value)) {
                        seen.add(value);
                        merged.push(value);
                    }
                }
                return merged;
            });
            // Freshly loaded values must join the kept set while the user has
            // excluded nothing: otherwise applying the filter would send a
            // positive list missing every value beyond this window (and the
            // new items would render with an empty checkbox although the
            // master «Выбрать все» is checked).
            if (allStillSelectedRef.current) {
                setKept((previous) => new Set([...previous, ...loaded]));
            }
        } catch {
            // Keep what is loaded; the next scroll or the button retries.
        } finally {
            loadingRef.current = false;
        }
    }, [column]);

    const onListScroll = useCallback(
        (event: React.UIEvent<HTMLDivElement>) => {
            const element = event.currentTarget;
            const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
            if (remaining <= FACET_LOAD_THRESHOLD) void loadNextPage();
        },
        [loadNextPage],
    );

    // A short list never scrolls, so the tail is requested once after each
    // render: either the list still fits (keep filling) or it overflows and the
    // scroll handler takes over. `listRef` points AT the list element (HeroUI's
    // ListBox root carries the class), so match the node itself as well.
    useEffect(() => {
        const root = listRef.current;
        if (!root) return;
        const element =
            root.querySelector(".column-filter__list") ??
            (root.classList.contains("column-filter__list") ? root : null);
        if (!element) return;
        if (element.scrollHeight <= element.clientHeight + 8) void loadNextPage();
    }, [loadNextPage, values.length]);

    const selectedKeys = useMemo(() => {
        const keys: Key[] = [];
        if (allActive) keys.push(ALL_KEY);
        // Only advertise the null bucket when the column actually renders it:
        // react-aria keeps keys it can't match to an item, so a stale
        // `NULL_KEY` would linger in `onChange` and look like a kept bucket.
        if (includeNulls && hasNulls) keys.push(NULL_KEY);
        // Only keys that are IN THE LIST right now: a search hides most values,
        // and the master then mirrors what is visible — otherwise «Выбрать
        // найденные» (which keeps exactly the found set) would read as
        // «selected» and pressing it would toggle the found values OFF, sending
        // an EMPTY filter to the grid while the funnel still lit up.
        for (const value of values) {
            if (kept.has(value)) keys.push(value);
        }
        return keys;
    }, [allActive, hasNulls, includeNulls, kept, values]);

    searchRef.current = search;

    /**
     * ???????? ??????????: keep exactly what the search matched, drop the rest.
     * The source narrowed the list, so `values` IS the found set.
     */
    const selectFound = () => {
        setKept(new Set(values));
        // ???????? is not a value a search can match.
        setIncludeNulls(false);
        setAllActive(false);
        allStillSelectedRef.current = false;
        ignoreMasterEchoRef.current = true;
        foundAppliedRef.current = true;
        window.setTimeout(() => {
            ignoreMasterEchoRef.current = false;
        }, 0);
    };

    /*
     * The master row is an ACTION whose caption says what a press will do:
     * - «Снять выбор» — everything is kept, so the press takes everything away;
     * - «Выбрать найденные» — a search narrows the list, so the press keeps
     *   exactly what it found;
     * - «Выбрать все» — something was taken away, so the press puts everything
     *   back.
     * The caption and the icon follow the SELECTION state, because that is what
     * the user is looking at while they pick values.
     */
    // «Выбрать найденные» answers a search that also EXCLUDED something — with
    // everything still kept its press would change nothing, so the caption stays
    // «Снять выбор» (which is a real action).
    const somethingExcluded =
        kept.size !== values.length || (hasNulls && !includeNulls) || !allActive;
    const masterState: "clear" | "found" | "all" =
        allActive || foundAppliedRef.current
            ? "clear"
            : search !== null && somethingExcluded
              ? "found"
              : "all";
    const masterLabel =
        masterState === "clear"
            ? "Снять выбор"
            : masterState === "found"
              ? "Выбрать найденные"
              : "Выбрать все";

    const onSelectionChange = (keys: Key[]) => {
        const next = new Set(keys);
        const hadAll = selectedKeys.includes(ALL_KEY);
        const hasAll = next.has(ALL_KEY);
        if (ignoreMasterEchoRef.current) return;
        // In the «Выбрать найденные» state the master does NOT mean "select
        // everything" — it means "select what I found". Checked BEFORE the
        // generic "ends the found state" reset below: that reset cleared the
        // flag first, so the caption fell back to «Выбрать все» and the press
        // was read as a plain master toggle — which, since the found values were
        // already kept, UNCHECKED them and sent an empty filter to the grid.
        if (hasAll && !hadAll && masterState === "found") {
            selectFound();
            return;
        }
        // The OTHER way round: the state is what it is, and the press toggles
        // it. React-aria does not reliably hand back the master's key (`__all__`)
        // — a press on the row arrives as "the visible values are selected, the
        // master is not", which is exactly what «Выбрать найденные» leaves
        // behind. Reading that as "an individual value was checked" made the
        // first press on «Снять выбор» a no-op: it re-adopted the found set and
        // only the SECOND press cleared anything.
        if ((hasAll && !hadAll) || (masterState === "clear" && !hasAll)) {
            // «Снять выбор» clears, «Выбрать все» restores; after a search that
            // left nothing kept, the press is «Выбрать найденные» again.
            const turnOn = masterState === "all";
            setAllActive(turnOn);
            setKept(turnOn ? new Set(values) : new Set());
            setIncludeNulls(turnOn);
            allStillSelectedRef.current = turnOn;
            foundAppliedRef.current = false;
            return;
        }
        // Any deliberate selection change ends the "just applied the found set"
        // state — otherwise its caption («Снять выбор») would stick even after
        // the user unchecks a value.
        foundAppliedRef.current = false;
        // The master's own toggle. Its key disappears on every individual
        // uncheck too (react-aria drops it from the selection), so the removal
        // counts as a master toggle only when the key comes back or the whole
        // selection became empty. The distinction from an individual uncheck is
        // that there ALL_KEY stays.
        const nextKept = new Set<string>();
        for (const key of next) {
            const text = String(key);
            if (text !== ALL_KEY && text !== NULL_KEY) nextKept.add(text);
        }
        setKept(nextKept);
        // «Выбрать все» mirrors the extremes: unchecking the last kept option
        // unchecks it, selecting everything again checks it back. In between it
        // keeps whatever the user left (so «всё, кроме некоторых» stays checked).
        // The comparison is against the KEPT SET, not the loaded window: a
        // window that arrives right after a selection only widens `values`, and
        // an untouched column kept every value from page one — comparing with
        // `values.length` read it as «something excluded» and flipped the master
        // off by itself.
        const keepsNulls = hasNulls && next.has(NULL_KEY);
        const keepsEverything =
            nextKept.size > 0 &&
            [...values].every((value) => nextKept.has(value)) &&
            (keepsNulls || !hasNulls) &&
            // A window is a SLICE; only a selection that also still holds every
            // value loaded so far counts as «everything».
            nextKept.size >= values.length;
        // «Выбрать все»/«Снять выбор» mirror the extremes: everything kept — on,
        // nothing kept — off, and ANY exclusion also counts as "off" (the caption
        // then offers «Выбрать все»/«Выбрать найденные», which is what the user
        // needs at that point). Re-selecting everything by hand turns it on again.
        if (keepsEverything) setAllActive(true);
        else setAllActive(false);
        // Only a selection that still keeps EVERYTHING leaves the tail top-up
        // armed (it also re-arms after the user unchecks something and then
        // restores every option by hand).
        allStillSelectedRef.current = keepsEverything;
        setIncludeNulls(next.has(NULL_KEY));
    };

    const applied = useMemo(() => {
        const keptValues = values.filter((value) => kept.has(value));
        // A column without a null bucket has nothing to keep there, so the
        // state flag must not leak into the request.
        const keepsNulls = includeNulls && hasNulls;
        // «Everything» is the WHOLE column, not the window loaded so far: a
        // column can hold six figures of distinct values (the renovation ID
        // column has ~248k), so comparing against `values.length` would turn
        // «всё, кроме одного» into a 199-value whitelist. The tail top-up ref is
        // the reliable «nothing was taken away» signal (the master stays checked
        // in between), and then the request carries no constraint at all.
        //
        // NEVER while a search narrows the list: the kept set is then a POSITIVE
        // choice of the found values, and «no constraint» would silently undo it.
        if (search === null && allStillSelectedRef.current && (includeNulls || !hasNulls)) {
            return null;
        }
        // Without that state, every value the user actually saw must be kept —
        // otherwise the selection would claim values that are merely unloaded.
        // Judged on the KEPT SET (what the filter would send), not on the
        // visible window — see `onSelectionChange`. Right after a value is
        // UNCHECKED the set still holds it (react-aria reports the change
        // before React commits), and reading it here would claim "everything"
        // and silently drop the exclusion.
        const excludedSomewhere = keptValues.length !== kept.size;
        if (search === null && allStillSelectedRef.current && !excludedSomewhere) return null;
        const keepsEverything =
            keptValues.length > 0 &&
            keptValues.length === values.length &&
            (keepsNulls || !hasNulls);
        if (search === null && keepsEverything) return null;
        return {
            column,
            values: keptValues,
            includeNulls: keepsNulls,
        } satisfies ColumnFilter;
        // `allActive` stays in the deps: it re-runs the memo after the master
        // toggle even though the decision reads the ref (which is not reactive).
    }, [allActive, column, hasNulls, includeNulls, kept, search, values]);

    // The filter this popover opened with. «Отменить» (the footer, Escape, a
    // click outside) drops the pending selection, so the owner keeps exactly
    // this one — the owner re-applies the same value, which is a no-op.
    const initialFilter = useRef(initial);
    const popoverRef = useRef<HTMLElement | null>(null);
    const { isOpen, dismiss } = usePopoverDismiss(popoverRef, onClose);
    // Guards a double close: the footer's «Применить» closes via SmartSelect,
    // and the owner removes this component right after.
    const settledRef = useRef(false);
    const confirm = () => {
        if (settledRef.current) return;
        settledRef.current = true;
        onApply(applied);
        dismiss();
    };
    const cancel = () => {
        if (settledRef.current) return;
        settledRef.current = true;
        onApply(initialFilter.current);
        dismiss();
    };
    /**
     * «По умолчанию» — the user's remembered DEFAULT for this column (the value
     * the workspace restores with «Сбросить все фильтры и сортировки»), not the
     * one that was in force before opening. Cancel is symmetric to confirm: the
     * owner assigns the given filter and closes.
     */
    const reset = () => {
        if (settledRef.current) return;
        settledRef.current = true;
        onApply(defaultFilter);
        dismiss();
    };

    // react-aria sizes the popover from the anchor; the commit group is wider
    // than that once its labels expand, and it re-measures on hover — which made
    // the panel jump between «По умолчанию» and «Отменить». The group's own
    // reserved width becomes the panel's floor, so it never moves again.
    const [minWidth, setMinWidth] = useState<number>();
    useEffect(() => {
        if (typeof document === "undefined") return;
        let frame = 0;
        let last = 0;
        const measure = () => {
            const group = document.querySelector<HTMLElement>(
                ".column-filter__popover .smart-select__footer .button-group",
            );
            if (!group) {
                frame = window.requestAnimationFrame(measure);
                return;
            }
            // The panel must be the widest state the group ever takes: its
            // reserved `minWidth` (computed from the labels that will expand)
            // plus a margin. The extra room is deliberately generous — the group
            // re-measures while its labels animate, and the panel was chasing
            // those frames, which read as the whole popover twitching.
            const reserved = Number.parseFloat(getComputedStyle(group).minWidth) || 0;
            const width = Math.max(group.getBoundingClientRect().width, reserved);
            if (width > 0 && Math.abs(width - last) > 0.5) {
                last = width;
                setMinWidth(Math.ceil(width) + 64);
            }
            frame = window.requestAnimationFrame(measure);
        };
        frame = window.requestAnimationFrame(measure);
        return () => window.cancelAnimationFrame(frame);
    }, []);

    // A SHORT list needs no search control — this is the SmartSelect rule with
    // its threshold of ten: below that the popover shows no text field, no
    // number pair and no date pair. The list is small enough to read, and the
    // master «Выбрать все» stays the only action above it.
    //
    // The count is `total` (matches in the WHOLE scope under the other filters),
    // not the loaded window, so a paged column reports its real size.
    const showSearch = Math.max(totalWithoutSearch, total) >= SEARCH_MIN_OPTIONS;

    const content = (
        <SmartSelect
            multiple
            forceSearchable={showSearch}
            defaultOpen
            isOpen={isOpen}
            popoverRef={popoverRef}
            closeOnUnhover={false}
            canConfirm
            onConfirm={confirm}
            onCancel={cancel}
            onReset={reset}
            resetLabel={`По умолчанию: ${columnLabel}`}
            value={selectedKeys.map(String)}
            onChange={(next) => {
                const keys = Array.isArray(next) ? (next as Key[]) : [];
                onSelectionChange(keys);
            }}
            onOpenChange={(open) => {
                // Any other dismissal is a cancel: applying only happens with
                // the explicit «Применить».
                if (!open) cancel();
            }}
            ariaLabel={`Фильтр: ${columnLabel}`}
            placeholder="Поиск..."
            // react-aria derives the popover's INLINE `max-height` from the space
            // between the anchor and the viewport edge. The funnel sits near the
            // top of a table, so that space is small; anchoring the popover a
            // little lower buys the list a usable slice (the footer and the
            // search field are fixed rows inside it).
            anchor={{ left: anchor.left, top: anchor.top, width: anchor.width }}
            popoverClassName="column-filter__popover"
            {...(minWidth !== undefined && { popoverMinWidth: minWidth })}
            searchMode="custom"
            renderListbox={() => (
                <>
                    <div className="column-filter__search" hidden={!showSearch}>
                        {order === "number" ? (
                            <ColumnFilterRange
                                columnLabel={columnLabel}
                                value={range}
                                onChange={setRange}
                            />
                        ) : order === "date" ? (
                            <ColumnFilterDateRange
                                columnLabel={columnLabel}
                                value={dateRange}
                                onChange={setDateRange}
                                kind={dateOnly ? "date" : "datetime"}
                            />
                        ) : (
                            <SearchField
                                autoFocus
                                name="column-filter-search"
                                aria-label={`Поиск: ${columnLabel}`}
                                variant="secondary"
                                value={query}
                                onChange={setQuery}
                            >
                                <SearchField.Group>
                                    <SearchField.SearchIcon />
                                    <SearchField.Input placeholder="Поиск..." />
                                    <SearchField.ClearButton />
                                </SearchField.Group>
                            </SearchField>
                        )}
                    </div>
                    <ListBox
                        ref={listRef}
                        className="column-filter__list"
                        selectionMode="multiple"
                        renderEmptyState={() => <EmptyState>Нет значений</EmptyState>}
                        onScroll={onListScroll}
                    >
                        {/* The master is an ACTION: its caption names the press,
                            and the icon follows the caption — «Снять выбор» a
                            list, «Выбрать все» a task list, «Выбрать найденные»
                            a list with a selection. */}
                        <ListBox.Item key={ALL_KEY} id={ALL_KEY} textValue={masterLabel}>
                            {masterState === "clear" ? (
                                <List className="column-filter__master-icon" aria-hidden="true" />
                            ) : masterState === "found" ? (
                                <ListSelect
                                    className="column-filter__master-icon"
                                    aria-hidden="true"
                                />
                            ) : (
                                <TaskList
                                    className="column-filter__master-icon"
                                    aria-hidden="true"
                                />
                            )}
                            <span>{masterLabel}</span>
                            {rowCount > 0 ? (
                                <span className="text-muted ms-1">
                                    {rowCount}
                                    {showUniqueCount ? ` (${total} уникальных)` : null}
                                </span>
                            ) : null}
                        </ListBox.Item>
                        <Separator className="smart-select__separator" />
                        {hasNulls ? (
                            <ListBox.Item key={NULL_KEY} id={NULL_KEY} textValue="Пустые">
                                <span>Пустые</span>
                                {nullCount !== undefined ? (
                                    <span className="text-muted ms-1">{nullCount}</span>
                                ) : null}
                                <ListBox.ItemIndicator />
                            </ListBox.Item>
                        ) : null}
                        {values.map((value) => (
                            <ListBox.Item key={value} id={value} textValue={value}>
                                <span>{value}</span>
                                {counts.get(value) !== undefined ? (
                                    <span className="text-muted ms-1">{counts.get(value)}</span>
                                ) : null}
                                <ListBox.ItemIndicator />
                            </ListBox.Item>
                        ))}
                    </ListBox>
                </>
            )}
        />
    );
    // The header funnel lives in the environment state the grid syncs on every
    // render, so the popover is mounted elsewhere in the tree when it opens.
    // React events then never pass through the grid's layers, while a portal
    // keeps every press bubbling to the document for the close-on-outside-click.
    return typeof document === "undefined" ? content : createPortal(content, document.body);
}
