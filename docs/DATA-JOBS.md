# Data jobs: import, export, XLSX

Import/preflight and export detail moved from `ARCHITECTURE.md` (Phase 8
split). The API endpoints and environment remain in `ARCHITECTURE.md`.

## Renovation plan import (technical directorate, `.xlsb` / `.xlsx`)

`back/src/renovation/import_plan.py` validates an uploaded «РБ ПОФ» workbook
before anything is written. Columns are matched **only by header names** —
normalised (case, whitespace, quotes), never by positions, since columns are
added/removed between drops. The header may be row 1 or row 2 (row 1 then holds
the extra info); the work-code heading is the anchor, and a sheet without it is
still read by its other headings so the report can name what is missing. A
numbering helper row under the headings is detected by shape — a row of numbers,
whether typed or stored as text under a «Текстовый» format — and skipped, as is
a blank spacer between the header and it.
A workbook the analyzer cannot open at all (a broken container, another format,
no sheet whose name starts with «РБ») is `invalid_format`. A **readable** sheet
is never rejected for its format: when a required heading is absent, the
preflight answers `has_errors` with `missing_required_columns`, and the dialog
shows «Файл содержит ошибки» under the input plus «Не найдены обязательные
столбцы:» with the missing names. Required headings: «Код работы»,
«Наименование ДО» (or its fallback «Предприятие»), «Группа критичности
оборудования», «Категория работ», «Вид работ», «Отнесение затрат», «Способ
выполнения работ», «Дата начала работ», «Дата окончания работ». Any other
missing column is simply not loaded; an unknown one is ignored. Only two
headings need an alias: «Лот» → direction code (`lotting_activity_direction_code`) and the
suffix-free «Отнесение затрат (OPEX/CAPEX)» / «Способ выполнения работ».
Asset identification uses «Наименование ДО» — the official repair code
(`BSZR`) that is also an input of the work ID; the UI shows the asset name.
A drop without that column names the legal entity in «Предприятие» instead:
both are matched against the same `r_assets` dictionary (renovation assets are
`r_assets`, the raw-materials ones `rm_assets`), the code by code and
the name by name (trimmed, case-folded, and with every quote style folded to a
plain `"` — the files write `АО "БСЗ"` while the dictionary holds «АО «БСЗ»»).
Whether the file resolved by code or by name is carried as
`PlanFileScope.assets_are_names` / `ParsedPlanRow.asset_is_name`, so the router
and the loader look up the right map. A name the dictionary does not have is a
file error, exactly like an unknown code — never a silently dropped row.
The dictionary's key is `r_assets.code`, which IS the repair code (migration
0030 dropped the old internal slug). «Дефектная ведомость/ВОР №»
(`r_plan_rows.defect_list_or_bill_of_quantities_number`) is TEXT for the same reason: a seven-digit number with a
meaningful leading zero (`0706179`) cannot be stored as a number — the grid, the
importer and the legacy ID path all normalise it to digits and, when needed,
pad short values to seven.
Text cells are stored trimmed (a non-breaking space included) and an empty
string — or a cell holding only whitespace — counts as an empty cell: it is
skipped, never reported as «Неверный тип данных», even for an optional field
such as «Влияние на производство». Numeric cells accept the text shapes Excel
leaves behind: `1 234,50`, `1.234,50` and `1234.5` all parse; the last decimal
separator wins when both are present. In an integer field (`decimals = 0` or an
`integer` rule) a fractional tail is accepted only when it is zero (`123,00` →
123); a non-zero tail is a «Недопустимые значения» error so the operator decides
what to do with the fraction. The same category carries numeric rule violations:
`minimum`, `maximum` and the 0–100 bounds of `percentage`. The «Лот» code gets its mandatory trailing dot
added silently when the file has `1.1.1` instead of `1.1.1.`
(`renovation_id.normalize_direction_code` — the same rule the grid applies on
edit).

