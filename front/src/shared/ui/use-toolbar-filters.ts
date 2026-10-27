"use client";

import { useCallback, useMemo } from "react";

import { useRestoredState } from "@/shared/hooks/use-restored-state";

import { writeWorkspaceQuery } from "./query-state";

/**
 * A panel filter VALUE: a scalar, or a list for a multi-select. `null` (or an
 * empty list) means "no constraint" — the parameter is then dropped from the URL.
 */
export type ToolbarFilterValue = string | string[] | null;

/** The panel filters of ONE tab, keyed by the parameter suffix (`slice`, `org`, …). */
export type ToolbarFilterSet = Readonly<Record<string, ToolbarFilterValue>>;

const QUERY_PREFIX = "f.";
const EMPTY: ToolbarFilterSet = {};

/** The URL parameter of one panel filter of one tab, e.g. `f.kppkRealization.org`. */
export function toolbarFilterParam(tabId: string, name: string): string {
    return `${QUERY_PREFIX}${tabId}.${name}`;
}

/** Read the filters one tab was opened with (empty when the URL carries none). */
export function readToolbarFilters(tabId: string | null): ToolbarFilterSet {
    if (typeof window === "undefined" || !tabId) return EMPTY;
    const params = new URLSearchParams(window.location.search);
    const prefix = toolbarFilterParam(tabId, "");
    const filters: Record<string, ToolbarFilterValue> = {};
    for (const [key, value] of params.entries()) {
        if (!key.startsWith(prefix) || value === "") continue;
        filters[key.slice(prefix.length)] = decodeValue(value);
    }
    return filters;
}

/** Every tab's filters as they sit in the URL, read on the client only. */
function readAllToolbarFilters(): Record<string, ToolbarFilterSet> {
    if (typeof window === "undefined") return {};
    const params = new URLSearchParams(window.location.search);
    const tabs = new Set<string>();
    for (const key of params.keys()) {
        if (!key.startsWith(QUERY_PREFIX)) continue;
        const end = key.indexOf(".", QUERY_PREFIX.length);
        if (end > QUERY_PREFIX.length) tabs.add(key.slice(QUERY_PREFIX.length, end));
    }
    return Object.fromEntries([...tabs].map((tab) => [tab, readToolbarFilters(tab)]));
}

/**
 * Toolbar ("panel") filters kept in the URL, scoped PER TAB.
 *
 * A tab is part of the address already (`?section=`), so a panel filter belongs
 * to the tab that renders it: `?section=rawMaterialPrices&f.rawMaterialPrices.…`.
 * Two consequences drive the design:
 *
 * - switching tabs never touches another tab's parameters, so returning to a
 *   tab restores the filters the user left there;
 * - a value that is "no constraint" is DROPPED from the URL rather than written
 *   as an empty string, which keeps the address readable and makes "absent"
 *   the single representation of a cleared filter.
 *
 * RevoGrid's own column filters/sorting are NOT part of this: they stay grid
 * state.
 */
export function useToolbarFilters(tabId: string | null) {
    // SSR renders "no filters"; the real sets are restored after hydration, the
    // same two-phase rule every workspace state follows, so markup matches.
    const [sets, setSets] = useRestoredState<Record<string, ToolbarFilterSet>>(
        {},
        readAllToolbarFilters,
    );

    const setFilter = useCallback(
        (tab: string, name: string, value: ToolbarFilterValue) => {
            const encoded = encodeValue(value);
            writeWorkspaceQuery(toolbarFilterParam(tab, name), encoded);
            setSets((current) => {
                const next: Record<string, ToolbarFilterValue> = { ...current[tab] };
                if (encoded === null) delete next[name];
                else next[name] = value;
                return { ...current, [tab]: next };
            });
        },
        [setSets],
    );

    return useMemo(
        () => ({
            filters: (tabId !== null && sets[tabId]) || EMPTY,
            setFilter,
        }),
        [setFilter, sets, tabId],
    );
}

/** A list round-trips as JSON (null when empty); a scalar as itself. */
function encodeValue(value: ToolbarFilterValue): string | null {
    if (value === null || value === "") return null;
    if (Array.isArray(value)) return value.length > 0 ? JSON.stringify(value) : null;
    return value;
}

function decodeValue(raw: string): ToolbarFilterValue {
    if (!raw.startsWith("[")) return raw;
    try {
        const parsed: unknown = JSON.parse(raw);
        return Array.isArray(parsed)
            ? parsed.filter((item): item is string => typeof item === "string")
            : raw;
    } catch {
        return raw;
    }
}

/**
 * Decide what a RESTORED panel filter is worth, given the options it must be
 * one of — the shared rule every workspace uses when a filter's valid values
 * only arrive with a later request.
 *
 * `options === null` means "not known yet". That is the whole point: a restored
 * value validated against an EMPTY list is indistinguishable from a stale one,
 * so a naive `options.includes(value)` clears a perfectly good filter on the
 * first render and silently deletes it from the URL. That is exactly how
 * «Блок дирекции = МТО» was lost on every reload of the plan, and how the raw
 * materials archive version reset itself.
 *
 * - `"pending"` — the options have not loaded; keep the value UNTOUCHED and do
 *   not rewrite the URL. Re-evaluate when they arrive.
 * - `"keep"`    — the value is one of the options (or nothing is stored).
 * - `"clear"`   — the value is genuinely gone; drop it so it cannot filter
 *   every row away.
 */
export type RestoredFilterVerdict = "pending" | "keep" | "clear";

export function restoredFilterVerdict(
    stored: string | null,
    options: readonly string[] | null,
): RestoredFilterVerdict {
    if (stored === null) return "keep";
    // The options are not known yet: the value may well be valid.
    if (options === null) return "pending";
    return options.includes(stored) ? "keep" : "clear";
}
