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
 * What to show in parentheses: an admin — «админ»; an approver — «Утверждающий»;
 * a block owner — «Ответственный за блок»; only an asset worker gets the
 * comma-joined asset list (e.g. «СНХЗ, БСК») — the central roles implicitly
 * cover every asset, so listing them adds noise.
 */
export function memberDetail(member: CollabMember): string | null {
    switch (member.role) {
        case "admin":
            return "админ";
        case "approver":
            return "Утверждающий";
        case "block_owner":
            return "Ответственный за блок";
        case "asset_worker":
            return assetsDetail(member);
        default:
            return assetsDetail(member) || member.role || null;
    }
}

function assetsDetail(member: CollabMember): string | null {
    const assets = (member.assets ?? []).filter((asset) => asset.trim().length > 0);
    if (assets.length > 0) return assets.join(", ");
    return "Работник по активам";
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
