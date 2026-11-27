/**
 * Generate compile-time HTTP DTOs from the live backend OpenAPI contract.
 *
 *   bun run --cwd front gen:openapi            # write generated/openapi.d.ts
 *   bun run --cwd front gen:openapi --check     # CI freshness gate (no writes)
 *
 * The schema is exported in-process from `app.openapi()` (no HTTP server or
 * database). The pinned generator emits types only — no client, no SDK, no
 * runtime dependency. Generated types are compile-time only; they provide no
 * runtime validation of backend responses.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const frontRoot = fileURLToPath(new URL("../", import.meta.url));
const target = fileURLToPath(new URL("../src/shared/api/generated/openapi.d.ts", import.meta.url));
const checkOnly = process.argv.includes("--check");
const tempDir = mkdtempSync(join(tmpdir(), "rc-openapi-"));

const banner = [
    "// GENERATED FILE — do not edit.",
    "// Source: live app.openapi() exported by back/src/openapi_export.py",
    "// Regenerate with: bun run --cwd front gen:openapi",
    "",
].join("\n");

try {
    const schemaPath = join(tempDir, "openapi.json");
    const outDir = join(tempDir, "generated");
    mkdirSync(outDir, { recursive: true });
    execFileSync(
        "uv",
        [
            "run",
            "--directory",
            join(repoRoot, "back"),
            "python",
            "-m",
            "src.openapi_export",
            schemaPath,
        ],
        { stdio: "inherit" },
    );
    const outFile = join(outDir, "openapi.d.ts");
    execFileSync(
        "bun",
        [
            "x",
            "--no-install",
            "swagger-typescript-api",
            "generate",
            "-p",
            schemaPath,
            "-o",
            outDir,
            "-n",
            "openapi.d.ts",
            "--no-client",
            "--enum-style=union",
            "--type-only-imports",
            "--disable-formatting",
            "--silent",
        ],
        { cwd: frontRoot, stdio: "inherit" },
    );
    writeFileSync(outFile, banner + readFileSync(outFile, "utf8"), "utf8");
    execFileSync("bun", ["x", "--no-install", "oxfmt", outFile], {
        cwd: frontRoot,
        stdio: "inherit",
    });

    const generated = readFileSync(outFile);
    const current = existsSync(target) ? readFileSync(target) : null;
    if (checkOnly) {
        if (!current?.equals(generated)) {
            console.error(
                `Stale generated contract: ${target} (run bun run --cwd front gen:openapi)`,
            );
            process.exitCode = 1;
        } else {
            console.log("Generated OpenAPI declarations are fresh.");
        }
    } else if (!current?.equals(generated)) {
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, generated);
        console.log(`Generated ${target}`);
    } else {
        console.log(`Generated OpenAPI declarations unchanged: ${target}`);
    }
} finally {
    rmSync(tempDir, { recursive: true, force: true });
}