A date is read from its **value**, not from the workbook's declared cell type:
the customer's files routinely store a correct date as text (the column or just
the cell is formatted «Текстовый»), so `05.11.2003`, `5.11.03`, `05/11/2003`,
an ISO string and any of those with an optional time are all accepted and
normalised to a `date`. Only a value that is not a real calendar date
(`31.02.2003`, arbitrary text) stays a «Неверный тип данных» error. The apply
pass shares the parser, so the preflight and the load agree on what a date is.

Both containers the customer drops are accepted, chosen by the upload's suffix:
`.xlsb` is read with `pyxlsb`, `.xlsx` with `openpyxl` (`read_only`,
`data_only`). The OOXML reader therefore sees the formula **cached values** the
last Excel save left behind — it never re-evaluates a formula; the plan's own
recompute rebuilds the derived columns after the load regardless.

Checks, in order: the file must be a readable `.xlsb` or `.xlsx` whose header
carries
«Код работы» plus an asset column («Наименование ДО», or «Предприятие» when
the code column is absent) and at least half of the template columns;
cell types under the header (text/number/date); duplicate work codes
(«Код работы», listed with the rows they occupy) — the **only** blocker on the
code itself, since the code is loaded from the file as-is (the ID formula is
off, see «Renovation plan» below; the «Некорректно рассчитанные коды» check is
archived in `inspect_plan_file` behind `RENOVATION_ID_FORMULA_ENABLED`); an
asset whose code (or name) is not in the project's `rm_assets` dictionary
(`unknown_assets` — the asset is a reference value like any catalog label);
Excel's cached `#REF!` values (`formula_reference_errors` — a formula whose
referenced cell/range is gone; it is its own category, because in a text field
it would otherwise be stored as ordinary text and in a numeric one it would
masquerade as a type error; calculated template columns are checked too, even
though their values are recomputed after the load); numeric cells that violate
their field rules (`invalid_values` — a fraction in an integer column or a
value outside `minimum`/`maximum`/`percentage`); and reference labels
absent from the form's own
catalogs, compared strictly (whitespace/case only — no typo tolerance, no «@»
options). Every cell issue carries the form field's own label, so the dialog
names the column as «Цех (столбец H)» instead of only the Excel letter. Without
errors the response carries the assets (by name), the new rows and the rows to
update (existing `work_or_repair_code`).

The preflight dialog (`plan-excel-import-modal.tsx`) closes only by its close
button, Escape, or the «Загрузить» hand-off: the backdrop is
`isDismissable={false}`, so a stray click outside cannot discard a checked file
or interrupt an upload.

The upload body is bounded by `ImportUploadLimitsMiddleware` before multipart
parsing, then Starlette's `UploadFile` keeps it in its spooled temporary file.
The inspect endpoint hashes and parses it through bounded row iterators; its
scope-discovery pass (asset codes plus the reference catalogs the template
header uses) remains intentionally separate until a future one-pass preflight.
Both parse passes run in `asyncio.to_thread` with the DB connection released
(Phase 5.2): the endpoint reads its consistent reference snapshot first, calls
`session.close()` before the CPU work, and lets the session lazily reacquire a
connection for any later reads. The snapshot is taken once, so no dictionary can
change midway through a parse. Apply stages the bytes and does not parse them in
the HTTP request.

`RENOVATION_IMPORT_MAX_BYTES` defaults to 256 MiB for each file. The HTTP body
limit adds a 1 MiB allowance for multipart boundaries/fields and also covers
chunked uploads without `Content-Length`. `RENOVATION_UPLOAD_TIMEOUT_SECONDS`
(300 seconds) bounds receiving the body, excluding subsequent parse/apply time.
Oversized or slow bodies return 413/408, and the multipart parser closes partial
temporary files. Match proxy upload limits to these values in production.
The preflight answers in ONE request after the whole workbook is parsed, so
every hop must outlast that parse: `location /` in `infra/nginx/` sets
`proxy_read_timeout`/`proxy_send_timeout 86400s` (nginx's 60 s default answered
504 on a big drop while the analysis was still running), and the shared backend
proxy (`front/src/shared/api/backend-proxy.ts`) passes a dedicated
`undici.Agent` with `headersTimeout`/`bodyTimeout` of 24 h. Without it Node's
fetch gives up waiting for response headers after 300 s and raises
`UND_ERR_HEADERS_TIMEOUT` — the dev server runs Bun, whose fetch ignores the
dispatcher, so the failure appeared only in production. The same dispatcher
keeps long export bodies from being cut.

