/**
 * The plan grid's chunk loader.
 *
 * Two "obvious" optimisations broke the grid and must not come back; both are
 * documented in `chunk-loader.ts`:
 *
 *  - resolving a superseded chunk with `{ data: [] }` — the plugin marks a chunk
 *    loaded on any resolved answer and never retries it, so those rows stayed
 *    blank when the user scrolled back;
 *  - rejecting a superseded chunk — `loadChunk` has no `catch`, so the
 *    rejection escapes as an unhandled `Runtime AbortError`.
 *
 * The loader therefore forwards each request exactly once and always resolves
 * with the server's rows.
 */

import { describe, expect, test } from "bun:test";

import { createChunkLoader } from "@/features/renovation/chunk-loader";

describe("createChunkLoader", () => {
    test("resolves with the rows the server returned", async () => {
        const load = createChunkLoader<{ id: number }>(async (offset) => ({
            rows: [{ id: offset }],
            total: 500,
        }));

        await expect(load(0, 100)).resolves.toEqual({ data: [{ id: 0 }], total: 500 });
        await expect(load(100, 100)).resolves.toEqual({ data: [{ id: 100 }], total: 500 });
    });

    test("forwards the offset and limit unchanged", async () => {
        const calls: Array<[number, number]> = [];
        const load = createChunkLoader<number>(async (offset, limit) => {
            calls.push([offset, limit]);
            return { rows: [], total: 0 };
        });

        await load(700, 100);
        expect(calls).toEqual([[700, 100]]);
    });

    test("never resolves a chunk empty on its own", async () => {
        // An empty chunk may only come from the server: the plugin treats ANY
        // resolved chunk as loaded, so a locally invented empty one is a hole.
        const load = createChunkLoader<number>(async () => ({ rows: [1, 2, 3], total: 3 }));

        await expect(load(0, 100)).resolves.toEqual({ data: [1, 2, 3], total: 3 });
    });

    test("lets a genuine server failure through", async () => {
        // The grid shows its own error state; swallowing it would leave a
        // silently blank window.
        const load = createChunkLoader<number>(async () => {
            throw new Error("сервер недоступен");
        });

        await expect(load(0, 100)).rejects.toThrow("сервер недоступен");
    });
});
