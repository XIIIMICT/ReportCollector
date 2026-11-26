import { describe, expect, test } from "bun:test";

import {
    DEFAULT_MIN,
    DEFAULT_REPORT_PATH,
    coveragePercent,
    evaluateCoverage,
    parseArgs,
    totalLines,
} from "../../scripts/coverage-gate-parse";

/**
 * The coverage gate is the only thing standing between "we collect coverage" and
 * "we enforce it", so its parser is pinned: a wrong LF/LH sum, an accepted bad
 * threshold or a NaN comparison would silently pass a regression.
 */

const multiFileReport = [
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

describe("parseArgs — valid contract", () => {
    test("the floor stays at 65 by default", () => {
        expect(DEFAULT_MIN).toBe(65);
        expect(parseArgs([])).toEqual({ min: 65, path: DEFAULT_REPORT_PATH });
    });

    test("--min with a separate value", () => {
        expect(parseArgs(["--min", "65"])).toEqual({ min: 65, path: DEFAULT_REPORT_PATH });
    });

    test("--min=65 is validated identically to --min 65", () => {
        expect(parseArgs(["--min=65"])).toEqual(parseArgs(["--min", "65"]));
        expect(parseArgs(["--min=73.34"])).toEqual(parseArgs(["--min", "73.34"]));
    });

    test("a custom report path is accepted", () => {
        expect(parseArgs(["coverage/custom.info"])).toEqual({
            min: 65,
            path: "coverage/custom.info",
        });
        expect(parseArgs(["--min=70", "coverage/custom.info"])).toEqual({
            min: 70,
            path: "coverage/custom.info",
        });
        expect(parseArgs(["coverage/custom.info", "--min", "70"])).toEqual({
            min: 70,
            path: "coverage/custom.info",
        });
    });

    test("fractional thresholds and the range boundaries are accepted", () => {
        expect(parseArgs(["--min=0.5"]).min).toBe(0.5);
        expect(parseArgs(["--min", ".5"]).min).toBe(0.5);
        expect(parseArgs(["--min=100"]).min).toBe(100);
    });
});

describe("parseArgs — malformed input", () => {
    const invalid: readonly (readonly string[])[] = [
        ["--min"],
        ["--min="],
        ["--min", "abc"],
        ["--min=abc"],
        ["--min", "NaN"],
        ["--min=NaN"],
        ["--min=Infinity"],
        ["--min=-Infinity"],
        ["--min=0"],
        ["--min=-5"],
        ["--min", "-1"],
        ["--min=101"],
        ["--min=1e2"],
        ["--min", " 65"],
        ["--min=65 "],
        ["--bogus"],
        ["--min=65", "--bogus"],
        ["-m"],
        ["--"],
        [""],
        ["--min=65", "one.info", "two.info"],
        ["--min=65", "--min=70"],
        ["--min", "65", "--min=70"],
    ];

    for (const argv of invalid) {
        test(`rejects ${JSON.stringify(argv)}`, () => {
            expect(() => parseArgs(argv)).toThrow();
        });
    }

    test("a missing value names the flag", () => {
        expect(() => parseArgs(["--min"])).toThrow(/--min/);
    });

    test("unknown flags are rejected, not silently skipped", () => {
        expect(() => parseArgs(["--min=65", "--bogus"])).toThrow(/unknown option/);
    });

    test("non-numeric values explain the expected range", () => {
        expect(() => parseArgs(["--min=abc"])).toThrow(/\(0, 100\]/);
        expect(() => parseArgs(["--min=101"])).toThrow(/\(0, 100\]/);
    });
});

describe("totalLines — valid reports", () => {
    test("an lcov report is summarised by its LF/LH records", () => {
        expect(totalLines(multiFileReport)).toEqual({ found: 200, hit: 158 });
    });

    test("a report with no line records totals to zero", () => {
        expect(totalLines("TN:\nend_of_record")).toEqual({ found: 0, hit: 0 });
    });

    test("records are summed across every file, not taken from the last one", () => {
        const lcov = "LF:10\nLH:10\nLF:10\nLH:0";
        expect(totalLines(lcov)).toEqual({ found: 20, hit: 10 });
    });

    test("a third file is aggregated too", () => {
        const lcov = [
            "SF:a.ts",
            "LF:100",
            "LH:50",
            "end_of_record",
            "SF:b.ts",
            "LF:200",
            "LH:100",
            "end_of_record",
            "SF:c.ts",
            "LF:300",
            "LH:150",
            "end_of_record",
        ].join("\n");
        expect(totalLines(lcov)).toEqual({ found: 600, hit: 300 });
    });

    test("CRLF line endings are handled like LF", () => {
        const lcov =
            "SF:a.ts\r\nLF:10\r\nLH:7\r\nend_of_record\r\nSF:b.ts\r\nLF:20\r\nLH:13\r\nend_of_record\r\n";
        expect(totalLines(lcov)).toEqual({ found: 30, hit: 20 });
    });

    test("explicit zero counts are kept without becoming NaN", () => {
        expect(totalLines("SF:empty.ts\nLF:0\nLH:0\nend_of_record")).toEqual({
            found: 0,
            hit: 0,
        });
    });
});

describe("totalLines — malformed reports", () => {
    const invalid: readonly string[] = [
        "LF:abc\nLH:0",
        "LF:10\nLH:abc",
        "LF:NaN\nLH:0",
        "LF:10\nLH:NaN",
        "LF:Infinity\nLH:0",
        "LF:999999999999999999999999\nLH:0",
        "LF:-1\nLH:0",
        "LF:10\nLH:-1",
        "LF:1.5\nLH:1",
        "LF:10\nLH:1.5",
        "LF:\nLH:0",
        "LF:10\nLH: 10",
        "LF:10\nLH:11",
        "LH:5",
        "LF:10",
        "SF:a.ts\nLF:10\nend_of_record",
        "SF:a.ts\nLF:10\nSF:b.ts\nLH:10",
        "LF:10\nLF:10\nLH:10",
        "SF:a.ts\nLF:10\nLH:10\nLF:5\nLH:5\nend_of_record",
    ];

    for (const lcov of invalid) {
        test(`rejects ${JSON.stringify(lcov)}`, () => {
            expect(() => totalLines(lcov)).toThrow();
        });
    }

    test("LH greater than LF names both counts", () => {
        expect(() => totalLines("LF:10\nLH:11")).toThrow(/LH:11 exceeds LF:10/);
    });

    test("a non-numeric LF names the field", () => {
        expect(() => totalLines("LF:abc\nLH:0")).toThrow(/LF/);
    });
});

describe("coveragePercent and evaluateCoverage — final comparison", () => {
    test("the percentage is computed from all records", () => {
        expect(coveragePercent({ found: 200, hit: 158 })).toBe(79);
        expect(coveragePercent({ found: 100, hit: 65 })).toBe(65);
        expect(coveragePercent({ found: 3, hit: 2 })).toBeCloseTo(66.6667, 4);
    });

    test("NaN totals throw instead of making `NaN < min` a false pass", () => {
        expect(() => coveragePercent({ found: NaN, hit: 0 })).toThrow();
        expect(() => coveragePercent({ found: 10, hit: NaN })).toThrow();
        expect(() => coveragePercent({ found: Infinity, hit: 10 })).toThrow();
        expect(() => evaluateCoverage("LF:10\nLH:NaN", 65)).toThrow();
    });

    test("empty, negative and fractional totals are rejected", () => {
        expect(() => coveragePercent({ found: 0, hit: 0 })).toThrow(/no usable line records/);
        expect(() => coveragePercent({ found: 10, hit: 11 })).toThrow();
        expect(() => coveragePercent({ found: 10, hit: -1 })).toThrow();
        expect(() => coveragePercent({ found: 10.5, hit: 5 })).toThrow();
    });

    test("a report with no usable line records fails the gate", () => {
        expect(() => evaluateCoverage("TN:\nend_of_record", 65)).toThrow(/no usable line records/);
        expect(() => evaluateCoverage("LF:0\nLH:0", 65)).toThrow(/no usable line records/);
    });

    test("the floor is inclusive", () => {
        expect(evaluateCoverage(multiFileReport, 65)).toEqual({
            found: 200,
            hit: 158,
            percent: 79,
            passes: true,
        });
        expect(evaluateCoverage(multiFileReport, 79).passes).toBe(true);
        expect(evaluateCoverage(multiFileReport, 79.01).passes).toBe(false);
    });

    test("a 100% floor requires every line", () => {
        expect(evaluateCoverage("LF:10\nLH:10", 100).passes).toBe(true);
        expect(evaluateCoverage("LF:10\nLH:9", 100).passes).toBe(false);
    });

    test("an invalid floor is rejected before comparing", () => {
        for (const min of [NaN, Infinity, -Infinity, 0, -1, 101]) {
            expect(() => evaluateCoverage(multiFileReport, min)).toThrow();
        }
    });
});
