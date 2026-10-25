"use client";

import { EmptyState, ListBox, SearchField, Separator, type Key } from "@heroui/react";
import { List, ListSelect, TaskList } from "iconoir-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { usePopoverDismiss } from "@/shared/hooks/use-popover-dismiss";
import { FACET_PAGE, type ColumnFilter, type FacetSource } from "@/shared/ui/column-filter-facets";
import { SmartSelect } from "@/shared/ui/smart-select";

/** The contract lives with the sources; re-exported for the popover's callers. */
export type { ColumnFilter, FacetSource } from "@/shared/ui/column-filter-facets";

/** Sentinel keys for the two pinned options (never column values). */
const ALL_KEY = "__all__";
const NULL_KEY = "__null__";
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
    // True between «Выбрать найденные» and the next deliberate selection change:
    // the user has just SET the selection, so the caption reads «Снять выбор»
    // even though the kept set is only the matched window. It is a REF because
    // the caption is derived during the same render; the value is reset by any
    // other selection change.
    const foundAppliedRef = useRef(false);

    facetSourceRef.current = facets;

    useEffect(() => {
        let cancelled = false;
        loadingRef.current = true;
        exhaustedRef.current = false;
        loadedCountRef.current = 0;
        void facetSourceRef
            .current(column, { offset: 0, limit: FACET_PAGE })
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
        // Not `facetSource`: a rebuilt client source must not restart the list
        // (see `facetSourceRef`) — only a different column resets it.
    }, [column]);

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
        for (const value of kept) keys.push(value);
        return keys;
    }, [allActive, hasNulls, includeNulls, kept]);

    /**
     * The master item is an ACTION with three states:
     * - `clear` — «Снять выбор» while everything is kept;
     * - `found` — «Выбрать найденные» while a query narrows the list and
     *   something is excluded: it selects only the values the query matched —
     *   the LOADED ones, because the rest are not fetched yet;
     * - `all` — «Выбрать все» once something is excluded and no query is typed.
     *
     * Clearing the query keeps the selection as it is; only the caption moves
     * on (a kept-everything state stays `clear`, a partial one becomes `all`).
     */
    // The query narrows the LOADED window only (a column can hold hundreds of
    // thousands of values, fetched in pages) — the caption therefore says
    // «найденные», never «все».
    const [query, setQuery] = useState("");
    const normalizedQuery = query.trim().toLocaleLowerCase("ru-RU");
    const filteredValues = useMemo(
        () =>
            normalizedQuery === ""
                ? values
                : values.filter((value) =>
                      value.toLocaleLowerCase("ru-RU").includes(normalizedQuery),
                  ),
        [normalizedQuery, values],
    );

    const masterState: "clear" | "found" | "all" =
        allActive || foundAppliedRef.current ? "clear" : normalizedQuery !== "" ? "found" : "all";
    const masterLabel =
        masterState === "clear"
            ? "Снять выбор"
            : masterState === "found"
              ? "Выбрать найденные"
              : "Выбрать все";

    /**
     * «Выбрать найденные»: keep exactly what the query matched, drop the rest.
     * The unmatched tail is not loaded, so this is a POSITIVE list of the
     * matched values — no more and no less than «найденные» shows.
     */
    const selectFound = () => {
        setKept(new Set(filteredValues));
        // «Пустые» is not a value a query can match.
        setIncludeNulls(false);
        setAllActive(false);
        allStillSelectedRef.current = false;
        ignoreMasterEchoRef.current = true;
        foundAppliedRef.current = true;
        window.setTimeout(() => {
            ignoreMasterEchoRef.current = false;
        }, 0);
    };

    const onSelectionChange = (keys: Key[]) => {
        const next = new Set(keys);
        const hadAll = selectedKeys.includes(ALL_KEY);
        const hasAll = next.has(ALL_KEY);
        if (ignoreMasterEchoRef.current) return;
        // Any deliberate selection change ends the "just applied the found set"
        // state — otherwise its caption («Снять выбор») would stick even after
        // the user unchecks a value.
        foundAppliedRef.current = false;
        // In the «Выбрать найденные» state the master does NOT mean "select
        // everything" — it means "select what I found".
        if (hasAll && !hadAll && masterState === "found") {
            selectFound();
            return;
        }
        // The master's OWN toggle. Its key disappears on every individual
        // uncheck too (react-aria drops it from the selection), so the removal
        // counts as a master toggle only when the whole selection became empty —
        // that is the case where the user really switched it off.
        // The master's own toggle has two shapes:
        //  - the key comes BACK → "select everything";
        //  - the key is gone AND nothing is left → "clear everything".
        // Everything in between is an individual value being unchecked: react-aria
        // drops ALL_KEY from the selection then, which used to look like a
        // master toggle and re-selected the whole column.
        // Unchecking the master removes ALL_KEY, and react-aria may keep the
        // individual keys in the same event — but the user asked for "nothing".
        // The distinction from an individual uncheck: there ALL_KEY stays.
        const switchedAllOff = hadAll && !hasAll;
        if ((hasAll && !hadAll) || switchedAllOff) {
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
        foundAppliedRef.current = false;
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
        const keepsEverything =
            values.length > 0 && nextKept.size === values.length && (keepsNulls || !hasNulls);
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
        } satisfies ColumnFilter;
        // `allActive` stays in the deps: it re-runs the memo after the master
        // toggle even though the decision reads the ref (which is not reactive).
    }, [allActive, column, hasNulls, includeNulls, kept, values]);

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

    const content = (
        <SmartSelect
            multiple
            forceSearchable
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
                    <ListBox
                        ref={listRef}
                        className="column-filter__list"
                        selectionMode="multiple"
                        renderEmptyState={() => <EmptyState>Нет значений</EmptyState>}
                        onScroll={onListScroll}
                    >
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
