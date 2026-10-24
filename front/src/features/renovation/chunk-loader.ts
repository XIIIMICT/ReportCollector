/**
 * Chunk loading for the plan grid's InfinityScrollPlugin.
 *
 * History — read before "optimising" this away:
 *
 * The plugin loads the viewport's chunks in a SEQUENTIAL loop (`await loadChunk`
 * per chunk), so while one request is in flight no later chunk starts. It looks
 * like a queue that should be interrupted on a fast scroll, and two attempts to
 * interrupt it both broke the grid:
 *
 *  1. Resolving a superseded chunk with `{ data: [] }` — the plugin adds a chunk
 *     to `loadedChunks` on ANY resolved answer, including an empty one, and then
 *     never asks for it again. Those rows stayed blank forever, so scrolling
 *     back up showed holes.
 *  2. Rejecting a superseded chunk (an `AbortError`) — `loadChunk` awaits
 *     `config.loadData` inside `try`/`finally` only, with no `catch`, and its
 *     caller does not catch either. The rejection escapes as an unhandled
 *     promise (`Runtime AbortError`).
 *
 * Both are the plugin's contract, not ours to change. The real fix for the slow
 * scroll was on the server: `OFFSET` on a 306k-row plan cost ~3 s per chunk and
 * the cost grew with depth, which is what made the queue back up beyond the
 * viewport. Windows are now read by keyset, so every chunk costs the same
 * (~150 ms) whatever its offset — the queue drains as fast as the user scrolls.
 *
 * So: no aborting, no empty resolutions. One request at a time, always resolved
 * with the rows the server returned.
 */

/** Rows of one chunk. */
export const CHUNK_ROWS = 100;

export type ChunkPage<T> = { rows: T[]; total: number };

/**
 * Build the `loadData` of the InfinityScroll config.
 *
 * Deliberately thin: it forwards to the loader and lets errors through only when
 * the server genuinely failed (the grid then shows its error state). A chunk is
 * never resolved empty to stand in for "cancelled" — see the module note.
 */
export function createChunkLoader<T>(
    load: (offset: number, limit: number) => Promise<ChunkPage<T>>,
): (skip: number, limit: number) => Promise<{ data: T[]; total: number }> {
    return async (skip: number, limit: number) => {
        const page = await load(skip, limit);
        return { data: page.rows, total: page.total };
    };
}
