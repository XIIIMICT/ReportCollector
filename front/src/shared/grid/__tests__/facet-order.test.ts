import { describe, expect, it } from "bun:test";

import { facetOrderOf, fieldFacetOrder } from "@/shared/grid/grid-config";

describe("fieldFacetOrder", () => {
    it("follows the field's own cell type", () => {
        expect(fieldFacetOrder({ cellType: "text", isCalculated: false })).toBe("text");
        expect(fieldFacetOrder({ cellType: "number", isCalculated: false })).toBe("number");
        expect(fieldFacetOrder({ cellType: "date", isCalculated: false })).toBe("date");
        expect(fieldFacetOrder({ cellType: "reference", isCalculated: false })).toBe("text");
        expect(fieldFacetOrder({ cellType: "boolean", isCalculated: false })).toBe("text");
        // A derived cell carries a real per-row number: it is searched that way.
        expect(fieldFacetOrder({ cellType: "text", isCalculated: true })).toBe("number");
    });

    it("treats a text field as text, whatever the column looks like", () => {
        // «Дефектная ведомость/ВОР №» is text since migration 0030: a seven-digit
        // number whose leading zero the field keeps — not a number range.
        expect(fieldFacetOrder({ cellType: "text", isCalculated: false })).not.toBe("number");
    });

    it("falls back to text for an unknown column", () => {
        expect(fieldFacetOrder(undefined)).toBe("text");
    });
});

describe("facetOrderOf", () => {
    it("keeps the dictionary columns' own kinds", () => {
        expect(
            facetOrderOf({ key: "totalCost", label: "Стоимость", width: 10, kind: "number" }),
        ).toBe("number");
        expect(
            facetOrderOf({ key: "createdAt", label: "Создано", width: 10, kind: "datetime" }),
        ).toBe("date");
        expect(facetOrderOf({ key: "name", label: "Имя", width: 10, kind: "text" })).toBe("text");
        expect(facetOrderOf(undefined)).toBe("text");
    });
});
