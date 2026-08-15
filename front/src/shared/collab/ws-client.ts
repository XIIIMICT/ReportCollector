export type CollabUser = {
    userId: string;
    name: string;
    email: string;
    sessionId: string;
};

export type CollabMember = CollabUser & {
    selection?: Record<string, unknown> | null;
    /** Assets the member has access to (for presence scoping). */
    assets?: string[];
    /** The member's role in the project (admin or asset_worker). */
    role?: string;
};

export type CollabStatus = "offline" | "connecting" | "online";

export type CollabCellChanged = {
    section: string;
    reportMonth: string | null;
    rowId: string;
    field: string;
    value: unknown;
    revision: number;
    actionId: number;
    user: { userId: string; name: string; email: string };
    at: string;
};

export type CollabHandlers = {
    onHello?: (me: CollabUser, members: CollabMember[]) => void;
    onMembers?: (members: CollabMember[]) => void;
    onCellLocked?: (key: string, user: CollabUser) => void;
    onCellUnlocked?: (key: string, user: CollabUser) => void;
    onCellChanged?: (event: CollabCellChanged) => void;
    onConnectionChange?: (status: CollabStatus) => void;
    onLeaseDenied?: (keys: string[], user: CollabUser) => void;
    /**
     * Another tab of the same account took over the user's selection
     * (the backend keeps one presence entry per user). The local selection
     * must be cleared silently — the server entry already belongs to the
     * other tab.
     */
    onSelectionPreempted?: () => void;
};

/**
 * Whether two presence selections are identical. The backend stores the
 * selection object as sent ({label, cellKeys}), so a plain compare of the
 * label plus the cell keys is enough to tell our own echo from another
 * tab's takeover.
 */
function sameSelection(
    a: Record<string, unknown> | null | undefined,
    b: Record<string, unknown> | null | undefined,
): boolean {
    if (a === b) return true;
    if (!a || !b) return false;
    if (a.label !== b.label) return false;
    const aKeys = Array.isArray(a.cellKeys) ? (a.cellKeys as unknown[]) : [];
    const bKeys = Array.isArray(b.cellKeys) ? (b.cellKeys as unknown[]) : [];
    return aKeys.length === bKeys.length && aKeys.every((key, index) => key === bKeys[index]);
}

const HEARTBEAT_INTERVAL_MS = 15000;
const LEASE_RENEW_INTERVAL_MS = 10000;
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 16000;
// How much inactivity (mouse/keyboard/hidden tab) is allowed before the
// member is considered gone: leases are released immediately, presence fades
// by the server TTL (25s). You can't "block" a cell just by walking away:
// without renewal the lease expires in 20s.
const IDLE_TIMEOUT_MS = 120000;

// One browser session = one page load: the session id is created once per
// page and shared by the WebSocket (room presence) and the REST edit calls
// (undo/redo scoping). A page reload gets a fresh id, so undo/redo history
// never survives a reload.
let pageSessionId: string | null = null;

export function getCollabSessionId(): string {
    pageSessionId ??= crypto.randomUUID();
    return pageSessionId;
}

/**
 * The backend returns room members as a map {userId: member},
 * the client works with a list — normalize at the protocol boundary.
 */
function toMembers(raw: unknown): CollabMember[] {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.values(raw as Record<string, CollabMember>).filter(
        (member): member is CollabMember => Boolean(member) && typeof member === "object",
    );
}

/** The single backend WS endpoint: the project goes in the query (rooms are isolated by it). */
export const COLLAB_WS_PATH = "/api/ws/raw-materials";

