/**
 * Pure parsing/validation for the frontend coverage gate. No I/O, no exits:
 * the executable entry point owns the filesystem and the exit codes.
 *
 * LCOV is line-oriented. A line record is an `LF` count followed by its `LH`
 * count; records are normally grouped by `SF:` / `end_of_record`, but bare
 * `LF`/`LH` pairs are also accepted (and are what the original gate summed).
 * Every count must be a finite non-negative integer and `LH` may never exceed
 * `LF` — a malformed report must fail the gate, never become 0 or NaN.
 */

import { join } from "node:path";

export const DEFAULT_MIN = 65;
export const DEFAULT_REPORT_PATH = join("coverage", "lcov.info");

const MIN_FLAG = "--min";
const INTEGER = /^\d+$/;
const DECIMAL = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

export type GateArgs = { min: number; path: string };
export type LineTotals = { found: number; hit: number };
export type CoverageResult = LineTotals & { percent: number; passes: boolean };

/** The floor is a percentage in (0, 100]; NaN and infinities are rejected. */
export function validateMin(value: number): number {
    if (!Number.isFinite(value) || value <= 0 || value > 100) {
        throw new Error(`${MIN_FLAG} expects a percentage in (0, 100], got ${String(value)}`);
    }
    return value;
}

function readMin(raw: string | undefined): number {
    if (raw === undefined) {
        throw new Error(`${MIN_FLAG} requires a value: --min 65 or --min=65`);
    }
    // Reject whitespace, hex, exponent notation, NaN/Infinity and signs before
    // Number(): both CLI spellings must follow exactly the same rules.
    if (!DECIMAL.test(raw)) {
        throw new Error(`${MIN_FLAG} expects a percentage in (0, 100], got "${raw}"`);
    }
    return validateMin(Number(raw));
}

/** Parse the documented contract: `--min 65`, `--min=65`, and one report path. */
export function parseArgs(argv: readonly string[]): GateArgs {
    let min = DEFAULT_MIN;
    let path = DEFAULT_REPORT_PATH;
    let hasMin = false;
    let hasPath = false;

    for (let index = 0; index < argv.length; index += 1) {
        const argument = argv[index];
        if (argument === undefined) continue;

        if (argument === MIN_FLAG) {
            if (hasMin) throw new Error(`${MIN_FLAG} was provided more than once`);
            min = readMin(argv[index + 1]);
            hasMin = true;
            index += 1;
        } else if (argument.startsWith(`${MIN_FLAG}=`)) {
            if (hasMin) throw new Error(`${MIN_FLAG} was provided more than once`);
            min = readMin(argument.slice(MIN_FLAG.length + 1));
            hasMin = true;
        } else if (argument.startsWith("-")) {
            throw new Error(
                `unknown option "${argument}" — expected --min <percent>, --min=<percent> or a report path`,
            );
        } else {
            if (argument.length === 0) throw new Error("report path must not be empty");
            if (hasPath) throw new Error(`only one report path is allowed, got "${argument}"`);
            path = argument;
            hasPath = true;
        }
    }

    return { min, path };
}

function parseCount(raw: string, field: "LF" | "LH"): number {
    if (!INTEGER.test(raw)) {
        throw new Error(`invalid ${field} "${raw}" — expected a non-negative integer`);
    }
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) {
        throw new Error(`invalid ${field} "${raw}" — expected a finite non-negative integer`);
    }
    return value;
}

/**
 * Sum every LF/LH pair in an LCOV report, rejecting incomplete or inconsistent
 * records instead of silently contributing a malformed count to the total.
 * Both LF and CRLF reports are accepted.
 */
export function totalLines(lcov: string): LineTotals {
    const totals: LineTotals = { found: 0, hit: 0 };
    let pendingLf: number | undefined;
    let inFile = false;
    let pairsInFile = 0;

    for (const rawLine of lcov.split(/\r?\n/)) {
        const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;

        if (line.startsWith("SF:")) {
            if (pendingLf !== undefined) {
                throw new Error(`line record has LF:${pendingLf} without a matching LH`);
            }
            inFile = true;
            pairsInFile = 0;
        } else if (line.startsWith("LF:")) {
            if (pendingLf !== undefined) {
                throw new Error("duplicate LF before a matching LH in a line record");
            }
            if (inFile && pairsInFile > 0) {
                throw new Error("file record has more than one LF/LH pair");
            }
            pendingLf = parseCount(line.slice(3), "LF");
        } else if (line.startsWith("LH:")) {
            if (pendingLf === undefined) {
                throw new Error("LH without a matching LF in a line record");
            }
            const hit = parseCount(line.slice(3), "LH");
            if (hit > pendingLf) {
                throw new Error(`LH:${hit} exceeds LF:${pendingLf} in a line record`);
            }
            totals.found += pendingLf;
            totals.hit += hit;
            pendingLf = undefined;
            pairsInFile += 1;
        } else if (line === "end_of_record") {
            if (pendingLf !== undefined) {
                throw new Error(`line record has LF:${pendingLf} without a matching LH`);
            }
            inFile = false;
            pairsInFile = 0;
        }
    }

    if (pendingLf !== undefined) {
        throw new Error(`line record has LF:${pendingLf} without a matching LH`);
    }
    return totals;
}

/** Reject NaN/empty totals before the comparison: `NaN < min` is false. */
export function coveragePercent(totals: LineTotals): number {
    const { found, hit } = totals;
    if (!Number.isSafeInteger(found) || !Number.isSafeInteger(hit)) {
        throw new Error(`invalid LCOV line totals: LF=${String(found)}, LH=${String(hit)}`);
    }
    if (found === 0) {
        throw new Error("no usable line records in the LCOV report");
    }
    if (hit < 0 || hit > found) {
        throw new Error(`invalid LCOV line totals: LF=${found}, LH=${hit}`);
    }
    return (hit / found) * 100;
}

/** Validate a whole report and decide the gate outcome without side effects. */
export function evaluateCoverage(lcov: string, min: number): CoverageResult {
    const floor = validateMin(min);
    const totals = totalLines(lcov);
    const percent = coveragePercent(totals);
    return { ...totals, percent, passes: percent >= floor };
}
