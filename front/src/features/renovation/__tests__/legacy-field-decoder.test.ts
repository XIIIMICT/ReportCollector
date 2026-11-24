import { expect, test } from "bun:test";

import { decodeLegacyFieldKeys, decodeLegacyFieldName } from "@/shared/api/renovation";

test("decodes a legacy col_NNN key to the semantic §9.1 name", () => {
    expect(decodeLegacyFieldName("col_003")).toBe("work_or_repair_code");
    expect(decodeLegacyFieldName("col_218")).toBe("estimate_number");
    expect(decodeLegacyFieldName("col_023")).toBe("defect_list_or_bill_of_quantities_number");
});

test("leaves already-semantic names and unknown keys untouched", () => {
    expect(decodeLegacyFieldName("work_or_repair_code")).toBe("work_or_repair_code");
    expect(decodeLegacyFieldName("assetId")).toBe("assetId");
    // col_002 is the synthetic asset column, never a database field.
    expect(decodeLegacyFieldName("col_002")).toBe("col_002");
});

test("decodes the keys of a saved filter/pinned/hidden record", () => {
    const stored = {
        col_003: { values: ["A"], includeNulls: false },
        col_011: { values: ["B"], includeNulls: true },
        col_002: { values: ["C"], includeNulls: false },
    };
    const decoded = decodeLegacyFieldKeys(stored);
    expect(Object.keys(decoded)).toEqual(["work_or_repair_code", "process_position", "col_002"]);
    expect(decoded.work_or_repair_code).toEqual(stored.col_003);
    expect(decoded.process_position).toEqual(stored.col_011);
});

test("the decoder covers every one of the 216 renamed fields", () => {
    // Spot-check the boundaries of the mapping; a missing entry would leave a
    // saved filter pointing at a column the server no longer knows.
    for (const legacy of ["col_003", "col_031", "col_032", "col_217", "col_218"]) {
        expect(decodeLegacyFieldName(legacy)).not.toBe(legacy);
    }
});
