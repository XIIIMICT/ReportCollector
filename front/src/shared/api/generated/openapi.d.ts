// GENERATED FILE — do not edit.
// Source: live app.openapi() exported by back/src/openapi_export.py
// Regenerate with: bun run --cwd front gen:openapi
/* eslint-disable */
/* tslint:disable */
// @ts-nocheck
/*
 * ---------------------------------------------------------------
 * ## THIS FILE WAS GENERATED VIA SWAGGER-TYPESCRIPT-API        ##
 * ##                                                           ##
 * ## AUTHOR: acacode                                           ##
 * ## SOURCE: https://github.com/acacode/swagger-typescript-api ##
 * ---------------------------------------------------------------
 */

/**
 * Theme
 * UI theme. Stored as a DB enum (user_theme), not free text.
 */
export type Theme = "system" | "light" | "dark";

/**
 * RProjectRole
 * Fixed set of renovation project roles. Hardcoded; the DB stores an enum.
 */
export type RProjectRole = "admin" | "approver" | "block_owner" | "asset_worker" | "blocked";

/**
 * RPlanState
 * Lifecycle of a renovation plan version.
 */
export type RPlanState = "working" | "pending_approval" | "archived";

/**
 * RBlock
 * Responsibility block of the renovation plan (Excel columns grouped by owner).
 *
 * The three blocks map to company directorates (the legacy surname-based names
 * were replaced): tech = Техническая дирекция, ksir = Дирекция КСиР (капитальное
 * строительство и ремонт), mto = Дирекция МТО (материально-техническое
 * обеспечение). Required for block_owner/asset_worker roles; empty for
 * admin/approver.
 */
export type RBlock = "tech" | "ksir" | "mto";

/**
 * ProjectRole
 * Fixed set of project roles. Hardcoded; the DB stores an enum.
 */
export type ProjectRole = "admin" | "reviewer" | "asset_worker" | "blocked";

/**
 * LogisticsPriceKind
 * The nature of a price in the «Логистика» main table.
 *
 * ``INDICATIVE`` is an indicative (estimate) price; ``PRICE`` is the firm
 * price the shipment is actually billed at. The DB type is
 * ``logistics_price_kind`` (migration 0032), so a future main-table column
 * can reference the same enum the API exposes.
 */
export type LogisticsPriceKind = "INDICATIVE" | "PRICE";

/** AssetAccessRead */
export interface AssetAccessRead {
    /** Name */
    name: string;
}

/**
 * AssetCreateRequest
 * Asset creation.
 */
export interface AssetCreateRequest {
    /**
     * Name
     * @minLength 1
     * @maxLength 255
     */
    name: string;
}

/**
 * AssetOptionRead
 * Asset with id: the grid maps asset names to their ids.
 */
export interface AssetOptionRead {
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
}

/**
 * AssetRowRead
 * «Активы» dictionary row.
 */
export interface AssetRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * AssetUpdateRequest
 * Asset edit.
 */
export interface AssetUpdateRequest {
    /** Name */
    name?: string | null;
}

/** Body_apply_renovation_plan_import_api_renovation_plan_import_apply_post */
export interface BodyApplyRenovationPlanImportApiRenovationPlanImportApplyPost {
    /**
     * Expected Rows
     * @min 0
     * @default 0
     */
    expected_rows?: number;
    /** File */
    file: File | Blob;
}

/** Body_inspect_renovation_plan_import_api_renovation_plan_import_inspect_post */
export interface BodyInspectRenovationPlanImportApiRenovationPlanImportInspectPost {
    /** File */
    file: File | Blob;
}

/**
 * CurrentUserRead
 * Current user: profile and available (activated) projects.
 */
export interface CurrentUserRead {
    /** Access */
    access: ProjectAccessRead[];
    /** Email */
    email: string | null;
    /** Name */
    name: string | null;
    /**
     * UI theme. Stored as a DB enum (user_theme), not free text.
     * @default "system"
     */
    theme?: Theme;
}

/**
 * DeliveryBasisCreateRequest
 * Delivery basis creation.
 */
export interface DeliveryBasisCreateRequest {
    /**
     * Name
     * @minLength 1
     * @maxLength 255
     */
    name: string;
}

/**
 * DeliveryBasisRowRead
 * «Базисы поставки» dictionary row.
 */
export interface DeliveryBasisRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * DeliveryBasisUpdateRequest
 * Delivery basis edit.
 */
export interface DeliveryBasisUpdateRequest {
    /** Name */
    name?: string | null;
}

/**
 * DictionaryBatchRequest
 * Batch of dictionary edits for one section (one request, one undo action).
 */
export interface DictionaryBatchRequest {
    /** Section */
    section: "rawMaterials" | "assets" | "deliveryBases" | "users";
    /**
     * Updates
     * @maxItems 10000
     * @minItems 1
     */
    updates: SrcRawMaterialsSchemasCellUpdate[];
}

/**
 * DictionaryCellUpdate
 * Single dictionary cell edit (values may be lists, e.g. assetNames).
 */
export interface DictionaryCellUpdate {
    /**
     * Field
     * @minLength 1
     * @maxLength 64
     */
    field: string;
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /** Value */
    value?: string | number | boolean | string[] | null;
}

/**
 * DictionaryReplaceRequest
 * Dictionary value replacement: `null` deletes the value instead.
 */
export interface DictionaryReplaceRequest {
    /** Targetid */
    targetId?: string | null;
}

/**
 * ExportColumnRequest
 * One exported table column — the shared request shape of every project.
 *
 * It lives here, next to the plumbing that validates it, so a project's
 * schemas never have to import another project's module (КППК and
 * «Логистика» used to take it from «Сырьё»).
 */
export interface ExportColumnRequest {
    /**
     * Headerpath
     * @maxItems 3
     * @minItems 1
     */
    headerPath: string[];
    /** Horizontalalignment */
    horizontalAlignment?: "left" | "center" | "right" | null;
    /**
     * Key
     * @minLength 1
     * @maxLength 64
     */
    key: string;
    /** Numberformat */
    numberFormat?: string | null;
    /** Verticalalignment */
    verticalAlignment?: "top" | "center" | "bottom" | null;
    /**
     * Width
     * @min 1
     * @max 100
     * @default 12
     */
    width?: number;
}

/**
 * ExportJobRead
 * The job status the front polls / downloads by.
 */
export interface ExportJobRead {
    /**
     * Attempt
     * @default 0
     */
    attempt?: number;
    /** Error */
    error?: string | null;
    /** Filename */
    fileName: string;
    /** Heartbeatat */
    heartbeatAt?: string | null;
    /** Id */
    id: string;
    /** Phase */
    phase?: string | null;
    /** Progress */
    progress: number;
    /** Startedat */
    startedAt?: string | null;
    /** Status */
    status: "pending" | "running" | "done" | "failed";
    /** Total */
    total: number;
}

/**
 * ExportViewRequest
 * Excel export request («вся» — without rowIds, «видимая» — with rowIds).
 */
export interface ExportViewRequest {
    /**
     * Columns
     * @maxItems 64
     * @minItems 1
     */
    columns: ExportColumnRequest[];
    /**
     * Filename
     * @minLength 1
     * @maxLength 255
     */
    fileName: string;
    /**
     * Headerpinned
     * @default true
     */
    headerPinned?: boolean;
    /** Reportmonth */
    reportMonth?: string | null;
    /** Rowids */
    rowIds?: string[] | null;
    /** Section */
    section:
        | "materialMovement"
        | "rawMaterialPrices"
        | "rawMaterials"
        | "assets"
        | "deliveryBases"
        | "users";
    /** Snapshotid */
    snapshotId?: string | null;
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
}