Reports are cached in Valkey (`src/renovation/inspect_cache.py`): the key is
`rc:planimport:v<INSPECT_CACHE_VERSION>:<sha256 of the bytes>:<suffix>:<scope>`,
where the version includes both the analyzer version and the renovation-ID
generator flag, and the suffix is the upload's own `.xlsb`/`.xlsx` (identical
bytes in different containers go through different readers and must never share
a report). The version is the analyzer's: bump `INSPECT_CACHE_VERSION` whenever
a deploy changes what a report contains, so a report written by the previous
analyzer is not served for up to the TTL. It was bumped from `7` to `8` together
with the plan-token change below: every entry written with the old scope is
unreachable at the new version prefix, so no rollout needs a Valkey flush.

The scope starts with the id of the CURRENT working plan and that plan's
work-code generation: `plan=<uuid>|rev=<n>|…` (`plan=none|rev=0|…` before a
working plan exists). Codes are compared only inside that plan, so archiving it
and opening a newer version re-keys the report even for identical bytes.
`rev` is `r_plans.revision`, maintained by database triggers on
`r_plan_rows` **in the same transaction as the write**: every row insert, every
row delete and every `work_or_repair_code` change increments it, including cell
edits, undo/redo, imports and the recompute paths that write codes. An archived
plan's changes bump only that plan's counter and cannot invalidate a report for
the working plan. This replaced the old `count(*) + max(updated_at)` token over
the touched assets: the maximum missed a delete/update of a non-latest row, so
the preflight kept serving a report that still listed a removed code.

The rest of the scope is file-derived. The asset part names the file's assets
(«Наименование ДО» repair codes → asset ids, or «Предприятие» names → asset ids
when the code column is absent). The dictionary part carries
`count(*) + max(updated_at)` of every reference catalog the file has a column
for (`scan_file_scope` maps the template header through the form schema, so a
catalog the file never mentions cannot affect its report) and the same token
for the asset entries the file names. Adding the value a cached report called
missing therefore changes the key and lets the same file be re-uploaded
immediately instead of serving the stale error until the TTL. The TTL is one
hour. The cache is optional — without `VALKEY_URL` or when Valkey is unreachable
(or an entry is malformed) the file is analysed again; no cache failure can fail
the import.

The upload is **hashed before any workbook reader is opened**: the hash of the
received bytes is the cache key, so a repeat never pays parse time to find out
whether it may use the cache. The scope-discovery pass (`scan_file_scope`) is
itself a full workbook read, so its result is cached separately by digest
(`rc:planimport:v<version>:scope:<sha256>`, 7 days) and a scope-cache hit skips
that pass entirely: a repeated upload of a large file reads the bytes for the
hash, then hits Valkey and answers without opening the workbook. Scope keys are
case-folded for both repair codes and legal-entity names; an upper-cased code
lookup used to resolve no touched asset on code-based files and silently
dropped the plan part from the scope.

`inspect_plan_file` returns only; the actual import is a separate step.

### Loading the checked file

`POST /plan/import/apply` (admin, multipart `file`, `.xlsb` or `.xlsx`) stages the
validated workbook and returns `202` with a durable `renovation_import` job.
The accepted bytes live under `IMPORT_DIR`; the job payload stores only a
digest, plan id and internal input key. `GET /plan/import/jobs/{id}` polls the
bounded result and `DELETE` cancels it. A supervisor claims the PostgreSQL
lease and starts `src.scripts.import_worker` in a separate Python process, so
XLSB/XLSX parsing and plan recomputation do not share the ASGI interpreter or its
log handler. The child takes the **shared** project advisory gate, streams
batches of 200 and commits the plan writes **and** the terminal job result in
one transaction. A shared lock is compatible with other shared locks: this gate
does NOT serialize the import against normal cell edits. Cancellation/lease loss
therefore rolls the transaction back; a lost HTTP response cannot cause a
second apply. The input is removed only after a terminal job state or TTL
cleanup. Gate A's approved form-blocking policy handles the
import/edit interaction instead of this advisory lock: `GET /plan/import/active`
exposes the active job (FIO + progress) to every member, `usePlanImportLock`
polls it and blocks the form with a red chip, and `update_plan_cells` refuses
writes with 409 while a job is active. A successful import clears the
`renovationPlan/` undo stack in the same transaction: old cell actions would
otherwise replay obsolete values over the imported rows.

