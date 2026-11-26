import { describe, expect, test } from "bun:test";

import * as gridConfig from "../grid-config";
import * as descriptors from "../grid-config-descriptors";
import {
    ADMIN_SECTIONS,
    DESCRIPTORS,
    getEditableKeys,
    getVisibleDescriptors,
    KPPK_GROUPED_POSITION_DESCRIPTORS,
    KPPK_POSITION_DESCRIPTORS,
    MAIN_SECTIONS,
    RENOVATION_AGGREGATE_DESCRIPTORS,
    renovationCatalogDescriptors,
    SERVICE_DESCRIPTORS,
} from "../grid-config-descriptors";
import type { ColumnDescriptor, SectionId } from "../grid-config-types";

const STATIC_SECTIONS = [
    "materialMovement",
    "rawMaterialPrices",
    "rawMaterials",
    "assets",
    "deliveryBases",
    "users",
    "renovationAssets",
    "renovationUsers",
    "kppkPositions",
    "kppkRaws",
    "kppkOrgs",
    "kppkUsers",
    "logisticsCountries",
    "logisticsTransportTypes",
    "logisticsOrgs",
    "logisticsUsers",
    "logisticsRates",
] as const satisfies readonly SectionId[];

// These forms intentionally have no static table: their columns are pasted in
// as `columnDescriptors` by the section component.
const INJECTED_ONLY_SECTIONS = [
    "kppkRealizationPlan",
    "kppkRealizationFact",
    "kppkCost",
] as const satisfies readonly SectionId[];

const keys = (columns: ColumnDescriptor[]): string[] => columns.map(({ key }) => key);

describe("section coverage", () => {
    test("every static section id has a descriptor set, empty sets included", () => {
        expect(Object.keys(DESCRIPTORS).sort()).toEqual([...STATIC_SECTIONS].sort());
        for (const section of STATIC_SECTIONS) {
            expect(Array.isArray(DESCRIPTORS[section])).toBe(true);
        }
        expect(DESCRIPTORS.logisticsRates).toEqual([]);
        expect(keys(DESCRIPTORS.materialMovement ?? [])).toHaveLength(15);
    });

    test("dynamic forms have no static set and are supplied by the caller", () => {
        for (const section of INJECTED_ONLY_SECTIONS) {
            expect(DESCRIPTORS[section]).toBeUndefined();
            expect(getVisibleDescriptors(section, false)).toEqual([]);
        }
        expect(DESCRIPTORS["renovationCatalog:work_category" as SectionId]).toBeUndefined();
        expect(getVisibleDescriptors("renovationCatalog:work_category", false)).toEqual([]);
    });

    test("main/admin section sets stay intact", () => {
        expect([...MAIN_SECTIONS]).toEqual(["materialMovement", "rawMaterialPrices"]);
        expect(MAIN_SECTIONS.has("rawMaterialPrices")).toBe(true);
        expect(MAIN_SECTIONS.has("assets")).toBe(false);
        expect(ADMIN_SECTIONS).toEqual(["rawMaterials", "assets", "deliveryBases", "users"]);
    });
});

