# Language-standard audit (2026-09-07)

## Scope and method

Reviewed tracked source, tests, configuration, `AGENTS.md` and the Markdown files in `docs/`. Used Cyrillic searches for candidates and Python AST inspection to distinguish docstrings from runtime strings. Excluded dependencies, generated artifacts and local seed workbooks. This is a prioritized inventory of confirmed examples, not a claim that every Cyrillic match is a violation.

The CodeQL workflow was corrected separately. The language findings below are **not translated yet**; no application strings or behavior were changed by this audit.

## Confirmed violations

| Area | Locations (line numbers at audit time) | Finding |
|---|---|---|
| Root configuration | `.env.example:1`, `compose.yaml:7`, `justfile:2`, `.gitignore:1` | Long Russian explanations and section headings in configuration comments. |
| Backend tooling | `back/.gitignore:1`, `back/Dockerfile:19`, `back/alembic.ini:4`, `back/pyproject.toml:53` | Russian comments, including inline explanations of Ruff exclusions. |
| Frontend tooling | `front/.gitignore:1`, `front/Dockerfile:2` | Russian explanatory comments. |
| Infrastructure | `infra/compose.external-services.yaml:1`, `infra/compose.alloy.yaml:1` | Russian descriptions of services, profiles and log shipping. |
| Migration runner | `back/migrations/env.py:1`, `:40`, `:64` | Russian module and function docstrings. Do not rewrite already-applied revision files as part of a translation pass. |
| Authentication | `back/src/auth/passwords.py:1`, `back/src/auth/router.py:84`, `:117`, `:137`, `:180` | Russian module/endpoint docstrings. Endpoint docstrings may also appear in generated OpenAPI; classify that audience explicitly before translating them. |
| Collaboration | `back/src/collab/events.py:1` | Russian module documentation explaining room routing and pub/sub. |
| Renovation calculations | `back/src/renovation/calculations.py:1`, `back/src/renovation/renovation_id.py:1`, `:118`, `:215` | Russian module documentation and implementation comments. Keep validation messages, catalog labels and identifier fixtures unchanged. |
| Operator script | `back/src/scripts/grant_access.py:1` | A Russian module docstring mixes implementation documentation with operator instructions. Move human-facing instructions to clearly designated user documentation if necessary. |
| Backend tests | `back/tests/test_renovation_calculations.py:1`, `back/tests/test_renovation_id.py:1`, `:50`, `:155`, `back/tests/test_renovation_real_form.py:1`, `:105` | Russian test docstrings and explanatory comments. |
| Styles | `front/src/app/globals.css:61`, `:132`, `:139`, `:963`, `:1110`, `:1134`, `:1299`, `:1383`, `:1828` | Russian or mixed-language explanations and section headings. |
| Browser tests | `front/e2e/helpers/auth.ts:4`, `front/e2e/auth.spec.ts:12`, `front/e2e/collab.spec.ts:23`, `:116`, `:176`, `front/e2e/clipboard.spec.ts:194`, `front/e2e/raw-materials-ui.spec.ts:25`, `:238` | Russian comments describing redirects, selection, leases, clipboard behavior and seed assumptions. |

Two additional categories need attention:

- **Developer-facing test titles** are Russian in `front/src/shared/grid/__tests__/grid-clipboard.test.ts:27`, `grid-export.test.ts`, and several `front/e2e/*.spec.ts` files. They are report descriptions, not UI content; translate titles separately from assertions and test data.
- **Damaged or mixed wording** appears in `back/src/renovation/plan.py:46` (mojibake quotation marks) and `:360` (an English/Russian hybrid term). These are comments, not runtime failures, but should be cleaned up with the translation pass.

## Agent documentation and legitimate Cyrillic

The reviewed Markdown explanations in `AGENTS.md` and `docs/*.md` are already predominantly English. Most Cyrillic matches are exact screen/column names, error examples, catalog values, personal names, workbook sheet names or sample identifiers embedded in English prose. These are different from Russian explanatory paragraphs. For an English-only prose standard, preserve exact UI/data quotations where needed to map documentation to the application; transliterate personal names in narrative prose if they are not literal data. A literal zero-Cyrillic rule would additionally require rewriting these references, so it must not be confused with prose-language compliance.

Keep Russian UI labels, toasts, API validation errors, exports, catalog values and assertions against those values unchanged. Provisioning JSON/PowerShell data and source workbooks are not agent prose. No separate Russian human-documentation Markdown tree currently exists among tracked files.

`WORKFLOW.md` explicitly still allows existing Russian log-message text. Under that written standard it is not automatically a violation; purely diagnostic new messages are English. If the intended policy is now stricter, update that exception deliberately before translating logs.

## Follow-up boundary

Translate comments/docstrings/test descriptions as a dedicated change, preserving runtime strings and behavior. Use a semantic review, not a blanket Cyrillic ban: a regular expression alone cannot distinguish a Russian UI example from an explanation or detect comments reliably inside strings. After translation, run format/lint/type/tests and inspect diffs for accidental changes to fixtures, SQL, configuration values or applied migrations.
