import type { SectionId } from "@/shared/grid/grid-config";

/**
 * The per-section rules that used to be scattered across both sides of the
 * app: `MAIN_SECTIONS` checks, `isAdmin &&` ternaries, `showPaste`/`showApprove`
 * switches, the "dictionary room is admin-only" rule in `collab/access.py` and
 * the admin gate in `collab/undo.py`.
 *
 * This module is the front side of the single source of truth. The backend
 * mirrors it in `back/src/common/section_policy.py`; the two must stay in sync
 * and `back/tests/test_section_policy.py` checks the frontend/backend parity.
 *
 * The rules (see `docs/COLLAB.md` and `docs/GRID.md`):
 *   - collaboration is on for every editable form and off for the non-editable
 *     ones (fixed month and immutable archive; pending-plan rules stay local);
 *   - clipboard, undo/redo and export work the same in every project;
 *   - Ctrl+A is disabled on chunked (large) tables;
 *   - the pin/service-columns controls are common to every project (the pin
 *     toggle in «Вид таблицы», service columns via the header's «+»).
 */

/** Who may join the section's collaboration room. */
export type CollabMode =
    /** every project member with access; the room also shares presence/leases */
    | "full"
    /** only project admins have anything to edit, so only they join */
    | "admin"
    /** non-editable table: no room, no presence, no leases */
    | "none";

/** Who may undo/redo on the section. */
export type UndoMode =
    /** every user with current write access to the section */
    | "member"
    /** only admins */
    | "admin"
    /** nothing is journaled there */
    | "none";

/** Who may export the section. */
export type ExportMode = "member" | "admin" | "none";

/** How the section's rows are authored. */
export type RowOpsMode =
    /** the trailing «+» placeholder row creates a record */
    | "placeholder"
    /** records are created through a dialog (users) */
    | "dialog"
    /** rows are added through the plan's «Добавить строку» dialog */
    | "plan"
    /** rows are seeded / imported, not authored in the grid */
    | "none";

export type SectionPolicy = {
    collab: CollabMode;
    undo: UndoMode;
    export: ExportMode;
    rowOps: RowOpsMode;
    /**
     * The grid loads its rows in chunks (Infinity Scroll), so Ctrl+A is
     * disabled: a whole-table copy would overflow the clipboard. Expressed as
     * the loading model, not as a guessed row count — a dictionary that later
     * becomes chunked inherits the rule automatically.
     */
    chunked: boolean;
};

/**
 * Every section the app knows, including the renovation plan and the dynamic
 * catalogs — `grid-config.ts`'s `SectionId` covers the grid descriptors only.
 */
export type PolicySectionId = SectionId | "renovationPlan";

const FORM: SectionPolicy = {
    collab: "full",
    undo: "member",
    export: "member",
    rowOps: "none",
    chunked: false,
};

const DICTIONARY: SectionPolicy = {
    collab: "admin",
    undo: "admin",
    export: "member",
    rowOps: "placeholder",
    chunked: false,
};

const PLAN: SectionPolicy = {
    collab: "full",
    undo: "member",
    export: "member",
    rowOps: "plan",
    chunked: true,
};

/**
 * A table owned by another system: read-only, no room, no journal — but the
 * grid is still EXPORTABLE, like every other dictionary. The «Логистика»
 * dictionaries are the only ones today.
 */
const READ_ONLY: SectionPolicy = {
    collab: "none",
    undo: "none",
    export: "member",
    rowOps: "none",
    chunked: false,
};