describe("getVisibleDescriptors", () => {
    test("an adminOnly column follows showAssetColumn without reordering the rest", () => {
        const withoutAsset = getVisibleDescriptors("materialMovement", false);
        const withAsset = getVisibleDescriptors("materialMovement", true);
        expect(keys(withoutAsset)).not.toContain("assetName");
        expect(keys(withAsset)[0]).toBe("rawMaterialName");
        expect(keys(withAsset)[1]).toBe("assetName");
        expect(keys(withAsset)).toHaveLength(keys(withoutAsset).length + 1);
        expect(keys(withAsset).filter((key) => key !== "assetName")).toEqual(keys(withoutAsset));
    });

    test("Keycloak removes only local password resets of the users dictionaries", () => {
        expect(
            keys(getVisibleDescriptors("users", true, false, { keycloakEnabled: true })),
        ).not.toContain("resetPassword");
        expect(keys(getVisibleDescriptors("users", true, false))).toContain("resetPassword");
        expect(
            keys(getVisibleDescriptors("kppkUsers", true, false, { keycloakEnabled: true })),
        ).not.toContain("resetPassword");
        expect(keys(getVisibleDescriptors("kppkUsers", true, false))).toContain("resetPassword");
        // Unaffected section: the flag must not leak into other dictionaries.
        expect(
            keys(
                getVisibleDescriptors("rawMaterialPrices", true, false, { keycloakEnabled: true }),
            ),
        ).toContain("storageStandardTons");
    });

    test("service columns are appended last and reuse the shared definitions", () => {
        const plain = getVisibleDescriptors("materialMovement", true, false);
        const withService = getVisibleDescriptors("materialMovement", true, true);
        expect(keys(plain)).not.toContain("createdAt");
        expect(keys(withService).slice(-3)).toEqual(["createdAt", "updatedAt", "updatedBy"]);
        expect(keys(withService).slice(0, -3)).toEqual(keys(plain));
        for (const service of SERVICE_DESCRIPTORS) {
            expect(withService).toContain(service);
        }
    });

    test("injected descriptors override the table completely", () => {
        const injected: ColumnDescriptor[] = [
            { key: "custom", label: "Своё", width: 100, kind: "text" },
        ];
        expect(getVisibleDescriptors("kppkCost", false, false, { descriptors: injected })).toEqual(
            injected,
        );
        expect(
            keys(
                getVisibleDescriptors("kppkCost", false, true, {
                    descriptors: injected,
                    keycloakEnabled: true,
                }),
            ),
        ).toEqual(["custom", "createdAt", "updatedAt", "updatedBy"]);
    });

    test("column order, labels, paths and widths survive the move", () => {
        const movement = DESCRIPTORS.materialMovement ?? [];
        const planTons = movement.find(({ key }) => key === "demandPlanTons");
        expect(planTons).toMatchObject({
            label: "План, т",
            width: 118,
            kind: "number",
            path: ["Потребность"],
        });
        expect(movement.find(({ key }) => key === "storageStandardTons")?.exportable).toBe(false);
        expect(movement.find(({ key }) => key === "deviationReason")?.fixed).toBeUndefined();
        const prices = DESCRIPTORS.rawMaterialPrices ?? [];
        expect(prices.find(({ key }) => key === "deliveryBasisName")).toMatchObject({
            label: "Базис поставки",
            width: 150,
            kind: "basis",
        });
        expect(prices.find(({ key }) => key === "businessPlanUsdPerTon")?.path).toEqual([
            "Бизнес-план 2026",
        ]);
    });
});

describe("getEditableKeys", () => {
    test("movement edits only authored columns", () => {
        const editable = getEditableKeys("materialMovement", true, false);
        expect(editable.has("demandPlanTons")).toBe(true);
        expect(editable.has("deviationReason")).toBe(true);
        expect(editable.has("rawMaterialName")).toBe(false);
        expect(editable.has("storageStandardTons")).toBe(false);
        expect(editable.has("demandDeviationTons")).toBe(false);
        expect(editable.has("createdAt")).toBe(false);
    });

    test("the delivery basis of «Цены» is admin-only", () => {
        expect(getEditableKeys("rawMaterialPrices", false, true).has("deliveryBasisName")).toBe(
            false,
        );
        expect(getEditableKeys("rawMaterialPrices", true, true).has("deliveryBasisName")).toBe(
            true,
        );
        expect(getEditableKeys("rawMaterialPrices", true, true).has("businessPlanUsdPerTon")).toBe(
            true,
        );
    });

    test("fixed and read-only dictionary columns stay out of the editable set", () => {
        const users = getEditableKeys("users", true, true);
        expect(users.has("email")).toBe(true);
        expect(users.has("resetPassword")).toBe(true);
        expect(users.has("lastAuthenticatedAt")).toBe(false);
        expect(getEditableKeys("renovationAssets", true, true).has("repairCount")).toBe(false);
        expect(getEditableKeys("renovationAssets", true, true).has("repairCode")).toBe(true);
        const logistics = getEditableKeys("logisticsUsers", true, true);
        expect(logistics.has("email")).toBe(false);
        expect(logistics.has("fullname")).toBe(true);
        expect(getEditableKeys("logisticsCountries", true, true).size).toBe(0);
        expect(getEditableKeys("kppkUsers", true, true).has("orgNames")).toBe(false);
    });
});

