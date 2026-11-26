import type { RenovationMeta, RenovationPlanVersion } from "@/shared/api/renovation";
import type { SectionTab } from "@/shared/grid/section-tabs";
import type { ArchiveItem } from "@/shared/ui/workspace-state";

import { formatVersionStamp, VERSION_STATE_LABELS } from "./renovation-workspace-view";

/**
 * Pure view-model derivations of the renovation workspace.
 *
 * No React state, no hooks, no lifecycle: the workspace stays the composition
 * root and keeps every `useMemo`/`useCallback` (and therefore every referential
 * identity its children and effects rely on). These functions only replace the
 * bodies of those memos.
 */

export type RenovationSection = { id: string; label: string };

/**
 * Version-picker items: the working version is excluded and an approve
 * response wins over the polled list. The name carries the state only; the
 * description carries the date+time (an archived version reads as a noun
 * phrase, «Утверждённая версия»).
 */
export function buildArchiveItems(
    versions: readonly RenovationPlanVersion[] | null,
    approveVersions: readonly RenovationPlanVersion[] | null,
): ArchiveItem[] {
    const effective = approveVersions ?? versions;
    if (!effective || effective.length === 0) return [];
    return effective
        .filter((version) => version.state !== "working")
        .map((version) => ({
            id: version.id,
            name:
                version.state === "archived"
                    ? "Утверждённая версия"
                    : `Версия ${VERSION_STATE_LABELS[version.state]}`,
            description: formatVersionStamp(version.createdAt),
        }));
}

/** Plan first, then the dictionaries and users — dictionaries are admin-only. */
export function renovationSections(
    meta: RenovationMeta | null,
    isAdmin: boolean,
): RenovationSection[] {
    if (!meta) return [];
    if (!isAdmin) return [{ id: "plan", label: "Пообъектный план" }];
    return [
        { id: "plan", label: "Пообъектный план" },
        { id: "assets", label: "Предприятия" },
        ...meta.catalogs.map((catalog) => ({ id: catalog.id, label: catalog.label })),
        { id: "users", label: "Пользователи" },
    ];
}

/** Separators: before the first dictionary and before the users table. */
export function sectionTabDescriptors(sections: readonly RenovationSection[]): SectionTab[] {
    return sections.map((section, index) => ({
        id: section.id,
        label: section.label,
        separatorBefore: index === 1 || section.id === "users",
    }));
}

/**
 * Fields the current user may edit: an approver has none; a block owner may
 * edit only their block plus central fields with no block assignment.
 */
export function editablePlanFieldKeys(
    meta: RenovationMeta | null,
    isApprover: boolean,
    userBlock: string | null,
): Set<string> {
    const keys = new Set<string>();
    if (!meta) return keys;
    if (isApprover) return keys;
    for (const field of meta.fields) {
        if (field.isCalculated || field.isReadOnly) continue;
        if (userBlock && field.responsibilityBlock && field.responsibilityBlock !== userBlock) {
            continue;
        }
        keys.add(field.dbName);
    }
    return keys;
}

/** Formula results that update live from remote edits but are never typed. */
export function calculatedPlanFieldKeys(meta: RenovationMeta | null): Set<string> {
    const keys = new Set<string>();
    if (!meta) return keys;
    for (const field of meta.fields) {
        if (field.isCalculated) keys.add(field.dbName);
    }
    return keys;
}

/** Remote cell values are merged into this union of editable + calculated keys. */
export function mergeablePlanFieldKeys(
    editableFieldKeys: ReadonlySet<string>,
    calculatedFieldKeys: ReadonlySet<string>,
): Set<string> {
    const keys = new Set(editableFieldKeys);
    for (const key of calculatedFieldKeys) keys.add(key);
    return keys;
}

/** Names sent to the collab room so members without an asset overlap are hidden. */
export function renovationAssetNames(meta: RenovationMeta | null): string[] {
    return (meta?.assets ?? []).map((asset) => asset.name);
}

/** The grid's row-scope filter: null means no asset restriction. */
export function planAssetFilterSet(assetFilter: string | null): Set<string> | null {
    return assetFilter ? new Set([assetFilter]) : null;
}
