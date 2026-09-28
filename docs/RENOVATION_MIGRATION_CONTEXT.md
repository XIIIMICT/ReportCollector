
> _Terminology update (blocks):_ the three responsibility blocks were renamed from people's surnames to directorates — `tashpaev`→`tech` (Техническая дирекция), `borodiichuk`→`ksir` (Дирекция КСиР), `babaeva`→`mto` (Дирекция МТО). Where the text below says «Tashpaev / Borodiychuk / Babaeva», read it as the corresponding directorate. The seed block-owner/worker placeholders were likewise relabelled and are to be replaced by real employees.

# Renovation Project Migration Context

## Purpose

This document is a handoff for an agent adding the renovation/object-plan project to the consolidated ReportCollector web application. It records the reusable domain behavior, spreadsheet contract, workflow, permissions, and grid lessons from the legacy `ReportCollector-Renovation` application.

The legacy repository is a behavioral reference, not an architecture template. The new implementation belongs in this repository and must follow its existing engineering conventions, authentication model, collaboration protocol, and documentation requirements.

## Architectural decision

The target is the ReportCollector web stack:

- Next.js frontend;
- Python/FastAPI backend;
- PostgreSQL as the durable store;
- WebSockets for live collaboration;
- Valkey for ephemeral leases, presence and cross-worker pub/sub (durable
  undo/redo history lives in PostgreSQL `collab_edit_stacks`, not in Valkey).

Do **not** port Tauri, Rust, SQLite, desktop packaging, or Windows 7 compatibility work. Do **not** reproduce the legacy generic `form_row`/`form_value` EAV model. Renovation should have its own flat, typed PostgreSQL tables and project-specific API/schema. Other departments may evolve independently and should not be forced through a shared dynamic-field persistence layer.

Reuse the platform mechanisms already documented in [ARCHITECTURE.md](ARCHITECTURE.md), [COLLAB.md](COLLAB.md), and [GRID.md](GRID.md), while keeping renovation-specific domain services and tables isolated from the existing raw-materials project.

## Domain overview

The form is a very wide, ordered object-level renovation plan. Its column metadata, reference values, calculated-field definitions, responsibility blocks, and original ordering are captured in the legacy [`renovation-form.json`](../../ReportCollector-Renovation/src-tauri/resources/renovation-form.json). Treat that file as migration input to be reviewed and converted into explicit Python models, migrations, and frontend column descriptors; it should not become a runtime schema engine.

Important invariants:

- Row order is business data. New rows are appended at the end and must never appear before older rows unless the user is viewing an explicitly sorted projection.
- Store an immutable monotonic position/order key. Pagination, filtering, export, snapshots, and live updates need a deterministic `(position, id)` order.
- Calculated fields are server-authoritative and read-only in the UI. Recalculate them after edits and imports.
- Reference fields are validated against canonical values. Editable reference cells use a selector; read-only reference cells look like plain text, not disabled inputs.
- Asset-scoped users see and edit only permitted assets. Central roles may span all assets.
- Missing source columns are empty values, not an import failure.

## Roles and permissions

Legacy role names are useful domain labels; integrate them with ReportCollector's project/user provisioning rather than copying the desktop session tables.

| Role/person | Asset scope | Working plan | Pending plan | Special actions |
|---|---|---|---|---|
| Asset user | Assigned assets | Edit permitted fields; append rows; delete eligible rows | Read-only | None |
| Tashpaev | All assets | Edit the Tashpaev responsibility block | Read-only | Participate in the first-stage agreement |
| Borodiychuk | All assets | Edit the Borodiychuk responsibility block | Read-only | Participate in the first-stage agreement |
| Babaeva | All assets | Edit the Babaeva responsibility block | Read-only | Participate in the first-stage agreement |
| Vasilenko (Ekaterina Igorevna, `vasilenko@ruschem.ru`) | All assets | Edit every non-calculated field; append/delete eligible rows | The only editor | Import XLSX/XLSB |
| Rogov | All assets | Read-only | Read-only | Final approval and archival |

The desired Russian workflow terminology is:

1. Tashpaev, Borodiychuk, and Babaeva perform **agreement** (`Согласование`).
2. Rogov performs final **approval** (`Утверждение`).

