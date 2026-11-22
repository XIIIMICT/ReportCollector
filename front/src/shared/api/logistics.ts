import { requestJson, requestWrite } from "@/shared/api/api-error";
import { ReportCollectorApiError } from "@/shared/api/report-collector";
import { downloadTableExport } from "@/shared/api/table-export";
import { getCollabSessionId } from "@/shared/collab/ws-client";
import type { ExportColumn } from "@/shared/grid/grid-config";

/**
 * The «Логистика» dictionaries. They are read straight from the corporate
 * `dict` schema and are never edited here: the API exposes GET endpoints only.
 *
 * `source` says where the rows came from. `fallback` means the external table
 * is absent and the server returned its built-in demo list — the workspace
 * warns about it instead of presenting the values as real data.
 */

export type LogisticsSectionId = "logisticsCountries" | "logisticsTransportTypes" | "logisticsOrgs";

export type LogisticsSource = "external" | "fallback";

export type LogisticsCountryRow = {
    id: string;
    shortname: string;
    /** Computed by the server: «РФ» (alpha2=RU), «БЗ» (ts/sng), otherwise «ДЗ». */
    market: string;
};

export type LogisticsTransportRow = {
    id: string;
    /** Groups consecutive rows: the transport type cell is merged over them. */
    transportTypeId: string;
    transportType: string;
    /** The source vehicle-type id; null when the type has no mappings. */
    vehicleTypeId: string | null;
    /** Empty when the transport type has no mapped vehicle types. */
    vehicleType: string;
};

export type LogisticsOrgRow = {
    id: string;
    prefixRu: string;
};

export type LogisticsHazardClassRow = {
    /** The class number, 1..9. */
    value: number;
    name: string;
};

export type LogisticsRole = "admin" | "reviewer" | "asset_worker" | "blocked";

