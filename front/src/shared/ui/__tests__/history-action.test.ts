import { expect, test } from "bun:test";

import { applyHistoryResult, runHistoryAction, type PendingHistorySkip } from "../history-action";

test("replay waits for the current write and skips only the matching direction", async () => {
    let finish!: () => void;
    const write = new Promise<void>((resolve) => {
        finish = resolve;
    });
    const calls: unknown[] = [];
    const replay = runHistoryAction({
        direction: "undo",
        pendingWrite: write,
        pendingSkip: { actionId: 42, direction: "undo" },
        request: async (skip) => {
            calls.push(skip);
            return "result";
        },
        apply: (result, direction) => {
            calls.push(result, direction);
        },
    });
    await Promise.resolve();
    expect(calls).toEqual([]);
    finish();
    await replay;
    expect(calls).toEqual([42, "result", "undo"]);
    await runHistoryAction({
        direction: "redo",
        pendingWrite: null,
        pendingSkip: { actionId: 42, direction: "undo" },
        request: async (skip) => {
            expect(skip).toBeNull();
            return true;
        },
        apply: () => {},
    });
});

test("applied and empty results clear conflicts and re-read history; only applied refreshes rows", () => {
    for (const applied of [true, false]) {
        let skip: PendingHistorySkip | null = { actionId: 1, direction: "undo" };
        let states = 0;
        let rows = 0;
        applyHistoryResult({
            result: { applied, reason: applied ? "applied" : "empty", conflicts: [] },
            direction: "undo",
            setPendingSkip: (next) => {
                skip = next;
            },
            refreshState: () => {
                states++;
            },
            refresh: () => {
                rows++;
            },
        });
        expect(skip).toBeNull();
        expect(states).toBe(1);
        expect(rows).toBe(applied ? 1 : 0);
    }
});

test("locked/business conflicts keep the action id for a second-click skip", () => {
    for (const reason of ["locked", "conflict"]) {
        const calls: unknown[] = [];
        applyHistoryResult({
            result: {
                applied: false,
                reason,
                conflicts: [{ actionId: 17, reason, field: "__deleted", user: "Иванов" }],
            },
            direction: "redo",
            setPendingSkip: (next) => {
                calls.push(next);
            },
            refreshState: () => {
                calls.push("state");
            },
            refresh: () => {
                calls.push("rows");
            },
            businessRuleFields: ["__created", "__deleted"],
        });
        expect(calls).toEqual([{ actionId: 17, direction: "redo" }]);
    }
});
