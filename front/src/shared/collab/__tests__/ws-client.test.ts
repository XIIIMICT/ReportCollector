import { afterEach, beforeEach, expect, mock, test } from "bun:test";

import { CollabWsClient, getCollabSessionId } from "../ws-client";

class TestSocket {
    static OPEN = 1;
    static instances: TestSocket[] = [];
    readyState = 1;
    onopen: (() => void) | null = null;
    onclose: ((event: CloseEvent) => void) | null = null;
    onerror: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    sent: unknown[] = [];
    constructor(readonly url: string) {
        TestSocket.instances.push(this);
    }
    send(value: string) {
        this.sent.push(JSON.parse(value));
    }
    close() {
        this.readyState = 3;
        this.onclose?.({ code: 1000, reason: "", wasClean: true } as CloseEvent);
    }
    receive(value: unknown) {
        this.onmessage?.({ data: JSON.stringify(value) });
    }
}

const originalFetch = globalThis.fetch;
const originalSocket = globalThis.WebSocket;
let client: CollabWsClient;
beforeEach(() => {
    TestSocket.instances = [];
    globalThis.WebSocket = TestSocket as unknown as typeof WebSocket;
    globalThis.fetch = Object.assign(
        async () =>
            Response.json({
                ticket: "test-ticket",
                wsUrl: "ws://127.0.0.1:8000/api/ws/renovation",
            }),
        { preconnect: originalFetch.preconnect },
    );
});
afterEach(() => {
    client?.disconnect();
    globalThis.fetch = originalFetch;
    globalThis.WebSocket = originalSocket;
});

async function connected(handlers: ConstructorParameters<typeof CollabWsClient>[1] = {}) {
    client = new CollabWsClient(
        { projectKey: "renovation", section: "plan", reportMonth: null, scope: null },
        handlers,
    );
    client.connect();
    for (let attempt = 0; attempt < 20 && !TestSocket.instances.length; attempt++)
        await Promise.resolve();
    expect(TestSocket.instances).toHaveLength(1);
    const socket = TestSocket.instances[0];
    if (!socket) throw new Error("Socket not created");
    socket.onopen?.();
    return socket;
}

test("ticket URL carries the page session and project scope", async () => {
    const status = mock();
    const socket = await connected({ onConnectionChange: status });
    const url = new URL(socket.url);
    expect(url.searchParams.get("sessionId")).toBe(getCollabSessionId());
    expect(url.searchParams.get("project")).toBe("renovation");
    expect(url.searchParams.get("ticket")).toBe("test-ticket");
    expect(status.mock.calls).toEqual([["connecting"], ["online"]]);
});

test("reports the browser close code before reconnecting", async () => {
    const closed = mock();
    const socket = await connected({ onConnectionClose: closed });
    socket.onclose?.({ code: 1013, reason: "relay unavailable", wasClean: false } as CloseEvent);
    expect(closed).toHaveBeenCalledWith({
        code: 1013,
        reason: "relay unavailable",
        wasClean: false,
    });
});

test("releases only granted leases and forwards denial", async () => {
    const denied = mock();
    const socket = await connected({ onLeaseDenied: denied });
    client.acquireLeases(["a", "b"]);
    expect(socket.sent.at(-1)).toEqual({ type: "lease:acquire", cells: ["a", "b"] });
    socket.receive({ type: "lease:granted", cells: ["a"] });
    socket.receive({ type: "lease:denied", cells: [{ key: "b", user: { userId: "other" } }] });
    expect(denied).toHaveBeenCalledWith(["b"], { userId: "other" });
    client.releaseLeases(["a", "b"]);
    expect(socket.sent.at(-1)).toEqual({ type: "lease:release", cells: ["a"] });
    const count = socket.sent.length;
    client.releaseLeases(["a"]);
    expect(socket.sent).toHaveLength(count);
});

test("another tab preempts selection, an own echo does not", async () => {
    const preempted = mock();
    const socket = await connected({ onSelectionPreempted: preempted });
    socket.receive({ type: "hello", user: { userId: "me" }, members: {} });
    const selection = { label: "A1", cellKeys: ["a"] };
    client.setSelection(selection);
    socket.receive({ type: "presence:update", members: { me: { userId: "me", selection } } });
    expect(preempted).not.toHaveBeenCalled();
    socket.receive({
        type: "presence:update",
        members: { me: { userId: "me", selection: { label: "B1", cellKeys: ["b"] } } },
    });
    expect(preempted).toHaveBeenCalledTimes(1);
});

test("ignores malformed messages without breaking subsequent updates", async () => {
    const changed = mock();
    const socket = await connected({ onCellChanged: changed });
    socket.onmessage?.({ data: "not json" });
    socket.receive(null);
    socket.receive({ type: "cell:changed", rowId: "row", value: 7 });
    expect(changed).toHaveBeenCalledWith({ type: "cell:changed", rowId: "row", value: 7 });
});