export type LogisticsUserRow = {
    id: string;
    email: string;
    fullname: string;
    /** The membership role; `blocked` is the project-access off state. */
    role: LogisticsRole;
    /** Derived from the role on the server (blocked → false). */
    isActive: boolean;
    /** Assets granted to an `asset_worker` (empty for the other roles). */
    assetIds: string[];
    assetNames: string[];
    lastAuthenticatedAt: string | null;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export type LogisticsUserCreatePayload = {
    email: string;
    fullname: string;
    role: LogisticsRole;
    assetIds: string[];
};

export type LogisticsPriceKind = "INDICATIVE" | "PRICE";

/** One «Ставка» row; the computed fields arrive ready to display. */
export type LogisticsRateRow = {
    id: string;
    periodDate: string;
    assetId: string;
    assetName: string;
    /** Computed: a hazard class is set. */
    dangerous: boolean;
    hazardClassValue: number | null;
    product: string | null;
    direction: "РФ" | "БЗ" | "ДЗ" | "Импорт" | "Экспорт";
    countryFromId: string;
    countryFromName: string;
    originPoint: string | null;
    countryToId: string;
    countryToName: string;
    destinationPoint: string | null;
    transportTypeId: string;
    transportTypeName: string;
    vehicleTypeId: string | null;
    vehicleTypeName: string;
    loadingTons: number;
    freightRateRub: number;
    freightRateUsd: number;
    pprRateRub: number;
    pprRateUsd: number;
    deliveryRateRub: number;
    deliveryRateUsd: number;
    netRateRub: number;
    netRateUsd: number;
    /** The previous month's net rates for the same identity (null: none). */
    prevNetRateRub: number | null;
    prevNetRateUsd: number | null;
    /** Computed: net − previous net; «%» is the rub difference for now. */
    deviationRub: number | null;
    deviationUsd: number | null;
    deviationPercent: number | null;
    priceKind: LogisticsPriceKind;
    note: string | null;
    createdAt: string;
    updatedAt: string;
    updatedBy: string;
};

export type LogisticsRateView = {
    periodDate: string;
    /** The constant USD rate the server used for the dollar columns. */
    usdRate: number;
    rows: LogisticsRateRow[];
};

export type LogisticsRateCreatePayload = {
    periodDate: string;
    /** null for an asset worker — the server stamps their asset. */
    assetId: string | null;
    hazardClassValue: number | null;
    countryFromId: string;
    countryToId: string;
    transportTypeId: string;
    vehicleTypeId: string | null;
    loadingTons: number;
    freightRateRub: number;
    pprRateRub: number;
    deliveryRateRub: number;
    priceKind: LogisticsPriceKind;
    note: string | null;
};

export type LogisticsUndoResult = {
    applied: boolean;
    reason?: string;
    conflicts?: Array<{ user?: string; field?: string; reason?: string }>;
};

export type LogisticsUndoState = {
    canUndo: boolean;
    canRedo: boolean;
};

export type LogisticsDictionary<T> = {
    source: LogisticsSource;
    rows: T[];
};

export async function getLogisticsCountries(): Promise<LogisticsDictionary<LogisticsCountryRow>> {
    return requestJson("/api/logistics/dictionaries/countries");
}

export async function getLogisticsTransportTypes(): Promise<
    LogisticsDictionary<LogisticsTransportRow>
> {
    return requestJson("/api/logistics/dictionaries/transport-types");
}

export async function getLogisticsOrgs(): Promise<LogisticsDictionary<LogisticsOrgRow>> {
    return requestJson("/api/logistics/dictionaries/orgs");
}

/**
 * The «Класс опасности» dictionary (1..9). It has no tab yet; the future main
 * table uses it as a reference.
 */
export async function getLogisticsHazardClasses(): Promise<LogisticsHazardClassRow[]> {
    return requestJson("/api/logistics/dictionaries/hazard-classes");
}

export async function getLogisticsUsers(): Promise<LogisticsUserRow[]> {
    return requestJson("/api/logistics/users");
}

export async function createLogisticsUser(
    payload: LogisticsUserCreatePayload,
): Promise<LogisticsUserRow> {
    return requestWrite(`/api/logistics/users?session_id=${getCollabSessionId()}`, "POST", payload);
}

export async function updateLogisticsUserCell(
    id: string,
    field: string,
    value: unknown,
): Promise<LogisticsUserRow> {
    return requestWrite(`/api/logistics/users/${id}?session_id=${getCollabSessionId()}`, "PATCH", {
        [field]: value,
    });
}

export async function deleteLogisticsUser(id: string): Promise<void> {
    await requestWrite(`/api/logistics/users/${id}?session_id=${getCollabSessionId()}`, "DELETE");
}

export async function getLogisticsUsersUndoState(): Promise<LogisticsUndoState> {
    return requestJson(`/api/logistics/users/undo-state?session_id=${getCollabSessionId()}`);
}

export async function undoLogisticsUsers(skipActionId?: number): Promise<LogisticsUndoResult> {
    return requestWrite(`/api/logistics/users/undo?session_id=${getCollabSessionId()}`, "POST", {
        section: "logisticsUsers",
        skipActionId,
    });
}

export async function redoLogisticsUsers(skipActionId?: number): Promise<LogisticsUndoResult> {
    return requestWrite(`/api/logistics/users/redo?session_id=${getCollabSessionId()}`, "POST", {
        section: "logisticsUsers",
        skipActionId,
    });
}

export async function getLogisticsRates(month: number, year: number): Promise<LogisticsRateView> {
    return requestJson(`/api/logistics/rates?month=${month}&year=${year}`);
}

export async function createLogisticsRate(
    payload: LogisticsRateCreatePayload,
): Promise<LogisticsRateRow> {
    return requestWrite(`/api/logistics/rates?session_id=${getCollabSessionId()}`, "POST", payload);
}

export async function updateLogisticsRateCell(
    id: string,
    field: string,
    value: unknown,
): Promise<LogisticsRateRow> {
    return requestWrite(`/api/logistics/rates/${id}?session_id=${getCollabSessionId()}`, "PATCH", {
        field,
        value,
    });
}

export async function deleteLogisticsRate(id: string): Promise<void> {
    await requestWrite(`/api/logistics/rates/${id}?session_id=${getCollabSessionId()}`, "DELETE");
}

export type LogisticsExportSection =
    | "logisticsRates"
    | "logisticsCountries"
    | "logisticsTransportTypes"
    | "logisticsOrgs"
    | "logisticsUsers";

export type LogisticsExportRequest = {
    section: LogisticsExportSection;
    headerPinned: boolean;
    columns: ExportColumn[];
    /** `null` — «вся таблица»; the visible rows' ids — «видимая». */
    rowIds: string[] | null;
    fileName: string;
    /** Required for «Ставки»: the grid shows one month/year. */
    month?: number | null;
    year?: number | null;
};

/** Download a «Логистика» grid as XLSX (inline: the tables are small). */
export async function exportLogisticsTable(request: LogisticsExportRequest): Promise<Blob> {
    return downloadTableExport("/api/logistics/export", request, () => {
        throw new ReportCollectorApiError("Выгрузка недоступна.", 500);
    });
}

export async function getLogisticsRatesUndoState(periodDate: string): Promise<LogisticsUndoState> {
    return requestJson(
        `/api/logistics/rates/undo-state?session_id=${getCollabSessionId()}&period_date=${periodDate}`,
    );
}

export async function undoLogisticsRates(
    periodDate: string,
    skipActionId?: number,
): Promise<LogisticsUndoResult> {
    return requestWrite(`/api/logistics/rates/undo?session_id=${getCollabSessionId()}`, "POST", {
        section: "logisticsRates",
        reportMonth: periodDate,
        skipActionId,
    });
}

export async function redoLogisticsRates(
    periodDate: string,
    skipActionId?: number,
): Promise<LogisticsUndoResult> {
    return requestWrite(`/api/logistics/rates/redo?session_id=${getCollabSessionId()}`, "POST", {
        section: "logisticsRates",
        reportMonth: periodDate,
        skipActionId,
    });
}
