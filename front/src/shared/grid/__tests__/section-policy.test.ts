import { describe, expect, test } from "bun:test";

import {
    declaredSectionIds,
    exportAvailableFor,
    isChunkedSection,
    readOnlyPolicy,
    sectionPolicy,
    undoAvailableFor,
    collaborationAvailableFor,
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
            "kppkUsers",
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

    test("users require admin for room and history; export is for everyone", () => {
        expect(collaborationAvailableFor("kppkUsers", false)).toBe(false);
        expect(collaborationAvailableFor("kppkUsers", true)).toBe(true);
        expect(undoAvailableFor("kppkUsers", false)).toBe(false);
        // The shared rule: export is available to every member, dictionaries
        // included — the member reads the same rows the export renders.
        expect(exportAvailableFor("kppkUsers", false)).toBe(true);
        expect(sectionPolicy("kppkUsers").rowOps).toBe("dialog");
        expect(sectionPolicy("kppkRealizationPlan").rowOps).toBe("none");
        expect(sectionPolicy("kppkRealizationFact").rowOps).toBe("none");
    });
});
