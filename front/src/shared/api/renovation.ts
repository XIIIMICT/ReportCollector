import { readApiDetail, requestJson, requestWrite } from "@/shared/api/api-error";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { downloadTableExport, tableExportJobFileUrl } from "@/shared/api/table-export";
import type { ExportJobDescriptor } from "@/shared/api/table-export";
import { getCollabSessionId } from "@/shared/collab/ws-client";
import type { FacetSearch } from "@/shared/ui/column-filter-facets";

export const RENOVATION_SECTION = "renovationPlan";

/**
 * Legacy `col_NNN` db names of the renovation plan fields, mapped to the
 * semantic §9.1 names used by the current schema/API (migration 0041).
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

export type RenovationPlanImportCellIssue = {
    location: string;
    value: string;
    /** Human-readable field label (no header hierarchy). */
    column_label?: string | null;
};
export type RenovationPlanImportResult = {
    status: "invalid_format" | "has_errors" | "ready";
    /** Required headings absent from a readable file (a content error). */
    missing_required_columns: string[];
    duplicate_codes: string[];
    invalid_calculated_codes: string[];
    invalid_data_types: RenovationPlanImportCellIssue[];
    /** Numeric cells breaking the field's integer/min/max/percentage rules. */
    invalid_values: RenovationPlanImportCellIssue[];
    /** Excel «#REF!» cached values — a broken formula reference. */
    formula_reference_errors: RenovationPlanImportCellIssue[];
    /** Required cells left empty in a row — an error of the file. */
    missing_required_values: RenovationPlanImportCellIssue[];
    missing_dictionary_values: RenovationPlanImportCellIssue[];
    unknown_assets: RenovationPlanImportCellIssue[];
    /** Rows the load will not write: the asset cell is empty. */
    skipped_rows: number[];
    /** Every data row lacked the asset — there is nothing to load. */
    all_rows_skipped: boolean;
    assets: string[];
    new_rows: number;
    rows_to_update: number;
};

export type PlanImportProgress = {
    /** The bytes are on their way to the server. */
    uploading: () => void;
    /** The upload finished; from here the server parses the file. */
    analyzing: () => void;
};

/**
 * Check a workbook before loading it, reporting the TWO waits apart.
 *
 * `fetch` cannot say when the request body has been handed over, so this uses
 * `XMLHttpRequest`, whose `upload.onloadend` marks exactly that point: before it
 * the browser is sending bytes (the user's connection), after it the server is
 * parsing (its own work). The modal shows a different caption for each.
 */
export function inspectRenovationPlanImport(
    file: File,
    progress?: PlanImportProgress,
): Promise<RenovationPlanImportResult> {
    const body = new FormData();
    body.append("file", file);
    return new Promise((resolvePromise, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", "/api/renovation/plan/import/inspect");
        request.responseType = "text";
        // No Content-Type header: the browser must set it itself to attach the
        // multipart boundary that the backend parser needs.
        request.upload.onloadend = () => progress?.analyzing();
        request.onload = () => {
            if (request.status < 200 || request.status >= 300) {
                let detail: string | null = null;
                try {
                    detail = readApiDetail(JSON.parse(request.responseText) as unknown);
                } catch {
                    detail = null;
                }
                // 413 comes from the proxy in front of the API (nginx's
                // `client_max_body_size`) and carries no JSON body of its own —
                // falling through to the generic message made a too-large file
                // read as a broken one.
                reject(
                    new ReportCollectorApiError(
                        request.status === 413
                            ? "Файл имеет слишком большой размер"
                            : (detail ?? `Не удалось проверить файл (${request.status}).`),
                        request.status,
                    ),
                );
                return;
            }
            try {
                resolvePromise(JSON.parse(request.responseText) as RenovationPlanImportResult);
            } catch (reason) {
                reject(reason instanceof Error ? reason : new Error(String(reason)));
            }
        };
        request.onerror = () => reject(new Error("Не удалось загрузить файл."));
        request.onabort = () => reject(new Error("Загрузка файла прервана."));
        progress?.uploading();
        request.send(body);
    });
}

