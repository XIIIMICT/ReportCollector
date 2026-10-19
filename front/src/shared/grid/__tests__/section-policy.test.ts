import { describe, expect, test } from "bun:test";

import {
    declaredSectionIds,
    exportAvailableFor,
    isChunkedSection,
    readOnlyPolicy,
    sectionPolicy,
    undoAvailableFor,
} from "@/shared/grid/section-policy";

describe("section policy", () => {
    test("declares every project section", () => {
        const ids = declaredSectionIds();
        for (const section of [
            "materialMovement",
            "rawMaterialPrices",
            "rawMaterials",
            "assets",
            "deliveryBases",
            "users",
            "renovationPlan",
            "renovationAssets",
            "renovationUsers",
            "kppkPositions",
            "kppkRaws",
            "kppkOrgs",
        ] as const) {
            expect(ids).toContain(section);
        }
    });

    test("dynamic renovation catalogs fall back to the dictionary policy", () => {
        expect(sectionPolicy("renovationCatalog:material").collab).toBe("admin");
    });

    test("only the chunked plan disables select-all", () => {
        expect(isChunkedSection("renovationPlan")).toBe(true);
        expect(isChunkedSection("kppkOrgs")).toBe(false);
        expect(isChunkedSection("materialMovement")).toBe(false);
    });

    test("members undo their own edits on forms, not on admin dictionaries", () => {
        expect(undoAvailableFor("materialMovement", false)).toBe(true);
        expect(undoAvailableFor("rawMaterials", false)).toBe(false);
        expect(undoAvailableFor("rawMaterials", true)).toBe(true);
        expect(undoAvailableFor("kppkRaws", false)).toBe(false);
    });

    test("every role exports every dictionary", () => {
        expect(exportAvailableFor("kppkOrgs", false)).toBe(true);
        expect(exportAvailableFor("assets", false)).toBe(true);
    });

    test("a non-editable variant has no collaboration and no history", () => {
        const archive = readOnlyPolicy(sectionPolicy("materialMovement"));
        expect(archive.collab).toBe("none");
        expect(archive.undo).toBe("none");
        expect(archive.export).toBe("member");
    });
});
