import { afterEach, beforeEach, expect, mock, test } from "bun:test";

import { CollabWsClient, getCollabSessionId } from "../ws-client";

class TestSocket {
    static OPEN = 1;
    static instances: TestSocket[] = [];
    readyState = 1;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
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
        this.onclose?.();
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
        { projectKey: "renovation", section: "plan", reportMonth: null },
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
        { projectKey: "raw_materials", section: "prices", reportMonth: null },
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
