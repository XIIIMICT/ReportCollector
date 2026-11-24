/**
 * Renovation API — plan reads, facets, cell/row edits and lifecycle.
 *
 * Moved verbatim from `renovation.ts` (Phase 4). Runtime request/response shapes
 * are unchanged; `renovation.ts` is now a barrel re-exporting every workflow.
 */

import { requestJson, requestWrite } from "@/shared/api/api-error";
import type { CellUpdate } from "@/shared/api/raw-materials";
import { getCollabSessionId } from "@/shared/collab/ws-client";
import type { FacetSearch } from "@/shared/ui/column-filter-facets";

import type {
    RenovationApprove,
    RenovationColumnFilter,
    RenovationDiff,
    RenovationMeta,
    RenovationPlan,
    RenovationPlanRow,
    RenovationPlanVersion,
    RenovationSortColumn,
    RenovationUpdate,
} from "./renovation-shared";

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
    /** Whole-plan row count (ignoring the asset filter) for the ┬л╨▓╤Б╨╡╨│╨╛┬╗ counter. */
    totalAll: number | null;
    /**
     * Rows before the requested `anchorPosition` within the filtered scope тАФ
     * the anchor row's 0-based index (`total` when it is past the last row).
     */
    anchorOffset: number | null;
};

export async function getRenovationMeta(): Promise<RenovationMeta> {
    return requestJson("/api/renovation/meta");
}

export async function getRenovationPlan(planId?: string): Promise<RenovationPlan> {
    const query = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
    return requestJson(`/api/renovation/plan${query}`);
}

/** One active header filter: kept display values + the ┬л╨Я╤Г╤Б╤В╤Л╨╡┬╗ bucket. */
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
    // A `timestamptz` window is typed as LOCAL date and time (┬л15.10.2026
    // 00:00┬╗), exactly what the cells print. Without the zone the server has to
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
    /** Whether the column has a NULL/empty bucket (the ┬л╨Я╤Г╤Б╤В╤Л╨╡┬╗ option). */
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

export async function submitRenovationPlan(): Promise<RenovationPlanVersion> {
    return requestWrite("/api/renovation/plan/submit", "POST");
}

export async function approveRenovationPlan(): Promise<RenovationApprove> {
    return requestWrite("/api/renovation/plan/approve", "POST");
}