Some legacy source comments or UI strings reflect an earlier naming reversal. Preserve the behavior, but use the terminology above in the new product. The exact quorum rule for the three first-stage roles must be confirmed before implementation: the desktop implementation is useful evidence, but the product requirement should be made explicit in the new state machine and tests.

## Plan lifecycle and snapshots

Model lifecycle transitions explicitly; avoid inferring state from nullable timestamps.

```text
WORKING -- first-stage agreement --> PENDING_APPROVAL
PENDING_APPROVAL -- Rogov approval --> immutable archive snapshot + new WORKING version
```

- The working plan is editable according to role and asset permissions.
- First-stage agreement fixes a consistent snapshot/baseline and changes the sidebar status from `Рабочая версия` to `На согласовании`.
- While pending, only Vasilenko can change the pending version.
- Rogov's approval creates an immutable final archive snapshot and makes a working version available again.
- Archive rows must be value copies, not references whose displayed values can change with the live dictionaries.
- State transitions and snapshot creation must be one PostgreSQL transaction with authorization and current-state checks.
- Broadcast the committed transition over the project room so every client changes version/state together.

Rows included in an accepted baseline cannot be deleted later. Rows added after the current baseline may be deleted by asset users within their scope and by Vasilenko. The UI previously showed a strong horizontal boundary below the last baseline row; retain an equivalent visual cue, but enforce eligibility on the server. Persist baseline membership or a baseline generation on each row rather than deriving it from its current screen index.

## Editing, collaboration, and history

Adopt ReportCollector's existing canonical cell keys, WebSocket rooms, presence, and lease semantics. Scope rooms and undo stacks by renovation project and plan/version so actions cannot leak between forms or versions.

Required behavior:

- `Ctrl+Z` and `Ctrl+Y` work by `event.code`, including with a Russian keyboard layout.
- History is limited to 50 user actions for this project unless the product decision is deliberately changed.
- Cell edits, row append/delete, and import are logical actions.
- Normal edits must pass the same permission, reference, calculation, revision, and lease checks when undone or redone.
- Never store 80,000 per-row deltas in Valkey for an import. Import into a staged dataset/version, validate it, then atomically swap the active dataset pointer. Undo/redo can swap dataset/version identifiers in constant time; garbage-collect unreachable datasets after their history entries expire.
- A final approval should invalidate history that could mutate the archived baseline.

Follow the concurrency lessons in [COLLAB.md](COLLAB.md): durable values and versions belong in PostgreSQL; Valkey state is disposable coordination state, not the source of truth.

## Spreadsheet import contract

The legacy importer accepts `.xlsx` and `.xlsb`. The real customer workbook is partial: it contains the technical-department block rather than every application column.

Import should be an asynchronous/staged operation with visible progress (processed rows, total when known, current phase, and a terminal error report). For large workbooks, parse and insert in batches without loading the entire normalized dataset into browser memory. Only Vasilenko may start an import.

Rules already validated against real data:

- Match columns by normalized semantic header, not only by physical position.
- Missing application columns become `NULL`; unknown **present** columns should be reported and may be ignored according to an explicit policy.
- Preserve input row order.
- Recompute calculated fields instead of trusting workbook check/formula columns.
- Keep inventory numbers as text because they may contain leading zeroes or letters.
- Drop hidden time components from date cells and keep the calendar date.
- Round monetary values to the declared database precision.
- Resolve duplicated work-type labels with the work category.
- Normalize Latin `OPEX`/`CAPEX` and Cyrillic lookalikes `ОРЕХ`/`САРЕХ` to canonical Latin reference values before validation. This specifically prevents the former failure at cell `V74180`.
- Produce actionable errors containing sheet, row, column/header, raw value, and reason; collect a bounded sample instead of failing with only the first opaque message.

The real validation fixture had sheet `РБ_2027_БСК`, 74,188 physical rows by 37 columns (two header rows and 74,186 data rows). All 74,186 rows imported successfully in the isolated legacy test. Of 216 application fields, 110 were calculated; the XLSB supplied 26 of the 106 input fields, leaving 80 input fields empty. See the full [`real-xlsb-validation.md`](../../ReportCollector-Renovation/outputs/real-data-analysis/real-xlsb-validation.md).