export type RenovationPlanImportApplyResult = {
    id: string;
    status: "pending" | "running" | "done" | "failed";
    progress: number;
    total: number;
    file_name: string;
    error: string | null;
    new_rows: number;
    updated_rows: number;
    /** `parse` | `write` | `recompute` | `done` — the server's current stage. */
    phase: string | null;
};

/** The slice of the import job the progress bar reads. */
export type PlanImportJobState = Pick<
    RenovationPlanImportApplyResult,
    "progress" | "total" | "phase"
>;

/**
 * Load a checked Excel file (`.xlsb` or `.xlsx`) into the working plan.
 *
 * The backend stages the workbook and returns a durable job. `expectedRows` is
 * the preflight's row count and lets the server size its progress bar to the
 * write and recompute halves; the caller may poll
 * `getRenovationPlanImportJob` while the worker runs.
 */
export async function applyRenovationPlanImport(
    file: File,
    expectedRows: number,
): Promise<RenovationPlanImportApplyResult> {
    const body = new FormData();
    body.append("file", file);
    body.append("expected_rows", String(Math.max(0, Math.trunc(expectedRows))));
    const response = await fetch("/api/renovation/plan/import/apply", {
        method: "POST",
        body,
        cache: "no-store",
    });
    if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as unknown;
        throw new ReportCollectorApiError(
            readApiDetail(payload) ?? `Не удалось загрузить файл (${response.status}).`,
            response.status,
        );
    }
    return (await response.json()) as RenovationPlanImportApplyResult;
}

export async function getRenovationPlanImportJob(
    jobId: string,
): Promise<RenovationPlanImportApplyResult> {
    const response = await fetch(`/api/renovation/plan/import/jobs/${encodeURIComponent(jobId)}`, {
        cache: "no-store",
    });
    if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as unknown;
        throw new ReportCollectorApiError(
            readApiDetail(payload) ??
                `Не удалось получить состояние загрузки (${response.status}).`,
            response.status,
        );
    }
    return (await response.json()) as RenovationPlanImportApplyResult;
}

