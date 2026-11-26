/**
 * Coverage gate for the frontend.
 *
 * `bun test` can emit coverage (`--coverage-reporter=lcov`) but has no
 * `--coverage-threshold`, so the number was collected and thrown away: the
 * backend requires 80% and the frontend could quietly halve without anyone
 * noticing. This reads the LCOV report Bun writes and fails below the floor.
 *
 * Usage: `bun scripts/coverage-gate.ts [--min 65] [--min=65] [report/path]`
 *
 * It measures LINE coverage, like the backend's
 * `coverage report --fail-under=80`, so the two numbers are comparable.
 *
 * A gate is a ratchet against REGRESSION, not a target to clear on day one —
 * and the number is only a proxy. It was 72 while the real level sat just under
 * it, so the gate failed on new UI code whose lines (JSX prop plumbing, layout)
 * are expensive to test and cheap to break without anyone noticing the break.
 * Chasing percentiles by testing presentational JSX produces tests that assert
 * markup and fail on every cosmetic change, which teaches the team to ignore red.
 *
 * 65 sits below the current level with room for a feature-sized addition; raise
 * it when MEANINGFUL tests lift the number — behaviour with a silent failure
 * mode (a wrong bound emptying a table, an unparsed entry crashing a mount), not
 * rendered markup. Parsing/validation lives in `coverage-gate-parse.ts`.
 */

import { readFileSync } from "node:fs";

import {
    evaluateCoverage,
    parseArgs,
    type CoverageResult,
    type GateArgs,
} from "./coverage-gate-parse";

function fail(error: unknown): never {
    console.error(`coverage gate: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
}

function main(): void {
    let args: GateArgs;
    try {
        args = parseArgs(Bun.argv.slice(2));
    } catch (error) {
        fail(error);
    }

    let lcov: string;
    try {
        lcov = readFileSync(args.path, "utf8");
    } catch {
        console.error(
            `coverage gate: ${args.path} not found — run "bun test --coverage-reporter=lcov" first.`,
        );
        process.exit(1);
    }

    let result: CoverageResult;
    try {
        result = evaluateCoverage(lcov, args.min);
    } catch (error) {
        fail(error);
    }

    const { found, hit, percent, passes } = result;
    const summary = `${percent.toFixed(2)}% of ${found} lines (${hit} hit)`;
    if (!passes) {
        console.error(`coverage gate: ${summary} is below the ${args.min}% floor.`);
        process.exit(1);
    }
    console.log(`coverage gate: ${summary} meets the ${args.min}% floor.`);
}

// Only run when executed directly, so the parser stays unit-testable.
if (import.meta.main) main();