A synthetic 80,000-row XLSX was also used for performance and undo/redo testing. Preserve both real-file compatibility and a generated large-fixture test in the web implementation; do not make the real customer workbook a required CI artifact if it contains confidential data.

## Excel export contract

Export is based on the customer's workbook template rather than recreating the sheet visually from scratch:

- remove the template's example data row;
- insert exported rows into the existing table/range;
- retain formatting, merged headers, widths, print settings, and the supplied formulas;
- update formula ranges and workbook calculation metadata safely;
- avoid stale or invalid `calcChain.xml` entries (the desktop export once caused Excel to report removed formula records);
- preserve canonical ordering and export either the selected/visible scope or the full plan as the UI specifies;
- show server-side export progress and stream/download the completed artifact.

Template ownership, version, checksum, and expected header mapping should be explicit server configuration. Add a test that opens the result with an independent workbook library and checks formulas/styles, plus a manual Excel smoke test for release candidates.

## Renovation ID

The calculated identifier format is:

```text
Year_LegalEntityCode_DirectionCode_RepairObjectCode_AdditionalNumbering
```

Examples:

- `2027_BSKR_1.1.1._1382467_01`
- `2027_BSKR_1.1.1._Кор-И101_00`
- `2027_BSKR_1.1.1._10.2.44.7_00`

Rules:

- Preserve an existing non-empty ID; generated IDs are not silently regenerated after source-field edits.
- Year comes from the work start date.
- Legal entity code is an official four-letter code, not a guessed transliteration plus `R`.
- Direction code has a form such as `1.1.1.` (including the trailing dot after normalization).
- Repair-object precedence is: improvement project code; otherwise a seven-digit defect statement number; otherwise — when no defect statement is present — the free-text «корректировка» (col_218); if neither a defect statement nor a correction is given, the repair-object component is empty (`<NULL>`). The former «technological position + abbreviated work type + Russian month» composite is no longer generated.
- Additional numbering defaults to `00`; an explicit suffix must be two digits.
- A missing required component is rendered in the composite ID as the literal `<NULL>` in that component's place (e.g. `2027_BSKR_<NULL>_1382467_00`) — never a placeholder in the missing source cell itself. The composed field (col_003) always carries a string; `missing_components` in the result keeps the list for data-quality follow-up.

Official legal entity codes currently captured in the legacy configuration are `BSKR`, `BSZR`, `KSZR`, `TIAF`, `SNHZ`, `KUCH`, `VOZR`, `MFKR`, `MDRU`, `SXRU`, `DGOK`, `MINU`, `ASHK`, `HIMR`, `NHDR`, `SNAB`, and `NHRS`.

Known work-type abbreviations and the «technology position + work type + month» object-code rule above are **legacy**: the port used them only for the former no-defect composite, which is no longer generated (see Rules). They are kept only in `renovation_id.py` as dormant helpers for reference.

Port the rule as a pure, heavily tested Python domain function. The legacy behavior and edge-case tests are in [`renovation_id.rs`](../../ReportCollector-Renovation/src-tauri/src/renovation_id.rs).

### Unresolved source data

The real XLSB contains textual `Функциональное направление` but no authoritative mapping to a direction code such as `1.1.1.`. Consequently every real row currently lacks that ID component. Obtain and model a reference mapping from functional direction (or another authoritative source attribute) to direction code.

The last nine aggregate rows also lack both a defect statement number and a technological position, so the repair-object component needs a separate service/object-code rule. Future improvement-project rows require an authoritative project-code source. Repeated defect statements require an explicit source for the two-digit additional number; otherwise `00` remains the default.

Do not hide these gaps by guessing. Keep the missing component as the literal `<NULL>` inside the composed ID until an approved mapping or rule exists (RESOLVED 2026-08-19: `<NULL>` in the composite field, not `NULL`, not `<УГАДАЙТЕ>`), and keep incomplete IDs queryable for data-quality follow-up.

### Update 2026-09-03: correction column and object-code rule

