"use client";

/* oxlint-disable react-hooks/exhaustive-deps, react/exhaustive-effect-dependencies, react/memo-dependencies -- dependency lists are moved verbatim from DataGrid; `gridRef` is a stable ref object, not reactive state. */

/**
 * The whole "remembered position" lifecycle of a client grid.
 *
 * A form opens at the row where the user left it. The stored value is a stable
 * row ID, not an ordinal: the restore looks it up in the rows on screen NOW and
 * a deleted or filtered-out row simply leaves the form at its default instead
 * of landing on somebody else's record.
 *
 * All four flags and every timer/listener live here. The only thing handed back
 * is the debounced "remember" trigger: `DataGrid`'s `onViewportscroll` prop
 * calls it for vertical scrolling, while `scrollchange` is installed here.
 */

import { useCallback, useEffect, useRef } from "react";

import type { GridRow } from "./grid-config";
import type { GridRef, RevoGridAdapter } from "./revo-grid-adapter";
import { readScrollPosition, writeScrollPosition } from "./scroll-position";

type Args = {
    gridRef: GridRef;
    gridAdapter: RevoGridAdapter<GridRow>;
    rows: GridRow[];
    rowHeight: number;
    /** The form whose position is remembered, or null when the form keeps none. */
    scrollFormKey: string | null;
    /** Normalized scope (filters/sorting/version) the position belongs to. */
    scrollScope: string;
};

export function useGridScrollRestore({
    gridRef,
    gridAdapter,
    rows,
    rowHeight,
    scrollFormKey,
    scrollScope,
}: Args): () => void {
    /** Set once the remembered row of this scope has been restored (or given up on). */
    const scrollRestoredRef = useRef(false);
    /** The scope the flags above belong to, so a form switch re-arms them. */
    const lastScrollScopeRef = useRef<string | null>(null);
    /** A restore is travelling: its transient scroll positions must not be saved. */
    const scrollRestoringRef = useRef(false);

    useEffect(() => {
        // A different form or scope is a different position: forget the old one.
        scrollRestoredRef.current = false;
        scrollRestoringRef.current = false;
    }, [scrollFormKey, scrollScope]);

    // ── Remembered position ────────────────────────────────────────────────
    // A form opens where the user left it. The stored value is the row ID, so
    // the restore looks it up in the list that is on screen NOW — a row that was
    // deleted (or hidden by a filter) simply leaves the form at its default
    // instead of silently landing on somebody else's record.
    useEffect(() => {
        if (scrollFormKey === null) return;
        if (typeof window === "undefined") return;
        // A form switch is a different position: re-arm for the new scope.
        if (lastScrollScopeRef.current !== `${scrollFormKey}\u0000${scrollScope}`) {
            lastScrollScopeRef.current = `${scrollFormKey}\u0000${scrollScope}`;
            scrollRestoredRef.current = false;
            scrollRestoringRef.current = false;
        }
        if (scrollRestoredRef.current) return;
        const grid = gridRef.current;
        if (!grid) return;
        const realRows = rows.filter((row) => !row.isPlaceholder && !row.isDeleted);
        if (realRows.length === 0) return;
        const stored = readScrollPosition(scrollFormKey, scrollScope);
        if (!stored) {
            // Nothing remembered: the form keeps its own default (the top).
            scrollRestoredRef.current = true;
            return;
        }
        const index = realRows.findIndex((row) => row.id === stored.rowId);
        // Give up after a bounded wait: the row may be filtered out for good, and
        // a form must never sit waiting for a position that cannot arrive.
        scrollRestoredRef.current = true;
        if (index <= 0) return;
        scrollRestoringRef.current = true;
        let attempts = 0;
        const attempt = () => {
            const current = gridRef.current;
            if (!current || attempts >= 20) {
                scrollRestoringRef.current = false;
                return;
            }
            attempts += 1;
            // A grid that has not laid itself out cannot scroll: wait for height.
            if (current.clientHeight < rowHeight * 3) {
                window.setTimeout(attempt, 80);
                return;
            }
            void (async () => {
                try {
                    await current.scrollToRow(index);
                } catch {
                    // Not upgraded yet — retried below.
                }
                window.setTimeout(() => {
                    const settled = gridRef.current;
                    const scroller = settled?.querySelector(
                        "revogr-scroll-virtual.vertical",
                    ) as HTMLElement | null;
                    if (!settled || attempts >= 20) {
                        scrollRestoringRef.current = false;
                        return;
                    }
                    if (Math.abs((scroller?.scrollTop ?? 0) - index * rowHeight) <= rowHeight * 2) {
                        scrollRestoringRef.current = false;
                        return;
                    }
                    attempt();
                }, 120);
            })();
        };
        attempt();
    }, [rows, rowHeight, scrollFormKey, scrollScope]);

    const rememberScrollPosition = useCallback(() => {
        if (scrollFormKey === null) return;
        const grid = gridRef.current;
        if (!grid) return;
        if (!scrollRestoredRef.current || scrollRestoringRef.current) return;
        void (async () => {
            try {
                const [visible, scroller] = await Promise.all([
                    gridAdapter.getVisibleRows(rows),
                    Promise.resolve(
                        grid.querySelector("revogr-scroll-virtual.vertical") as HTMLElement | null,
                    ),
                ]);
                const top = scroller?.scrollTop ?? 0;
                const index = Math.max(0, Math.round(top / rowHeight));
                const row = (visible as GridRow[])[index];
                // A placeholder slot (the «＋» add-row) is not a record: keep the
                // last real position instead of forgetting the form.
                if (!row || row.isPlaceholder || row.isDeleted) return;
                writeScrollPosition(scrollFormKey, { rowId: row.id, scope: scrollScope });
            } catch {
                // Grid disposed mid-read — the next scroll saves instead.
            }
        })();
    }, [gridAdapter, rowHeight, rows, scrollFormKey, scrollScope]);

    /** Debounced save: a scroll fires dozens of events, one write is enough. */
    const scrollRememberTimerRef = useRef<number | undefined>(undefined);
    const scheduleScrollRemember = useCallback(() => {
        if (scrollFormKey === null) return;
        if (scrollRememberTimerRef.current !== undefined) {
            window.clearTimeout(scrollRememberTimerRef.current);
        }
        scrollRememberTimerRef.current = window.setTimeout(() => {
            scrollRememberTimerRef.current = undefined;
            rememberScrollPosition();
        }, 250);
    }, [rememberScrollPosition, scrollFormKey]);

    useEffect(
        () => () => {
            if (scrollRememberTimerRef.current !== undefined) {
                window.clearTimeout(scrollRememberTimerRef.current);
            }
        },
        [],
    );

    useEffect(() => {
        const grid = gridRef.current;
        if (!grid || scrollFormKey === null) return;
        const onScroll = () => scheduleScrollRemember();
        grid.addEventListener("scrollchange", onScroll);
        return () => {
            grid.removeEventListener("scrollchange", onScroll);
        };
    }, [scheduleScrollRemember, scrollFormKey]);

    return scheduleScrollRemember;
}
