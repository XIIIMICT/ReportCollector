/**
 * The undo/redo counters every action bar shows.
 *
 * They are booleans in disguise (`canUndo ? 1 : 0`), not lengths: the server
 * journal reports whether an action is available and the buttons only need
 * enabled/disabled. The apply-on-read block was copy-pasted eight times across
 * the workspaces; one copy drifting left a button enabled over an empty stack.
 */

export type UndoStateReader = () => Promise<{ canUndo: boolean; canRedo: boolean }>;

/** Write a resolved undo state into the two counters. */
export function applyUndoState(
    state: { canUndo: boolean; canRedo: boolean },
    setUndoCount: (value: number) => void,
    setRedoCount: (value: number) => void,
): void {
    setUndoCount(state.canUndo ? 1 : 0);
    setRedoCount(state.canRedo ? 1 : 0);
}

/**
 * Read the state and apply it, swallowing failures.
 *
 * A failed read must keep the previous state — silently disabling both buttons
 * because one poll failed is worse than a stale counter. `isCurrent` guards the
 * other stale case: a read that started before a newer commit or re-read must
 * not overwrite the newer state when it finally resolves.
 */
export async function refreshUndoCounters(
    fetchState: UndoStateReader,
    setUndoCount: (value: number) => void,
    setRedoCount: (value: number) => void,
    isCurrent: () => boolean = () => true,
): Promise<void> {
    try {
        const state = await fetchState();
        if (!isCurrent()) return;
        applyUndoState(state, setUndoCount, setRedoCount);
    } catch {
        // Keep the previous state.
    }
}
