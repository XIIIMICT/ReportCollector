"use client";

import { useMemo, useRef, type MutableRefObject } from "react";

import type { CollabRoom } from "./use-collab-room";

/** Owner of a leased / selected cell (both maps carry the same shape). */
export type RemoteCellOwner = { fullname: string; email: string };
/** Owner of a cell for the highlight tooltip (carries the selection label). */
export type RemoteCellMarker = { name: string; label: string | null };

export type RemoteCellMaps = {
    remoteLocks: ReadonlyMap<string, RemoteCellOwner>;
    remotePresence: ReadonlyMap<string, RemoteCellOwner>;
    remoteCellInfo: ReadonlyMap<string, RemoteCellMarker>;
};

/**
 * Reuse the previous Map when its content didn't change.
 *
 * Presence heartbeats replace `room.members` / `room.lockedCells` with fresh
 * references even when nothing changed. These maps feed the grid's `columns`
 * memo, so a new identity per tick rebuilt the whole 200+-column grid and froze
 * the drag cursor / janked scrolling. The content fingerprint keeps downstream
 * memos (and the grid) stable across identical ticks.
 */
function stabilized<T>(
    ref: MutableRefObject<Map<string, T>>,
    keyRef: MutableRefObject<string>,
    next: Map<string, T>,
): Map<string, T> {
    const key = JSON.stringify([...next.entries()]);
    if (key === keyRef.current) return ref.current;
    keyRef.current = key;
    ref.current = next;
    return next;
}

/**
 * The three remote-cell maps both workspaces hand to their grid:
 * leased cells, selected cells (presence) and the combined highlight marker.
 *
 * Content-stabilized, so a collab heartbeat with unchanged state does not
 * rebuild the grid.
 */
export function useRemoteCellMaps(room: CollabRoom): RemoteCellMaps {
    const locksRef = useRef<Map<string, RemoteCellOwner>>(new Map());
    const locksKeyRef = useRef("");
    const presenceRef = useRef<Map<string, RemoteCellOwner>>(new Map());
    const presenceKeyRef = useRef("");
    const cellInfoRef = useRef<Map<string, RemoteCellMarker>>(new Map());
    const cellInfoKeyRef = useRef("");

    const remoteLocks = useMemo(() => {
        const result = new Map<string, RemoteCellOwner>();
        for (const [key, user] of room.lockedCells) {
            result.set(key, { fullname: user.name || "", email: user.email || "" });
        }
        return stabilized(locksRef, locksKeyRef, result);
    }, [room.lockedCells]);

    // Cells selected by other members (presence): they're also protected from
    // overwrite, same as leased ones.
    const remotePresence = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, RemoteCellOwner>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as { cellKeys?: unknown } | null | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            for (const key of cellKeys) {
                if (!result.has(key)) {
                    result.set(key, {
                        fullname: member.name || "",
                        email: member.email || "",
                    });
                }
            }
        }
        return stabilized(presenceRef, presenceKeyRef, result);
    }, [room.me?.userId, room.members]);

    // Cells where other members are: their presence ranges (canonical keys —
    // the same for everyone regardless of the column set) plus leased cells.
    // Needed for the yellow highlight and the FIO tooltip.
    const remoteCellInfo = useMemo(() => {
        const meId = room.me?.userId ?? null;
        const result = new Map<string, RemoteCellMarker>();
        for (const member of room.members) {
            if (member.userId === meId) continue;
            const selection = member.selection as
                | { label?: unknown; cellKeys?: unknown }
                | null
                | undefined;
            const cellKeys = Array.isArray(selection?.cellKeys)
                ? selection.cellKeys.filter((key): key is string => typeof key === "string")
                : [];
            if (cellKeys.length === 0) continue;
            const name = member.name || member.email || "";
            const label =
                typeof selection?.label === "string" && selection.label ? selection.label : null;
            for (const key of cellKeys) {
                if (!result.has(key)) result.set(key, { name, label });
            }
        }
        for (const [key, user] of room.lockedCells) {
            if (!result.has(key)) {
                result.set(key, { name: user.name || "", label: null });
            }
        }
        return stabilized(cellInfoRef, cellInfoKeyRef, result);
    }, [room.lockedCells, room.me?.userId, room.members]);

    return { remoteLocks, remotePresence, remoteCellInfo };
}
