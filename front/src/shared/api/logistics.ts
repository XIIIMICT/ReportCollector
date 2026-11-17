import { requestJson } from "@/shared/api/api-error";

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