`renovation-form.json` gained a nullable free-text field **`col_218` «Корректировка (при отсутствии дефектной ведомости)»**, inserted immediately after `col_023` (№ дефектной ведомости); migration **0015** adds `renovation_plan_rows.col_218` (nullable `TEXT`). Behaviour:

- A defect-statement number (`col_023`) still takes precedence — a correction entered alongside it does not change the code.
- Only when **no** defect statement is present does the correction, if given, become the repair-object component verbatim; with neither a defect statement nor a correction the repair-object component is empty (`<NULL>`).
- The former «technological position + abbreviated work type + Russian month» composite is **no longer generated**; the work-type abbreviation helpers remain only as dormant legacy reference.
- `col_218` is seeded as `NULL` (nullable), so no data migration is required beyond the additive column.

## Grid and UX behavior worth retaining

- Asset filter and responsibility-block filter (`All`, Tashpaev, Borodiychuk, Babaeva); block views retain the row-number and asset columns.
- Optional `Закрепить заголовок` switch, off by default and independent of fullscreen mode.
- Persist user column widths and support genuine drag resizing.
- Wide/merged headers need explicit horizontal-scroll regression tests. The legacy grid showed white/grey repaint artifacts when virtualized headers crossed pinned boundaries; do not copy CSS workarounds blindly.
- A header-numbering row includes the synthetic row-number column.
- Empty grid space uses the same moderately dark background as empty cells.
- Fullscreen transitions move the header, top toolbar, sidebar, footer, and their children as coordinated units.
- Hide actions a role cannot use where showing a permanently disabled control adds no information.
- Put keyboard shortcuts in accessible tooltips (a visual `kbd` is appropriate), not in every button label.
- Avoid nested focus rectangles while a cell editor is active.
- Use progress UI for both import and export, including cancellation only when the backend can honor it safely.
- Sorting/filtering must never change the canonical persisted row order or cause screen indices to address the wrong source rows. Apply the `getVisibleRows()` lesson from [GRID.md](GRID.md).

The target grid is revo-grid, not the legacy ReactGrid implementation. Reproduce observable behavior with the current grid's supported APIs instead of porting component internals.

## Suggested flat PostgreSQL shape

Exact columns should be generated from the reviewed form specification, but the persistence boundary should resemble:

- `renovation_plans`: version/dataset identity, lifecycle state, baseline generation, timestamps and actors;
- `renovation_plan_rows`: one typed, flat row containing the project's input and calculated columns, plus `id`, `plan_id`, `asset_id`, monotonic `position`, baseline generation, revision, and audit fields;
- project-specific reference tables where values have business identity, labels, ordering, and active periods;
- `renovation_snapshots` and flat snapshot rows/value copies for immutable archives;
- import jobs, staged datasets, and structured validation errors;
- an audit log for state transitions and privileged bulk operations.

Index for actual access paths: `(plan_id, position, id)`, `(plan_id, asset_id, position, id)`, lifecycle/status lookups, incomplete-ID reporting, and any confirmed high-frequency filters. Avoid speculative indexes on all 200+ fields. If a calculated value is expensive and frequently filtered/exported, store it as a server-maintained typed column; otherwise calculate it in the domain service. Use PostgreSQL constraints for types and simple invariants, and domain services for cross-field precedence rules.

## What not to port

- Tauri commands, Rust modules, desktop session tokens, filesystem dialogs, NSIS packaging, WebView2, or Windows 7 accommodations.
- SQLite locking/transaction assumptions and schema-repair-on-start behavior.
- Generic EAV storage (`form_value`) or a single universal table for unrelated departmental forms.
- Client-authoritative permission, calculation, row-order, workflow, or reference validation.
- Legacy terminology that reverses `Согласование` and `Утверждение`.
- Grid-specific rendering hacks without a failing revo-grid test proving they are still necessary.
- Generated test values such as `<УГАДАЙТЕ>` as if they were approved master data.

## Legacy source map

