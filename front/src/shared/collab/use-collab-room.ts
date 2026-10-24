"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    CollabWsClient,
    type CollabCellChanged,
    type CollabMember,
    type CollabRowsChanged,
    type CollabStatus,
    type CollabUser,
} from "./ws-client";

export const MOVEMENT_TABLE = "rm_material_movements";
export const PRICE_TABLE = "rm_raw_material_prices";
export const RAW_MATERIAL_TABLE = "rm_raw_materials";
export const ASSET_TABLE = "rm_assets";
export const DELIVERY_BASIS_TABLE = "rm_delivery_bases";
export const USER_TABLE = "app_users";

export function canonicalCellKey(targetTable: string, rowId: string, field: string): string {
    return `${targetTable}/${rowId}/${field}`;
}

export type CollabRoom = {
    status: CollabStatus;
    /** The current user (for showing «Вы» in the presence list). */
    me: CollabUser | null;
    members: CollabMember[];
    /** Cells leased by others: canonical key → user. */
    lockedCells: ReadonlyMap<string, CollabUser>;
    acquireLeases: (keys: string[]) => void;
    releaseLeases: (keys: string[]) => void;
    setSelection: (selection: Record<string, unknown> | null) => void;
};

export function useCollabRoom({
    projectKey,
    section,
    reportMonth,
    scope = null,
    enabled,
    visibleAssets,
    onCellChanged,
    onRowsChanged,
    onSelectionPreempted,
    onReconnect,
}: {
    projectKey: string;
    section: string;
    reportMonth: string | null;
    /** Additional room scope, used by КППК to isolate a selected ДЗО. */
    scope?: string | null;
    enabled: boolean;
    /** The current user's assets: members without an overlap aren't shown. */
    visibleAssets?: string[] | null;
    onCellChanged?: (event: CollabCellChanged) => void;
    /** The plan row set changed elsewhere — the owner should refetch the data. */
    onRowsChanged?: (event: CollabRowsChanged) => void;
    /** Another tab of the same account took over the user's selection. */
    onSelectionPreempted?: () => void;
    onReconnect?: () => void;
}): CollabRoom {
    const clientRef = useRef<CollabWsClient | null>(null);
    const handlersRef = useRef({ onCellChanged, onRowsChanged, onSelectionPreempted, onReconnect });
    handlersRef.current.onReconnect = onReconnect;
    handlersRef.current.onCellChanged = onCellChanged;
    handlersRef.current.onRowsChanged = onRowsChanged;
    handlersRef.current.onSelectionPreempted = onSelectionPreempted;
    const assetsRef = useRef(visibleAssets);
    assetsRef.current = visibleAssets;
    const [status, setStatus] = useState<CollabStatus>("offline");
    const [me, setMe] = useState<CollabUser | null>(null);
    const [members, setMembers] = useState<CollabMember[]>([]);
    const [lockedCells, setLockedCells] = useState<Map<string, CollabUser>>(new Map());
    const mySessionIdRef = useRef<string | null>(null);

    const filterMembers = useCallback((all: CollabMember[]): CollabMember[] => {
        const visible = assetsRef.current;
        if (!visible || visible.length === 0) return all;
        return all.filter(
            (member) =>
                !member.assets ||
                member.assets.length === 0 ||
                member.assets.some((asset) => visible.includes(asset)),
        );
    }, []);

    useEffect(() => {
        if (!enabled) {
            setStatus("offline");
            setMe(null);
            setMembers([]);
            setLockedCells(new Map());
            return;
        }
        const client = new CollabWsClient(
            { projectKey, section, reportMonth, scope },
            {
                onHello: (me, roomMembers) => {
                    mySessionIdRef.current = me.sessionId;
                    setMe(me);
                    setMembers(filterMembers(roomMembers));
                },
                onReconnect: () => handlersRef.current.onReconnect?.(),
                onMembers: (roomMembers) => setMembers(filterMembers(roomMembers)),
                onCellLocked: (key, user) => {
                    if (user.sessionId === mySessionIdRef.current) return;
                    setLockedCells((current) => {
                        const next = new Map(current);
                        next.set(key, user);
                        return next;
                    });
                },
                onCellUnlocked: (key) => {
                    setLockedCells((current) => {
                        if (!current.has(key)) return current;
                        const next = new Map(current);
                        next.delete(key);
                        return next;
                    });
                },
                onCellChanged: (event) => handlersRef.current.onCellChanged?.(event),
                onRowsChanged: (event) => handlersRef.current.onRowsChanged?.(event),
                onSelectionPreempted: () => handlersRef.current.onSelectionPreempted?.(),
                onConnectionChange: (next) => {
                    if (next !== "online") {
                        // cell:unlocked events fired during the gap are lost:
                        // drop the remote lease map, otherwise cells released
                        // while disconnected stay blocked in the UI forever
                        // (server leases expire by TTL in 20s; the fresh room
                        // state rebuilds the map from live events on connect).
                        setLockedCells(new Map());
                    }
                    setStatus(next);
                },
            },
        );
        clientRef.current = client;
        client.connect();
        return () => {
            client.disconnect();
            clientRef.current = null;
        };
    }, [enabled, filterMembers, projectKey, reportMonth, scope, section]);

    const acquireLeases = useCallback((keys: string[]) => {
        clientRef.current?.acquireLeases(keys);
    }, []);

    const releaseLeases = useCallback((keys: string[]) => {
        clientRef.current?.releaseLeases(keys);
    }, []);

    const setSelection = useCallback((selection: Record<string, unknown> | null) => {
        clientRef.current?.setSelection(selection);
    }, []);

    return useMemo(
        () => ({
            status,
            me,
            members,
            lockedCells,
            acquireLeases,
            releaseLeases,
            setSelection,
        }),
        [acquireLeases, lockedCells, me, members, releaseLeases, setSelection, status],
    );
}
