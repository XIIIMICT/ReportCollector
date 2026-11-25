/**
 * Renovation API — shared types, constants and the legacy field decoder.
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import type { ExportJobDescriptor } from "@/shared/api/table-export";

export const RENOVATION_SECTION = "renovationPlan";

/**
 * Legacy `col_NNN` db names of the renovation plan fields, mapped to the
 * semantic names frozen in migration 0041's RENAME and used by the current schema/API.
 * Saved user state (filters, sorting, pinned/hidden columns) may still
 * carry the old keys; decode them on READ so nothing is silently lost.
 * `col_002` is intentionally absent: it is the synthetic asset column,
 * never a database field (represented by `assetId` / `__assetName`).
 */
const LEGACY_FIELD_NAMES: Record<string, string> = {
    col_003: "work_or_repair_code",
    col_004: "production_name",
    col_005: "workshop_name",
    col_006: "department_or_installation_name",
    col_007: "equipment_class",
    col_008: "equipment_category",
    col_009: "equipment_unit_name",
    col_010: "technical_location_name",
    col_011: "process_position",
    col_012: "inventory_number",
    col_013: "functional_area",
    col_014: "production_impact",
    col_015: "equipment_criticality_group_id",
    col_016: "bpof_section",
    col_017: "work_category_id",
    col_018: "work_type_id",
    col_019: "cost_allocation_type_id",
    col_020: "execution_method_id",
    col_021: "work_executor_name",
    col_022: "work_description",
    col_023: "defect_list_or_bill_of_quantities_number",
    col_024: "work_start_date",
    col_025: "work_end_date",
    col_026: "work_duration_days",
    col_027: "labor_hours",
    col_028: "work_cost_rub_ex_vat",
    col_029: "customer_materials_cost_rub_ex_vat",
    col_030: "contractor_supplied_materials_cost_rub_ex_vat",
    col_031: "total_cost_rub_ex_vat",
    col_218: "estimate_number",
    col_032: "plan_after_proposed_adjustment_total_cost_thousand_rub_ex_vat",
    col_033: "adj_cw_cost_k_rub",
    col_034: "adj_cm_cost_k_rub",
    col_035: "adj_q1_total_cost_k_rub",
    col_036: "adj_q1_cw_cost_k_rub",
    col_037: "adj_q1_cm_cost_k_rub",
    col_038: "adj_january_total_cost_k_rub",
    col_039: "adj_january_cw_cost_k_rub",
    col_040: "adj_january_cm_cost_k_rub",
    col_041: "adj_february_total_cost_k_rub",
    col_042: "adj_february_cw_cost_k_rub",
    col_043: "adj_february_cm_cost_k_rub",
    col_044: "adj_march_total_cost_k_rub",
    col_045: "adj_march_cw_cost_k_rub",
    col_046: "adj_march_cm_cost_k_rub",
    col_047: "adj_q2_total_cost_k_rub",
    col_048: "adj_q2_cw_cost_k_rub",
    col_049: "adj_q2_cm_cost_k_rub",
    col_050: "adj_april_total_cost_k_rub",
    col_051: "adj_april_cw_cost_k_rub",
    col_052: "adj_april_cm_cost_k_rub",
    col_053: "adj_may_total_cost_k_rub",
    col_054: "adj_may_cw_cost_k_rub",
    col_055: "adj_may_cm_cost_k_rub",
    col_056: "adj_june_total_cost_k_rub",
    col_057: "adj_june_cw_cost_k_rub",
    col_058: "adj_june_cm_cost_k_rub",
    col_059: "adj_q3_total_cost_k_rub",
    col_060: "adj_q3_cw_cost_k_rub",
    col_061: "adj_q3_cm_cost_k_rub",
    col_062: "adj_july_total_cost_k_rub",
    col_063: "adj_july_cw_cost_k_rub",
    col_064: "adj_july_cm_cost_k_rub",
    col_065: "adj_august_total_cost_k_rub",
    col_066: "adj_august_cw_cost_k_rub",
    col_067: "adj_august_cm_cost_k_rub",
    col_068: "adj_september_total_cost_k_rub",
    col_069: "adj_september_cw_cost_k_rub",
    col_070: "adj_september_cm_cost_k_rub",
    col_071: "adj_q4_total_cost_k_rub",
    col_072: "adj_q4_cw_cost_k_rub",
    col_073: "adj_q4_cm_cost_k_rub",
    col_074: "adj_october_total_cost_k_rub",
    col_075: "adj_october_cw_cost_k_rub",
    col_076: "adj_october_cm_cost_k_rub",
    col_077: "adj_november_total_cost_k_rub",
    col_078: "adj_november_cw_cost_k_rub",
    col_079: "adj_november_cm_cost_k_rub",
    col_080: "adj_december_total_cost_k_rub",
    col_081: "adj_december_cw_cost_k_rub",
    col_082: "adj_december_cm_cost_k_rub",
    col_083: "adjustment_work_volume_unit",
    col_084: "adjustment_work_volume_quantity",
    col_085: "plan_adjustment_total_cost_thousand_rub_ex_vat",
    col_086: "plan_adjustment_construction_works_cost_thousand_rub_ex_vat",
    col_087: "plan_adjustment_customer_materials_cost_thousand_rub_ex_vat",
    col_088: "plan_adjustment_q1_total_cost_thousand_rub_ex_vat",
    col_089: "plan_adjustment_q1_construction_works_cost_thousand_rub_ex_vat",
    col_090: "plan_adjustment_q1_customer_materials_cost_thousand_rub_ex_vat",
    col_091: "plan_adjustment_january_total_cost_thousand_rub_ex_vat",
    col_092: "plan_adjustment_january_cw_cost_k_rub",
    col_093: "plan_adjustment_january_cm_cost_k_rub",
    col_094: "plan_adjustment_february_total_cost_thousand_rub_ex_vat",
    col_095: "plan_adjustment_february_cw_cost_k_rub",
    col_096: "plan_adjustment_february_cm_cost_k_rub",
    col_097: "plan_adjustment_march_total_cost_thousand_rub_ex_vat",
    col_098: "plan_adjustment_march_cw_cost_k_rub",
    col_099: "plan_adjustment_march_cm_cost_k_rub",
    col_100: "plan_adjustment_q2_total_cost_thousand_rub_ex_vat",
    col_101: "plan_adjustment_q2_construction_works_cost_thousand_rub_ex_vat",
    col_102: "plan_adjustment_q2_customer_materials_cost_thousand_rub_ex_vat",
    col_103: "plan_adjustment_april_total_cost_thousand_rub_ex_vat",
    col_104: "plan_adjustment_april_cw_cost_k_rub",
    col_105: "plan_adjustment_april_cm_cost_k_rub",
    col_106: "plan_adjustment_may_total_cost_thousand_rub_ex_vat",
    col_107: "plan_adjustment_may_construction_works_cost_thousand_rub_ex_vat",
    col_108: "plan_adjustment_may_customer_materials_cost_thousand_rub_ex_vat",
    col_109: "plan_adjustment_june_total_cost_thousand_rub_ex_vat",
    col_110: "plan_adjustment_june_cw_cost_k_rub",
    col_111: "plan_adjustment_june_cm_cost_k_rub",
    col_112: "plan_adjustment_q3_total_cost_thousand_rub_ex_vat",
    col_113: "plan_adjustment_q3_construction_works_cost_thousand_rub_ex_vat",
    col_114: "plan_adjustment_q3_customer_materials_cost_thousand_rub_ex_vat",
    col_115: "plan_adjustment_july_total_cost_thousand_rub_ex_vat",
    col_116: "plan_adjustment_july_cw_cost_k_rub",
    col_117: "plan_adjustment_july_cm_cost_k_rub",
    col_118: "plan_adjustment_august_total_cost_thousand_rub_ex_vat",
    col_119: "plan_adjustment_august_cw_cost_k_rub",
    col_120: "plan_adjustment_august_cm_cost_k_rub",
    col_121: "plan_adjustment_september_total_cost_thousand_rub_ex_vat",
    col_122: "plan_adjustment_september_cw_cost_k_rub",
    col_123: "plan_adjustment_september_cm_cost_k_rub",
    col_124: "plan_adjustment_q4_total_cost_thousand_rub_ex_vat",
    col_125: "plan_adjustment_q4_construction_works_cost_thousand_rub_ex_vat",
    col_126: "plan_adjustment_q4_customer_materials_cost_thousand_rub_ex_vat",
    col_127: "plan_adjustment_october_total_cost_thousand_rub_ex_vat",
    col_128: "plan_adjustment_october_cw_cost_k_rub",
    col_129: "plan_adjustment_october_cm_cost_k_rub",
    col_130: "plan_adjustment_november_total_cost_thousand_rub_ex_vat",
    col_131: "plan_adjustment_november_cw_cost_k_rub",
    col_132: "plan_adjustment_november_cm_cost_k_rub",
    col_133: "plan_adjustment_december_total_cost_thousand_rub_ex_vat",
    col_134: "plan_adjustment_december_cw_cost_k_rub",
    col_135: "plan_adjustment_december_cm_cost_k_rub",
    col_136: "plan_adjustment_type",
    col_137: "plan_adjustment_justification_document",
    col_138: "original_plan_total_cost_thousand_rub_ex_vat",
    col_139: "original_plan_construction_works_cost_thousand_rub_ex_vat",
    col_140: "original_plan_customer_materials_cost_thousand_rub_ex_vat",
    col_141: "original_plan_q1_total_cost_thousand_rub_ex_vat",
    col_142: "original_plan_q1_construction_works_cost_thousand_rub_ex_vat",
    col_143: "original_plan_q1_customer_materials_cost_thousand_rub_ex_vat",
    col_144: "original_plan_january_total_cost_thousand_rub_ex_vat",
    col_145: "original_plan_january_cw_cost_k_rub",
    col_146: "original_plan_january_cm_cost_k_rub",
    col_147: "original_plan_february_total_cost_thousand_rub_ex_vat",
    col_148: "original_plan_february_cw_cost_k_rub",
    col_149: "original_plan_february_cm_cost_k_rub",
    col_150: "original_plan_march_total_cost_thousand_rub_ex_vat",
    col_151: "original_plan_march_construction_works_cost_thousand_rub_ex_vat",
    col_152: "original_plan_march_customer_materials_cost_thousand_rub_ex_vat",
    col_153: "original_plan_q2_total_cost_thousand_rub_ex_vat",
    col_154: "original_plan_q2_construction_works_cost_thousand_rub_ex_vat",
    col_155: "original_plan_q2_customer_materials_cost_thousand_rub_ex_vat",
    col_156: "original_plan_april_total_cost_thousand_rub_ex_vat",
    col_157: "original_plan_april_construction_works_cost_thousand_rub_ex_vat",
    col_158: "original_plan_april_customer_materials_cost_thousand_rub_ex_vat",
    col_159: "original_plan_may_total_cost_thousand_rub_ex_vat",
    col_160: "original_plan_may_construction_works_cost_thousand_rub_ex_vat",
    col_161: "original_plan_may_customer_materials_cost_thousand_rub_ex_vat",
    col_162: "original_plan_june_total_cost_thousand_rub_ex_vat",
    col_163: "original_plan_june_construction_works_cost_thousand_rub_ex_vat",
    col_164: "original_plan_june_customer_materials_cost_thousand_rub_ex_vat",
    col_165: "original_plan_q3_total_cost_thousand_rub_ex_vat",
    col_166: "original_plan_q3_construction_works_cost_thousand_rub_ex_vat",
    col_167: "original_plan_q3_customer_materials_cost_thousand_rub_ex_vat",
    col_168: "original_plan_july_total_cost_thousand_rub_ex_vat",
    col_169: "original_plan_july_construction_works_cost_thousand_rub_ex_vat",
    col_170: "original_plan_july_customer_materials_cost_thousand_rub_ex_vat",
    col_171: "original_plan_august_total_cost_thousand_rub_ex_vat",
    col_172: "original_plan_august_cw_cost_k_rub",
    col_173: "original_plan_august_cm_cost_k_rub",
    col_174: "original_plan_september_total_cost_thousand_rub_ex_vat",
    col_175: "original_plan_september_cw_cost_k_rub",
    col_176: "original_plan_september_cm_cost_k_rub",
    col_177: "original_plan_q4_total_cost_thousand_rub_ex_vat",
    col_178: "original_plan_q4_construction_works_cost_thousand_rub_ex_vat",
    col_179: "original_plan_q4_customer_materials_cost_thousand_rub_ex_vat",
    col_180: "original_plan_october_total_cost_thousand_rub_ex_vat",
    col_181: "original_plan_october_cw_cost_k_rub",
    col_182: "original_plan_october_cm_cost_k_rub",
    col_183: "original_plan_november_total_cost_thousand_rub_ex_vat",
    col_184: "original_plan_november_cw_cost_k_rub",
    col_185: "original_plan_november_cm_cost_k_rub",
    col_186: "original_plan_december_total_cost_thousand_rub_ex_vat",
    col_187: "original_plan_december_cw_cost_k_rub",
    col_188: "original_plan_december_cm_cost_k_rub",
    col_189: "lotting_activity_direction_code",
    col_190: "general_contractor_procurement_code",
    col_191: "general_contractor_contract_date",
    col_192: "general_contractor_contract_number",
    col_193: "general_contractor_contract_price_thousand_rub_ex_vat",
    col_194: "general_contractor_counterparty_name",
    col_195: "general_contractor_counterparty_prequalification_status",
    col_196: "subcontractor_engagement_requirement",
    col_197: "general_contractor_contract_issue_description",
    col_198: "subcontract_contract_date",
    col_199: "subcontract_contract_number",
    col_200: "subcontractor_name",
    col_201: "subcontractor_prequalification_status",
    col_202: "has_materials_in_work_or_repair",
    col_203: "required_materials_delivery_date",
    col_204: "total_materials_cost",
    col_205: "centralized_supply_materials_cost_thousand_rub_ex_vat",
    col_206: "direct_supply_materials_cost_thousand_rub_ex_vat",
    col_207: "contractor_supply_materials_cost_thousand_rub_ex_vat",
    col_208: "em_contracting_cs_pct",
    col_209: "equipment_materials_contracting_direct_supply_readiness_percent",
    col_210: "em_delivery_cs_pct",
    col_211: "equipment_materials_delivery_direct_supply_readiness_percent",
    col_212: "materials_are_critical",
    col_213: "materials_readiness_issue_description",
    col_214: "contractor_equipment_materials_supply_readiness_percent",
    col_215: "contractor_supplied_materials_are_critical",
    col_216: "contractor_equipment_materials_supply_issue_description",
    col_217: "note",
};