export class CollabWsClient {
    private readonly sessionId = getCollabSessionId();
    private readonly clientId = crypto.randomUUID();
    private socket: WebSocket | null = null;
    private reconnectAttempt = 0;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    private renewTimer: ReturnType<typeof setInterval> | null = null;
    private ownedLeases = new Set<string>();
    private closed = false;
    /** The moment of the user's last activity (mouse, keyboard, focus). */
    private lastActivityAt = Date.now();
    /** Whether the member is away: inactivity > IDLE_TIMEOUT_MS or a hidden tab. */
    private away = false;
    private lastSelection: Record<string, unknown> | null = null;
    private meUserId: string | null = null;
    private readonly activityDetach: Array<() => void> = [];

    constructor(
        private readonly room: { projectKey: string; section: string; reportMonth: string | null },
        private readonly handlers: CollabHandlers,
    ) {}

    connect(): void {
        this.closed = false;
        this.attachActivityListeners();
        void this.open();
    }

    disconnect(): void {
        this.closed = true;
        this.clearTimers();
        this.detachActivityListeners();
        this.ownedLeases.clear();
        this.socket?.close();
        this.socket = null;
    }

    acquireLeases(keys: string[]): void {
        this.send({ type: "lease:acquire", cells: keys });
    }

    releaseLeases(keys: string[]): void {
        const owned = keys.filter((key) => this.ownedLeases.has(key));
        if (!owned.length) return;
        this.send({ type: "lease:release", cells: owned });
        for (const key of owned) this.ownedLeases.delete(key);
    }

    setSelection(selection: Record<string, unknown> | null): void {
        this.lastSelection = selection;
        this.send({ type: "presence", selection });
    }