/**
 * FixMovementRequest
 * «Движение» fixing request for the report month.
 */
export interface FixMovementRequest {
    /**
     * Reportmonth
     * @format date
     */
    reportMonth: string;
}

/** HTTPValidationError */
export interface HTTPValidationError {
    /** Detail */
    detail?: ValidationError[];
}

/**
 * KppkAppliedRead
 * Mutation result with the new row payload (grid-friendly).
 *
 * `row` is `None` when the edit removed the row it echoed back — a grouped
 * «ДЗО» edit that drops the position, or leaves the product with no assets at
 * all. The `applied` cells remain the record of what changed.
 */
export interface KppkAppliedRead {
    /** Row */
    row?:
        | KppkProductPositionRowRead
        | KppkRawRowRead
        | KppkOrgRowRead
        | KppkRealizationRowRead
        | KppkCostRowRead
        | null;
}

/**
 * KppkCostRowRead
 * One product's production costs; no market dimension.
 */
export interface KppkCostRowRead {
    /** Fullcostbp */
    fullCostBp?: number | null;
    /** Fullcostfact */
    fullCostFact?: number | null;
    /** Fullcostop */
    fullCostOp?: number | null;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Product */
    product: string;
    /** Productnum */
    productNum?: number | null;
    /** Productioncostbp */
    productionCostBp?: number | null;
    /** Productioncostfact */
    productionCostFact?: number | null;
    /** Productioncostop */
    productionCostOp?: number | null;
    /** Variablecostbp */
    variableCostBp?: number | null;
    /** Variablecostfact */
    variableCostFact?: number | null;
    /** Variablecostop */
    variableCostOp?: number | null;
}

/** KppkCostViewRead */
export interface KppkCostViewRead {
    /** The banner period of the «Реализация» view (месяц/год of the form header). */
    period: KppkRealizationPeriodRead;
    /** Rows */
    rows: KppkCostRowRead[];
}

/**
 * KppkExportViewRequest
 * Excel export of a «КППК» dictionary grid («вся» / «видимая»).
 */
export interface KppkExportViewRequest {
    /**
     * Columns
     * @maxItems 64
     * @minItems 1
     */
    columns: ExportColumnRequest[];
    /**
     * Filename
     * @minLength 1
     * @maxLength 255
     */
    fileName: string;
    /**
     * Headerpinned
     * @default true
     */
    headerPinned?: boolean;
    /** Rowids */
    rowIds?: string[] | null;
    /** Section */
    section: "kppkPositions" | "kppkRaws" | "kppkOrgs" | "kppkUsers";
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
}

/**
 * KppkOrgCellUpdate
 * One dictionary cell edit of a ДЗО row.
 */
export interface KppkOrgCellUpdate {
    /** Field */
    field: string;
    /** Value */
    value?: any;
}

/**
 * KppkOrgCreateRequest
 * A new ДЗО.
 */
export interface KppkOrgCreateRequest {
    /**
     * Code
     * @minLength 1
     * @maxLength 20
     */
    code: string;
    /**
     * Name
     * @minLength 1
     * @maxLength 200
     */
    name: string;
}

/**
 * KppkOrgRowRead
 * One row of «ДЗО».
 */