/** Decode one persisted field key; unknown keys pass through unchanged. */
export function decodeLegacyFieldName(key: string): string {
    return LEGACY_FIELD_NAMES[key] ?? key;
}

/** Decode the keys of a saved-state record (filters / pinned / hidden / sort). */
export function decodeLegacyFieldKeys<T>(stored: Record<string, T>): Record<string, T> {
    const result: Record<string, T> = {};
    for (const [key, value] of Object.entries(stored)) {
        result[decodeLegacyFieldName(key)] = value;
    }
    return result;
}

export type RenovationAsset = {
    id: string;
    code: string;
    name: string;
};

export type RenovationCatalogOption = {
    valueId: string;
    label: string;
};

export type RenovationCatalog = {
    id: string;
    label: string;
    options: RenovationCatalogOption[];
};

export type RenovationFieldRule = {
    kind: string;
    /** `minimum`'s bound, `percentage`'s lower bound, `field_compare`'s target. */
    value?: number | string;
    minimum?: number;
    maximum?: number;
    /** `allowed_values` whitelist. */
    values?: string[];
    /** `field_compare` operator (`gte`). */
    operator?: string;
    field?: string;
};

export type RenovationField = {
    dbName: string;
    label: string;
    cellType: string;
    catalog?: string | null;
    isCalculated: boolean;
    isReadOnly: boolean;
    decimals?: number | null;
    position: number;
    ordinal: number;
    responsibilityBlock?: string | null;
    /** Validation rules from the source workbook (see RenovationFieldRule). */
    rules?: RenovationFieldRule[];
    allowNull?: boolean;
};