    private async open(): Promise<void> {
        this.handlers.onConnectionChange?.("connecting");
        try {
            const ticketPath = `/api/collab/ticket?project=${encodeURIComponent(this.room.projectKey)}`;
            const response = await fetch(ticketPath, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Тикет не получен: ${response.status}.`);
            }
            const payload = (await response.json()) as { ticket: string; wsUrl?: string };
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const query = new URLSearchParams({
                project: this.room.projectKey,
                section: this.room.section,
                reportMonth: this.room.reportMonth ?? "",
                clientId: this.clientId,
                sessionId: this.sessionId,
                ticket: payload.ticket,
            });
            const baseUrl = payload.wsUrl?.trim()
                ? payload.wsUrl
                : `${protocol}//${window.location.host}${COLLAB_WS_PATH}`;
            const socket = new WebSocket(`${baseUrl}?${query}`);
            this.socket = socket;
            socket.onopen = () => {
                this.reconnectAttempt = 0;
                this.handlers.onConnectionChange?.("online");
                this.startHeartbeat();
                this.startLeaseRenewal();
            };
            socket.onmessage = (event) => this.handleMessage(event.data);
            socket.onclose = () => this.handleClose();
            socket.onerror = () => socket.close();
        } catch {
            this.scheduleReconnect();
        }
    }

    private handleMessage(raw: unknown): void {
        let message: Record<string, unknown>;
        try {
            message = JSON.parse(String(raw)) as Record<string, unknown>;
        } catch {
            return;
        }
        switch (message.type) {
            case "hello":
                this.meUserId = (message.user as CollabUser | undefined)?.userId ?? null;
                this.handlers.onHello?.(message.user as CollabUser, toMembers(message.members));
                break;
            case "presence:update": {
                const members = toMembers(message.members);
                // One presence entry per user: if MY user's entry carries a
                // selection I didn't send, another tab of the same account
                // took it over — clear mine locally (silently).
                const mine = this.meUserId
                    ? members.find((member) => member.userId === this.meUserId)
                    : null;
                if (
                    mine &&
                    this.lastSelection !== null &&
                    !sameSelection(this.lastSelection, mine.selection)
                ) {
                    // Don't resurrect the stale selection on reconnect or
                    // after returning from away.
                    this.lastSelection = null;
                    this.handlers.onSelectionPreempted?.();
                }
                this.handlers.onMembers?.(members);
                break;
            }
            case "cell:locked":
                this.handlers.onCellLocked?.(String(message.key), message.user as CollabUser);
                break;
            case "cell:unlocked":
                this.handlers.onCellUnlocked?.(String(message.key), message.user as CollabUser);
                break;
            case "cell:changed":
                this.handlers.onCellChanged?.(message as unknown as CollabCellChanged);
                break;
            case "lease:granted":
                for (const key of (message.cells as string[]) ?? []) {
                    this.ownedLeases.add(key);
                }
                break;
            case "lease:denied":
                if (message.cells && Array.isArray(message.cells)) {
                    const denied = message.cells as Array<{ key: string; user: CollabUser }>;
                    if (denied.length && denied[0]?.user) {
                        this.handlers.onLeaseDenied?.(
                            denied.map((cell) => cell.key),
                            denied[0].user,
                        );
                    }
                }
                break;
            case "pong":
            case "error":
                break;
        }
    }

    private handleClose(): void {
        this.clearTimers();
        this.ownedLeases.clear();
        this.socket = null;
        this.handlers.onConnectionChange?.("offline");
        this.scheduleReconnect();
    }

    private scheduleReconnect(): void {
        if (this.closed || this.reconnectTimer !== null) return;
        const delay = Math.min(
            RECONNECT_MAX_DELAY_MS,
            RECONNECT_BASE_DELAY_MS * 2 ** this.reconnectAttempt,
        );
        this.reconnectAttempt += 1;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            void this.open();
        }, delay);
    }

    /**
     * Recompute the "away" state: inactivity beyond IDLE_TIMEOUT_MS or a
     * hidden tab. On leaving, release all leases (cells free up immediately,
     * not after the 20s TTL); on return, show up in «На форме» again.
     */
    private refreshAwayState(): void {
        const away = document.hidden || Date.now() - this.lastActivityAt > IDLE_TIMEOUT_MS;
        if (this.away === away) return;
        this.away = away;
        if (away) {
            this.releaseLeases([...this.ownedLeases]);
        } else if (this.lastSelection !== null) {
            this.send({ type: "presence", selection: this.lastSelection });
        }
    }

    private startHeartbeat(): void {
        this.heartbeatTimer = setInterval(() => {
            this.refreshAwayState();
            if (this.away) return;
            this.send({ type: "ping" });
        }, HEARTBEAT_INTERVAL_MS);
    }

    private startLeaseRenewal(): void {
        this.renewTimer = setInterval(() => {
            this.refreshAwayState();
            if (this.away || !this.ownedLeases.size) return;
            this.send({ type: "lease:renew", cells: [...this.ownedLeases] });
        }, LEASE_RENEW_INTERVAL_MS);
    }

    private attachActivityListeners(): void {
        this.lastActivityAt = Date.now();
        this.away = false;
        const bump = () => {
            this.lastActivityAt = Date.now();
        };
        const bumpThrottled = () => {
            if (Date.now() - this.lastActivityAt > 5000) this.lastActivityAt = Date.now();
        };
        const add = (
            target: Document | Window,
            name: string,
            handler: () => void,
            options?: AddEventListenerOptions,
        ) => {
            target.addEventListener(name, handler, options);
            this.activityDetach.push(() => target.removeEventListener(name, handler, options));
        };
        for (const name of ["pointerdown", "keydown", "wheel", "touchstart"] as const) {
            add(document, name, bump, { capture: true, passive: true });
        }
        add(document, "pointermove", bumpThrottled, { capture: true, passive: true });
        add(window, "focus", bump);
        add(document, "visibilitychange", () => this.refreshAwayState());
    }

    private detachActivityListeners(): void {
        for (const detach of this.activityDetach.splice(0)) detach();
    }

    private clearTimers(): void {
        if (this.reconnectTimer !== null) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.heartbeatTimer !== null) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
        if (this.renewTimer !== null) {
            clearInterval(this.renewTimer);
            this.renewTimer = null;
        }
    }

    private send(payload: Record<string, unknown>): void {
        const socket = this.socket;
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify(payload));
    }
}