| Concern | Reference |
|---|---|
| Form, assets, references, blocks, fields | [`src-tauri/resources/renovation-form.json`](../../ReportCollector-Renovation/src-tauri/resources/renovation-form.json) |
| ID generation and tests | [`src-tauri/src/renovation_id.rs`](../../ReportCollector-Renovation/src-tauri/src/renovation_id.rs) |
| Import/export and real XLSB tests | [`src-tauri/src/spreadsheet.rs`](../../ReportCollector-Renovation/src-tauri/src/spreadsheet.rs) |
| Permissions, calculations, workflow | [`src-tauri/src/db.rs`](../../ReportCollector-Renovation/src-tauri/src/db.rs) |
| Undo/redo implementation | [`src-tauri/src/history.rs`](../../ReportCollector-Renovation/src-tauri/src/history.rs) |
| Legacy schema and constraints | [`docs/schema.sql`](../../ReportCollector-Renovation/docs/schema.sql) |
| Real workbook findings | [`outputs/real-data-analysis/real-xlsb-validation.md`](../../ReportCollector-Renovation/outputs/real-data-analysis/real-xlsb-validation.md) |
| Last desktop release notes | [`outputs/release-v6-2026-08-03/README.md`](../../ReportCollector-Renovation/outputs/release-v6-2026-08-03/README.md) |
| Frontend workspace/grid reference | [`src/components`](../../ReportCollector-Renovation/src/components) and [`src/grid`](../../ReportCollector-Renovation/src/grid) |

## Implementation progress

- **0006 (applied 2026-08-18)**: `rm_project_role` rename + `r_project_role`/`r_block` enums, `r_assets` (17), `r_user_assets`, `r_user_roles`. Role/block/permission model in `back/src/renovation/models.py`.
- **Seed (2026-08-19)**: 14 renovation users (`RENOVATION_USERS` in `back/src/scripts/seed_database.py`): Vasilenko (admin, all assets), Rogov (approver, all), the three block owners (Tashpaev/Borodiichuk/Babaeva, all assets) and 9 asset workers (`td-`/`opof-`/`umto-` per vos/snhz/bsk, single asset each). Keycloak provisioning: `infra/keycloak/provision-renovation-users.ps1` (idempotent, UTF-8 BOM — PowerShell 5.1 needs it for Cyrillic).
  **The seed itself is NOT yet applied to the dev DB**: the RM part of `seed()` is insert-always (only `--reset` works), so renovation seed data lands on the next `just seed`. Additive runs fail on `rm_asset_name_key` — do not attempt them.
- **0007 (applied 2026-08-19)**: `renovation_reference_values` (5 catalogs, 54 options seeded from `renovation-form.json`; `payload` holds criticality metadata), `renovation_plans` (lifecycle state + baseline generation, partial unique indexes: one working + one pending per project), `renovation_plan_rows` (224 columns: 9 system + 215 form fields `col_003..col_217`; `col_002` assets → `asset_id` FK; 5 reference fields → FKs to `renovation_reference_values.id`; numbers `NUMERIC(18,4)`; `(plan_id, position)` unique; `(plan_id, asset_id, position)` index). Models in `back/src/renovation/plan.py`; reference options in `back/src/renovation/reference_data.py` (used by the seed too).
- **0008 (applied 2026-08-20)**: audit columns on the renovation dictionaries — `r_assets.updated_by/updated_at`, `renovation_reference_values.updated_by/updated_at`, `app_users.last_authenticated_at`, `app_users_meta` for the renovation project (user status, saved theme, editor identity). `_column_exists` in the migration must iterate `get_columns()` elements (`any(col["name"] == ...)`), not call `.keys()` on the list.
- **Dictionaries editable (Phase 2, 2026-08-20)**: renovation assets, the five reference catalogs and project users become editable dictionaries on the «Ремонт» tab group — admin-only, reusing the «Сырьё» `RawMaterialsGrid` with `columnDescriptors`/`namespace` props. Assets — name + repair code (internal auto-`code` hidden); catalogs — add/delete options, `sort_order` hidden, sort by label; users — email/FIO/admin/assets/access, no deletion. Backend: `back/src/renovation/schemas.py` partial PATCH schemas (`RenovationAssetUpdateRequest`, `RenovationCatalogOptionUpdateRequest` — «at least one field» validator), `/dictionary/*` CRUD + `POST /dictionary/export` (all `AdminDependency`), section-scoped undo/redo (`renovationAssets`, `renovationCatalog:{id}`, `renovationUsers` → `app_users`). Collab rooms join the active tab; canonical cell keys use the collab table (`r_assets`, `renovation_reference_values`, `app_users`). Frontend: `front/src/features/renovation/renovation-dictionary-section.tsx` (per-cell PATCH, placeholder-row create, delete, export, undo per section). The Excel sheet name for a catalog is its `catalog_labels` entry from `renovation-form.json` (e.g. criticality → «Группа критичности оборудования»).
- **Open**: frontend (revo-grid, 11 header rows from `headerCells`), import/export, e2e. The seed reset decision above remains with the user.

