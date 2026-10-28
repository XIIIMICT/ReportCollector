"use client";

import { useEffect, useState, type DependencyList } from "react";

export type ScopedAsyncDataState<T> = {
    data: T | null;
    error: string | null;
    loading: boolean;
    /** Scope for which `data` was successfully loaded. */
    loadedScope: string | null;
    /** The scope requested by the latest load. */
    requestedScope: string;
    /** Increments after every successful load, including same-scope refreshes. */
    loadVersion: number;
};

/**
 * Load data while retaining the previous result as a stale, read-only view
 * during a scope transition. A cancelled request can never publish data for a
 * newer scope. The caller owns the dependency list, just like useAsyncData.
 */
export function useScopedAsyncData<T>(
    scope: string,
    loader: () => Promise<T>,
    deps: DependencyList,
): ScopedAsyncDataState<T> {
    const [state, setState] = useState<ScopedAsyncDataState<T>>({
        data: null,
        error: null,
        loading: true,
        loadedScope: null,
        requestedScope: scope,
        loadVersion: 0,
    });

    // oxlint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => {
        let cancelled = false;
        setState((current) => ({
            ...current,
            error: null,
            loading: true,
            requestedScope: scope,
        }));

        async function load() {
            try {
                const result = await loader();
                if (!cancelled) {
                    setState((current) => ({
                        data: result,
                        error: null,
                        loading: false,
                        loadedScope: scope,
                        requestedScope: scope,
                        loadVersion: current.loadVersion + 1,
                    }));
                }
            } catch (reason) {
                if (!cancelled) {
                    setState((current) => ({
                        ...current,
                        error: reason instanceof Error ? reason.message : "Ошибка загрузки данных.",
                        loading: false,
                        requestedScope: scope,
                    }));
                }
            }
        }

        void load();
        return () => {
            cancelled = true;
        };
        // The caller owns the dependency list and passes it through unchanged.
        // oxlint-disable-next-line react-hooks/exhaustive-deps
    }, deps);

    return state;
}
