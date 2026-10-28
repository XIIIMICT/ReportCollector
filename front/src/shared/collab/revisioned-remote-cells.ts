import type { CollabCellChanged } from "./ws-client";

export type RevisionedRemoteCell = {
    value: unknown;
    revision: number;
    eventAt: string;
    receivedAt: number;
};

/** Accept only newer events, and ignore events that predate an authoritative read. */
export function applyRevisionedRemoteCell(
    current: ReadonlyMap<string, RevisionedRemoteCell>,
    key: string,
    event: Pick<CollabCellChanged, "value" | "revision" | "at">,
    readStartedAt: number | null,
    receivedAt = Date.now(),
): Map<string, RevisionedRemoteCell> {
    // The server timestamp may be behind the browser clock. Use local receive
    // time for the read boundary, while retaining the server timestamp for
    // diagnostics and human-readable traces.
    if (readStartedAt !== null && receivedAt < readStartedAt) return new Map(current);
    const previous = current.get(key);
    if (previous && previous.revision >= event.revision) return new Map(current);
    const next = new Map(current);
    next.set(key, {
        value: event.value,
        revision: event.revision,
        eventAt: event.at,
        receivedAt,
    });
    return next;
}

/** Drop overlays proven older than the latest authoritative read. */
export function reconcileRevisionedRemoteCells(
    current: ReadonlyMap<string, RevisionedRemoteCell>,
    readStartedAt: number | null,
): Map<string, RevisionedRemoteCell> {
    if (readStartedAt === null || current.size === 0) return new Map(current);
    const next = new Map<string, RevisionedRemoteCell>();
    for (const [key, value] of current) {
        if (value.receivedAt > readStartedAt) next.set(key, value);
    }
    return next.size === current.size ? new Map(current) : next;
}
