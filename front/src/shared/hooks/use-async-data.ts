"use client";

import { useEffect, useState, type DependencyList } from "react";

type AsyncDataState<T> = {
    data: T | null;
    error: string | null;
};

/**
 * Loads data via `loader` and restarts the load when `deps` change.
 *
 * Cancels a stale request on unmount or dependency change,
 * normalizes errors into text for the user.
 */
export function useAsyncData<T>(loader: () => Promise<T>, deps: DependencyList): AsyncDataState<T> {
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let isCancelled = false;
        setError(null);

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
            }
        }

        void load();

        return () => {
            isCancelled = true;
        };
    }, deps);

    return { data, error };
}
