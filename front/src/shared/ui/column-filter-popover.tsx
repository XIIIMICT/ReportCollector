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
import { useEffect, useMemo, useRef, useState } from "react";

import { getRenovationPlanFacets, type RenovationColumnFilter } from "@/shared/api/renovation";

/** Sentinel keys for the two pinned options (never plan values). */
const ALL_KEY = "__all__";
const NULL_KEY = "__null__";
/** Lazy window size for the distinct values (matches the backend default). */
const FACET_PAGE = 200;

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
    const [query, setQuery] = useState("");
    const [allActive, setAllActive] = useState(true);
    // «Пустые» is on by default so the default state really is "everything".
    const [kept, setKept] = useState<Set<string>>(new Set(initial?.values ?? []));
    const [includeNulls, setIncludeNulls] = useState(initial?.includeNulls ?? true);
    // No prior filter (or one that already keeps everything) starts as "all".
    const keepAllOnLoad = useRef(initial === null);
    const { contains } = useFilter({ sensitivity: "base" });

    useEffect(() => {
        let cancelled = false;
        void getRenovationPlanFacets(column, { planId, asset, offset: 0, limit: FACET_PAGE })
            .then((facets) => {
                if (cancelled) return;
                const loaded = facets.values
                    .map((entry) => entry.value)
                    .filter((value): value is string => value !== null);
                setValues(loaded);
                setHasNulls(facets.hasNulls);
                if (keepAllOnLoad.current) {
                    setKept(new Set(loaded));
                    keepAllOnLoad.current = false;
                }
            })
            .catch(() => {
                if (!cancelled) setValues([]);
            });
        return () => {
            cancelled = true;
        };
    }, [column, planId, asset]);

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
        // Everything kept (values + the null bucket, or the column has no
        // nulls) means no constraint at all.
        const keepsEverything = keptValues.length === values.length && (keepsNulls || !hasNulls);
        if (keepsEverything) return null;
        return {
            column,
            values: keptValues,
            includeNulls: keepsNulls,
        } satisfies RenovationColumnFilter;
    }, [column, hasNulls, includeNulls, kept, values]);

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
                    className="column-filter__list"
                    selectionMode="multiple"
                    renderEmptyState={() => <EmptyState>Нет значений</EmptyState>}
                >
                    <ListBox.Item key={ALL_KEY} id={ALL_KEY} textValue="Выбрать все">
                        Выбрать все
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
            </Autocomplete.Popover>
        </Autocomplete>
    );
}
