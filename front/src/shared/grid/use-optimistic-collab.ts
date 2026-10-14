import { useOptimistic, useCallback } from "react";

export interface OptimisticUpdate<T> {
    type: "update" | "delete" | "create";
    id: string;
    data?: T;
}

/**
 * React 19 useOptimistic hook for collaborative editing
 * Provides instant UI feedback while server request is in flight
 */
export function useOptimisticCollab<T extends { id: string }>(
    serverData: T[],
    onServerUpdate: (update: OptimisticUpdate<T>) => Promise<void>,
) {
    const [optimisticData, addOptimistic] = useOptimistic(
        serverData,
        (state: T[], update: OptimisticUpdate<T>) => {
            switch (update.type) {
                case "update":
                    return state.map((item) =>
                        item.id === update.id ? { ...item, ...update.data } : item,
                    );
                case "delete":
                    return state.filter((item) => item.id !== update.id);
                case "create":
                    return update.data ? [...state, update.data] : state;
                default:
                    return state;
            }
        },
    );

    const updateItem = useCallback(
        async (id: string, data: Partial<T>) => {
            const update: OptimisticUpdate<T> = { type: "update", id, data: data as T };
            addOptimistic(update);
            try {
                await onServerUpdate(update);
            } catch (error) {
                // Server update failed, optimistic state will revert automatically
                console.error("Failed to update item:", error);
                throw error;
            }
        },
        [addOptimistic, onServerUpdate],
    );

    const deleteItem = useCallback(
        async (id: string) => {
            const update: OptimisticUpdate<T> = { type: "delete", id };
            addOptimistic(update);
            try {
                await onServerUpdate(update);
            } catch (error) {
                console.error("Failed to delete item:", error);
                throw error;
            }
        },
        [addOptimistic, onServerUpdate],
    );

    const createItem = useCallback(
        async (data: T) => {
            const update: OptimisticUpdate<T> = { type: "create", id: data.id, data };
            addOptimistic(update);
            try {
                await onServerUpdate(update);
            } catch (error) {
                console.error("Failed to create item:", error);
                throw error;
            }
        },
        [addOptimistic, onServerUpdate],
    );

    return {
        data: optimisticData,
        updateItem,
        deleteItem,
        createItem,
    };
}

/**
 * Cell-level optimistic updates for grid editing
 */
export function useOptimisticCell<_ extends Record<string, unknown>>(
    rowId: string,
    columnId: string,
    initialValue: unknown,
    onServerUpdate: (rowId: string, columnId: string, value: unknown) => Promise<void>,
) {
    const [optimisticValue, addOptimistic] = useOptimistic(
        initialValue,
        (_state: unknown, newValue: unknown) => newValue,
    );

    const updateCell = useCallback(
        async (value: unknown) => {
            addOptimistic(value);
            try {
                await onServerUpdate(rowId, columnId, value);
            } catch (error) {
                console.error("Failed to update cell:", error);
                throw error;
            }
        },
        [addOptimistic, onServerUpdate, rowId, columnId],
    );

    return {
        value: optimisticValue,
        updateCell,
    };
}
