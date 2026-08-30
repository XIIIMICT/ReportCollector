"use client";

import { useEffect, useState, type DependencyList } from "react";

type AsyncDataState<T> = {
    data: T | null;
    error: string | null;
    /** True until the loader for the current deps has settled. */
    loading: boolean;
};

/**
 * Loads data via `loader` and restarts the load when `deps` change.
 *
 * Cancels a stale request on unmount or dependency change (a late response is
 * ignored via the cancelled flag, so no state is written after unmount),
 * normalizes errors into text for the user. `loading` is `true` until the
 * current request settles.
 *
 * `data` is deliberately NOT reset on a deps change: callers use `data ===
 * null` as the "nothing yet" sentinel (initial load), and several loaders
 * resolve to `null` as a valid "no data" result. Reset/merge-on-switch is a
 * caller concern, not this hook's.
 */
export function useAsyncData<T>(loader: () => Promise<T>, deps: DependencyList): AsyncDataState<T> {
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let isCancelled = false;
        setError(null);
        setLoading(true);

        async function load() {
            try {
                const result = await loader();
                if (!isCancelled) {
                    setData(result);
                }
            } catch (err) {
                if (!isCancelled) {
                    setError(err instanceof Error ? err.message : "Ошибка загрузки данных.");
                }
            } finally {
                if (!isCancelled) {
                    setLoading(false);
                }
            }
        }

        void load();

        return () => {
            isCancelled = true;
        };
    }, deps);

    return { data, error, loading };
}