export interface KppkOrgRowRead {
    /** Code */
    code: string;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * KppkProductPositionCellUpdate
 * One dictionary cell edit of a «Продуктовые позиции» row.
 */
export interface KppkProductPositionCellUpdate {
    /** Field */
    field: string;
    /** Value */
    value?: any;
}

/**
 * KppkProductPositionCreateRequest
 * A new «Продуктовые позиции» entry.
 *
 * Two shapes over the same stored positions: the position view sends one
 * `orgName`, the default (grouped) view sends the whole `orgNames` set of a
 * product typed once. A NEW product may carry no ДЗО at all — the positions
 * can be added later.
 */
export interface KppkProductPositionCreateRequest {
    /**
     * Hasexcise
     * @default false
     */
    hasExcise?: boolean;
    /**
     * Name
     * @minLength 1
     * @maxLength 255
     */
    name: string;
    /** Orgname */
    orgName?: string | null;
    /** Orgnames */
    orgNames?: string[] | null;
    /** Productnum */
    productNum?: number | null;
}

/**
 * KppkProductPositionRowRead
 * One row of «Продуктовые позиции» — a product × ДЗО POSITION.
 */
export interface KppkProductPositionRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Hasexcise
     * @default false
     */
    hasExcise?: boolean;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /** Orgcode */
    orgCode?: string | null;
    /** Orgname */
    orgName: string;
    /**
     * Orgnames
     * @default []
     */
    orgNames?: string[];
    /**
     * Orgshortnames
     * @default []
     */
    orgShortNames?: string[];
    /**
     * Productid
     * @format uuid
     */
    productId: string;
    /** Productnum */
    productNum: number | null;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * KppkRawCellUpdate
 * One dictionary cell edit of a «Сырьевые позиции» row.
 */
export interface KppkRawCellUpdate {
    /** Field */
    field: string;
    /** Value */
    value?: any;
}

/**
 * KppkRawCreateRequest
 * A new raw item.
 */
export interface KppkRawCreateRequest {
    /**
     * Name
     * @minLength 1
     * @maxLength 2000
     */
    name: string;
    /** Rawnum */
    rawNum?: number | null;
}

/**
 * KppkRawRowRead
 * One row of «Сырьевые позиции».
 */
export interface KppkRawRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /** Rawnum */
    rawNum: number | null;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * KppkRealizationCellUpdate
 * One «Реализация» cell edit (the field names follow DB attributes).
 */
export interface KppkRealizationCellUpdate {
    /** Field */
    field: string;
    /** Value */
    value?: any;
}

/**
 * KppkRealizationExportRequest
 * Excel export of the «Реализация» view.
 *
 * The form is not a dictionary row list but one slice × ДЗО × period, so
 * `rowIds` is what separates the two toolbar modes:
 * - «вся таблица» — `rowIds` is null: the server re-reads the whole view.
 * - «видимая» — `rowIds` lists the rows the GRID currently shows, in the
 *   order it shows them. The grid filters and sorts client-side, so only it
 *   knows the visible set; the server must not guess. The order is the
 *   client's, which is what makes an export of a SORTED grid come out sorted.
 */
export interface KppkRealizationExportRequest {
    /**
     * Columns
     * @maxItems 16
     * @minItems 1
     */
    columns: ExportColumnRequest[];
    /**
     * Filename
     * @minLength 1
     * @maxLength 255
     */
    fileName: string;
    /**
     * Headerpinned
     * @default true
     */
    headerPinned?: boolean;
    /**
     * Mergerows
     * @default true
     */
    mergeRows?: boolean;
    /** Month */
    month?: number | null;
    /**
     * Orgid
     * @format uuid
     */
    orgId: string;
    /** Rowids */
    rowIds?: string[] | null;
    /** Slice */
    slice: "bp" | "op" | "fact";
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
    /** Year */
    year?: number | null;
}

/**
 * KppkRealizationPeriodRead
 * The banner period of the «Реализация» view (месяц/год of the form header).
 */
export interface KppkRealizationPeriodRead {
    /** Monthname */
    monthName: string;
    /**
     * Perioddate
     * @format date
     */
    periodDate: string;
    /**
     * Repdate
     * @format date
     */
    repDate: string;
    /** Yearnum */
    yearNum: number;
}

/**
 * KppkRealizationRowRead
 * One row of a «Реализация» slice (the slice leaves its columns filled).
 */
export interface KppkRealizationRowRead {
    /** Buyoutbpplan */
    buyoutBpPlan?: number | null;
    /** Buyoutfact */
    buyoutFact?: number | null;
    /** Buyoutmaxplan */
    buyoutMaxPlan?: number | null;
    /** Excisebp */
    exciseBp?: number | null;
    /** Excisefact */
    exciseFact?: number | null;
    /** Excisemaxplan */
    exciseMaxPlan?: number | null;
    /** Fact */
    fact?: number | null;
    /** Fcapricebpplan */
    fcaPriceBpPlan?: number | null;
    /** Fcapricefact */
    fcaPriceFact?: number | null;
    /** Fcapricemarketbpplan */
    fcaPriceMarketBpPlan?: number | null;
    /** Fcapricemarketfact */
    fcaPriceMarketFact?: number | null;
    /** Fcapricemarketmaxplan */
    fcaPriceMarketMaxPlan?: number | null;
    /** Fcapricemaxplan */
    fcaPriceMaxPlan?: number | null;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Monthbpplan */
    monthBpPlan?: number | null;
    /** Monthmaxplan */
    monthMaxPlan?: number | null;
    /** Product */
    product: string;
    /** Productnum */
    productNum?: number | null;
    /** Region */
    region: string;
}

/**
 * KppkRealizationViewRead
 * The «Реализация» view: the banner period, the рынки, the filtered rows.
 */
export interface KppkRealizationViewRead {
    /**
     * Exciseproducts
     * @default []
     */
    exciseProducts?: string[];
    /** Markets */
    markets: string[];
    /** The banner period of the «Реализация» view (месяц/год of the form header). */
    period: KppkRealizationPeriodRead;
    /** Rows */
    rows: KppkRealizationRowRead[];
}

/**
 * KppkReplaceRequest
 * Dictionary value replacement: `null` deletes the value instead.
 */
export interface KppkReplaceRequest {
    /** Targetid */
    targetId?: string | null;
}

/**
 * KppkUserCreateRequest
 * Create an administrator; organization grants are not implemented yet.
 */
export interface KppkUserCreateRequest {
    /**
     * Email
     * @format email
     */
    email: string;
    /**
     * Fullname
     * @minLength 1
     * @maxLength 255
     */
    fullname: string;
    /**
     * Isadmin
     * @default true
     */
    isAdmin?: boolean;
    role?: ProjectRole | null;
}

/**
 * KppkUserRowRead
 * One row of the «Пользователи» dictionary of «КППК».
 */
export interface KppkUserRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Email */
    email: string;
    /** Fullname */
    fullname: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Isactive */
    isActive: boolean;
    /** Isadmin */
    isAdmin: boolean;
    /** Lastauthenticatedat */
    lastAuthenticatedAt?: string | null;
    /** Orgnames */
    orgNames?: string[];
    /** Fixed set of project roles. Hardcoded; the DB stores an enum. */
    role: ProjectRole;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * KppkUserUpdateRequest
 * User fields shared with the raw-materials grid, without asset grants.
 */
export interface KppkUserUpdateRequest {
    /** Email */
    email?: string | null;
    /** Fullname */
    fullname?: string | null;
    /** Isactive */
    isActive?: boolean | null;
    /** Isadmin */
    isAdmin?: boolean | null;
    /**
     * Resetpassword
     * @default false
     */
    resetPassword?: boolean;
    role?: ProjectRole | null;
}

/** LocalLoginRequest */
export interface LocalLoginRequest {
    /** Email */
    email: string;
    /** Password */
    password: string;
}

/**
 * LocalLoginResponse
 * User identity for the browser session (no password hash).
 */
export interface LocalLoginResponse {
    /** Email */
    email: string | null;
    /** Issuer */
    issuer: string;
    /** Name */
    name: string | null;
    /** Sub */
    sub: string;
}

/**
 * LogisticsCountryRowRead
 * One country: the short name and the computed sales market.
 */
export interface LogisticsCountryRowRead {
    /** Id */
    id: string;
    /** Market */
    market: "РФ" | "БЗ" | "ДЗ";
    /** Shortname */
    shortname: string;
}

/** LogisticsDictionaryRead[LogisticsCountryRowRead] */
export interface LogisticsDictionaryReadLogisticsCountryRowRead {
    /** Rows */
    rows: LogisticsCountryRowRead[];
    /** Source */
    source: "external" | "fallback";
}

/** LogisticsDictionaryRead[LogisticsOrgRowRead] */
export interface LogisticsDictionaryReadLogisticsOrgRowRead {
    /** Rows */
    rows: LogisticsOrgRowRead[];
    /** Source */
    source: "external" | "fallback";
}

/** LogisticsDictionaryRead[LogisticsTransportRowRead] */
export interface LogisticsDictionaryReadLogisticsTransportRowRead {
    /** Rows */
    rows: LogisticsTransportRowRead[];
    /** Source */
    source: "external" | "fallback";
}

/**
 * LogisticsExportViewRequest
 * Excel export of a «Логистика» grid («вся» / «видимая»).
 */
export interface LogisticsExportViewRequest {
    /**
     * Columns
     * @maxItems 64
     * @minItems 1
     */
    columns: ExportColumnRequest[];
    /**
     * Filename
     * @minLength 1
     * @maxLength 255
     */
    fileName: string;
    /**
     * Headerpinned
     * @default true
     */
    headerPinned?: boolean;
    /** Month */
    month?: number | null;
    /** Rowids */
    rowIds?: string[] | null;
    /** Section */
    section:
        | "logisticsRates"
        | "logisticsCountries"
        | "logisticsTransportTypes"
        | "logisticsOrgs"
        | "logisticsUsers";
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
    /** Year */
    year?: number | null;
}

/**
 * LogisticsHazardClassRowRead
 * One «Класс опасности» value (1..9).
 */
export interface LogisticsHazardClassRowRead {
    /** Name */
    name: string;
    /** Value */
    value: number;
}

/**
 * LogisticsOrgRowRead
 * One organization, represented by its Russian prefix.
 */
export interface LogisticsOrgRowRead {
    /** Id */
    id: string;
    /** Prefixru */
    prefixRu: string;
}

/**
 * LogisticsRateCellUpdate
 * One editable cell of a rate; the service validates the field/value pair.
 */
export interface LogisticsRateCellUpdate {
    /** Field */
    field: string;
    /** Value */
    value: any;
}

/**
 * LogisticsRateCreateRequest
 * Insert a complete rate row; the placeholders are always NULL.
 */
export interface LogisticsRateCreateRequest {
    /** Assetid */
    assetId?: string | null;
    /**
     * Countryfromid
     * @format uuid
     */
    countryFromId: string;
    /**
     * Countrytoid
     * @format uuid
     */
    countryToId: string;
    /** Deliveryraterub */
    deliveryRateRub: number | string;
    /** Freightraterub */
    freightRateRub: number | string;
    /** Hazardclassvalue */
    hazardClassValue?: number | null;
    /** Loadingtons */
    loadingTons: number | string;
    /** Note */
    note?: string | null;
    /**
     * Perioddate
     * @format date
     */
    periodDate: string;
    /** Pprraterub */
    pprRateRub: number | string;
    /**
     * The nature of a price in the «Логистика» main table.
     *
     * ``INDICATIVE`` is an indicative (estimate) price; ``PRICE`` is the firm
     * price the shipment is actually billed at. The DB type is
     * ``logistics_price_kind`` (migration 0032), so a future main-table column
     * can reference the same enum the API exposes.
     */
    priceKind: LogisticsPriceKind;
    /**
     * Transporttypeid
     * @format uuid
     */
    transportTypeId: string;
    /** Vehicletypeid */
    vehicleTypeId?: string | null;
}

/**
 * LogisticsRateRowRead
 * One «Ставка» row with its computed columns.
 */
export interface LogisticsRateRowRead {
    /**
     * Assetid
     * @format uuid
     */
    assetId: string;
    /** Assetname */
    assetName: string;
    /**
     * Countryfromid
     * @format uuid
     */
    countryFromId: string;
    /** Countryfromname */
    countryFromName: string;
    /**
     * Countrytoid
     * @format uuid
     */
    countryToId: string;
    /** Countrytoname */
    countryToName: string;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Dangerous */
    dangerous: boolean;
    /** Deliveryraterub */
    deliveryRateRub: number;
    /** Deliveryrateusd */
    deliveryRateUsd: number;
    /** Destinationpoint */
    destinationPoint: string | null;
    /** Deviationpercent */
    deviationPercent: number | null;
    /** Deviationrub */
    deviationRub: number | null;
    /** Deviationusd */
    deviationUsd: number | null;
    /** Direction */
    direction: "РФ" | "БЗ" | "ДЗ" | "Импорт" | "Экспорт";
    /** Freightraterub */
    freightRateRub: number;
    /** Freightrateusd */
    freightRateUsd: number;
    /** Hazardclassvalue */
    hazardClassValue: number | null;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Loadingtons */
    loadingTons: number;
    /** Netraterub */
    netRateRub: number;
    /** Netrateusd */
    netRateUsd: number;
    /** Note */
    note: string | null;
    /** Originpoint */
    originPoint: string | null;
    /**
     * Perioddate
     * @format date
     */
    periodDate: string;
    /** Pprraterub */
    pprRateRub: number;
    /** Pprrateusd */
    pprRateUsd: number;
    /** Prevnetraterub */
    prevNetRateRub: number | null;
    /** Prevnetrateusd */
    prevNetRateUsd: number | null;
    /**
     * The nature of a price in the «Логистика» main table.
     *
     * ``INDICATIVE`` is an indicative (estimate) price; ``PRICE`` is the firm
     * price the shipment is actually billed at. The DB type is
     * ``logistics_price_kind`` (migration 0032), so a future main-table column
     * can reference the same enum the API exposes.
     */
    priceKind: LogisticsPriceKind;
    /** Product */
    product: string | null;
    /**
     * Transporttypeid
     * @format uuid
     */
    transportTypeId: string;
    /** Transporttypename */
    transportTypeName: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
    /** Vehicletypeid */
    vehicleTypeId: string | null;
    /** Vehicletypename */
    vehicleTypeName: string;
}

/**
 * LogisticsRateViewRead
 * The period view: its rows plus the FX rate used for the dollar columns.
 */
export interface LogisticsRateViewRead {
    /**
     * Perioddate
     * @format date
     */
    periodDate: string;
    /** Rows */
    rows: LogisticsRateRowRead[];
    /** Usdrate */
    usdRate: number;
}

/**
 * LogisticsTransportRowRead
 * One transport type × vehicle type pair.
 *
 * The dictionary is rendered as a grouped table: the transport type cell is
 * merged over its vehicle types. A type with no mapped vehicles still gets a
 * single row with an empty ``vehicleType`` — the type itself must not vanish.
 */
export interface LogisticsTransportRowRead {
    /** Id */
    id: string;
    /** Transporttype */
    transportType: string;
    /** Transporttypeid */
    transportTypeId: string;
    /** Vehicletype */
    vehicleType: string;
    /** Vehicletypeid */
    vehicleTypeId: string | null;
}

/**
 * LogisticsUserCreateRequest
 * Add a project member with a role (blocked by default) and its assets.
 */
export interface LogisticsUserCreateRequest {
    /** Assetids */
    assetIds?: string[];
    /**
     * Email
     * @format email
     */
    email: string;
    /** Fullname */
    fullname: string;
    /**
     * Fixed set of project roles. Hardcoded; the DB stores an enum.
     * @default "blocked"
     */
    role?: ProjectRole;
}

/**
 * LogisticsUserRowRead
 * One member of the «Логистика» project with its role and asset grants.
 */
export interface LogisticsUserRowRead {
    /** Assetids */
    assetIds: string[];
    /** Assetnames */
    assetNames: string[];
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Email */
    email: string;
    /** Fullname */
    fullname: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Isactive */
    isActive: boolean;
    /** Lastauthenticatedat */
    lastAuthenticatedAt: string | null;
    /** Fixed set of project roles. Hardcoded; the DB stores an enum. */
    role: ProjectRole;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * LogisticsUserUpdateRequest
 * Edit the mutable fields of a membership; email is immutable.
 */
export interface LogisticsUserUpdateRequest {
    /** Assetids */
    assetIds?: string[] | null;
    /** Assetnames */
    assetNames?: string[] | null;
    /** Fullname */
    fullname?: string | null;
    role?: ProjectRole | null;
}

/**
 * MovementMetaRead
 * «Движение» form status: completeness, finality, version archive.
 */
export interface MovementMetaRead {
    /** Iscomplete */
    isComplete: boolean;
    /** Isfinal */
    isFinal: boolean;
    /**
     * Reportmonth
     * @format date
     */
    reportMonth: string;
    /** Snapshots */
    snapshots: MovementSnapshotRead[];
}

/**
 * MovementRowRead
 * «Движение» form row for an asset-raw-material link in the report month.
 */
export interface MovementRowRead {
    /**
     * Assetid
     * @format uuid
     */
    assetId: string;
    /** Assetname */
    assetName: string;
    /** Balanceendtons */
    balanceEndTons: number | null;
    /** Balanceintransittons */
    balanceInTransitTons: number | null;
    /** Balanceonsitetons */
    balanceOnSiteTons: number | null;
    /** Balancestarttons */
    balanceStartTons: number | null;
    /** Businessplantons */
    businessPlanTons: number | null;
    /** Consumptionfacttons */
    consumptionFactTons: number | null;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Deliverydeviationtons */
    deliveryDeviationTons: number | null;
    /** Deliveryfacttons */
    deliveryFactTons: number | null;
    /** Deliveryplantons */
    deliveryPlanTons: number | null;
    /** Demanddeviationtons */
    demandDeviationTons: number | null;
    /** Demandplantons */
    demandPlanTons: number | null;
    /** Deviationreason */
    deviationReason: string | null;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /**
     * Key
     * @format uuid
     */
    key: string;
    /**
     * Priceid
     * @format uuid
     */
    priceId: string;
    /**
     * Rawmaterialid
     * @format uuid
     */
    rawMaterialId: string;
    /** Rawmaterialname */
    rawMaterialName: string;
    /** Storagestandardtons */
    storageStandardTons: number | null;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * MovementSnapshotRead
 * Fixed «Движение» version for the report month.
 */
export interface MovementSnapshotRead {
    /**
     * Fixedat
     * @format date-time
     */
    fixedAt: string;
    /** Fixedbyfullname */
    fixedByFullname: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Isfinal */
    isFinal: boolean;
    /**
     * Reportmonth
     * @format date
     */
    reportMonth: string;
}

/**
 * MovementUpdateRequest
 * Batch of «Движение» cell edits for the report month.
 */
export interface MovementUpdateRequest {
    /**
     * Reportmonth
     * @format date
     */
    reportMonth: string;
    /**
     * Updates
     * @maxItems 10000
     * @minItems 1
     */
    updates: SrcRawMaterialsSchemasCellUpdate[];
}

/**
 * PriceRowRead
 * «Цены» form row for an asset-raw-material link.
 */
export interface PriceRowRead {
    /**
     * Assetid
     * @format uuid
     */
    assetId: string;
    /** Assetname */
    assetName: string;
    /** Businessplanquantitytons */
    businessPlanQuantityTons: number | null;
    /** Businessplanrubperton */
    businessPlanRubPerTon: number | null;
    /** Businessplanusdperton */
    businessPlanUsdPerTon: number | null;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Deliverybasisname */
    deliveryBasisName: string | null;
    /** Deliverycostrub */
    deliveryCostRub: number | null;
    /** Fact2025 */
    fact2025: number | null;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /**
     * Key
     * @format uuid
     */
    key: string;
    /**
     * Rawmaterialid
     * @format uuid
     */
    rawMaterialId: string;
    /** Rawmaterialname */
    rawMaterialName: string;
    /** Storagestandardtons */
    storageStandardTons: number | null;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * PriceUpdateRequest
 * Batch of «Цены» cell edits.
 */
export interface PriceUpdateRequest {
    /**
     * Updates
     * @maxItems 10000
     * @minItems 1
     */
    updates: SrcRawMaterialsSchemasCellUpdate[];
}

/**
 * ProjectAccessCheckRead
 * Result of a self-requested project membership (see request_project_access).
 */
export interface ProjectAccessCheckRead {
    /** Hasaccess */
    hasAccess: boolean;
}

/** ProjectAccessRead */
export interface ProjectAccessRead {
    /** Assets */
    assets: AssetAccessRead[];
    /** Key */
    key: string;
    /** Name */
    name: string;
    /** Roles */
    roles: string[];
}

/**
 * RawMaterialCreateRequest
 * Raw-material item creation with asset links.
 */
export interface RawMaterialCreateRequest {
    /**
     * Assetids
     * @default []
     */
    assetIds?: string[];
    /**
     * Name
     * @minLength 1
     * @maxLength 255
     */
    name: string;
}

/**
 * RawMaterialRowRead
 * «Сырьевые позиции» dictionary row.
 */
export interface RawMaterialRowRead {
    /** Assetnames */
    assetNames: string[];
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RawMaterialUpdateRequest
 * Raw-material item edit: name and/or assets.
 */
export interface RawMaterialUpdateRequest {
    /** Assetids */
    assetIds?: string[] | null;
    /** Name */
    name?: string | null;
}

/**
 * RawMaterialsMetaRead
 * Form dictionaries: available assets, movement periods, delivery bases.
 */
export interface RawMaterialsMetaRead {
    /** Assets */
    assets: AssetOptionRead[];
    /** Deliverybases */
    deliveryBases: string[];
    /** Movementperiods */
    movementPeriods: string[];
}

/**
 * RenovationApproveRead
 * Approve result: the archived plan and the new working copy.
 */
export interface RenovationApproveRead {
    /** A renovation plan version (working, pending approval, or archived). */
    archived: RenovationPlanVersionRead;
    /** A renovation plan version (working, pending approval, or archived). */
    working: RenovationPlanVersionRead;
}

/**
 * RenovationAssetCreateRequest
 * A new renovation asset (name and the official repair code).
 */
export interface RenovationAssetCreateRequest {
    /**
     * Name
     * @minLength 1
     * @maxLength 255
     */
    name: string;
    /**
     * Repaircode
     * @minLength 1
     * @maxLength 64
     */
    repairCode: string;
}

/**
 * RenovationAssetRead
 * A renovation asset (legal entity) with its repair code.
 */
export interface RenovationAssetRead {
    /** Code */
    code: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /** Repaircode */
    repairCode: string;
}

/**
 * RenovationAssetRowRead
 * A dictionary grid row of a renovation asset (legal entity).
 */
export interface RenovationAssetRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Name */
    name: string;
    /** Repaircode */
    repairCode: string;
    /**
     * Repaircount
     * @default 0
     */
    repairCount?: number;
    /**
     * Totalcost
     * @default 0
     */
    totalCost?: number;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RenovationAssetUpdateRequest
 * Editable fields of a renovation asset (at least one must be set).
 */
export interface RenovationAssetUpdateRequest {
    /** Name */
    name?: string | null;
    /** Repaircode */
    repairCode?: string | null;
}

/**
 * RenovationCalculatedCellRead
 * A recalculated cell (formula field or the renovation ID).
 */
export interface RenovationCalculatedCellRead {
    /** Field */
    field: string;
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /** Value */
    value?: string | number | boolean | null;
}

/**
 * RenovationCatalogOptionCreateRequest
 * A new option of a renovation reference catalog.
 */
export interface RenovationCatalogOptionCreateRequest {
    /**
     * Label
     * @minLength 1
     * @maxLength 255
     */
    label: string;
}

/**
 * RenovationCatalogOptionRead
 * A selectable option of a renovation reference catalog.
 */
export interface RenovationCatalogOptionRead {
    /** Label */
    label: string;
    /** Valueid */
    valueId: string;
}

/**
 * RenovationCatalogOptionRowRead
 * A dictionary grid row of a renovation reference catalog option.
 */
export interface RenovationCatalogOptionRowRead {
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Label */
    label: string;
    /**
     * Repaircount
     * @default 0
     */
    repairCount?: number;
    /**
     * Totalcost
     * @default 0
     */
    totalCost?: number;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
    /** Valueid */
    valueId: string;
}

/**
 * RenovationCatalogOptionUpdateRequest
 * Editable fields of a renovation catalog option (at least one must be set).
 */
export interface RenovationCatalogOptionUpdateRequest {
    /** Label */
    label?: string | null;
}

/**
 * RenovationCatalogRead
 * A renovation reference catalog (criticality, work type, ...).
 */
export interface RenovationCatalogRead {
    /** Id */
    id: string;
    /** Label */
    label: string;
    /** Options */
    options: RenovationCatalogOptionRead[];
}

/**
 * RenovationCatalogReplaceRequest
 * Catalog value replacement: `null` deletes the value instead.
 */
export interface RenovationCatalogReplaceRequest {
    /** Targetid */
    targetId?: string | null;
}

/**
 * RenovationColumnFilter
 * One active header filter on a plan column.
 *
 * `values` are the display strings the user kept; `includeNulls` keeps the
 * «Пустые» bucket. Columns combine with AND; a column with every value kept is
 * simply omitted by the client (no constraint).
 */
export interface RenovationColumnFilter {
    /**
     * Column
     * @minLength 1
     */
    column: string;
    /** From */
    from?: string | null;
    /**
     * Includenulls
     * @default false
     */
    includeNulls?: boolean;
    /** To */
    to?: string | null;
    /** Valuemode */
    valueMode?: "exact" | null;
    /**
     * Values
     * @default []
     */
    values?: string[];
}

/**
 * RenovationDictionaryBatchRequest
 * Batch of renovation dictionary edits (one request, one undo action).
 */
export interface RenovationDictionaryBatchRequest {
    /** Catalog */
    catalog?: string | null;
    /** Section */
    section: "assets" | "users" | "catalog";
    /**
     * Updates
     * @maxItems 10000
     * @minItems 1
     */
    updates: DictionaryCellUpdate[];
}

/**
 * RenovationDiffCellRead
 * A cell of an archived version that differs from the previous version.
 */
export interface RenovationDiffCellRead {
    /** Field */
    field: string;
    /** Newvalue */
    newValue?: string | number | boolean | null;
    /** Oldvalue */
    oldValue?: string | number | boolean | null;
}

/**
 * RenovationDiffRead
 * Cell diff of an archived version vs the previous archived version.
 */
export interface RenovationDiffRead {
    /** Comparedtoplanid */
    comparedToPlanId?: string | null;
    /**
     * Deletedrows
     * @default []
     */
    deletedRows?: RenovationPlanRowRead[];
    /**
     * Planid
     * @format uuid
     */
    planId: string;
    /**
     * Rows
     * @default []
     */
    rows?: RenovationDiffRowRead[];
}

/**
 * RenovationDiffRowRead
 * A diff row: new rows carry every filled cell, changed rows only the changed ones.
 */
export interface RenovationDiffRowRead {
    /** Assetname */
    assetName: string;
    /**
     * Cells
     * @default []
     */
    cells?: RenovationDiffCellRead[];
    /** Isnew */
    isNew: boolean;
    /** Position */
    position: number;
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
}

/**
 * RenovationExportJobRead
 * A background plan-export job: status polling and the ready file name.
 */
export interface RenovationExportJobRead {
    /**
     * Attempt
     * @default 0
     */
    attempt?: number;
    /** Error */
    error?: string | null;
    /** Filename */
    fileName: string;
    /** Heartbeatat */
    heartbeatAt?: string | null;
    /** Id */
    id: string;
    /** Phase */
    phase?: string | null;
    /** Progress */
    progress: number;
    /** Startedat */
    startedAt?: string | null;
    /** Status */
    status: string;
    /** Total */
    total: number;
}

/**
 * RenovationExportViewRequest
 * Excel export of a renovation dictionary grid («вся» / «видимая»).
 */
export interface RenovationExportViewRequest {
    /**
     * Columns
     * @maxItems 64
     * @minItems 1
     */
    columns: ExportColumnRequest[];
    /**
     * Filename
     * @minLength 1
     * @maxLength 255
     */
    fileName: string;
    /**
     * Headerpinned
     * @default true
     */
    headerPinned?: boolean;
    /** Rowids */
    rowIds?: string[] | null;
    /** Section */
    section: string;
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
}

/**
 * RenovationFacetValueRead
 * One distinct display value of a plan column (a filter option).
 */
export interface RenovationFacetValueRead {
    /** Count */
    count: number;
    /** Value */
    value?: string | null;
}

/**
 * RenovationFacetsRead
 * Distinct values of one plan column, sorted, for the header filter.
 */
export interface RenovationFacetsRead {
    /** Column */
    column: string;
    /** Hasnulls */
    hasNulls: boolean;
    /**
     * Nullcount
     * @default 0
     */
    nullCount?: number;
    /** Presets */
    presets?: RenovationFacetValueRead[];
    /**
     * Rowcount
     * @default 0
     */
    rowCount?: number;
    /** Total */
    total: number;
    /** Totalwithoutsearch */
    totalWithoutSearch?: number | null;
    /** Values */
    values: RenovationFacetValueRead[];
}

/**
 * RenovationFieldRead
 * Form field description: the grid renders cells from these.
 */
export interface RenovationFieldRead {
    /**
     * Allownull
     * @default true
     */
    allowNull?: boolean;
    /** Catalog */
    catalog?: string | null;
    /** Celltype */
    cellType: string;
    /** Dbname */
    dbName: string;
    /** Decimals */
    decimals?: number | null;
    /** Iscalculated */
    isCalculated: boolean;
    /** Isreadonly */
    isReadOnly: boolean;
    /** Label */
    label: string;
    /** Ordinal */
    ordinal: number;
    /** Position */
    position: number;
    /** Responsibilityblock */
    responsibilityBlock?: string | null;
    /**
     * Rules
     * @default []
     */
    rules?: Record<string, any>[];
}

/**
 * RenovationHeaderCellRead
 * A merged header cell of the legacy form (11 levels, Excel ranges).
 */
export interface RenovationHeaderCellRead {
    /** Colspan */
    colSpan: number;
    /** Excelrange */
    excelRange: string;
    /** Id */
    id: string;
    /** Level */
    level: number;
    /** Rowspan */
    rowSpan: number;
    /** Startposition */
    startPosition: number;
    /** Text */
    text: string;
}

/**
 * RenovationHeaderRowRead
 * One header level of the legacy form (a row of merged cells).
 */
export interface RenovationHeaderRowRead {
    /** Cells */
    cells: RenovationHeaderCellRead[];
    /** Level */
    level: number;
}

/**
 * RenovationIdRead
 * The recomputed renovation ID of a row after edits.
 */
export interface RenovationIdRead {
    /**
     * Changed
     * @default false
     */
    changed?: boolean;
    /**
     * Missingcomponents
     * @default []
     */
    missingComponents?: string[];
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /** Value */
    value: string;
}

/**
 * RenovationImportJobRead
 * Durable Excel import status and bounded result.
 */
export interface RenovationImportJobRead {
    /**
     * Attempt
     * @default 0
     */
    attempt?: number;
    /** Error */
    error?: string | null;
    /** File Name */
    file_name: string;
    /** Heartbeatat */
    heartbeatAt?: string | null;
    /** Id */
    id: string;
    /**
     * New Rows
     * @default 0
     */
    new_rows?: number;
    /** Phase */
    phase?: string | null;
    /**
     * Progress
     * @default 0
     */
    progress?: number;
    /** Startedat */
    startedAt?: string | null;
    /** Status */
    status: "pending" | "running" | "done" | "failed";
    /**
     * Total
     * @default 0
     */
    total?: number;
    /**
     * Updated Rows
     * @default 0
     */
    updated_rows?: number;
    /**
     * User Name
     * @default ""
     */
    user_name?: string;
}

/**
 * RenovationMetaRead
 * Form dictionaries: assets, reference catalogs, field descriptions.
 */
export interface RenovationMetaRead {
    /** Assets */
    assets: RenovationAssetRead[];
    /** Catalogs */
    catalogs: RenovationCatalogRead[];
    /** Fields */
    fields: RenovationFieldRead[];
    /**
     * Headercells
     * @default []
     */
    headerCells?: RenovationHeaderCellRead[];
    /**
     * Headerrows
     * @default []
     */
    headerRows?: RenovationHeaderRowRead[];
    /** Pendingplanid */
    pendingPlanId?: string | null;
    /** Userblock */
    userBlock?: string | null;
    /**
     * Userroles
     * @default []
     */
    userRoles?: string[];
    /** Workingplanid */
    workingPlanId?: string | null;
}

/**
 * RenovationPlanExportRequest
 * Excel export of a plan version: all rows or only the visible ones.
 *
 * `rowIds` exports an exact set; when it is null, `asset` narrows the rows to
 * one asset, `block` narrows the exported columns to one responsibility block
 * and `filters` are the grid's active header filters (the same shape
 * `POST /plan/rows/query` takes) and `sorting` carries ordered server-side sort
 * levels — together the server-side equivalent of the grid's «видимая» view.
 *
 * `rowIds` has NO count cap (owner decision 2026-11-25: export everything that
 * exists). Duplicates are still refused, and the 4 GiB ZIP ceiling stays in
 * force — both are shared export invariants, not row-count limits.
 */
export interface RenovationPlanExportRequest {
    /** Asset */
    asset?: string | null;
    block?: RBlock | null;
    /** Filters */
    filters?: RenovationColumnFilter[] | null;
    /** Planid */
    planId?: string | null;
    /** Rowids */
    rowIds?: string[] | null;
    /** Sorting */
    sorting?: RenovationSortColumn[] | null;
    /**
     * Valuesonly
     * @default true
     */
    valuesOnly?: boolean;
}

/** RenovationPlanImportCellIssue */
export interface RenovationPlanImportCellIssue {
    /** Column Label */
    column_label?: string | null;
    /** Location */
    location: string;
    /** Value */
    value: string;
}

/** RenovationPlanImportResult */
export interface RenovationPlanImportResult {
    /**
     * All Rows Skipped
     * @default false
     */
    all_rows_skipped?: boolean;
    /** Assets */
    assets?: string[];
    /** Columns */
    columns?: string[];
    /** Duplicate Codes */
    duplicate_codes?: string[];
    /** Formula Reference Errors */
    formula_reference_errors?: RenovationPlanImportCellIssue[];
    /** Invalid Calculated Codes */
    invalid_calculated_codes?: string[];
    /** Invalid Data Types */
    invalid_data_types?: RenovationPlanImportCellIssue[];
    /** Invalid Values */
    invalid_values?: RenovationPlanImportCellIssue[];
    /** Issue Counts */
    issue_counts?: Record<string, number>;
    /** Missing Dictionary Values */
    missing_dictionary_values?: RenovationPlanImportCellIssue[];
    /** Missing Required Columns */
    missing_required_columns?: string[];
    /** Missing Required Values */
    missing_required_values?: RenovationPlanImportCellIssue[];
    /**
     * New Rows
     * @default 0
     */
    new_rows?: number;
    /**
     * Rows To Update
     * @default 0
     */
    rows_to_update?: number;
    /** Skipped Rows */
    skipped_rows?: number[];
    /** Status */
    status: "invalid_format" | "has_errors" | "ready";
    /**
     * Truncated
     * @default false
     */
    truncated?: boolean;
    /** Unknown Assets */
    unknown_assets?: RenovationPlanImportCellIssue[];
}

/**
 * RenovationPlanQuery
 * Column constraints and server sort levels travel in JSON, never in the URL.
 */
export interface RenovationPlanQuery {
    /** Anchor Position */
    anchor_position?: number | null;
    /**
     * Filters
     * @maxItems 256
     */
    filters?: RenovationColumnFilter[];
    /** Position From */
    position_from?: number | null;
    /** Position To */
    position_to?: number | null;
    /** Search Kind */
    search_kind?: "text" | "range" | "datetime" | null;
    /** Search Max */
    search_max?: number | null;
    /** Search Min */
    search_min?: number | null;
    /** Search Query */
    search_query?: string | null;
    /**
     * Sorting
     * @maxItems 256
     */
    sorting?: RenovationSortColumn[];
    /** Tz */
    tz?: string | null;
}

/**
 * RenovationPlanRead
 * A renovation plan version with its rows.
 */
export interface RenovationPlanRead {
    /** Baselineboundary */
    baselineBoundary?: number | null;
    /** Baselinegeneration */
    baselineGeneration: number;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Rows */
    rows: RenovationPlanRowRead[];
    /** State */
    state: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RenovationPlanRowRead
 * A plan row: flat typed values plus the computed renovation ID.
 */
export interface RenovationPlanRowRead {
    /** Assetcode */
    assetCode?: string | null;
    /** Assetid */
    assetId?: string | null;
    /** Assetname */
    assetName?: string | null;
    /** Assetrepaircode */
    assetRepairCode?: string | null;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /**
     * Invalidfields
     * @default []
     */
    invalidFields?: string[];
    /**
     * Missingcomponents
     * @default []
     */
    missingComponents?: string[];
    /** Position */
    position: number;
    /** Renovationid */
    renovationId?: string | null;
    /** Revision */
    revision: number;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
    /** Values */
    values: Record<string, string | number | boolean | null>;
}

/**
 * RenovationPlanRowsRead
 * A window of renovation plan rows plus the total row count (lazy loading).
 */
export interface RenovationPlanRowsRead {
    /** Anchoroffset */
    anchorOffset?: number | null;
    /** Baselineboundary */
    baselineBoundary?: number | null;
    /** Baselinegeneration */
    baselineGeneration: number;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Rows */
    rows: RenovationPlanRowRead[];
    /** State */
    state: string;
    /** Total */
    total: number;
    /** Totalall */
    totalAll?: number | null;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RenovationPlanSummaryRead
 * Numeric totals of the filtered plan (the pinned summary row).
 */
export interface RenovationPlanSummaryRead {
    /** Values */
    values: Record<string, number>;
}

/**
 * RenovationPlanVersionRead
 * A renovation plan version (working, pending approval, or archived).
 */
export interface RenovationPlanVersionRead {
    /** Baselinegeneration */
    baselineGeneration: number;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Createdby */
    createdBy: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Rowcount */
    rowCount: number;
    /** State */
    state: string;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RenovationRowAssignRequest
 * Point an asset-less draft row at an asset.
 */
export interface RenovationRowAssignRequest {
    /**
     * Assetid
     * @format uuid
     */
    assetId: string;
}

/**
 * RenovationRowCreateRequest
 * Append a plan row, optionally pre-assigning an asset.
 */
export interface RenovationRowCreateRequest {
    /** Assetid */
    assetId?: string | null;
}

/**
 * RenovationSortColumn
 * One server-side plan sort level, in priority order.
 */
export interface RenovationSortColumn {
    /**
     * Column
     * @minLength 1
     * @maxLength 64
     */
    column: string;
    /** Order */
    order: "asc" | "desc";
}

/**
 * RenovationUpdateRead
 * PATCH result: applied edits plus recalculated formula cells and IDs.
 */
export interface RenovationUpdateRead {
    /** Applied */
    applied: number;
    /**
     * Calculated
     * @default []
     */
    calculated?: RenovationCalculatedCellRead[];
    /**
     * Renovationids
     * @default []
     */
    renovationIds?: RenovationIdRead[];
}

/**
 * RenovationUpdateRequest
 * Batch of renovation plan cell edits (working plan only).
 */
export interface RenovationUpdateRequest {
    /**
     * Updates
     * @maxItems 500
     * @minItems 1
     */
    updates: SrcRenovationSchemasCellUpdate[];
}

/**
 * RenovationUserCreateRequest
 * A new renovation project user (existing accounts are re-used).
 */
export interface RenovationUserCreateRequest {
    /**
     * Assetnames
     * @default []
     */
    assetNames?: string[];
    block?: RBlock | null;
    /**
     * Email
     * @minLength 3
     * @maxLength 255
     */
    email: string;
    /**
     * Fullname
     * @minLength 1
     * @maxLength 255
     */
    fullname: string;
    /**
     * Fixed set of renovation project roles. Hardcoded; the DB stores an enum.
     * @default "asset_worker"
     */
    role?: RProjectRole;
}

/**
 * RenovationUserRowRead
 * A dictionary grid row of a renovation project user.
 */
export interface RenovationUserRowRead {
    /**
     * Assetnames
     * @default []
     */
    assetNames?: string[];
    block?: RBlock | null;
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Email */
    email: string;
    /** Fullname */
    fullname: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Isactive */
    isActive: boolean;
    /** Lastauthenticatedat */
    lastAuthenticatedAt?: string | null;
    /** Fixed set of renovation project roles. Hardcoded; the DB stores an enum. */
    role: RProjectRole;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * RenovationUserUpdateRequest
 * Editable fields of a renovation project user.
 */
export interface RenovationUserUpdateRequest {
    /** Assetnames */
    assetNames?: string[] | null;
    block?: RBlock | null;
    /** Fullname */
    fullname?: string | null;
    /** Isactive */
    isActive?: boolean | null;
    role?: RProjectRole | null;
}

/**
 * ResetPasswordRequest
 * new_password=None or empty — reset the password (hash → NULL).
 */
export interface ResetPasswordRequest {
    /** New Password */
    new_password?: string | null;
}

/**
 * SnapshotMovementRowRead
 * Row of a fixed «Движение» version (value copy at fixing time).
 */
export interface SnapshotMovementRowRead {
    /** Assetname */
    assetName: string;
    /** Balanceendtons */
    balanceEndTons: number | null;
    /** Balanceintransittons */
    balanceInTransitTons: number | null;
    /** Balanceonsitetons */
    balanceOnSiteTons: number | null;
    /** Balancestarttons */
    balanceStartTons: number | null;
    /** Businessplantons */
    businessPlanTons: number | null;
    /** Consumptionfacttons */
    consumptionFactTons: number | null;
    /** Deliverydeviationtons */
    deliveryDeviationTons: number | null;
    /** Deliveryfacttons */
    deliveryFactTons: number | null;
    /** Deliveryplantons */
    deliveryPlanTons: number | null;
    /** Demanddeviationtons */
    demandDeviationTons: number | null;
    /** Demandplantons */
    demandPlanTons: number | null;
    /** Deviationreason */
    deviationReason: string | null;
    /** Rawmaterialname */
    rawMaterialName: string;
    /**
     * Sourceassetrawmaterialid
     * @format uuid
     */
    sourceAssetRawMaterialId: string;
    /** Storagestandardtons */
    storageStandardTons: number | null;
}

/**
 * SnapshotViewRead
 * View version: the snapshot, the previous snapshot for comparison, and rows.
 */
export interface SnapshotViewRead {
    /** Comparisonsnapshotid */
    comparisonSnapshotId: string | null;
    /** Rows */
    rows: SnapshotMovementRowRead[];
    /** Fixed «Движение» version for the report month. */
    snapshot: MovementSnapshotRead;
}

/** UndoConflictRead */
export interface UndoConflictRead {
    /** Actionid */
    actionId: number;
    /** Field */
    field: string;
    /** Reason */
    reason: "conflict" | "locked";
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /**
     * User
     * @default ""
     */
    user?: string;
}

/**
 * UndoRequest
 * Undo/redo body: the form (section + month) and the action the user chose to skip.
 */
export interface UndoRequest {
    /** Reportmonth */
    reportMonth?: string | null;
    /** Section */
    section?: string | null;
    /** Skipactionid */
    skipActionId?: number | null;
}

/** UndoResponse */
export interface UndoResponse {
    /** Applied */
    applied: boolean;
    /**
     * Conflicts
     * @default []
     */
    conflicts?: UndoConflictRead[];
    /** Field */
    field?: string | null;
    /** Reason */
    reason?: "applied" | "empty" | "conflict" | "locked" | null;
    /** Reportmonth */
    reportMonth?: string | null;
    /** Revision */
    revision?: number | null;
    /** Rowid */
    rowId?: string | null;
    /** Section */
    section?: string | null;
    /** Value */
    value?: null;
}

/**
 * UndoStateRead
 * Undo/redo availability from the user's action stack.
 */
export interface UndoStateRead {
    /** Canredo */
    canRedo: boolean;
    /** Canundo */
    canUndo: boolean;
}

/**
 * UpdateProfileRequest
 * Profile update.
 *
 * theme — system/light/dark; password — a new password (only meaningful in
 * the local auth mode, ignored when Keycloak is enabled).
 */
export interface UpdateProfileRequest {
    /** Password */
    password?: string | null;
    theme?: Theme | null;
}

/**
 * UserCreateRequest
 * «Сырьё» project user creation (local account).
 */
export interface UserCreateRequest {
    /**
     * Assetnames
     * @default []
     */
    assetNames?: string[];
    /**
     * Email
     * @format email
     * @minLength 3
     * @maxLength 255
     */
    email: string;
    /**
     * Fullname
     * @minLength 1
     * @maxLength 255
     */
    fullname: string;
    /**
     * Isadmin
     * @default false
     */
    isAdmin?: boolean;
    role?: ProjectRole | null;
}

/**
 * UserRowRead
 * «Пользователи» dictionary row (admin section).
 */
export interface UserRowRead {
    /** Assetnames */
    assetNames: string[];
    /**
     * Createdat
     * @format date-time
     */
    createdAt: string;
    /** Email */
    email: string | null;
    /** Fullname */
    fullname: string;
    /**
     * Id
     * @format uuid
     */
    id: string;
    /** Isactive */
    isActive: boolean;
    /** Isadmin */
    isAdmin: boolean;
    /** Lastauthenticatedat */
    lastAuthenticatedAt: string | null;
    /** Fixed set of project roles. Hardcoded; the DB stores an enum. */
    role: ProjectRole;
    /**
     * Updatedat
     * @format date-time
     */
    updatedAt: string;
    /** Updatedby */
    updatedBy: string;
}

/**
 * UserUpdateRequest
 * «Сырьё» project user edit.
 */
export interface UserUpdateRequest {
    /** Assetnames */
    assetNames?: string[] | null;
    /** Email */
    email?: string | null;
    /** Fullname */
    fullname?: string | null;
    /** Isactive */
    isActive?: boolean | null;
    /** Isadmin */
    isAdmin?: boolean | null;
    /**
     * Resetpassword
     * @default false
     */
    resetPassword?: boolean;
    role?: ProjectRole | null;
}

/** ValidationError */
export interface ValidationError {
    /** Context */
    ctx?: object;
    /** Input */
    input?: any;
    /** Location */
    loc: (string | number)[];
    /** Message */
    msg: string;
    /** Error Type */
    type: string;
}

/**
 * CellUpdate
 * Single form/dictionary cell edit (rowId — the row id).
 */
export interface SrcRawMaterialsSchemasCellUpdate {
    /**
     * Field
     * @minLength 1
     * @maxLength 64
     */
    field: string;
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /** Value */
    value: string | number | boolean | string[] | null;
}

/**
 * CellUpdate
 * Single form cell edit (rowId — the row id, field — the db name).
 */
export interface SrcRenovationSchemasCellUpdate {
    /**
     * Field
     * @minLength 1
     * @maxLength 64
     */
    field: string;
    /**
     * Rowid
     * @format uuid
     */
    rowId: string;
    /** Value */
    value?: string | number | boolean | null;
}