describe("KPPK grouped and realization sets", () => {
    test("one row per position differs from one row per product", () => {
        expect(keys(KPPK_POSITION_DESCRIPTORS)).toEqual([
            "productNum",
            "name",
            "orgName",
            "hasExcise",
        ]);
        expect(keys(KPPK_GROUPED_POSITION_DESCRIPTORS)).toEqual(["productNum", "name", "orgNames"]);
        expect(KPPK_GROUPED_POSITION_DESCRIPTORS[2]?.displayKey).toBe("orgNamesDisplay");
        expect(KPPK_POSITION_DESCRIPTORS[0]).toBe(DESCRIPTORS.kppkPositions?.[0]);
    });

    test("realization forms take their columns only from the injected slice", () => {
        const realization: ColumnDescriptor[] = [
            { key: "volume", label: "Объём", width: 120, kind: "number" },
            { key: "price", label: "Цена", width: 120, kind: "number" },
        ];
        for (const section of ["kppkRealizationPlan", "kppkRealizationFact"] as const) {
            expect(keys(getVisibleDescriptors(section, false))).toEqual([]);
            expect(
                keys(getVisibleDescriptors(section, false, false, { descriptors: realization })),
            ).toEqual(["volume", "price"]);
            expect([...getEditableKeys(section, true, false, false, realization)]).toEqual([
                "volume",
                "price",
            ]);
        }
    });
});

describe("renovation catalog injection", () => {
    test("aggregate descriptors are shared by the assets grid and every catalog", () => {
        expect(keys(RENOVATION_AGGREGATE_DESCRIPTORS)).toEqual(["repairCount", "totalCost"]);
        for (const aggregate of RENOVATION_AGGREGATE_DESCRIPTORS) {
            expect(aggregate).toMatchObject({ kind: "calculated", fixed: true });
        }
        const catalog = renovationCatalogDescriptors();
        expect(keys(catalog)).toEqual(["label", "repairCount", "totalCost"]);
        expect(catalog[1]).toBe(RENOVATION_AGGREGATE_DESCRIPTORS[0]);
        expect(catalog[2]).toBe(RENOVATION_AGGREGATE_DESCRIPTORS[1]);
        expect(DESCRIPTORS.renovationAssets?.slice(2)).toEqual(RENOVATION_AGGREGATE_DESCRIPTORS);
    });

    test("a dynamic catalog is selectable and editable only through injection", () => {
        const section: SectionId = "renovationCatalog:work_category";
        const catalog = renovationCatalogDescriptors();
        expect(getVisibleDescriptors(section, true, false)).toEqual([]);
        expect(keys(getVisibleDescriptors(section, true, false, { descriptors: catalog }))).toEqual(
            ["label", "repairCount", "totalCost"],
        );
        expect([...getEditableKeys(section, true, true, false, catalog)]).toEqual(["label"]);
    });
});

test("grid-config re-exports every moved name by identity and keeps its builders", () => {
    const config = gridConfig as unknown as Record<string, unknown>;
    const moved = descriptors as unknown as Record<string, unknown>;
    const names = [
        "MAIN_SECTIONS",
        "ADMIN_SECTIONS",
        "RENOVATION_AGGREGATE_DESCRIPTORS",
        "renovationCatalogDescriptors",
        "DESCRIPTORS",
        "KPPK_GROUPED_POSITION_DESCRIPTORS",
        "KPPK_POSITION_DESCRIPTORS",
        "SERVICE_DESCRIPTORS",
        "fixed",
        "text",
        "number",
        "calculated",
        "select",
        "optionSelect",
        "getVisibleDescriptors",
        "getEditableKeys",
    ] as const;
    for (const name of names) {
        expect(config[name]).toBe(moved[name]);
    }
    for (const builder of [
        "exportColumnsFromGrid",
        "buildGridColumns",
        "markFilterableColumns",
        "nextBooleanValue",
    ]) {
        expect(typeof config[builder]).toBe("function");
    }
});
