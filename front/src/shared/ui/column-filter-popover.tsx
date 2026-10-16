"use client";

import {
    Autocomplete,
    EmptyState,
    ListBox,
    SearchField,
    Separator,
    useFilter,
    type Key,
} from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getRenovationPlanFacets, type RenovationColumnFilter } from "@/shared/api/renovation";

/** Sentinel keys for the two pinned options (never plan values). */
const ALL_KEY = "__all__";
const NULL_KEY = "__null__";
/** Lazy window size for the distinct values (matches the backend default). */
const FACET_PAGE = 200;
/** Start loading the next window this close (px) to the list's bottom. */
const FACET_LOAD_THRESHOLD = 120;

/**
 * The plan header column filter, built on HeroUI's multi-select Autocomplete.
 *
 * Default state is «everything»: every value plus the «Пустые» bucket (when the
 * column has NULLs), i.e. no constraint. The list has «Выбрать все» pinned on
 * top — a master toggle that stays checked while some values are unchecked,
 * clears every value when unchecked itself and restores them when re-checked —
 * then «Пустые», a separator and the column's distinct values alphabetically
 * (lazy-loaded from `/plan/facets`). The master is stateful, but when the user
 * unchecks the last remaining option the master unchecks itself, so «nothing
 * selected» never keeps a checked «Выбрать все». Whatever is kept becomes one
 * `RenovationColumnFilter`;
 * filters across columns combine with AND (server-side). The owner applies the
 * selection when the popover closes.
 */