export type RenovationHeaderCell = {
    id: string;
    text: string;
    startPosition: number;
    colSpan: number;
    rowSpan: number;
    level: number;
    excelRange: string;
};

export type RenovationHeaderRow = {
    level: number;
    cells: RenovationHeaderCell[];
};

export type RenovationMeta = {
    assets: RenovationAsset[];
    catalogs: RenovationCatalog[];
    fields: RenovationField[];
    headerCells: RenovationHeaderCell[];
    headerRows: RenovationHeaderRow[];
    workingPlanId: string | null;
    pendingPlanId: string | null;
    /** The current user's responsibility block (null for central roles). */
    userBlock: string | null;
    /** The user's renovation roles — source of truth for plan edit rights. */
    userRoles: string[];
};

export type RenovationPlanVersion = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    rowCount: number;
    createdAt: string;
    createdBy: string;
    updatedAt: string;
    updatedBy: string;
};

export type RenovationPlanRow = {
    id: string;
    assetId: string | null;
    assetCode: string | null;
    assetName: string | null;
    assetRepairCode: string | null;
    position: number;
    revision: number;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
    values: Record<string, string | number | boolean | null>;
    renovationId: string | null;
    missingComponents: string[];
    /** Fields whose stored value breaks a form rule (red highlight). */
    invalidFields?: string[];
};