### Ported (2026-08-19): renovation ID generator + formula engine

- `back/src/renovation/renovation_id.py` — full port of `renovation_id.rs` with the `<NULL>` semantics (decision 2026-08-19): a missing required component is substituted into the composite ID as the literal `MISSING_COMPONENT = "<NULL>"` in that component's place (`2027_BSKR_<NULL>_1382467_00`, fallback `ID39TLA*ТР*<NULL>`, missing year/month when `start_date` is absent); the composed value is always a string, never written into the missing source cell. `RenovationIdResult.missing_components` keeps the deterministic list (`start_date`, `asset_repair_code`, `direction_code`, then the branch component — `project_code` / `defect_number` / `technology_position` + `work_type_label`). A non-empty existing ID wins byte-for-byte. Error messages match the Rust originals; control characters are detected via `unicodedata.category == "Cc"` + explicit `\u2028`/`\u2029` (Python has no `isctrl()`).
- `back/tests/test_renovation_id.py` — 23 tests (documented samples, branches, validation, `<NULL>` substitution per component, missing-component ordering, byte-for-byte preservation).
- `back/src/renovation/calculations.py` — port of `formula.rs` (FormulaEngine): removes **all** calculated fields (including formula-less ones, which stay NULL), evaluates formulas in topological order over sorted keys, Number+None → 0, Date+None → skips the field, date−date → days, date±number → date shift (integer days only), division by zero → error, `round_to_places` (f64 semantics, `-0.0` → `0.0`), ASCII-only parser, exact legacy error texts.
- `back/tests/test_renovation_calculations.py` — 25 tests; `back/tests/test_renovation_real_form.py` — 8 integration tests against the real legacy `renovation-form.json` copied to `back/tests/test_data/` (59 formulas, 110 calculated fields, sample-row materialization, col_026 days, col_203 date shift, documented ID sample `2027_BSKR_1.1.1._1382467_00`, cycle rejection). The 51 calculated fields without a formula (col_034..col_082 groups, col_140, col_143) are removed but never computed — same as legacy.
- Both modules pass `ruff check`, `ruff format`, `ty check`; the full back suite is green (150 tests).

### Ported (2026-08-19): domain service + API + lifecycle + collaboration integration

