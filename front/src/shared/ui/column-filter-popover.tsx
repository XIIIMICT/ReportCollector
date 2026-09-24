"use client";

import { Autocomplete, EmptyState, ListBox, SearchField, useFilter, type Key } from "@heroui/react";
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
 * The list has «Выбрать все» pinned on top (a master toggle: it stays checked
 * while some values are unchecked, and unchecking it clears every value —
 * re-checking restores them), then «Пустые» (the NULL/empty bucket), then the
 * column's distinct values alphabetically, lazy-loaded from `/plan/facets`.
 * Whatever is kept becomes one `RenovationColumnFilter`; filters across columns
 * combine with AND (server-side). The owner applies the selection when the
 * popover closes.
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
    // The kept value set is the source of truth; the master flag only paints
    // «Выбрать все» (it stays checked while individual values are removed).
    const [allActive, setAllActive] = useState(true);
    const [kept, setKept] = useState<Set<string>>(() => new Set(initial?.values ?? []));
    const [includeNulls, setIncludeNulls] = useState(initial?.includeNulls ?? false);
    // A filter with no prior selection keeps everything once the values load.
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

    // The controlled selection (sentinel keys + kept values). «Выбрать все» is
    // present whenever its flag is on, regardless of the individual values.
    const selectedKeys = useMemo(() => {
        const keys: Key[] = [];
        if (allActive) keys.push(ALL_KEY);
        if (includeNulls) keys.push(NULL_KEY);
        for (const value of kept) keys.push(value);
        return keys;
    }, [allActive, includeNulls, kept]);

    const onSelectionChange = (keys: Key[]) => {
        const next = new Set(keys);
        const hadAll = selectedKeys.includes(ALL_KEY);
        const hasAll = next.has(ALL_KEY);
        if (hasAll !== hadAll) {
            // The master toggle was clicked: select all or clear all values.
            setAllActive(hasAll);
            setKept(hasAll ? new Set(values) : new Set());
        } else {
            // Individual value toggled — «Выбрать все» keeps its state.
            const nextKept = new Set<string>();
            for (const key of next) {
                const text = String(key);
                if (text !== ALL_KEY && text !== NULL_KEY) nextKept.add(text);
            }
            setKept(nextKept);
        }
        setIncludeNulls(next.has(NULL_KEY));
    };

    const filteredValues = query ? values.filter((value) => contains(value, query)) : values;

    const applied = useMemo(() => {
        // `kept` is the source of truth for values; `allActive` only paints the
        // master checkbox. Every value kept + no nulls = no constraint.
        const keptValues = values.filter((value) => kept.has(value));
        if (keptValues.length === values.length && !includeNulls) return null;
        return { column, values: keptValues, includeNulls } satisfies RenovationColumnFilter;
    }, [column, includeNulls, kept, values]);

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