export type RenovationPlanRows = {
    id: string;
    /** Backend wire values (RPlanState): "pending_approval" is the pending one. */
    state: "working" | "pending_approval" | "archived";
    baselineGeneration: number;
    updatedAt: string;
    updatedBy: string;
    rows: RenovationPlanRow[];
    total: number;
    baselineBoundary: number | null;
    /** Whole-plan row count (ignoring the asset filter) for the «всего» counter. */
    totalAll: number | null;
    /**
     * Rows before the requested `anchorPosition` within the filtered scope —
     * the anchor row's 0-based index (`total` when it is past the last row).
     */
    anchorOffset: number | null;
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

export type RenovationPlanExport = {
    blob: Blob;
    fileName: string;
};

export async function getRenovationMeta(): Promise<RenovationMeta> {
    return requestJson("/api/renovation/meta");
}

export async function getRenovationPlan(planId?: string): Promise<RenovationPlan> {
    const query = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
    return requestJson(`/api/renovation/plan${query}`);
}

/** One active header filter: kept display values + the «Пустые» bucket. */
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
export function browserTimeZone(): string | null {
    if (typeof Intl === "undefined") return null;
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch {
        return null;
    }
}

export async function getRenovationPlanRows(
    offset: number,
    limit: number,
    planId?: string,
    asset?: string | null,
    filters?: RenovationColumnFilter[] | null,
    sorting?: RenovationSortColumn[] | null,
    positionFrom?: number | null,
    positionTo?: number | null,
    anchorPosition?: number | null,
): Promise<RenovationPlanRows> {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    // A `timestamptz` window is typed as LOCAL date and time («15.10.2026
    // 00:00»), exactly what the cells print. Without the zone the server has to
    // read those bounds as UTC, and a filter the user typed would match nothing.
    const zone = browserTimeZone();
    if (planId) params.set("plan_id", planId);
    if (asset) params.set("asset", asset);
    return requestWrite(`/api/renovation/plan/rows/query?${params.toString()}`, "POST", {
        filters: filters ?? [],
        sorting: sorting ?? [],
        tz: zone,
        position_from: positionFrom,
        position_to: positionTo,
        anchor_position: anchorPosition,
    });
}

export async function getRenovationPlanSummary(
    params: {
        planId?: string | undefined;
        asset?: string | null | undefined;
        filters?: RenovationColumnFilter[] | null | undefined;
        positionFrom?: number | null | undefined;
        positionTo?: number | null | undefined;
    } = {},
): Promise<Record<string, number>> {
    const query = new URLSearchParams();
    if (params.planId) query.set("plan_id", params.planId);
    if (params.asset) query.set("asset", params.asset);
    const data = await requestWrite<{ values: Record<string, number> }>(
        `/api/renovation/plan/summary/query?${query.toString()}`,
        "POST",
        {
            filters: params.filters ?? [],
            tz: browserTimeZone(),
            position_from: params.positionFrom,
            position_to: params.positionTo,
        },
    );
    return data.values;
}

export type RenovationFacetValue = {
    value: string | null;
    count: number;
};

export type RenovationFacets = {
    column: string;
    values: RenovationFacetValue[];
    presets?: RenovationFacetValue[];
    totalWithoutSearch?: number | null;
    /** Distinct non-empty values across the whole filtered plan. */
    total: number;
    /** Whether the column has a NULL/empty bucket (the «Пустые» option). */
    hasNulls: boolean;
    /** Total rows represented by all buckets. */
    rowCount?: number;
    /** Missing on older backend workers during a rolling deployment. */
    nullCount?: number;
};

export async function getRenovationPlanFacets(
    column: string,
    params: {
        planId?: string | undefined;
        asset?: string | null | undefined;
        offset?: number | undefined;
        limit?: number | undefined;
        search?: FacetSearch | null | undefined;
        filters?: RenovationColumnFilter[] | undefined;
    } = {},
): Promise<RenovationFacets> {
    const query = new URLSearchParams({ column });
    if (params.planId) query.set("plan_id", params.planId);
    if (params.asset) query.set("asset", params.asset);
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    const search = params.search;
    return requestWrite(`/api/renovation/plan/facets/query?${query.toString()}`, "POST", {
        filters: params.filters ?? [],
        tz: browserTimeZone(),
        search_kind: search?.kind,
        search_query: search?.kind === "text" ? search.query : undefined,
        search_min: search && search.kind !== "text" ? search.min : undefined,
        search_max: search && search.kind !== "text" ? search.max : undefined,
    });
}

export async function getRenovationPlans(): Promise<RenovationPlanVersion[]> {
    return requestJson("/api/renovation/plans");
}

export async function updateRenovationCells(updates: CellUpdate[]): Promise<RenovationUpdate> {
    return requestWrite(`/api/renovation/plan?session_id=${getCollabSessionId()}`, "PATCH", {
        updates,
    });
}

export async function addRenovationRow(assetId: string): Promise<RenovationPlanRow> {
    return requestWrite("/api/renovation/plan/rows", "POST", { assetId });
}

export async function deleteRenovationRow(rowId: string): Promise<void> {
    return requestWrite(`/api/renovation/plan/rows/${encodeURIComponent(rowId)}`, "DELETE");
}

export async function getRenovationDiff(planId: string): Promise<RenovationDiff> {
    return requestJson(`/api/renovation/plan/diff?plan_id=${encodeURIComponent(planId)}`);
}

export type RenovationPlanExportParams = {
    planId?: string | undefined;
    rowIds?: string[] | null | undefined;
    asset?: string | null | undefined;
    block?: string | null | undefined;
    /** Active header filters — the server-side «видимая таблица» scope. */
    filters?: RenovationColumnFilter[] | null | undefined;
    /** Active server-side row ordering, in priority order. */
    sorting?: RenovationSortColumn[] | null | undefined;
};

export async function exportRenovationPlan(
    params: RenovationPlanExportParams = {},
): Promise<RenovationPlanExport> {
    const response = await fetch("/api/renovation/plan/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            planId: params.planId ?? null,
            rowIds: params.rowIds ?? null,
            asset: params.asset ?? null,
            block: params.block ?? null,
            filters: params.filters ?? null,
            sorting: params.sorting ?? null,
        }),
        cache: "no-store",
    });
    if (!response.ok) {
        let detail = `Запрос завершился ошибкой ${response.status}.`;
        try {
            const payload = (await response.json()) as { detail?: string };
            if (typeof payload.detail === "string") {
                detail = payload.detail;
            }
        } catch {
            // For a non-JSON response keep the generic message prepared above.
        }
        throw new ReportCollectorApiError(detail, response.status);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    // Prefer the RFC 5987 UTF-8 form — the plan name is Cyrillic and the bare
    // `filename` carries only a percent-encoded copy the browser would save raw.
    const star = /filename\*\s*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="?([^";]+)"?/.exec(disposition);
    let fileName = "";
    if (star?.[1]) {
        try {
            fileName = decodeURIComponent(star[1]);
        } catch {
            fileName = star[1];
        }
    } else if (plain?.[1]) {
        fileName = plain[1];
    }
    return {
        blob: await response.blob(),
        fileName: fileName || "Пообъектный план — рабочая версия.xlsx",
    };
}