- `back/src/renovation/service.py` — `read_meta` (216 fields from `renovation-form.json` via `form_schema.py`, 5 catalogs, assets), `read_plan` (by id/state; reference FKs → labels; `col_002` → asset name; renovation ID per row with `<NULL>` components; row filter by asset permissions), `update_plan_cells` (batch edits; reference values accepted as labels or ids; per-cell permission checks — admin/approver read-only, block owners edit their block in all assets, asset workers their block in their assets; `_recalculate_row` materializes 59 formulas; journaling via `record_cell_updates`, scope `renovationPlan/`; room broadcast), `submit_plan` (working → pending; admin or any block owner), `approve_plan` (pending → archived; admin or approver; creates the next working version as a copy of the archived rows with `baseline_generation + 1`, `revision 0`), `read_plan_versions`, `undo/redo` (delegates to the shared `collab/undo.py`, which reads the PostgreSQL journal `collab_edit_stacks`, with `skip_action_id`).
- `back/src/renovation/router.py` — `GET /api/renovation/meta`, `GET /plan` (`plan_id`/`state`), `GET /plans`, `PATCH /plan` (errors: 422 validation, 403 permission, 409 state, 404 not found), `POST /plan/submit`, `POST /plan/approve`, `POST /undo`/`/redo`/`GET /undo-state` (body `{"section": "renovationPlan"}`; `session_id` is a required query param on PATCH/undo/redo). `AccessDependency` resolves project/role/block/assets; `User`/`RBlock`/`RProjectRole` stay behind `TYPE_CHECKING`. `src/api.py` mounts the router.
- `back/src/renovation/access.py` — `resolve_renovation_access`: admin → all roles/assets; approver → read-only over all assets; block owner → own block, all assets; asset worker → own block, own assets; RUserRole must reference the renovation project (`RProjectRole` enum). WS access resolution in `back/src/collab/ws.py` mirrors it (admin/approver see all rows, asset users only their rows).
- `back/src/renovation/plan.py` — 30 `UUID | None` legacy fields re-typed to `str | None` (`col_003`, `col_011`, `col_016`, `col_189`, …); the 5 FK reference fields stay UUID. `col_023` (defect) is `NUMERIC(18,4)` in the DB, so the ID generator formats it as an integer (`.0f`).
- `back/src/renovation/form_schema.py` — lazy `lru_cache`d parsing of the copied `renovation-form.json` (field metas incl. `dbName`/`decimals`/formulas, reference catalogs, block↔field ownership), `formula_engine()`.
- Collaboration: rooms scoped `renovationPlan/`; `collab/undo` is shared (its `UndoScope` is a plain scope string); `collab/ws.py` gained `renovation_ws` (lease/presence handlers, `ws/renovation` sub-protocol with the same message shapes as raw-materials); `collab/room.py` gained `renovation_room_key`.
- Tests (`back/tests/`): `test_renovation_api.py` — 22 API tests (meta, plan read with labels + renovation IDs, versions, 403 access, patch + recalc `col_026 == 30`, `col_031 == 35`, multi-cell, reference labels, validation 422s, unknown row 404, bad date 422, block owner / asset worker permission matrix, undo/redo, submit/approve lifecycle, no-working-plan 404, archived read by id, approver read-only, seed-consistency `test_renovation_seed.py`). `conftest.py` seeds the renovation project per test (`_seed_renovation`).
- Cross-cutting fixes: `AUTH_IDENTITY_TTL` setting (default 60 s) replaces the hardcoded identity-signature window in `src/auth/service.py` + `src/auth/router.py`; `tests/conftest.py` sets `AUTH_IDENTITY_TTL=3600` so module-level signed headers stay valid in long full-suite runs. Existing tests updated for the third seeded project: user provisioning now asserts a renovation link (`test_raw_materials.py`), `/api/me` access lookups are key-based instead of `[0]` (`test_auth_scenario.py`).
- Full back suite green: **175 tests**, `ruff check`, `ruff format`, `ty check` all pass.

## Decisions required before implementation

1. Confirm whether first-stage agreement requires all three block owners, any one of them, or independent per-block approvals.

   **RESOLVED (2026-08-18): any one of the three block owners is sufficient** to move the plan to `На согласовании` (`PENDING_APPROVAL`). The first-stage agreement is a single plan-level transition, not per-block.
2. Obtain the functional-direction-to-direction-code reference.
3. Define repair-object codes for the nine aggregate/service rows and future improvement projects.
4. Confirm whether pending-plan edits by Vasilenko require a new agreement cycle before Rogov can approve.

   **RESOLVED (2026-08-19): Vasilenko (and admins generally) can keep editing a plan that already passed the first-stage agreement, right up to the moment Rogov approves it** — agreement is a signal, not a lock; the approval transition (`PENDING_APPROVAL` → archived + working copy) is what freezes the data.
5. Confirm snapshot/version retention and whether a post-approval working plan is copied from the approved version or starts empty.

   **RESOLVED (2026-08-18): the post-approval working version is a copy of the approved version** — approval does not wipe or reset the data; the working plan continues from the approved snapshot.
6. Approve the canonical workbook template, its ownership, and its versioning process.
7. Decide whether reference values are global, renovation-only, or effective-dated; do not infer this from the legacy SQLite layout.

When these decisions are made, update this document and the project-specific architecture/API documentation in the same change.
