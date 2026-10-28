import { expect, test } from "bun:test";

import { ScopedMutationQueue, StaleMutationScopeError } from "../mutation-queue";

test("reserves concurrent writes in input order", async () => {
    const queue = new ScopedMutationQueue();
    queue.setScope("a");
    const calls: string[] = [];
    let release!: () => void;
    const first = queue.enqueue("a", async () => {
        calls.push("first:start");
        await new Promise<void>((resolve) => {
            release = resolve;
        });
        calls.push("first:end");
        return 1;
    });
    const second = queue.enqueue("a", async () => {
        calls.push("second");
        return 2;
    });
    await Promise.resolve();
    expect(calls).toEqual(["first:start"]);
    release();
    await expect(first).resolves.toBe(1);
    await expect(second).resolves.toBe(2);
    expect(calls).toEqual(["first:start", "first:end", "second"]);
});

test("a failed item does not strand the following write", async () => {
    const queue = new ScopedMutationQueue();
    queue.setScope("a");
    const second = queue.enqueue("a", async () => {
        throw new Error("failed");
    });
    const third = queue.enqueue("a", async () => "ok");
    await expect(second).rejects.toThrow("failed");
    await expect(third).resolves.toBe("ok");
});

test("scope switch rejects work that was waiting in the old scope", async () => {
    const queue = new ScopedMutationQueue();
    queue.setScope("old");
    let release!: () => void;
    const first = queue.enqueue(
        "old",
        () =>
            new Promise<void>((resolve) => {
                release = resolve;
            }),
    );
    const stale = queue.enqueue("old", async () => "must not run");
    await Promise.resolve();
    queue.setScope("new");
    release();
    await expect(first).resolves.toBeUndefined();
    await expect(stale).rejects.toBeInstanceOf(StaleMutationScopeError);
    await expect(queue.enqueue("new", async () => "new")).resolves.toBe("new");
});