export type RenovationExportJobStatus = "pending" | "running" | "done" | "failed";

export type RenovationExportJob = {
    id: string;
    status: RenovationExportJobStatus;
    /** Rows written so far (progress bar numerator). */
    progress: number;
    /** Rows in the exported scope (progress bar denominator). */
    total: number;
    fileName: string;
    error: string | null;
};

/**
 * Start (or reuse) a background plan export.
 *
 * The full plan is minutes of CPU and hundreds of MB, so the server generates
 * the file in a single sequential worker; the call returns immediately and the
 * client polls `getRenovationExportJob` before downloading. An identical
 * request (same plan, filters and last-edit stamp) reuses the ready file.
 */
export async function createRenovationExportJob(
    params: RenovationPlanExportParams = {},
): Promise<RenovationExportJob> {
    return requestWrite<RenovationExportJob>("/api/renovation/plan/export/jobs", "POST", {
        planId: params.planId ?? null,
        rowIds: params.rowIds ?? null,
        asset: params.asset ?? null,
        block: params.block ?? null,
        filters: params.filters ?? null,
        sorting: params.sorting ?? null,
    });
}

export async function getRenovationExportJob(jobId: string): Promise<RenovationExportJob> {
    return requestJson<RenovationExportJob>(
        `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}`,
    );
}

/** URL of a ready export file (streamed into the chosen file by the client). */
export function renovationExportJobFileUrl(jobId: string): string {
    return `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}/file`;
}

/** Download the ready XLSX of a completed export job. */
export async function downloadRenovationExportJob(jobId: string): Promise<RenovationPlanExport> {
    const response = await fetch(
        `/api/renovation/plan/export/jobs/${encodeURIComponent(jobId)}/file`,
        { cache: "no-store" },
    );
    if (!response.ok) {
        let detail = `Запрос завершился ошибкой ${response.status}.`;
        try {
            const payload = (await response.json()) as { detail?: string };
            if (typeof payload.detail === "string") {
                detail = payload.detail;
            }
        } catch {
            // For a non-JSON response keep the generic message prepared above.
        }
        throw new ReportCollectorApiError(detail, response.status);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const star = /filename\*\s*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="?([^";]+)"?/.exec(disposition);
    let fileName = "";
    if (star?.[1]) {
        try {
            fileName = decodeURIComponent(star[1]);
        } catch {
            fileName = star[1];
        }
    } else if (plain?.[1]) {
        fileName = plain[1];
    }
    return {
        blob: await response.blob(),
        fileName: fileName || "Пообъектный план.xlsx",
    };
}

export async function submitRenovationPlan(): Promise<RenovationPlanVersion> {
    return requestWrite("/api/renovation/plan/submit", "POST");
}

export async function approveRenovationPlan(): Promise<RenovationApprove> {
    return requestWrite("/api/renovation/plan/approve", "POST");
}

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

export async function getRenovationDictionaryAssets(): Promise<RenovationAssetRow[]> {
    return requestJson("/api/renovation/dictionary/assets");
}

