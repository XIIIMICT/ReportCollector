"use client";

import { EmptyState, ListBox, Separator, type Key } from "@heroui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { getRenovationPlanFacets, type RenovationColumnFilter } from "@/shared/api/renovation";
import { SmartSelect } from "@/shared/ui/smart-select";

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
 * filters across columns combine with AND (server-side).
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
    planId,
    asset,
    initial,
    defaultFilter = null,
    onApply,
    onClose,
}: {
    column: string;
    columnLabel: string;
    anchor: DOMRect;
    planId?: string;
    asset?: string | null;
    initial: RenovationColumnFilter | null;
    /** The column's default filter («По умолчанию»); `null` = no constraint. */
    defaultFilter?: RenovationColumnFilter | null;
    onApply: (filter: RenovationColumnFilter | null) => void;
    onClose: () => void;
}) {
    const [values, setValues] = useState<string[]>([]);
    const [counts, setCounts] = useState<Map<string, number>>(new Map());
    const [hasNulls, setHasNulls] = useState(false);
    const [nullCount, setNullCount] = useState<number | undefined>();
    // Distinct value count across the whole filtered plan — shown on the
    // master item as «Выбрать все (N)» and it doubles as the "load more" guard.
    const [total, setTotal] = useState(0);
    const [rowCount, setRowCount] = useState(0);
    const showUniqueCount = column !== "col_003";
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
    // values (col_003 — the renovation ID — has ~248k, col_022 ~85k), so only
    // the first window arrives with the popover and the rest follow as the user
    // scrolls to the bottom (`/plan/facets` pages by offset/limit). The loading
    // state is a ref only: the popover shows no progress text — new windows
    // simply appear as they arrive.
    const listRef = useRef<HTMLDivElement>(null);
    const loadingRef = useRef(false);
    const exhaustedRef = useRef(false);
    // Distinct values rendered so far; also the next page's offset.
    const loadedCountRef = useRef(0);
    // A ref mirror of `values` for the paging callbacks: reading the state
    // inside a callback memoized without it would see a stale window.
    const valuesRef = useRef<string[]>([]);
    valuesRef.current = values;

    useEffect(() => {
        let cancelled = false;
        loadingRef.current = true;
        exhaustedRef.current = false;
        loadedCountRef.current = 0;
        void getRenovationPlanFacets(column, { planId, asset, offset: 0, limit: FACET_PAGE })
            .then((facets) => {
                if (cancelled) return;
                const loaded = facets.values
                    .map((entry) => entry.value)
                    .filter((value): value is string => value !== null);
                setCounts(
                    new Map(
                        facets.values
                            .filter(
                                (entry): entry is { value: string; count: number } =>
                                    entry.value !== null,
                            )
                            .map((entry) => [entry.value, entry.count]),
                    ),
                );
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
                setRowCount(
                    typeof facets.rowCount === "number" && Number.isFinite(facets.rowCount)
                        ? facets.rowCount
                        : 0,
                );
                loadedCountRef.current = loaded.length;
                exhaustedRef.current = loaded.length >= facets.total;
                if (allStillSelectedRef.current) {
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
            setCounts((previous) => {
                const next = new Map(previous);
                for (const entry of facets.values) {
                    if (entry.value !== null) next.set(entry.value, entry.count);
                }
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
            // Re-checking the master re-arms the tail top-up: the windows that
            // load later join the kept set again.
            allStillSelectedRef.current = hasAll;
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
        // The comparison is against the window loaded SO FAR: a window that
        // arrives right after a selection only widens `values`, and the master
        // must not flip on its own — the `allStillSelectedRef` guard below owns
        // the tail top-up instead.
        const keepsNulls = hasNulls && next.has(NULL_KEY);
        const keepsNothing = nextKept.size === 0 && !keepsNulls;
        const keepsEverything =
            values.length > 0 && nextKept.size === values.length && (keepsNulls || !hasNulls);
        if (keepsNothing) setAllActive(false);
        else if (keepsEverything) setAllActive(true);
        // Only a selection that still keeps EVERYTHING leaves the tail top-up
        // armed (it also re-arms after the user unchecks something and then
        // restores every option by hand).
        allStillSelectedRef.current = keepsEverything;
        setIncludeNulls(next.has(NULL_KEY));
    };

    const filteredValues = values;

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
        if (allStillSelectedRef.current && (includeNulls || !hasNulls)) return null;
        // Without that state, every value the user actually saw must be kept —
        // otherwise the selection would claim values that are merely unloaded.
        const keepsEverything =
            values.length > 0 && keptValues.length === values.length && (keepsNulls || !hasNulls);
        if (keepsEverything) return null;
        return {
            column,
            values: keptValues,
            includeNulls: keepsNulls,
        } satisfies RenovationColumnFilter;
        // `allActive` stays in the deps: it re-runs the memo after the master
        // toggle even though the decision reads the ref (which is not reactive).
    }, [allActive, column, hasNulls, includeNulls, kept, values]);

    // The filter this popover opened with. «Отменить» (the footer, Escape, a
    // click outside) drops the pending selection, so the owner keeps exactly
    // this one — the owner re-applies the same value, which is a no-op.
    const initialFilter = useRef(initial);
    // Guards a double close: the footer's «Применить» closes via SmartSelect,
    // and the owner removes this component right after.
    const settledRef = useRef(false);
    const confirm = () => {
        if (settledRef.current) return;
        settledRef.current = true;
        onApply(applied);
        onClose();
    };
    const cancel = () => {
        if (settledRef.current) return;
        settledRef.current = true;
        onApply(initialFilter.current);
        onClose();
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
        onClose();
    };

    const content = (
        <SmartSelect
            multiple
            forceSearchable
            defaultOpen
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
            anchor={{ left: anchor.left, top: anchor.top, width: anchor.width }}
            popoverClassName="column-filter__popover"
            renderListbox={() => (
                <ListBox
                    ref={listRef}
                    className="column-filter__list"
                    selectionMode="multiple"
                    renderEmptyState={() => <EmptyState>Нет значений</EmptyState>}
                    onScroll={onListScroll}
                >
                    <ListBox.Item key={ALL_KEY} id={ALL_KEY} textValue="Выбрать все">
                        <span>Выбрать все</span>
                        {rowCount > 0 ? (
                            <span className="text-muted ms-1">
                                {rowCount}
                                {showUniqueCount ? ` (${total} уникальных)` : null}
                            </span>
                        ) : null}
                        <ListBox.ItemIndicator />
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
                    {filteredValues.map((value) => (
                        <ListBox.Item key={value} id={value} textValue={value}>
                            <span>{value}</span>
                            {counts.get(value) !== undefined ? (
                                <span className="text-muted ms-1">{counts.get(value)}</span>
                            ) : null}
                            <ListBox.ItemIndicator />
                        </ListBox.Item>
                    ))}
                </ListBox>
            )}
        />
    );
    // The header funnel lives in the environment state the grid syncs on every
    // render, so the popover is mounted elsewhere in the tree when it opens.
    // React events then never pass through the grid's layers, while a portal
    // keeps every press bubbling to the document for the close-on-outside-click.
    return typeof document === "undefined" ? content : createPortal(content, document.body);
}
