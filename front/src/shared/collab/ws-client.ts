export type CollabUser = {
    userId: string;
    name: string;
    email: string;
    sessionId: string;
};

export type CollabMember = CollabUser & {
    selection?: Record<string, unknown> | null;
    /** Активы, к которым у участника есть доступ (для скоупа присутствия). */
    assets?: string[];
    /** Роль участника в проекте («Администратор», «Работник по активам»). */
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
};

const HEARTBEAT_INTERVAL_MS = 15000;
const LEASE_RENEW_INTERVAL_MS = 10000;
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 16000;

/**
 * Бэкенд отдаёт участников комнаты как map {userId: member},
 * клиент работает со списком — нормализуем на границе протокола.
 */
function toMembers(raw: unknown): CollabMember[] {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.values(raw as Record<string, CollabMember>).filter(
        (member): member is CollabMember => Boolean(member) && typeof member === "object",
    );
}

/** Единый WS-эндпоинт бэкенда: проект передаётся в query (комнаты изолируются по нему). */
export const COLLAB_WS_PATH = "/api/ws/raw-materials";

export class CollabWsClient {
    private readonly sessionId = crypto.randomUUID();
    private readonly clientId = crypto.randomUUID();
    private socket: WebSocket | null = null;
    private reconnectAttempt = 0;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    private renewTimer: ReturnType<typeof setInterval> | null = null;
    private ownedLeases = new Set<string>();
    private closed = false;

    constructor(
        private readonly room: { projectKey: string; section: string; reportMonth: string | null },
        private readonly handlers: CollabHandlers,
    ) {}

    connect(): void {
        this.closed = false;
        void this.open();
    }

    disconnect(): void {
        this.closed = true;
        this.clearTimers();
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
    }

    setSelection(selection: Record<string, unknown> | null): void {
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
                this.handlers.onHello?.(message.user as CollabUser, toMembers(message.members));
                break;
            case "presence:update":
                this.handlers.onMembers?.(toMembers(message.members));
                break;
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

    private startHeartbeat(): void {
        this.heartbeatTimer = setInterval(() => {
            this.send({ type: "ping" });
        }, HEARTBEAT_INTERVAL_MS);
    }

    private startLeaseRenewal(): void {
        this.renewTimer = setInterval(() => {
            if (!this.ownedLeases.size) return;
            this.send({ type: "lease:renew", cells: [...this.ownedLeases] });
        }, LEASE_RENEW_INTERVAL_MS);
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