export function ColumnFilterPopover({
    column,
    columnLabel,
    anchor,
    planId,
    asset,
    initial,
    onApply,
    onClose,
}: {
    column: string;
    columnLabel: string;
    anchor: DOMRect;
    planId?: string;
    asset?: string | null;
    initial: RenovationColumnFilter | null;
    onApply: (filter: RenovationColumnFilter | null) => void;
    onClose: () => void;
}) {
    const [values, setValues] = useState<string[]>([]);
    const [hasNulls, setHasNulls] = useState(false);
    // Distinct value count across the whole filtered plan — shown on the
    // master item as «Выбрать все (N)» and it doubles as the "load more" guard.
    const [total, setTotal] = useState(0);
    const [query, setQuery] = useState("");
    const [allActive, setAllActive] = useState(true);
    // «Пустые» is on by default so the default state really is "everything".
    const [kept, setKept] = useState<Set<string>>(new Set(initial?.values ?? []));
    const [includeNulls, setIncludeNulls] = useState(initial?.includeNulls ?? true);
    // No prior filter (or one that already keeps everything) starts as "all".
    const keepAllOnLoad = useRef(initial === null);
    const { contains } = useFilter({ sensitivity: "base" });
    // The value list is paged: a column can hold tens of thousands of distinct
    // values (col_003 — the renovation ID — has ~248k, col_022 ~85k), so only
    // the first window arrives with the popover and the rest follow as the user
    // scrolls to the bottom (`/plan/facets` pages by offset/limit).
    const [loading, setLoading] = useState(true);
    // Distinguishes the first window from a tail fetch: only the latter should
    // print «Загрузка…» over the already visible values.
    const [loadingMore, setLoadingMore] = useState(false);
    const listRef = useRef<HTMLDivElement>(null);
    const loadingRef = useRef(false);
    const exhaustedRef = useRef(false);
    // Distinct values rendered so far; also the next page's offset.
    const loadedCountRef = useRef(0);

    useEffect(() => {
        let cancelled = false;
        loadingRef.current = true;
        exhaustedRef.current = false;
        loadedCountRef.current = 0;
        setLoading(true);
        void getRenovationPlanFacets(column, { planId, asset, offset: 0, limit: FACET_PAGE })
            .then((facets) => {
                if (cancelled) return;
                const loaded = facets.values
                    .map((entry) => entry.value)
                    .filter((value): value is string => value !== null);
                setValues(loaded);
                setHasNulls(facets.hasNulls);
                setTotal(facets.total);
                loadedCountRef.current = loaded.length;
                exhaustedRef.current = loaded.length >= facets.total;
                if (keepAllOnLoad.current) {
                    setKept(new Set(loaded));
                    keepAllOnLoad.current = false;
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
                setLoading(false);
            });
        return () => {
            cancelled = true;
            loadingRef.current = false;
        };
    }, [column, planId, asset]);

    /**
     * Append the next window of distinct values.
     *
     * Ordering is the server's (casefold + value), so pages concatenate. A Set
     * dedupes values that moved between windows while the plan was edited.
     */
    const loadNextPage = useCallback(async () => {
        if (loadingRef.current || exhaustedRef.current) return;
        loadingRef.current = true;
        setLoadingMore(true);
        try {
            const facets = await getRenovationPlanFacets(column, {
                planId,
                asset,
                offset: loadedCountRef.current,
                limit: FACET_PAGE,
            });
            const loaded = facets.values
                .map((entry) => entry.value)
                .filter((value): value is string => value !== null);
            setHasNulls((previous) => previous || facets.hasNulls);
            setTotal(facets.total);
            if (loaded.length === 0) {
                exhaustedRef.current = true;
            }
            setValues((previous) => {
                const seen = new Set(previous);
                const merged = [...previous];
                for (const value of loaded) {
                    if (!seen.has(value)) {
                        seen.add(value);
                        merged.push(value);
                    }
                }
                loadedCountRef.current = merged.length;
                return merged;
            });
            // Freshly loaded values must join the kept set while the user has
            // not excluded anything: otherwise applying the filter would send a
            // positive list missing every value beyond this window.
            if (keepAllOnLoad.current) {
                setKept((previous) => new Set([...previous, ...loaded]));
            }
        } catch {
            // Keep what is loaded; the next scroll or the button retries.
        } finally {
            loadingRef.current = false;
            setLoading(false);
        }
    }, [column, planId, asset]);

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
    // scroll handler takes over.
    useEffect(() => {
        const element = listRef.current?.querySelector(".column-filter__list");
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
        for (const value of kept) keys.push(value);
        return keys;
    }, [allActive, hasNulls, includeNulls, kept]);

    const onSelectionChange = (keys: Key[]) => {
        const next = new Set(keys);
        const hadAll = selectedKeys.includes(ALL_KEY);
        const hasAll = next.has(ALL_KEY);
        if (hasAll !== hadAll) {
            // The master toggle: select everything (the values plus «Пустые») or
            // clear every option, «Пустые» included — unchecking «Выбрать все»
            // must leave nothing selected.
            setAllActive(hasAll);
            setKept(hasAll ? new Set(values) : new Set());
            setIncludeNulls(hasAll);
            return;
        }
        const nextKept = new Set<string>();
        for (const key of next) {
            const text = String(key);
            if (text !== ALL_KEY && text !== NULL_KEY) nextKept.add(text);
        }
        setKept(nextKept);
        // «Выбрать все» mirrors the extremes: unchecking the last kept option
        // unchecks it, selecting everything again checks it back. In between it
        // keeps whatever the user left (so «всё, кроме некоторых» stays checked).
        const keepsNulls = hasNulls && next.has(NULL_KEY);
        const keepsNothing = nextKept.size === 0 && !keepsNulls;
        const keepsEverything =
            values.length > 0 && nextKept.size === values.length && (keepsNulls || !hasNulls);
        if (keepsNothing) setAllActive(false);
        else if (keepsEverything) setAllActive(true);
        setIncludeNulls(next.has(NULL_KEY));
    };

    const filteredValues = query ? values.filter((value) => contains(value, query)) : values;

    const applied = useMemo(() => {
        const keptValues = values.filter((value) => kept.has(value));
        // A column without a null bucket has nothing to keep there, so the
        // state flag must not leak into the request.
        const keepsNulls = includeNulls && hasNulls;
        // «Everything» is the WHOLE column, not the window loaded so far: a
        // column can hold six figures of distinct values (the renovation ID
        // column has ~248k), so comparing against `values.length` would turn
        // «всё, кроме одного» into a 199-value whitelist. The master toggle is
        // the reliable signal for «nothing was taken away», and with it on the
        // request carries no constraint at all.
        if (allActive && (includeNulls || !hasNulls)) return null;
        // Without the master, every value the user actually saw must be kept —
        // otherwise the selection would claim values that are merely unloaded.
        const keepsEverything =
            values.length > 0 && keptValues.length === values.length && (keepsNulls || !hasNulls);
        if (keepsEverything) return null;
        return {
            column,
            values: keptValues,
            includeNulls: keepsNulls,
        } satisfies RenovationColumnFilter;
    }, [allActive, column, hasNulls, includeNulls, kept, values]);

    const close = () => {
        onApply(applied);
        onClose();
    };

    return (
        <Autocomplete
            selectionMode="multiple"
            value={selectedKeys}
            onChange={(keys) => onSelectionChange(keys as Key[])}
            aria-label={`Фильтр: ${columnLabel}`}
            defaultOpen
            onOpenChange={(open) => {
                if (!open) close();
            }}
        >
            <Autocomplete.Trigger
                className="column-filter__anchor"
                style={{ left: anchor.left, top: anchor.top, width: anchor.width }}
            >
                <Autocomplete.Value />
                <Autocomplete.Indicator />
            </Autocomplete.Trigger>
            <Autocomplete.Popover className="column-filter__popover">
                <SearchField autoFocus aria-label="Поиск значения" variant="secondary">
                    <SearchField.Group>
                        <SearchField.SearchIcon />
                        <SearchField.Input
                            placeholder="Поиск..."
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                        />
                        <SearchField.ClearButton />
                    </SearchField.Group>
                </SearchField>
                <ListBox
                    ref={listRef}
                    className="column-filter__list"
                    selectionMode="multiple"
                    renderEmptyState={() => <EmptyState>Нет значений</EmptyState>}
                    onScroll={onListScroll}
                >
                    <ListBox.Item key={ALL_KEY} id={ALL_KEY} textValue="Выбрать все">
                        {total > 0 ? `Выбрать все (${total})` : "Выбрать все"}
                        <ListBox.ItemIndicator />
                    </ListBox.Item>
                    {hasNulls ? (
                        <ListBox.Item key={NULL_KEY} id={NULL_KEY} textValue="Пустые">
                            Пустые
                            <ListBox.ItemIndicator />
                        </ListBox.Item>
                    ) : null}
                    {/* Separate the pinned options from the values; when «Пустые»
                        is absent the separator follows «Выбрать все». */}
                    {filteredValues.length > 0 ? (
                        <Separator className="column-filter__separator" />
                    ) : null}
                    {filteredValues.map((value) => (
                        <ListBox.Item key={value} id={value} textValue={value}>
                            {value}
                            <ListBox.ItemIndicator />
                        </ListBox.Item>
                    ))}
                </ListBox>
                {/* The window is paged: tell the user how much of the column is
                    on screen (a master-toggle count is meaningless for 248k
                    values without it) and that scrolling keeps loading. */}
                {!query && total > values.length ? (
                    <p className="column-filter__status" role="status">
                        {loading || loadingMore
                            ? "Загрузка…"
                            : `Показано ${values.length} из ${total}`}
                    </p>
                ) : null}
            </Autocomplete.Popover>
        </Autocomplete>
    );
}
