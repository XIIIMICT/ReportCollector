import { expect, test } from "bun:test";

import { totalLines } from "../../scripts/coverage-gate";

/**
 * The coverage gate is the only thing standing between "we collect coverage" and
 * "we enforce it", so its parser is pinned: a wrong LF/LH sum would silently
 * pass a regression.
 */

test("an lcov report is summarised by its LF/LH records", () => {
    const lcov = [
        "TN:",
        "SF:src/shared/ui/file-input.tsx",
        "LF:120",
        "LH:118",
        "end_of_record",
        "SF:src/features/kppk/kppk-workspace.tsx",
        "LF:80",
        "LH:40",
        "end_of_record",
    ].join("\n");

    expect(totalLines(lcov)).toEqual({ found: 200, hit: 158 });
});

test("a report with no line records totals to zero", () => {
    expect(totalLines("TN:\nend_of_record")).toEqual({ found: 0, hit: 0 });
});

test("records are summed across every file, not taken from the last one", () => {
    const lcov = "LF:10\nLH:10\nLF:10\nLH:0";
    expect(totalLines(lcov)).toEqual({ found: 20, hit: 10 });
});