const SECTION_POLICIES: Record<PolicySectionId, SectionPolicy> = {
    // ── «Сырьё» ──────────────────────────────────────────────────────────
    materialMovement: FORM,
    rawMaterialPrices: FORM,
    rawMaterials: DICTIONARY,
    assets: DICTIONARY,
    deliveryBases: DICTIONARY,
    users: { ...DICTIONARY, rowOps: "dialog" },
    // ── «Ремонт» ─────────────────────────────────────────────────────────
    renovationPlan: PLAN,
    renovationAssets: DICTIONARY,
    renovationUsers: { ...DICTIONARY, rowOps: "dialog" },
    // ── «КППК» ───────────────────────────────────────────────────────────
    // Admin-edited source-copy dictionaries: every member reads, exports, and
    // collaborates; undo mirrors the edit rule (a member has no edits to undo).
    kppkPositions: DICTIONARY,
    kppkRaws: DICTIONARY,
    kppkOrgs: DICTIONARY,
    kppkUsers: { ...DICTIONARY, rowOps: "dialog" },
    // «Реализация» (Таблицы.xlsx): the rows come from the source copies —
    // no placeholders; the editing stays admin-only like every «КППК» surface.
    kppkRealizationPlan: { ...DICTIONARY, rowOps: "none" },
    kppkRealizationFact: { ...DICTIONARY, rowOps: "none" },
    kppkCost: { ...DICTIONARY, rowOps: "none" },
    // ── «Логистика» ──────────────────────────────────────────────────────
    // The external dictionaries are read straight from the `dict` schema:
    // nothing is authored, journaled or shared there (they still export).
    logisticsCountries: READ_ONLY,
    logisticsTransportTypes: READ_ONLY,
    logisticsOrgs: READ_ONLY,
    // Ours, though: the users dictionary collaborates and journals like every
    // other project's one, with the dialog-owned create action.
    logisticsUsers: { ...DICTIONARY, rowOps: "dialog" },
    // The main form: collaborative members with undo/redo, and export; no
    // generic placeholder action — the draft row is owned by the rates
    // section, so `rowOps` is "none". Kept on ONE line: the backend parity
    // test parses inline entries only.
    logisticsRates: { ...FORM, export: "member" },
};

const CATALOG_POLICY: SectionPolicy = DICTIONARY;

/**
 * The policy of a section. Renovation catalogs are dynamic ids
 * (`renovationCatalog:<catalog>`), so they fall back to the catalog policy.
 */
export function sectionPolicy(section: PolicySectionId): SectionPolicy {
    return SECTION_POLICIES[section] ?? CATALOG_POLICY;
}

/** Whether the grid of this section disables Ctrl+A (chunked loading). */
export function isChunkedSection(section: PolicySectionId): boolean {
    return sectionPolicy(section).chunked;
}

/** Whether the section takes part in real-time collaboration at all. */
export function isCollabSection(section: PolicySectionId): boolean {
    return sectionPolicy(section).collab !== "none";
}

/** Lifecycle/read-only and column permissions remain additional guards. */
export function collaborationAvailableFor(section: PolicySectionId, isAdmin: boolean): boolean {
    const mode = sectionPolicy(section).collab;
    return mode === "full" || (mode === "admin" && isAdmin);
}

/** Whether undo/redo is offered to the current user on an editable section. */
export function undoAvailableFor(section: PolicySectionId, isAdmin: boolean): boolean {
    const mode = sectionPolicy(section).undo;
    if (mode === "none") return false;
    return mode === "admin" ? isAdmin : true;
}

/** Whether the current user may export the section. */
export function exportAvailableFor(section: PolicySectionId, isAdmin: boolean): boolean {
    const mode = sectionPolicy(section).export;
    if (mode === "none") return false;
    return mode === "admin" ? isAdmin : true;
}

/**
 * The non-editable variants of a section share its policy except that there is
 * no collaboration and nothing to undo: the movement version archive and the
 * archived/submitted renovation plan.
 */
export function readOnlyPolicy(policy: SectionPolicy): SectionPolicy {
    return { ...policy, collab: "none", undo: "none" };
}

/** Section ids that declare a policy — used by the backend-parity test. */
export function declaredSectionIds(): PolicySectionId[] {
    return Object.keys(SECTION_POLICIES) as PolicySectionId[];
}