export type RenovationPlan = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    baselineBoundary: number | null;
};

export type RenovationDiffCell = {
    field: string;
    oldValue?: string | number | boolean | null;
    newValue?: string | number | boolean | null;
};

export type RenovationDiffRow = {
    rowId: string;
    position: number;
    assetName: string;
    isNew: boolean;
    cells: RenovationDiffCell[];
};

export type RenovationDiff = {
    planId: string;
    comparedToPlanId: string | null;
    rows: RenovationDiffRow[];
    /** Full previous-version rows for the rows deleted in the target. */
    deletedRows: RenovationPlanRow[];
};

export type RenovationUpdate = {
    applied: number;
    calculated: Array<{ rowId: string; field: string; value: unknown }>;
    renovationIds: Array<{ rowId: string; value: string; missingComponents: string[] }>;
};

export type RenovationApprove = {
    archived: RenovationPlanVersion;
    working: RenovationPlanVersion;
};

export type RenovationUndoResult = {
    applied: boolean;
    reason: "applied" | "empty" | "conflict" | "locked" | null;
    conflicts: Array<{
        rowId: string;
        field: string;
        user: string;
        actionId: number;
        reason: "conflict" | "locked";
    }>;
    section: string | null;
    reportMonth: string | null;
    rowId: string | null;
    field: string | null;
    value: unknown;
    revision: number | null;
};

