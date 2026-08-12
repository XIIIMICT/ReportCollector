"use client";

import { useEffect, useState, type DependencyList } from "react";

type AsyncDataState<T> = {
    data: T | null;
    error: string | null;
};

/**
 * Загружает данные через `loader` и перезапускает загрузку при изменении `deps`.
 *
 * Отменяет устаревший запрос при размонтировании или смене зависимостей,
 * ошибки нормализует в текст для показа пользователю.
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
