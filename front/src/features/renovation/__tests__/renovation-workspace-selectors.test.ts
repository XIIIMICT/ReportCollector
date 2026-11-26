import { describe, expect, test } from "bun:test";

import type {
    RenovationField,
    RenovationMeta,
    RenovationPlanVersion,
} from "@/shared/api/renovation";

import {
    buildArchiveItems,
    calculatedPlanFieldKeys,
    editablePlanFieldKeys,
    mergeablePlanFieldKeys,
    planAssetFilterSet,
    renovationAssetNames,
    renovationSections,
    sectionTabDescriptors,
} from "../renovation-workspace-selectors";

function field(dbName: string, overrides: Partial<RenovationField> = {}): RenovationField {
    return {
        dbName,
        label: dbName,
        cellType: "text",
        isCalculated: false,
        isReadOnly: false,
        position: 1,
        ordinal: 2,
        ...overrides,
    };
}

function meta(overrides: Partial<RenovationMeta> = {}): RenovationMeta {
    return {
        assets: [],
        catalogs: [],
        fields: [],
        headerCells: [],
        headerRows: [],
        workingPlanId: null,
        pendingPlanId: null,
        userBlock: null,
        userRoles: [],
        ...overrides,
    };
}

function version(
    id: string,
    state: RenovationPlanVersion["state"],
    createdAt: string,
): RenovationPlanVersion {
    return {
        id,
        state,
        baselineGeneration: 0,
        rowCount: 0,
        createdAt,
        createdBy: "",
        updatedAt: createdAt,
        updatedBy: "",
    };
}

describe("renovation workspace selectors", () => {
    describe("buildArchiveItems", () => {
        test("missing, null and empty version lists produce no items", () => {
            expect(buildArchiveItems(null, null)).toEqual([]);
            expect(buildArchiveItems([], null)).toEqual([]);
            expect(buildArchiveItems(null, [])).toEqual([]);
        });

        test("working versions are excluded; pending and archived keep their labels and stamps", () => {
            const versions = [
                version("working", "working", "2026-01-01T00:00:00Z"),
                version("pending", "pending_approval", "2026-01-02T03:04:05Z"),
                version("archived", "archived", "2026-01-03T03:04:05Z"),
            ];
            const items = buildArchiveItems(versions, null);
            expect(items.map((item) => item.id)).toEqual(["pending", "archived"]);
            expect(items.map((item) => item.name)).toEqual([
                "Версия на согласовании",
                "Утверждённая версия",
            ]);
            expect(items[0]?.description).toContain("2026");
            expect(items[1]?.description).toContain("2026");
            // The selector reads the list, never mutates it.
            expect(versions.map((entry) => entry.id)).toEqual(["working", "pending", "archived"]);
        });

        test("the approve response wins over the polled list, even when empty", () => {
            const polled = [version("pending", "pending_approval", "2026-01-02T03:04:05Z")];
            expect(buildArchiveItems(polled, [])).toEqual([]);
            const fresh = [version("archived", "archived", "2026-01-03T03:04:05Z")];
            expect(buildArchiveItems(polled, fresh).map((item) => item.id)).toEqual(["archived"]);
        });

        test("an unparsable stamp is preserved verbatim", () => {
            const items = buildArchiveItems(
                [version("pending", "pending_approval", "not-a-date")],
                null,
            );
            expect(items[0]?.description).toBe("not-a-date");
        });
    });

    describe("renovationSections and sectionTabDescriptors", () => {
        test("missing meta has no tabs; a non-admin sees only the plan", () => {
            expect(renovationSections(null, true)).toEqual([]);
            expect(sectionTabDescriptors([])).toEqual([]);
            expect(renovationSections(meta(), false)).toEqual([
                { id: "plan", label: "Пообъектный план" },
            ]);
            expect(sectionTabDescriptors(renovationSections(meta(), false))).toEqual([
                { id: "plan", label: "Пообъектный план", separatorBefore: false },
            ]);
        });

        test("an admin gets the dictionaries in canonical order, users last", () => {
            const sections = renovationSections(
                meta({
                    catalogs: [
                        { id: "catalog-1", label: "Единицы измерения", options: [] },
                        { id: "catalog-2", label: "Вид работ", options: [] },
                    ],
                }),
                true,
            );
            expect(sections).toEqual([
                { id: "plan", label: "Пообъектный план" },
                { id: "assets", label: "Предприятия" },
                { id: "catalog-1", label: "Единицы измерения" },
                { id: "catalog-2", label: "Вид работ" },
                { id: "users", label: "Пользователи" },
            ]);
            expect(sectionTabDescriptors(sections)).toEqual([
                { id: "plan", label: "Пообъектный план", separatorBefore: false },
                { id: "assets", label: "Предприятия", separatorBefore: true },
                { id: "catalog-1", label: "Единицы измерения", separatorBefore: false },
                { id: "catalog-2", label: "Вид работ", separatorBefore: false },
                { id: "users", label: "Пользователи", separatorBefore: true },
            ]);
        });
    });

    describe("plan field-key sets", () => {
        test("missing meta and approver produce empty editable keys", () => {
            expect(editablePlanFieldKeys(null, false, null).size).toBe(0);
            expect(editablePlanFieldKeys(meta({ fields: [field("qty")] }), true, null).size).toBe(
                0,
            );
            expect(calculatedPlanFieldKeys(null).size).toBe(0);
            expect(mergeablePlanFieldKeys(new Set(), new Set()).size).toBe(0);
        });

        test("an editable key set excludes calculated/read-only fields and other blocks", () => {
            const fields = [
                field("central"),
                field("tech_qty", { responsibilityBlock: "tech" }),
                field("mto_qty", { responsibilityBlock: "mto" }),
                field("formula", { isCalculated: true }),
                field("locked", { isReadOnly: true }),
            ];
            const editable = editablePlanFieldKeys(meta({ fields }), false, "tech");
            expect([...editable]).toEqual(["central", "tech_qty"]);
            expect([...editablePlanFieldKeys(meta({ fields }), false, null)]).toEqual([
                "central",
                "tech_qty",
                "mto_qty",
            ]);
            expect(fields.map((entry) => entry.dbName)).toEqual([
                "central",
                "tech_qty",
                "mto_qty",
                "formula",
                "locked",
            ]);
        });

        test("mergeable keys are the union of editable and calculated keys", () => {
            const fields = [field("qty"), field("total", { isCalculated: true })];
            const editable = editablePlanFieldKeys(meta({ fields }), false, null);
            const calculated = calculatedPlanFieldKeys(meta({ fields }));
            expect(calculated).toEqual(new Set(["total"]));
            expect(mergeablePlanFieldKeys(editable, calculated)).toEqual(new Set(["qty", "total"]));
            // The inputs are not mutated.
            expect(editable).toEqual(new Set(["qty"]));
            expect(calculated).toEqual(new Set(["total"]));
        });
    });

    describe("asset-name and filter-set derivation", () => {
        test("names follow meta asset order; missing meta gives none", () => {
            expect(renovationAssetNames(null)).toEqual([]);
            expect(
                renovationAssetNames(
                    meta({
                        assets: [
                            { id: "1", code: "A", name: "Первое" },
                            { id: "2", code: "B", name: "Второе" },
                        ],
                    }),
                ),
            ).toEqual(["Первое", "Второе"]);
        });

        test("an absent asset filter is null; a present one becomes a singleton set", () => {
            expect(planAssetFilterSet(null)).toBeNull();
            const set = planAssetFilterSet("first");
            expect(set).toEqual(new Set(["first"]));
            expect(set?.has("second")).toBe(false);
        });
    });
});