export async function createRenovationAsset(
    name: string,
    repairCode: string,
): Promise<RenovationAssetRow> {
    // session_id makes the create an undoable action (server-side journal).
    return requestWrite(
        `/api/renovation/dictionary/assets?session_id=${getCollabSessionId()}`,
        "POST",
        { name, repairCode },
    );
}

export async function updateRenovationAssetCells(updates: CellUpdate[]): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "assets",
        updates,
    });
}

export async function deleteRenovationAsset(assetId: string): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/assets/${encodeURIComponent(assetId)}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

/** Remove a user from «Ремонт» (only possible once their access is revoked). */
export async function deleteRenovationUser(userId: string): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/users/${encodeURIComponent(userId)}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

export async function getRenovationCatalogOptions(
    catalogId: string,
): Promise<RenovationCatalogOptionRow[]> {
    return requestJson(`/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}`);
}

export async function createRenovationCatalogOption(
    catalogId: string,
    label: string,
): Promise<RenovationCatalogOptionRow> {
    // session_id makes the create an undoable action (server-side journal).
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options?session_id=${getCollabSessionId()}`,
        "POST",
        { label },
    );
}

export async function updateRenovationCatalogOptionCells(
    catalogId: string,
    updates: CellUpdate[],
): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "catalog",
        catalog: catalogId,
        updates,
    });
}

export async function deleteRenovationCatalogOption(
    catalogId: string,
    valueId: string,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options/${encodeURIComponent(valueId)}?session_id=${getCollabSessionId()}`,
        "DELETE",
    );
}

export async function replaceRenovationAsset(
    assetId: string,
    targetId: string | null,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/assets/${encodeURIComponent(assetId)}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        { targetId },
    );
}

export async function replaceRenovationCatalogOption(
    catalogId: string,
    valueId: string,
    targetId: string | null,
): Promise<void> {
    return requestWrite(
        `/api/renovation/dictionary/catalogs/${encodeURIComponent(catalogId)}/options/${encodeURIComponent(valueId)}/replace?session_id=${getCollabSessionId()}`,
        "POST",
        { targetId },
    );
}

export async function getRenovationDictionaryUsers(): Promise<RenovationUserRow[]> {
    return requestJson("/api/renovation/dictionary/users");
}

export async function createRenovationUser(payload: {
    email: string;
    fullname: string;
    role: string;
    block: string | null;
    assetNames: string[];
}): Promise<RenovationUserRow> {
    return requestWrite("/api/renovation/dictionary/users", "POST", payload);
}

export async function updateRenovationUserCells(updates: CellUpdate[]): Promise<unknown> {
    return requestWrite(`/api/renovation/dictionary?session_id=${getCollabSessionId()}`, "PATCH", {
        section: "users",
        updates,
    });
}

export type RenovationExportColumn = {
    key: string;
    headerPath: string[];
    width: number;
    numberFormat: string | null;
};

export type RenovationExportJobDownload = {
    job: ExportJobDescriptor;
    fileUrl: string;
};

/** Download a renovation dictionary export — inline, or as a background job past the inline row limit. */
export async function exportRenovationDictionary(request: {
    section: string;
    headerPinned: boolean;
    columns: RenovationExportColumn[];
    rowIds: string[] | null;
    fileName: string;
}): Promise<Blob | RenovationExportJobDownload> {
    return downloadTableExport("/api/renovation/dictionary/export", request, (job, base) => ({
        job,
        fileUrl: tableExportJobFileUrl(base, job.id),
    }));
}

// ─── Section-scoped undo/redo (plan and dictionaries) ─────────────────────

export async function undoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/undo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function redoCells(
    skipActionId: number | null,
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoResult> {
    return requestWrite(`/api/renovation/redo?session_id=${getCollabSessionId()}`, "POST", {
        skipActionId,
        section,
        reportMonth: null,
    });
}

export async function getUndoState(
    section: string = RENOVATION_SECTION,
): Promise<RenovationUndoState> {
    const params = new URLSearchParams({ section, session_id: getCollabSessionId() });
    return requestJson(`/api/renovation/undo-state?${params.toString()}`);
}
