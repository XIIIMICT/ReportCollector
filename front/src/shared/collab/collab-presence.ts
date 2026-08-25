"use client";

import type { CollabMember, CollabStatus } from "./ws-client";

/** Long labels shown inline when the room is not online. */
export const COLLAB_STATUS_LABELS: Record<Exclude<CollabStatus, "online">, string> = {
    connecting: "Подключение к совместному редактированию...",
    offline: "Совместное редактирование недоступно",
};

/** Last name only (the first word of the full name, fullname.partition(" ")[0]). */
export function shortName(fullname: string): string {
    const first = fullname.trim().split(/\s+/)[0];
    return first || fullname;
}

/**
 * What to show in parentheses: for an admin — the role, for an asset worker —
 * the comma-joined asset list (e.g. «СНХЗ, БСК»).
 */
export function memberDetail(member: CollabMember): string | null {
    if (member.role === "admin") return "админ";
    const assets = (member.assets ?? []).filter((asset) => asset.trim().length > 0);
    if (assets.length > 0) return assets.join(", ");
    return member.role === "asset_worker" ? "Работник по активам" : member.role || null;
}

/** Cell / cell range where the member currently is (from presence). */
export function memberSelectionLabel(member: CollabMember | null): string | null {
    if (!member) return null;
    const label = member.selection?.label;
    return typeof label === "string" && label.length > 0 ? label : null;
}

/**
 * The inline presence line: «только вы» when alone, otherwise «вы, Иван,
 * Пётр…» (last names). `meId` excludes the current user from the list.
 */
export function membershipSummary(members: CollabMember[], meId: string | null): string {
    if (members.length <= 1) return "только вы";
    const others = members
        .filter((member) => member.userId !== meId)
        .sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email, "ru"));
    if (others.length === 0) return "только вы";
    return `вы, ${others.map((member) => shortName(member.name || member.email)).join(", ")}`;
}
