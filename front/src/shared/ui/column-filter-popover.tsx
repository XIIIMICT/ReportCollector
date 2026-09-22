"use client";

import { SearchField } from "@heroui/react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { getRenovationPlanFacets, type RenovationColumnFilter } from "@/shared/api/renovation";

/** A fixed page size for the lazy value window (matches the backend default). */
const FACET_PAGE = 200;

/**
 * The plan header column filter.
 *
 * A fixed-position popover anchored to the leaf header cell: a search box plus a
 * checklist with «Выбрать все» and «Пустые» pinned on top, then the column's
 * distinct values (lazy-loaded from `/plan/facets`, alphabetical). Values are
 * combined into one `RenovationColumnFilter`; every column filter combines with
 * AND server-side. Applying happens once the popover closes (its owner commits
 * the returned selection), so intermediate clicks never re-query.
 *
 * "Выбрать все" stays present and acts as a master toggle: it is checked when
 * every value is selected; unchecking some values keeps it visible as an
 * unchecked master that re-selects everything on click.
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
    const [query, setQuery] = useState("");
    const [values, setValues] = useState<string[]>([]);
    const [total, setTotal] = useState<number | null>(null);
    const [hasNulls, setHasNulls] = useState(false);
    const [loading, setLoading] = useState(true);
    // «Выбрать все» governs every value; «Пустые» is the separate NULL bucket.
    // A column with no prior filter keeps everything.
    const [allSelected, setAllSelected] = useState(initial === null);
    const [selected, setSelected] = useState<Set<string>>(() => new Set(initial?.values ?? []));
    const [includeNulls, setIncludeNulls] = useState(initial?.includeNulls ?? false);

    const rootRef = useRef<HTMLDivElement>(null);

    // Load the distinct values (first window; the list is scrollable and the
    // search narrows within the loaded set — the plan values are bounded).
    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        void getRenovationPlanFacets(column, { planId, asset, offset: 0, limit: FACET_PAGE })
            .then((facets) => {
                if (cancelled) return;
                const loaded = facets.values
                    .map((entry) => entry.value)
                    .filter((value): value is string => value !== null);
                setValues(loaded);
                setTotal(facets.total);
                setHasNulls(facets.hasNulls);
            })
            .catch(() => {
                if (!cancelled) setValues([]);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [column, planId, asset]);

    // Apply when the pointer presses anywhere outside the popover (the "close"
    // moment); Escape cancels without applying.
    useEffect(() => {
        const onPointerDown = (event: PointerEvent) => {
            const root = rootRef.current;
            if (root && !root.contains(event.target as Node)) {
                commit();
            }
        };
        // `capture` so it runs before any handler that stops propagation.
        document.addEventListener("pointerdown", onPointerDown, true);
        return () => document.removeEventListener("pointerdown", onPointerDown, true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [allSelected, selected, includeNulls, values, hasNulls]);

    const keptValues = allSelected ? values : values.filter((value) => selected.has(value));

    const commit = () => {
        // No constraint at all → drop the filter (server treats it as "all").
        const keepsAllValues = allSelected && !includeNulls;
        if (keepsAllValues) {
            onApply(null);
        } else {
            onApply({ column, values: keptValues, includeNulls });
        }
        onClose();
    };

    const visibleValues = query
        ? values.filter((value) => value.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
        : values;

    const style = popoverStyle(anchor);

    if (typeof document === "undefined") return null;
    return createPortal(
        <div
            ref={rootRef}
            className="column-filter"
            style={style}
            role="dialog"
            aria-label={`Фильтр: ${columnLabel}`}
        >
            <SearchField aria-label="Поиск значения" variant="secondary">
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
            <div className="column-filter__list" role="group">
                <FilterRow
                    label="Выбрать все"
                    checked={allSelected}
                    count={total}
                    onToggle={() => {
                        const next = !allSelected;
                        setAllSelected(next);
                        if (next) setSelected(new Set(values));
                    }}
                />
                {hasNulls ? (
                    <FilterRow
                        label="Пустые"
                        checked={includeNulls}
                        onToggle={() => setIncludeNulls((value) => !value)}
                    />
                ) : null}
                {loading ? (
                    <div className="column-filter__empty">Загрузка…</div>
                ) : visibleValues.length === 0 ? (
                    <div className="column-filter__empty">Нет значений</div>
                ) : (
                    visibleValues.map((value) => (
                        <FilterRow
                            key={value}
                            label={value}
                            checked={allSelected || selected.has(value)}
                            onToggle={() => {
                                if (allSelected) {
                                    // Leaving "all": keep everything except the
                                    // clicked value.
                                    setAllSelected(false);
                                    setSelected(new Set(values.filter((item) => item !== value)));
                                    return;
                                }
                                setSelected((current) => {
                                    const next = new Set(current);
                                    if (next.has(value)) next.delete(value);
                                    else next.add(value);
                                    return next;
                                });
                            }}
                        />
                    ))
                )}
            </div>
            <div className="column-filter__actions">
                <button
                    type="button"
                    className="column-filter__reset"
                    onClick={() => {
                        setAllSelected(true);
                        setSelected(new Set(values));
                        setIncludeNulls(false);
                    }}
                >
                    Сбросить
                </button>
                <button type="button" className="column-filter__apply" onClick={commit}>
                    Применить
                </button>
            </div>
        </div>,
        document.body,
    );
}

function FilterRow({
    label,
    checked,
    count,
    onToggle,
}: {
    label: string;
    checked: boolean;
    count?: number | null;
    onToggle: () => void;
}) {
    return (
        <label className="column-filter__row">
            <input type="checkbox" checked={checked} onChange={onToggle} />
            <span className="column-filter__label">{label}</span>
            {count != null ? <span className="column-filter__count">{count}</span> : null}
        </label>
    );
}

/** Anchor the popover just below the header cell, kept inside the viewport. */
function popoverStyle(anchor: DOMRect): React.CSSProperties {
    const width = Math.min(280, Math.max(200, anchor.width + 80));
    const left = Math.min(Math.max(8, anchor.left), window.innerWidth - width - 8);
    const top = Math.min(anchor.bottom + 4, window.innerHeight - 320);
    return { position: "fixed", left, top, width };
}
