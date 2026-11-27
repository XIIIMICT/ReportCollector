import { expect, test } from "bun:test";

import { buildHistoryActions } from "../action-items";
import { applyUndoState, refreshUndoCounters } from "../undo-counters";

type Counters = { canUndo: boolean; canRedo: boolean };

test("a stale undo-state read cannot disable the control after a committed edit", async () => {
    let undoCount = 0;
    let redoCount = 0;
    const setUndoCount = (value: number) => {
        undoCount = value;
    };
    const setRedoCount = (value: number) => {
        redoCount = value;
    };
    // The workspace's sequencing: a read captures the epoch, a newer write
    // bumps it, and the read is applied only while it is still current.
    let epoch = 0;
    const startRead = (state: Promise<Counters>) => {
        const current = ++epoch;
        return refreshUndoCounters(
            () => state,
            setUndoCount,
            setRedoCount,
            () => current === epoch,
        );
    };
    const undoEnabled = () =>
        buildHistoryActions({
            show: true,
            offline: false,
            canUndo: undoCount > 0,
            canRedo: redoCount > 0,
            onUndo: () => {},
            onRedo: () => {},
        }).find((item) => item.id === "undo")?.disabled === false;

    // The form mounts and reads the (empty) server stack. The snapshot is taken
    // now, but its response is delivered only after the price edit is committed.
    let resolveStale!: (state: Counters) => void;
    const staleRead = startRead(
        new Promise<Counters>((resolve) => {
            resolveStale = resolve;
        }),
    );
    epoch += 1; // the commit supersedes the in-flight read
    applyUndoState({ canUndo: true, canRedo: false }, setUndoCount, setRedoCount);
    expect(undoEnabled()).toBe(true); // enabled immediately after the edit

    resolveStale({ canUndo: false, canRedo: false });
    await staleRead;
    expect(undoCount).toBe(1);
    expect(undoEnabled()).toBe(true); // the late empty snapshot must not roll it back

    // A genuinely newer read still applies: after undo, the server reports an
    // empty undo stack and a non-empty redo stack.
    await startRead(Promise.resolve({ canUndo: false, canRedo: true }));
    expect(undoCount).toBe(0);
    expect(redoCount).toBe(1);
});