test("a delayed own presence echo cannot clear a newer selection", async () => {
    const preempted = mock();
    const socket = await connected({ onSelectionPreempted: preempted });
    socket.receive({ type: "hello", user: { userId: "me" }, members: {} });
    client.setSelection({ label: "B1", cellKeys: ["b"] });
    socket.receive({
        type: "presence:update",
        members: {
            me: {
                userId: "me",
                sessionId: getCollabSessionId(),
                selection: { label: "A1", cellKeys: ["a"] },
            },
        },
    });
    expect(preempted).not.toHaveBeenCalled();
});

test("disconnect during ticket fetch does not open a leaked socket", async () => {
    let resolve!: (response: Response) => void;
    globalThis.fetch = Object.assign(
        () =>
            new Promise<Response>((done) => {
                resolve = done;
            }),
        { preconnect: originalFetch.preconnect },
    );
    client = new CollabWsClient(
        { projectKey: "raw_materials", section: "prices", reportMonth: null, scope: null },
        {},
    );
    client.connect();
    client.disconnect();
    resolve(Response.json({ ticket: "late" }));
    for (let attempt = 0; attempt < 20; attempt++) await Promise.resolve();
    expect(TestSocket.instances).toHaveLength(0);
});

test("a restored hello requests authoritative data, initial hello does not", async () => {
    const reconnect = mock();
    const socket = await connected({ onReconnect: reconnect });
    socket.receive({ type: "hello", user: { userId: "me" }, members: {} });
    expect(reconnect).not.toHaveBeenCalled();
    socket.receive({ type: "hello", user: { userId: "me" }, members: {} });
    expect(reconnect).toHaveBeenCalledTimes(1);
});

type SeenMember = { userId: string; name?: string };

/** Capture both member callbacks: hello is authoritative, deltas are merges. */
function memberWatcher() {
    const seen: SeenMember[][] = [];
    const onHello = (_me: unknown, members: SeenMember[]) => seen.push(members);
    const onMembers = (members: SeenMember[]) => seen.push(members);
    const ids = () => (seen.at(-1) ?? []).map((member) => member.userId);
    return { onHello, onMembers, ids, seen };
}

test("delta presence merges idempotently and ignores stale frames", async () => {
    const watch = memberWatcher();
    const socket = await connected(watch);
    socket.receive({
        type: "hello",
        user: { userId: "me" },
        members: { me: { userId: "me" }, a: { userId: "a", name: "A" } },
    });
    expect(watch.ids()).toEqual(["me", "a"]);

    socket.receive({
        type: "presence:update",
        seq: 10,
        changes: [
            { userId: "b", member: { userId: "b", name: "B" } },
            { userId: "a", member: null },
        ],
    });
    expect(watch.ids()).toEqual(["me", "b"]);

    // Same seq again, and an older seq from the other worker: neither may
    // resurrect `a` or change the merged view.
    socket.receive({
        type: "presence:update",
        seq: 10,
        changes: [{ userId: "a", member: { userId: "a" } }],
    });
    socket.receive({
        type: "presence:update",
        seq: 9,
        changes: [{ userId: "c", member: { userId: "c" } }],
    });
    expect(watch.ids()).toEqual(["me", "b"]);
    expect(watch.seen).toHaveLength(2); // hello + the accepted delta only

    socket.receive({
        type: "presence:update",
        seq: 11,
        changes: [{ userId: "b", member: { userId: "b", name: "B2" } }],
    });
    expect(watch.ids()).toEqual(["me", "b"]);
    expect(watch.seen.at(-1)?.[1]?.name).toBe("B2");
});

test("full repair snapshot replaces state only when newer; hello resets the seq", async () => {
    const watch = memberWatcher();
    const socket = await connected(watch);
    socket.receive({ type: "hello", user: { userId: "me" }, members: { me: { userId: "me" } } });

    socket.receive({ type: "presence:update", seq: 20, members: { x: { userId: "x" } } });
    expect(watch.ids()).toEqual(["x"]);
    socket.receive({ type: "presence:update", seq: 15, members: { y: { userId: "y" } } });
    expect(watch.ids()).toEqual(["x"]);

    // A reconnect is authoritative even though its (seq-less) hello would
    // otherwise look older: it resets the sequence for the fresh socket.
    socket.receive({ type: "hello", user: { userId: "me" }, members: { me: { userId: "me" } } });
    expect(watch.ids()).toEqual(["me"]);
    socket.receive({
        type: "presence:update",
        seq: 1,
        changes: [{ userId: "z", member: { userId: "z" } }],
    });
    expect(watch.ids()).toEqual(["me", "z"]);
});

test("selection preemption also works through a delta frame", async () => {
    const preempted = mock();
    const socket = await connected({ onSelectionPreempted: preempted });
    socket.receive({ type: "hello", user: { userId: "me" }, members: { me: { userId: "me" } } });
    client.setSelection({ label: "A1", cellKeys: ["a"] });
    socket.receive({
        type: "presence:update",
        seq: 5,
        changes: [
            { userId: "me", member: { userId: "me", selection: { label: "A1", cellKeys: ["a"] } } },
        ],
    });
    expect(preempted).not.toHaveBeenCalled();
    socket.receive({
        type: "presence:update",
        seq: 6,
        changes: [
            { userId: "me", member: { userId: "me", selection: { label: "B1", cellKeys: ["b"] } } },
        ],
    });
    expect(preempted).toHaveBeenCalledTimes(1);
});
