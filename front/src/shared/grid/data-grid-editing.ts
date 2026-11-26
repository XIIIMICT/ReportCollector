/**
 * Pure decision logic for DataGrid's edit events.
 *
 * RevoGrid lifecycle stays in the component: `preventDefault`, lease refs and
 * notifications are made there. These helpers only decide whether a cell edit
 * is allowed and rewrite a range patch in place, with every dependency passed
 * explicitly (no React state, callbacks or module-level lookups).
 */

import type { ColumnDescriptor, GridRow } from "./grid-config-types";

/** Owner of a leased / selected cell — the shape both collaboration maps carry. */
export type RemoteOwner = { fullname: string; email: string };

export type NormalizeOutcome = { ok: true; value: unknown } | { ok: false; message: string };

export type EditCheckArgs = {
    descriptor: ColumnDescriptor | undefined;
    row: GridRow;
    editableKeys: ReadonlySet<string>;
    canEdit: (descriptor: ColumnDescriptor, row: GridRow) => boolean;
    remoteLocks: ReadonlyMap<string, RemoteOwner>;
    remotePresence: ReadonlyMap<string, RemoteOwner>;
};

/** `activeLease` is the key I am editing myself; only `checkEdit` uses it. */
export type EditArgs = EditCheckArgs & { activeLease: string | null };

/**
 * The descriptor and canonical key are returned so the caller does not have to
 * repeat the lookup or guess a key while handling the denial.
 */
export type EditDecision =
    | { allowed: true; descriptor: ColumnDescriptor; canonicalKey: string | undefined }
    | { allowed: false; owner?: string; canonicalKey: string | undefined };

export type RangePatchArgs = {
    /** Patches are mutated in place exactly as RevoGrid handed them over. */
    patches: Record<string, GridRow>;
    models: Readonly<Record<string, GridRow | undefined>>;
    rows: readonly GridRow[];
    descriptors: ReadonlyMap<string, ColumnDescriptor>;
    editableKeys: ReadonlySet<string>;
    canEdit: (descriptor: ColumnDescriptor, row: GridRow) => boolean;
    remoteLocks: ReadonlyMap<string, RemoteOwner>;
    remotePresence: ReadonlyMap<string, RemoteOwner>;
    normalizeValue: (value: unknown, kind: ColumnDescriptor["kind"]) => NormalizeOutcome;
};

export type RangePatchResult = { skipped: Map<string, string>; invalid: string | null };

function ownerName(owner: RemoteOwner): string {
    return owner.fullname || owner.email || "другой пользователь";
}

function baseDecision(args: EditCheckArgs) {
    const { descriptor, row, editableKeys, canEdit } = args;
    if (!descriptor || !editableKeys.has(descriptor.key) || !canEdit(descriptor, row)) return null;
    return { descriptor, canonicalKey: row.canonicalCells?.[descriptor.key] };
}

function deny(canonicalKey: string | undefined, owner?: RemoteOwner): EditDecision {
    return owner
        ? { allowed: false, canonicalKey, owner: ownerName(owner) }
        : { allowed: false, canonicalKey };
}

/** Starting an edit: any remote lock OR foreign selection protects the cell. */
export function checkEditStart(args: EditCheckArgs): EditDecision {
    const base = baseDecision(args);
    if (!base) return deny(undefined);
    if (base.canonicalKey) {
        const owner =
            args.remoteLocks.get(base.canonicalKey) ?? args.remotePresence.get(base.canonicalKey);
        if (owner) return deny(base.canonicalKey, owner);
    }
    return { allowed: true, ...base };
}

/** Committing an edit: a lock always blocks, presence yields to my own lease. */
export function checkEdit(args: EditArgs): EditDecision {
    const base = baseDecision(args);
    if (!base) return deny(undefined);
    if (base.canonicalKey) {
        const byLease = args.remoteLocks.get(base.canonicalKey);
        const byPresence = args.remotePresence.get(base.canonicalKey);
        const owner = byLease ?? (args.activeLease !== base.canonicalKey ? byPresence : undefined);
        if (owner) return deny(base.canonicalKey, owner);
    }
    return { allowed: true, ...base };
}

export function uneditableReason(descriptor: ColumnDescriptor): string {
    if (descriptor.kind === "calculated") return "расчётное поле";
    if (descriptor.fixed) return "фиксированное поле";
    return "поле недоступно для редактирования";
}

/**
 * Filter and normalize a range patch in place. The first invalid value ends the
 * scan; the caller must report `invalid` before emitting skipped-cell messages.
 */
export function normalizeRangePatch(args: RangePatchArgs): RangePatchResult {
    const skipped = new Map<string, string>();
    for (const [rowIndex, patch] of Object.entries(args.patches)) {
        const currentRow = args.models[Number(rowIndex)] ?? args.rows[Number(rowIndex)];
        for (const prop of Object.keys(patch)) {
            const descriptor = args.descriptors.get(String(prop));
            if (!descriptor) {
                delete patch[prop];
                continue;
            }
            if (!args.editableKeys.has(descriptor.key)) {
                delete patch[prop];
                skipped.set(descriptor.label, uneditableReason(descriptor));
                continue;
            }
            if (!currentRow || !args.canEdit(descriptor, currentRow)) {
                delete patch[prop];
                skipped.set(descriptor.label, uneditableReason(descriptor));
                continue;
            }
            const canonicalKey = currentRow.canonicalCells?.[descriptor.key];
            if (
                canonicalKey &&
                (args.remoteLocks.has(canonicalKey) || args.remotePresence.has(canonicalKey))
            ) {
                delete patch[prop];
                skipped.set(descriptor.label, "ячейка занята другим пользователем");
                continue;
            }
            const normalized = args.normalizeValue(patch[prop], descriptor.kind);
            if (!normalized.ok) return { skipped, invalid: normalized.message };
            patch[prop] = normalized.value;
        }
    }
    return { skipped, invalid: null };
}