export type RenovationUndoState = {
    canUndo: boolean;
    canRedo: boolean;
};

// ─── Dictionary grid rows ───────────────────────────────────────────────────

export type RenovationAssetRow = {
    id: string;
    repairCode: string;
    name: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
    /** Computed: the working plan's row count for this asset. */
    repairCount: number;
    /** Computed: sum of «Стоимость итого» (total_cost_rub_ex_vat) over the plan rows. */
    totalCost: number;
};

export type RenovationCatalogOptionRow = {
    valueId: string;
    label: string;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
    /** Computed: the working plan's row count for this option. */
    repairCount: number;
    /** Computed: sum of «Стоимость итого» (total_cost_rub_ex_vat) over the plan rows. */
    totalCost: number;
};

export type RenovationUserRow = {
    id: string;
    email: string;
    fullname: string;
    role: string;
    block: string | null;
    assetNames: string[];
    isActive: boolean;
    lastAuthenticatedAt: string | null;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export const RENOVATION_ASSETS_SECTION = "renovationAssets";
export const RENOVATION_USERS_SECTION = "renovationUsers";

export function renovationCatalogSection(catalogId: string): `renovationCatalog:${string}` {
    return `renovationCatalog:${catalogId}`;
}

// ─── Dictionary CRUD ───────────────────────────────────────────────────────

export type RenovationExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
};

export type RenovationExportJobDownload = {
    job: ExportJobDescriptor;
    fileUrl: string;
    jobsBaseUrl: string;
};

/** Download a renovation dictionary export — inline, or as a background job past the inline row limit. */
export type RenovationColumnFilter = {
    column: string;
    values: string[];
    includeNulls: boolean;
    valueMode?: "exact";
    /** A `timestamptz` window INSTEAD of `values` (both ends or one). */
    from?: string | null;
    to?: string | null;
};

export type RenovationSortColumn = {
    column: string;
    order: "asc" | "desc";
};

/** The browser's IANA zone — the one the cells print their timestamps in. */
