"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    CollabWsClient,
    type CollabCellChanged,
    type CollabMember,
    type CollabStatus,
    type CollabUser,
} from "./ws-client";

export const MOVEMENT_TABLE = "rm_material_movement";
export const PRICE_TABLE = "rm_raw_material_price";
export const RAW_MATERIAL_TABLE = "rm_raw_material";
export const ASSET_TABLE = "rm_asset";
export const DELIVERY_BASIS_TABLE = "rm_delivery_basis";
export const USER_TABLE = "app_user";

export function canonicalCellKey(targetTable: string, rowId: string, field: string): string {
    return `${targetTable}/${rowId}/${field}`;
}

export type CollabRoom = {
    status: CollabStatus;
    /** Текущий пользователь (для отображения «Вы» в списке присутствия). */
    me: CollabUser | null;
    members: CollabMember[];
    /** Чужие арендованные ячейки: canonical key → пользователь. */
    lockedCells: ReadonlyMap<string, CollabUser>;
    acquireLeases: (keys: string[]) => void;
    releaseLeases: (keys: string[]) => void;
    setSelection: (selection: Record<string, unknown> | null) => void;
};

export function useCollabRoom({
    projectKey,
    section,
    reportMonth,
    enabled,
    visibleAssets,
    onCellChanged,
}: {
    projectKey: string;
    section: string;
    reportMonth: string | null;
    enabled: boolean;
    /** Активы текущего пользователя: участники без пересечения не показываются. */
    visibleAssets?: string[] | null;
    onCellChanged?: (event: CollabCellChanged) => void;
}): CollabRoom {
    const clientRef = useRef<CollabWsClient | null>(null);
    const handlersRef = useRef({ onCellChanged });
    handlersRef.current.onCellChanged = onCellChanged;
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
            { projectKey, section, reportMonth },
            {
                onHello: (me, roomMembers) => {
                    mySessionIdRef.current = me.sessionId;
                    setMe(me);
                    setMembers(filterMembers(roomMembers));
                },
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
                onConnectionChange: setStatus,
            },
        );
        clientRef.current = client;
        client.connect();
        return () => {
            client.disconnect();
            clientRef.current = null;
        };
    }, [enabled, projectKey, reportMonth, section]);

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
