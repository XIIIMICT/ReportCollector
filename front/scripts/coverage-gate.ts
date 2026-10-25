/**
 * Coverage gate for the frontend.
 *
 * `bun test` can emit coverage (`--coverage-reporter=lcov`) but has no
 * `--coverage-threshold`, so the number was collected and thrown away: the
 * backend requires 80% and the frontend could quietly halve without anyone
 * noticing. This reads the LCOV report Bun writes and fails below the floor.
 *
 * Usage: `bun scripts/coverage-gate.ts [--min 75]`
 *
 * It measures LINE coverage, like the backend's
 * `coverage report --fail-under=80`, so the two numbers are comparable.
 *
 * The floor sits just under the current level (73.3% lines): a gate is a
 * ratchet against REGRESSION, not a target someone has to clear on day one.
 * Raise it as the number grows.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const DEFAULT_MIN = 72;

function parseArgs(argv: readonly string[]): { min: number; path: string } {
    let min = DEFAULT_MIN;
    let path = join("coverage", "lcov.info");
    for (let index = 0; index < argv.length; index += 1) {
        const argument = argv[index];
        if (argument === "--min") {
            const value = Number(argv[index + 1]);
            if (!Number.isFinite(value) || value <= 0 || value > 100) {
                throw new Error(`--min expects a percentage, got ${String(argv[index + 1])}`);
            }
            min = value;
            index += 1;
        } else if (argument?.startsWith("--min=")) {
            min = Number(argument.slice("--min=".length));
        } else if (typeof argument === "string" && !argument.startsWith("--")) {
            path = argument;
        }
    }
    return { min, path };
}

type Totals = { found: number; hit: number };

/** Sum the `LF` (lines found) and `LH` (lines hit) records of an LCOV report. */
export function totalLines(lcov: string): Totals {
    const totals: Totals = { found: 0, hit: 0 };
    for (const line of lcov.split("\n")) {
        if (line.startsWith("LF:")) totals.found += Number(line.slice(3));
        else if (line.startsWith("LH:")) totals.hit += Number(line.slice(3));
    }
    return totals;
}

function main(): void {
    const { min, path } = parseArgs(Bun.argv.slice(2));
    let lcov: string;
    try {
        lcov = readFileSync(path, "utf8");
    } catch {
        console.error(
            `coverage gate: ${path} not found — run "bun test --coverage-reporter=lcov" first.`,
        );
        process.exit(1);
    }

    const { found, hit } = totalLines(lcov);
    if (found === 0) {
        console.error("coverage gate: the report contains no line records.");
        process.exit(1);
    }

    const percent = (hit / found) * 100;
    const summary = `${percent.toFixed(2)}% of ${found} lines (${hit} hit)`;
    if (percent < min) {
        console.error(`coverage gate: ${summary} is below the ${min}% floor.`);
        process.exit(1);
    }
    console.log(`coverage gate: ${summary} meets the ${min}% floor.`);
}

// Only run when executed directly, so the parser stays unit-testable.
if (import.meta.main) main();
