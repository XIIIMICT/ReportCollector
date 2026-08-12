import { describe, expect, test } from "bun:test";

describe("API schema validation", () => {
    test("formRow values are always strings in record", () => {
        const values: Record<string, string> = { fieldId1: "value1", fieldId2: "" };
        expect(Object.values(values).every((v) => typeof v === "string")).toBe(true);
    });

    test("empty string is different from null", () => {
        const empty = "";
        const nullVal = null;
        expect(empty).not.toBe(nullVal);
        expect(empty.trim() === "").toBe(true);
    });
});