**A completed import is history.** Admission deduplicates by
`plan_id:digest`, but imports reuse only a `pending`/`running` job: a repeated
upload while the first load is still running attaches to it, while a file whose
import already finished gets a NEW job and is applied again. Exports keep
reusing a `done` record — their artifact is a valid answer to the same query;
for an import the old run's success is not. Reusing the done import record made
the UI show the previous run's success toast without loading the workbook.

The job reports progress per phase: `parse`, `write`, `recompute`. The client
sends `expected_rows` (the preflight's `new_rows + rows_to_update`), so the
worker's `total` is twice that — one half for the write, one for the recompute
of exactly those rows — and the terminal result overwrites it with the actual
row count. The manager persists a progress change within a second (it polls the
child's in-memory counters, while the 30-second lease still renews every 10 s),
which is what feeds the footer's determinate progress bar: pressing «Загрузить»
closes the dialog, the load continues behind a bar that names the current stage
and states the percentage, centered on the footer itself — the status and the
action bar take equal tracks around it, so it holds the panel's center instead
of the gap their content leaves. Just above `lg` the full action bar needs more
than its half, and the bar gives that width back (it never covers the buttons);
below `sm` it halves to 10em and the status summary collapses to its badge,
while the status lines ellipsize rather than run under the bar on a tight
footer. Only the terminal outcome is a toast. The bar survives a switch
between the project's forms (the same workspace instance) and re-attaches after
leaving and returning to the project: the job id is kept per project in
`sessionStorage` (`front/src/features/renovation/use-plan-import-job.ts`), so a
remount resumes polling until the job settles. The analysis wait before it
keeps its indeterminate bar inside the dialog.

Rows are matched by «Код работы» (`work_or_repair_code`) **inside the target
working plan**: a code present there updates its single matching row in one
set-based `UPDATE ... FROM`; a code absent there — including one that only
exists in an archived version — **appends** a row at the end (picking up the
positional sequence after the current last row). The preflight reads existing
codes from the working plan only, and the report scope carries that plan's id.
A code repeated in the uploaded workbook is refused by the preflight **and**
again by apply (one seen-code set spans all 200-row batches); a code already
duplicated in the target plan is refused by apply with an actionable error that
names the conflicting row ids and positions — no ambiguous overwrite. Apply
re-checks uniqueness inside its own transaction: it never trusts
`expected_rows`, the inspect request or frontend flags. A plan-scoped advisory
lock serializes applies of the same plan and a unique index on the
transaction-local staging table stops a newly seen code from being inserted
twice. A row without the asset value is skipped by the loader with the same rule
the preflight applies; every other blank required cell
is an error and refuses the whole file, so the written counts match the report.

**Owner-approved overwrite semantics (Phase 5):** the file does not patch the
plan — it replaces values. An empty or whitespace-only cell is an explicit
`NULL`, so on an update it CLEARS the old value; only a column absent from the
FILE HEADER keeps its current value. Applied equally to duplicates: every match
gets the same new values. This is a deliberate change from the earlier «blank
means keep» rule and is covered by `test_a_blank_cell_clears_an_existing_value_on_update`.

Existing rows are staged in a transaction-local `TEMP TABLE` generated from the
ORM column types with no constraints and `ON COMMIT DROP` (no migration and no
cleanup policy; commit or rollback removes it). Batches insert into staging;
after the last batch ONE `UPDATE r_plan_rows ... FROM plan_import_stage` applies
all updates — there is no per-row UPDATE round trip. New rows keep the batched
multi-row `INSERT`. A multi-row `INSERT` demands identical keys per row, so every
row is padded to the batch's key union; a key the file did not supply becomes
`NULL` on a created row. The write boundary converts ISO date text to real
`DATE` columns (`work_start_date/025/191/199/203`) and resolves reference labels
to catalog ids before staging.

Reference cells are resolved to the ids of the **live** `r_reference_values`
dictionary — the same rows the preflight (and the «Справочники» editor) sees.
A bulk cell PATCH takes ONE catalog snapshot (`prefetch_reference_ids`, exact
label → id, including deleted rows) and resolves every reference cell in memory
(Phase 6.2); a single-cell undo/redo still uses the one-row lookup. Resolution
stays strict: an unknown label refuses the batch with the same error as before.
Both passes therefore agree: an option the operator adds after the seed is
loadable at once. Reading the seed's static `REFERENCE_OPTIONS` here instead was
a trap — the preflight said «ready» and the load then refused the whole file
(«значение … не найдено в справочнике»). That is the «Собственными силами» case:
the customer's file spells it in the instrumental case while the seed carries
«Собственные силы»; likewise the file's «ОКР» and «ДО» are abbreviations the seed
spells out («Остановочный капитальный ремонт», «Диагностические обследования»),
so the operator adds them as options of «Категория работ» / «Вид работ». Numeric
cells lose their float tail first
(`3.0` → the label `3`). A label no dictionary row carries is a **strict
error**: the preflight lists it under «Значение отсутствует в справочнике» and
the load fails the job with a Russian error and rolls back the whole
transaction.
The same strictness covers blank required cells — «Код работы», «Группа
критичности», «Категория работ», «Вид работ», «Отнесение затрат», «Способ
выполнения» and both work dates — and every blank one is an **error**: the
preflight lists the exact cells under «Не заполнены обязательные значения»
(`missing_required_values`, column letter and sheet row) and refuses the file
(`has_errors`). The apply pass refuses the file too, so a direct call cannot
write a partial plan. The **asset** is the only row-level skip: a row without
«Наименование ДО»/«Предприятие» cannot be attributed to the plan, so it is not
written and its sheet number is listed under «Пропущенные строки»
(`skipped_rows`). That skip is not an error unless every data row lacks the
asset — then the preflight fails (`has_errors`, `all_rows_skipped`) and says «Во
всех строках не заполнено «Наименование ДО» или «Предприятие». Нечего
загружать.». An asset cell that is filled but unknown to the dictionary is a
file error like any catalog label (`unknown_assets`), never a skip.

The load is **not** journaled into undo/redo: it rewrites many rows at once, so
a single «Отменить» cannot meaningfully revert it.

**Resolved by Gate A (2026-11-25):** the shared advisory gate never serialised
the load against cell edits. The form is now blocked for every member while a
job is active (`GET /plan/import/active` + `usePlanImportLock` red chip), the
server refuses writes with 409, and the import clears the plan's Undo history
in its own transaction. A stale editor cannot commit over imported values;
stale cell leases themselves are unchanged and still expire on their TTL.

### Work-code uniqueness rollout (migration 0042 — BLOCKED)

Uniqueness is one code per **plan version**, on the semantic column
`r_plan_rows.work_or_repair_code` (migration 0041 renamed `col_003`). The live
schema still has the non-unique `ix_r_plan_rows_plan_work_or_repair_code`.
Migration `0042_plan_work_code_unique` is authored on the fix branch — it
creates `uq_r_plan_rows_plan_work_or_repair_code` on `(plan_id, work_or_repair_code)`,
has no `IF NOT EXISTS`, and therefore **fails on any existing duplicate**;
`lock_timeout=30s` makes a blocked build fail loudly. It is **not applied by the
author** to dev or prod. The backend tests apply the full chain to a dedicated
throwaway `rc_test*` schema, which is how the index is exercised in CI.

Read-only audit (SELECT only, `SET TRANSACTION READ ONLY`, exit 1 when duplicates
exist, so it can gate the rollout):

    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates
    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates --format json
    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates --plan-id <uuid>
    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates --dry-run

Each group reports `plan_id`, `plan_state`, `work_or_repair_code`,
`duplicate_count`, `row_ids` and `positions` in position order. A code repeated
in *different* plan versions is expected and is not reported. `--dry-run`
prints a commented SQL **template** only; nothing is executed, seeded or
deleted by the script.

Maintainer procedure to unblock the index:

1. Announce a maintenance window and make sure no import job is pending/running
   (`GET /api/renovation/plan/import/active`); the plan form is blocked while one
   runs.
2. Back up the real table before any data change, e.g.
   `pg_dump -n rc -t r_plan_rows -Fc -f r_plan_rows_before_uniqueness.dump <db>`
   (adjust schema/database; verify the dump can be listed).
3. Run the audit and save its JSON. For every `(plan_id, code)` group, inspect the
   named rows and choose the surviving one — the lowest position is a hint, not
   an automatic decision. This is a **data change**, not a schema change.
4. Delete only the reviewed losing row ids, scoped by `plan_id` and
   `work_or_repair_code`, in one explicit transaction; do not renumber positions
   (gaps are allowed and `uq_r_plan_rows_position` stays valid). Fix the underlying
   workbook codes if the rows represent distinct works.
5. Re-run the audit until it exits 0 with zero groups and zero rows involved.
6. With a maintainer approval recorded, run `just migrate`. If it fails on a
   duplicate, the migration transaction has rolled back: stop, clean and audit
   again. Never edit an applied migration. `downgrade()` drops only the index.

### Plan work-code revision rollout (migration 0043 — AUTHORED ONLY)

Migration `0043_plan_work_code_revision` is authored on the fix branch and was
**not executed by the author** against dev or prod; only the disposable
`rc_test*` schema used by the backend tests applies the full chain. It adds
`r_plans.revision INTEGER NOT NULL DEFAULT 0` and three
statement-level triggers on `r_plan_rows` (INSERT, DELETE, UPDATE of
`work_or_repair_code`) that increment that counter using transition tables —
one plan-row update per affected plan per statement, not per row. Existing
plans need no backfill: they start at `0`, and the cache version bump makes any
entry written under the old scheme unreachable. The triggers are in the
database, so no application write path can bypass the invalidation (cell edits,
row create/delete/asset assignment, undo/redo, import apply, recompute and
maintenance scripts all go through them); a recompute that rewrites the same
code value does not bump because the UPDATE uses `IS DISTINCT FROM`.

Rollout order: 0043 is a linear child of `0042_plan_work_code_unique`, whose
own rollout is still blocked by the live duplicate audit above. `just migrate`
on a live DB therefore requires the 0042 maintainer gate first; afterwards 0043
is additive. The connection's `lock_timeout=30s` applies. `downgrade()` drops
the three triggers, the function and the column; no business data is rewritten.
The cache keeps working during rollout: an old backend reading a schema without
the column is impossible once the migration is applied, and the new backend
never serves a pre-bump entry because of the `8:` key prefix.

## Excel export dialog (all projects)

Every workspace uses the same export button and the same HeroUI v3 `Modal`
(`front/src/shared/ui/excel-export-dialog.tsx`) instead of the old hover menu.
Two switches default ON: «Учитывать фильтры и сортировки» (visible view; OFF =
whole table) and «Сохранять значения вместо формул» (OFF = live Excel formulas).
All export requests carry `valuesOnly` (default `true`); computed cells in
«Сырьё» and the renovation plan are written as stored values by default, and
the pinned totals row is static. Formula mode restores the live deviation /
plan formulas, including the plan's derived columns; Логистика deviations
use the previous month's net as an embedded constant operand. КППК currently
has no computed columns, so the switch is accepted but inert there.

After confirmation the job starts immediately; the same button becomes an
enabled «Отменить» while the durable job runs. Cancellation sends `DELETE` to
the job endpoint and aborts local polling/POST (DocMasker pattern), then the
button returns to «Скачать в Excel». Inline exports stay a direct Blob download.

## Renovation plan export jobs
Excel-export plumbing is shared by every project in `back/src/common/`:
`xlsx_stream.py` carries the raw-XML streaming mechanics (static package parts,
the sheet prefix with the frozen pane, cell builders, `merge_cells_xml` with
first-writer-wins range dedup), `xlsx_table.py` renders the standard table
workbook (multi-level `headerPath` headers with merges, live deviation
formulas, datetimes in the shared format, «Да»/«Нет» booleans, dropdown lists
from a hidden «Списки» sheet — the exact semantics the openpyxl renderer had),
and `export.py` carries the request validation (limits, uniqueness, header
shape, file name, per-section allowlists). A project module keeps only its
vocabulary — sheet names, allowed keys, which column references which list —
and calls into here (`src/raw_materials/export.py`, `src/kppk/export.py`,
`src/renovation/export.py`). The renovation PLAN keeps its specialized
sheet-layout writer (the 11-level Excel header, the aggregating band, 59 live
formula columns) built on the same `xlsx_stream`.

### Exported workbook layout

The exported `.xlsx` mirrors the grid exactly: 11 merged header rows, then the
**aggregating band of two rows** — the **number row** (12) carrying each exported
column's workbook ordinal − 1 (so «Предприятие» = 1) and the **totals row** (13)
carrying «Итог» plus a live `SUM` over the exported data rows for every
additive numeric column (the exact range `first..first+row_total−1`; with no
promised rows the totals carry the cached value only, without `<f>`). A column
with no total **merges its number over both rows** (`B12:B13`), exactly like the
grid's number-over-empty cell; a column with a total keeps the two rows separate.
Data rows start dense at row 14: a filtered
export keeps each surviving row's stored plan position in column A, but the
workbook row is a compact output index, so a filter never leaves thousands of
blank rows after the header. The pane freezes the top 13 rows (header + the two
aggregating rows) like the grid.

Excel repair-dialogs («файл повреждён») on duplicate `<mergeCell ref>` or on
`<row>` elements out of ascending `r` order. The row layout is therefore
**canonical**: static parts → header rows → number row → totals row → data rows,
all written into `RenovationWorkbookWriter.__init__` before the first data
batch. The writer also carries a `row_total` **promise** of the data-row count
given by the caller (the job passes `read_plan_export_meta`'s `total`, the
streaming endpoint the materialized `len(plan.rows)`): more rows than promised
fail the write at once, fewer fail at `close()` — both raise
`ExportRowCountMismatchError` («…количество строк плана изменилось во время
выгрузки…»), and the worker deletes its `.part` instead of renaming a broken
file — the totals' live `SUM` needs the exact range, so the count is checked,
not assumed.

The container matters just as much as the XML. `zipfile` on a seekable sink
writes each entry a **draft local header** (zero CRC and sizes) and patches it
by seeking back at `close()`. The download streamer tails the growing `.part`
(see below), so the browser receives the draft bytes and never the patch — the
downloaded archive is born invalid and Excel repair-dialogs it with a bare
«проверка на уровне файла» (a full 520 MB production export hit exactly this).
The writer therefore wraps its sink in `xlsx_stream.UnseekableSink`: the
non-seekable proxy switches `zipfile` to **data-descriptor entries** (bit 3 —
"sizes follow the data"), so every byte is written exactly once, in order, and
the served stream equals the finalized file. It deliberately stays on plain
ZIP, not zip64: a streamed zip64 entry's local header carries an all-zero
zip64 extra field, which real Excel rejects outright (COM-verified: a
descriptor entry without zip64 opens clean, with zip64 fails; the re-zipped
3.5 GB production sheet — 306k rows — opens in ~3 min). CPython 3.14 lowered
`zipfile.ZIP64_LIMIT` from 4 GiB to 2 GiB out of caution, which would push the
~3.3 GiB real sheet into zip64; `xlsx_stream` restores the format's true
4 GiB − 1 ceiling on import. Past that ceiling the write cannot be streamed
safely at all, and `close()` raises `ExportTooLargeError` («Выгрузка
превышает 4 ГБ — Excel не сможет открыть такой файл…») — the job fails with a
user-facing message instead of producing a workbook Excel would repair.
