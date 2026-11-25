# ReportCollector — phased implementation brief

## 1. Mission

You are the implementation agent for **XIIIMICT/ReportCollector**. Your task is to turn the findings from the senior production-readiness audit into a sequence of small, reviewable, tested changes. Work **phase by phase**, not as one large rewrite.

The goal is to improve correctness, maintainability, performance on large renovation-plan documents, and the reliability of background jobs while preserving existing business behavior unless the owner explicitly approves a behavior change.

This document is an implementation brief, not proof that every audit observation is still true. The repository may have changed since the audit. Re-check the current branch, commit, code, tests, migrations, and documentation before acting. Treat audit findings as leads to verify, not as permission to make assumptions.

## 2. Non-negotiable working rules

1. **Inspect before changing.** Read the repository's current `AGENTS.md` and any applicable nested agent instructions. Check the branch, `git status`, current commit, existing migrations, and the relevant tests. Do not overwrite unrelated working-tree changes.
2. **Work in independently reviewable phases.** Before each phase, state its scope and acceptance criteria. Implement it, run the focused tests and relevant broader checks, update documentation, and report the exact result before moving on. Do not silently bundle unrelated refactors and behavior changes.
3. **Do not invent business rules.** Whenever more than one materially different behavior or data policy is plausible, stop at that decision point and ask the project owner. Present concrete alternatives, consequences, and a recommendation. Do not choose one and proceed on the assumption that it is acceptable.
4. **Do not block unrelated work unnecessarily.** If a decision blocks one part of a phase, complete only the independent work that cannot constrain the eventual choice. Clearly mark the blocked work and do not implement a provisional solution that would be hard to reverse.
5. **Preserve data.** Database migrations must be forward-only and safe for existing data. Do not drop, truncate, silently rewrite, or reinterpret production data. Do not edit an already-applied migration; add a new migration.
6. **Preserve behavior during decomposition.** Extract code into better boundaries first, with no intended behavior change. Make semantic/performance changes in separate steps with regression tests.
7. **Do not claim tests passed unless they were run.** Report commands, results, skipped checks, environmental blockers, and any assumptions. Do not replace an unavailable integration test with a claim based only on static inspection.
8. **No speculative infrastructure changes.** The infrastructure/deployment scope is explicitly deferred in this brief. Do not modify `infra/`, Compose files, NGINX configuration, deployment topology, monitoring configuration, or production runbooks.
9. **No premature microservices rewrite.** Keep the application a modular monolith unless the owner later approves a different architecture.
10. **Do not perform an unrequested broad cleanup.** Remove dead code only after proving it is unused, and keep each removal related to an agreed phase.

## 3. Decisions already made by the project owner

These are approved directions. Do not ask the owner to repeat them.

### 3.1 Object storage

- Moving import/export artifacts to S3 or S3-compatible object storage is **deferred to TODO**.
- Keep the current storage behavior for now. Do not introduce an object-storage abstraction, credentials, buckets, or a storage migration in this implementation sequence.
- Document the current local/shared-filesystem limitation accurately so it is not mistaken for multi-node-safe object storage.

### 3.2 Obsolete renovation-plan seeding

- The `just seed-renovation-plan` seeding workflow is no longer needed and should be removed.
- Remove the command, its dedicated script/implementation, and seed-only workarounds that exist solely to make that seed data fit the application. This includes the seed-specific use of `@` to represent values missing from reference dictionaries and seed-specific per-column exception rules.
- Search all references before removal: Justfile, scripts, code, tests, CI, docs, fixtures, and developer instructions.
- Do **not** remove or weaken legitimate production import behavior merely because it shares a helper with the old seeder. First establish whether each special case is seed-only or still required by real Excel imports and normal application flows. Remove only the obsolete seed path and behavior that has no remaining production purpose.
- Remove stale documentation and tests for the seeding workflow. Do not replace it with another seeding mechanism unless explicitly requested.

### 3.3 Import updates at scale

- For bulk updates of existing `r_plan_rows`, use a **staging-table plus PostgreSQL `UPDATE ... FROM`** approach. The expected imports are large; do not leave the main path as a loop issuing one `UPDATE` statement per row.
- Preserve current field semantics exactly unless the owner approves a change. In particular, distinguish an omitted source field from an explicitly empty/cleared value. Existing rows may need omitted fields left unchanged; newly inserted rows may need defaults/nulls. Derive the rules from the current implementation and tests, then lock them down with tests before replacing the update path.
- Prefer a bounded, transactionally safe staging pipeline. The staging load, updates, inserts, and corresponding plan changes must have clearly defined atomicity. Avoid materializing the whole workbook or all changed rows in memory when bounded streaming/batched staging is practical.

### 3.4 Large archive-version comparison

- Full comparison of two complete archived plans is currently not connected to a user-facing workflow.
- Put the expensive full-plan diff redesign/streaming/background implementation in TODO. Do not implement it as part of this work.
- Do not confuse this deferred archive-to-archive comparison with ordinary row queries or any currently used change history/Undo behavior.

### 3.5 Load and stress testing

- A full load/stress-test programme is TODO. Do not add a speculative benchmark suite or claim production capacity has been certified.
- It is acceptable to add focused deterministic regression tests needed for the code changes in this brief. Keep representative multi-user/load certification in the TODO list.

### 3.6 Import while users are editing

- The behavior when an import touches rows that users are concurrently editing is a **mandatory owner decision**.
- Do not independently choose locking, lease revocation, optimistic conflict handling, last-write-wins, import blocking, or undo-history invalidation.
- First trace the existing collaboration leases, row/project advisory locks, row revisions, cell journal, undo records, import transaction, and outbox. Then present the owner with concrete alternatives and trade-offs. Wait for an explicit answer before changing this concurrency policy.
- You may fix unrelated import defects that do not pre-empt the eventual policy, but do not add row-locking/revision behavior to the import path as a supposedly obvious fix.

### 3.7 Formula recalculation after Undo

- The expected invariant is: **formula-derived values must be recalculated after Undo just as after every other supported data mutation**.
- The audit's concern must be re-verified against the actual Undo path. Do not assume recalculation is missing merely because one code path or comment was not obvious.
- Trace the complete Undo request through persistence, value restoration, revision/journal updates, formula dependency handling, recomputation, and websocket/outbox notifications. Add a regression test proving that Undo restores source values and recalculates all affected formulas.
- If the test already passes, retain the behavior and correct any stale/inaccurate documentation. If a real path fails to recalculate, fix that path without changing the expected invariant.

### 3.8 Keycloak session lifetime

- The current Keycloak setup/lifetime is a **demo-environment concern**. Production will use the organization's own session limits/policies.
- Do not redesign authentication, shorten application cookies, add Keycloak session enforcement, or reinterpret the current demo configuration as a production security defect in this task.
- You may correct an objectively contradictory comment/documentation value if it can be done without changing authentication/session behavior. Otherwise record the issue for later review.

### 3.9 Infrastructure, monitoring, and operations

- All infrastructure/deployment/monitoring work is deferred to TODO. Do not modify `infra/` or deployment configuration in this task.
- This includes, but is not limited to: NGINX upload/body limits and timeouts; Compose port exposure and networks; health/readiness wiring; metrics exposure and multi-process scraping; container TLS/build settings; worker/API process topology; production storage mounting; deployment changes for separate workers; backup/restore setup; operational monitoring and alerting.
- Production runbooks and operational procedures are also TODO. Do not write or modify production runbooks now.
- You may add concise TODO entries that accurately capture the deferred items and their dependencies. Do not make infrastructure changes under the guise of fixing an application issue.

### 3.10 Input and resource limits

- Review and tune file/document/API resource limits **last**, after the functional and structural work.
- The deployment is intended for a corporate environment and legitimate source files may be very large. Do not introduce conservative or arbitrary limits that reject realistic employee workbooks merely because a theoretical abuse case exists.
- At the final limits phase, inspect actual parser/database/memory behavior and existing operational constraints. Propose generous, configurable ceilings that reject only unreasonable inputs while still protecting against pathological archive expansion, pathological workbook structure, unbounded issue reports, and accidental resource exhaustion.
- Do not guess business thresholds. If the available evidence does not establish appropriate ceilings, present the proposal and ask the owner before enforcing them.
- Do not change NGINX or other infrastructure-level request limits; those are deferred under §3.9.

### 3.11 Ownership of the `r_plan_rows` naming dictionary

- The project owner explicitly wants **ChatGPT/the audit author to propose every replacement field name**, rather than delegating the naming decisions to the implementation agent.
- The implementation agent must not invent, infer, or independently finalize the field-name mapping. It must consume the complete, owner-visible mapping dictionary supplied with this brief (or a later revision of it).
- The complete ChatGPT-authored mapping is now embedded in **§9.1**. It covers all 216 physical `col_***` columns in `r_plan_rows`, retains the 11-level frontend header hierarchy, and puts `col_218` at its actual non-terminal position 31.
- The frontend order is based on the current single renovation-plan grid form (`renovation-form.json`), with sibling order preserved at every nested header level. Group/header cells are not database fields.
- `col_002` is intentionally excluded from the database mapping: it is represented by `asset_id` and the synthetic frontend `__assetName` column, not a physical `r_plan_rows.col_002` column.
- Names in §9.1 are ChatGPT's proposed canonical English `snake_case` identifiers. The implementation agent must use them exactly; it may not invent a different mapping. If the source has drifted, stop and report the exact difference.
- The dictionary flags observed source metadata anomalies (`col_083`, `col_198`, `col_199`, and the historical reuse of `col_218`). Preserve current types/behavior during a name-only migration and ask the owner before any separate semantic/type correction.
- **Hard gate:** the agent must run the mechanical coverage/order/path checks listed in §9.1 and report the result before coding. The owner-visible mapping is supplied here; do not block Phase 3 merely because it was not agent-authored.

## 4. Important owner-decision gates

The following issues must not be silently resolved by the agent. They may be discovered during another phase; when that happens, record the evidence and ask before implementing the relevant behavior.

### Gate A — Import versus active collaborative edits

The current audit found that the project-level shared advisory lock used by import is compatible with other shared locks; it does not, by itself, serialize imports against normal row edits. The normal cell-edit path uses row locking/leases/revisions/journaling, while the import path may bypass some or all of those mechanisms.

After verifying the current code, present alternatives such as:

- **Block or defer the import** while affected rows have active edit leases. Explain how leases expire, how the user is notified, and whether the import is retried.
- **Optimistic conflict detection** using row revisions/snapshots. Explain whether a conflict rejects the whole import or only affected rows, and how users resolve conflicts.
- **Explicit serialization** of import and editing using a suitable exclusive lock/maintenance window. Explain the impact on collaborative editing and long-running imports.
- Another solution, if the current architecture suggests a better option.

For each option, explain correctness, user experience, implementation cost, failure/recovery behavior, and interaction with Undo. Recommend one, but do not implement it until the owner chooses.

### Gate B — Renovation row-code uniqueness scope

The audited preflight code appeared to load existing `col_003` values across all `RenovationPlanRow` records, not just the target plan. This may mean code uniqueness is global across all plan versions, or it may be an accidental over-broad query.

Ask the owner whether the code must be unique:

1. across the entire database/all plans and archived versions;
2. only within one plan/version; or
3. under another explicit business scope.

Do not change the query scope, add a unique constraint, or make historical records block a new import until this is answered. Explain the effect on importing a new version from an older plan.

### Gate C — Duplicate codes inside one uploaded workbook

Verify whether the uploaded workbook can contain repeated business codes and what the current parser/apply path does with them. Ask whether duplicates should:

- reject the entire import;
- reject only duplicate rows and report them;
- be resolved by a defined first/last-row rule; or
- be allowed for a documented business reason.

Do not silently rely on iteration order or last-write-wins. Do not add a database uniqueness constraint until Gates B and C have been answered and the intended scope is clear.

### Gate D — Physical versus logical PostgreSQL column order

The owner wants `r_plan_rows` fields renamed from `col_***` to meaningful English names and arranged in the same order as the frontend currently displays them, including the non-terminal position of `col_218`.

Derive the canonical order from the actual frontend display configuration; do not assume numeric `col_001`…`col_218` order. The complete replacement-name mapping must be authored by ChatGPT and supplied in this brief; the implementation agent only verifies it against the checkout and implements it. Include legacy field, new English `snake_case` name, displayed label, and display position.

A PostgreSQL column rename does not reorder the physical columns in the table. The recommended default is to make the **logical schema/model/projection order** match the frontend order, while renaming the existing columns in place and preserving data. Do not rebuild/copy the entire table merely to change PostgreSQL's physical attribute order.

If the owner specifically requires literal physical catalog/tuple order (for example, `SELECT *` in psql must show that order), stop and ask. Explain the table-rebuild migration, lock/downtime, disk-space, foreign-key/index/trigger, and rollback implications before proceeding.

### General decision rule

Ask the owner whenever a choice changes business semantics, stored-data meaning, uniqueness, conflict resolution, user-visible workflow, public/API compatibility, or requires a materially different migration strategy. A question must include:

- the verified current behavior and relevant code locations;
- the available options;
- pros, cons, and failure modes;
- your recommended option and why;
- what work is blocked by the decision.

Do not ask about routine, reversible implementation details that are already determined by this brief. Do not ask again about decisions already made in §3.

## 5. Required implementation sequence

Follow this sequence unless repository evidence reveals a dependency that makes a different order materially safer. If you need to change the order, explain why before doing so.

### Phase 0 — Re-establish the baseline and create a verified work plan

1. Read root and nested `AGENTS.md` files and the current architecture, workflow, collaboration, import/export, database, and TODO documentation.
2. Record branch, HEAD commit, working-tree state, Python/Node/package-manager versions, PostgreSQL/Valkey versions used by tests, and relevant migration head.
3. Inspect the current implementation of the audit's key areas, especially:
   - `back/src/common/job_repository.py` and durable import/export job managers;
   - `back/src/main.py` lifespan/task startup;
   - `back/src/renovation/router.py`, `import_plan.py`, `import_rows.py`, and `import_apply.py`;
   - `back/src/renovation/service.py`, `plan.py`, and related query/facet/formula code;
   - `back/src/collab/locking.py`, persistence, Undo, leases, journal, outbox, and websocket paths;
   - `front/src/shared/api/backend-proxy.ts`, the renovation API client, and frontend column display definitions;
   - migrations and tests covering `r_plan_rows`.
4. Verify each audit observation against current code. Mark it `confirmed`, `not reproduced`, `already fixed`, or `needs owner decision`.
5. Identify the existing test commands and run a baseline focused test set before changing code. Record pre-existing failures separately.
6. Create a concise phase checklist and a decision log. Do not start a broad implementation while the baseline is unknown.

**Exit criteria:** the owner can see the verified findings, proposed sequence, baseline test status, and any early business-rule questions. No implementation changes are required in this phase beyond an agreed plan/checklist.

### Phase 1 — Remove the obsolete renovation-plan seeding workflow

1. Search for `seed-renovation-plan` and all seeder references.
2. Identify seeder-only code paths, special `@` handling, special per-column rules, fixtures, and docs.
3. Confirm that each candidate is not required by real user imports or normal plan editing.
4. Remove the old Just command and seeder implementation, associated seed-only workarounds, and stale tests/docs.
5. Preserve production import semantics and reference-dictionary validation.
6. Run all import parsing, dictionary resolution, and renovation-plan tests. Search again to prove there are no dangling references.

**Exit criteria:** no supported command or documentation advertises the obsolete seeder; no seed-specific exception remains in production code; normal Excel import behavior is covered and unchanged.

### Phase 2 — Correct and clarify durable job concurrency contracts

1. Verify the current `JobRepository.claim` implementation and how import, export, and query-export managers pass concurrency limits.
2. Fix the confirmed cross-kind counting problem: a per-kind concurrency limit must count only jobs of that kind. If a global active-job cap is also required, model it as a separate explicit limit rather than accidentally comparing all kinds against one manager's per-kind limit.
3. Preserve atomic job claiming, lease expiration/recovery, fencing-token semantics, and protection against stale workers completing or publishing results.
4. Add regression tests with mixed active job kinds proving that one kind cannot consume another kind's per-kind slots, and tests for any separately configured global cap.
5. Inspect old in-memory job managers and duplicated job abstractions. Remove an obsolete implementation only after a repository-wide reference/test search proves it has no callers. Extract genuinely shared job contracts/errors from export-specific modules if that reduces cross-domain coupling, without changing state semantics.
6. Keep worker process separation and deployment topology deferred under §3.9. Do not modify `back/src/main.py` startup architecture or deployment files as part of this phase unless a purely code-level fix is necessary and does not change deployment topology; ask first if that boundary is unclear.

**Exit criteria:** per-kind and global concurrency are distinct, deterministic, and tested; lease/fencing behavior remains intact; no dead manager is removed without evidence.

### Phase 3 — Rename `r_plan_rows` fields to meaningful English names and canonical frontend order

This is a high-priority schema modernization task. Treat it as a cross-cutting migration, not a cosmetic rename in one model file. The replacement names are to be authored by ChatGPT and supplied in this brief; the implementation agent must not invent them.

1. Find the authoritative frontend column display configuration. It may contain several nested levels (for example: view/tab → section → subgroup → leaf column). Traverse the tree recursively, preserving the configured sibling order at every level. Record the complete hierarchy path and depth-first leaf position for every displayed column. Include hidden and conditionally displayed leaf columns; visibility must not cause a field to disappear from the dictionary. Determine the distinct grid views/forms if they have separate column trees. Do not infer order from the old numeric suffix.
2. Build a structural inventory from the frontend tree before naming anything. Distinguish group nodes from actual data-column leaves. Resolve each leaf's data/property binding to its `r_plan_rows` field. Preserve repeated presentation of the same field in separate views as separate presentation entries, but keep only one canonical database mapping for that field. Also inventory every `col_***` field in the ORM/database model that has no frontend placement.
3. Consume the complete **ChatGPT-authored field-name dictionary in §9.1**. It contains one row for every actual `col_***` field in `r_plan_rows`, with the Russian leaf label, full nested header path, form position, form type, and new English `snake_case` name. The dictionary is the naming source of truth; do not replace it with an agent-generated proposal. Verify its source snapshot before implementation.
4. Before coding, verify the dictionary mechanically against the current ORM model, database schema, and every frontend column tree. Report any missing, extra, duplicated, multiply-bound, or mismatched legacy columns. If anything does not reconcile, stop and ask for clarification rather than silently editing the dictionary.
4. Apply the naming rules below consistently:
   - Name fields by business meaning, never by their old `col_###` number or display position.
   - Use `_id` for identifiers/foreign keys, `_date` for calendar dates, `_at` for timestamps, `_count` for counts, `_amount` for monetary values, and explicit unit suffixes such as `_tons`, `_kg`, or `_percent` where supported by the actual semantics.
   - Use `is_`/`has_` for booleans. Prefer singular nouns for one value and plural nouns only for actual collections.
   - Keep related concepts lexically consistent (for example, planned vs actual, start vs end, quantity vs amount). Distinguish similarly named fields by their real business role.
   - Avoid vague names (`value`, `data`, `field_17`), unexplained abbreviations, transliteration, overloaded terms, and reserved SQL words. Do not encode presentation-only details in the database name.
   - Preserve the actual unit, precision, nullability, and reference semantics; never infer a unit solely from a Russian label if code/formulas/import data show otherwise.
5. Include every `col_***` column belonging to `r_plan_rows`, including hidden, conditionally displayed, formula-derived, or rarely used fields. Keep the logical model/schema/projection order equal to the frontend order.
6. Rename columns with safe forward migrations, preferably in-place `ALTER TABLE ... RENAME COLUMN` operations where supported. Preserve all existing row data, IDs, primary/foreign keys, constraints, indexes, defaults, and relevant database comments. Rename associated constraints/indexes only where it improves clarity and can be done safely; do not rebuild the table just to rename it.
7. Update every reference to the old names across:
   - SQLModel/SQLAlchemy models and repository/service queries;
   - migrations and migration tests (never rewrite applied migration history);
   - API schemas, serialization, filters, sorting, facets, exports, formulas, validation, dictionaries, audit/change history, and collaboration payloads;
   - frontend column definitions, grid renderers, editors, filter/sort state, saved views, import mapping, and tests;
   - documentation, scripts, fixtures, and test data.
8. Search dynamically constructed column references and strings, not only static `col_` identifiers. Pay special attention to JSON keys and persisted user preferences/saved filters that may contain legacy names.
9. Decide and test how backward compatibility is handled for any persisted or external API payload that may still contain `col_***`. Do not silently break saved user state. If a compatibility mapping or data migration is required, implement it explicitly; if the supported compatibility window is a business/product decision, ask the owner.
10. Make the **logical** model/schema/projection/display order match the form positions in §9.1, including `estimate_number` (legacy `col_218`) at position 31. PostgreSQL physical attribute order is not changed by column renames. Do not rebuild the table merely to change physical attribute order; only do so after presenting the costs and obtaining explicit approval.
11. Add migration tests that start from the previous schema, apply the migration, and verify data and important schema objects. Add application tests proving the grid, filtering, sorting, import/export, formulas, and saved state still work with the new names.
12. Search for every legacy `col_***` reference after migration. Any remaining use must be deliberate and documented, such as a legacy import-file position mapping or a compatibility decoder—not an accidental internal schema dependency.

**Exit criteria:** the database model and application use the exact approved readable names; every field maps to the correct frontend column and order; existing data is preserved; `col_218` is in its real frontend position; migrations and compatibility behavior are tested and documented.

### Phase 4 — Decompose large modules before or alongside subsystem changes

Decomposition is a major maintainability goal, not a documentation-only exercise. Apply it incrementally. Where a subsystem is about to receive a substantial behavior change, extract the current behavior into appropriate modules first, then implement the change in the new boundary.

Priority areas identified in the audit include:

- `back/src/renovation/service.py` (very large; queries, mutations, formulas, dictionaries, imports/exports and unrelated concerns are mixed);
- `back/src/renovation/router.py` (large router with several workflows);
- `front/src/shared/api/renovation.ts` (large feature API client);
- common durable-job code that currently imports generic errors/constants from export-specific modules.

Guidelines:

1. Choose boundaries based on responsibilities and dependency direction, not arbitrary line counts. Likely areas include plan reads/queries, facets, cell mutations, formulas/recalculation, dictionaries/reference resolution, import orchestration, export orchestration, and route groups.
2. Keep public service/router contracts stable while moving implementations. Use a small façade/re-export layer temporarily if needed to avoid a risky all-at-once update.
3. Avoid circular imports and avoid creating a new generic framework for a single caller.
4. Keep database transaction ownership clear. A split must not accidentally move queries outside a transaction, extend a session through CPU-heavy parsing, or change locking boundaries.
5. Preserve authorization checks at the route/service boundary. Do not accidentally make an internal helper callable without the same authorization guarantees.
6. Split frontend API modules by feature/workflow, with shared transport/error handling in one place. Keep runtime request/response shapes unchanged during pure extraction.
7. Add or retain tests around each extracted public contract. Make extraction-only changes separately reviewable from behavior changes.
8. Update package/module documentation and nested `AGENTS.md` instructions where they materially help agents work in `back/`, `front/`, `infra/`, and `docs/`. Do not proliferate instructions that simply duplicate the root file.

**Exit criteria:** the largest modules have clear, named responsibilities and smaller entry points; dependency direction and transaction ownership are documented; extraction has not changed behavior; tests remain green. Do not impose an arbitrary maximum line count as the only success metric.

### Phase 5 — Make large-file imports efficient and transactionally correct

Re-verify the current import path first. Implement the agreed staging-table update strategy while preserving current user-visible import semantics.

#### 5.1 Replace per-row updates with staging plus `UPDATE FROM`

1. Trace the parser output, preflight validation, matching of uploaded rows to existing plan rows, batch flush, insert/update behavior, job progress, and transaction boundaries.
2. Design a staging representation containing the target row identity/matching key and normalized imported values. It must also represent which source fields were actually supplied, so omitted values are distinguishable from explicit empty values.
3. Load staging data in bounded batches using the database driver/SQLAlchemy facilities actually available in this project. Keep the staging operation and target mutations on the correct transaction/connection. Prefer a temporary staging table or another well-scoped staging mechanism; explain any persistent staging choice and its cleanup/recovery behavior.
4. Apply updates with PostgreSQL `UPDATE target ... FROM staging ...`, not a Python loop issuing one UPDATE per row. Group or encode changed-field masks so omitted source cells do not overwrite existing values. Do not generate a huge SQL statement with thousands of parameters if a staged relational update is more appropriate.
5. Insert new rows from staging in set-based/batched SQL where practical. Preserve generated identifiers, ordering/positions, plan ownership, defaults, validation, and all existing import semantics.
6. Validate duplicate matching keys before applying mutations, but do not choose the business policy for duplicate business codes until Gates B and C are answered. Avoid nondeterministic `UPDATE FROM` joins where multiple stage rows can match one target row.
7. Preserve atomicity: a failed import must not leave a partly applied plan unless the current product explicitly supports partial application and the owner confirms that behavior. Preserve correct durable-job states and error reporting.
8. Add integration tests for large batches, mixed inserts and updates, omitted versus explicit blank fields, repeated target matches, transaction rollback, and job retry/fencing behavior.
9. Add a test or query-level assertion showing that existing-row updates are set-based and do not issue one UPDATE roundtrip per imported row.

#### 5.2 Avoid holding database resources during CPU-heavy parsing

- Inspect the lifecycle of the async DB session/connection used by preflight while parsing runs through `asyncio.to_thread` or another executor.
- Load the minimal consistent reference-data snapshot needed for validation, then release the database session/connection before CPU-heavy workbook parsing when possible.
- Ensure the snapshot semantics are explicit; do not introduce inconsistent dictionary reads midway through a parse.
- Add tests for session/connection cleanup on success, validation errors, cancellation, and exceptions.

#### 5.3 Reduce unnecessary workbook passes and memory use

- Verify the current file-hash, scope scan, inspect, cache-hit, and apply parse sequence.
- Avoid repeated full workbook reads when the same normalized information can safely be retained or staged. Do not cache large parsed workbooks in unbounded process memory.
- Keep cache keys correct: file content hash plus all behavior-affecting input parameters, including plan/target context and any reference/deobfuscation keys or modes that affect results. Include a cache/schema version and invalidation/version mechanism where the existing cache design requires it.
- Preserve Valkey as disposable cache/coordination state, not the source of truth. Do not store unique business state solely in Valkey.
- Verify cache-hit behavior cannot cause a preflight result for one set of parameters to be reused for another.

#### 5.4 Stream upload proxying where safely supported

- Inspect `front/src/shared/api/backend-proxy.ts`, current Next.js request semantics, and backend multipart handling.
- The audited path appeared to read the complete uploaded request into a Node `Buffer` before forwarding it. Replace this with a streaming, backpressure-aware proxy if supported by the actual framework/runtime, including abort propagation and correct multipart headers/boundary handling.
- Do not trade away request validation or error propagation. Add tests for large upload streaming, client abort, backend error, and content-length absence where applicable.
- If the supported runtime cannot safely stream this request, explain the limitation and present alternatives rather than implementing a fragile workaround.
- Do not change NGINX limits or Docker/deployment settings; those are deferred.

#### 5.5 Revisit full-table existing-code lookup only after owner decision

- The preflight appeared to build an in-memory set of existing business codes across all plans. This has both a possible performance problem and a business-scope ambiguity.
- Ask Gate B before changing its scope or adding constraints. Once answered, implement an appropriately indexed, bounded query for the approved scope and add tests for archived/new plan behavior.

#### 5.6 Preserve and improve large-export behavior

- Preserve the existing strengths of the export path: durable job state, lease/fencing protection, streaming database reads, streaming XLSX generation, and publication of an artifact only after successful completion.
- Verify that partial/failed/stale attempts cannot replace a successfully completed artifact. Add regression tests if coverage is missing.
- The current inline-versus-background decision appeared to use row count alone. Review whether a better estimate should include row count, selected column count, and a realistic estimate of cell/value size. Large, wide plans can be expensive even when the row count is modest.
- If changing the inline threshold affects user-visible behavior or requires a product-level latency target, present the options and recommendation to the owner before choosing thresholds. Do not tune it by guesswork.
- Preserve the XLSX compatibility ceiling (the audit noted an intentional approximately 4 GiB limit). Do not bypass it. If a supported export can exceed it, present options such as a clear actionable rejection or splitting into multiple workbooks/files and ask the owner before changing product behavior.
- Artifact retention, cleanup, disk quotas, and storage lifecycle are deferred with storage/operations work. Do not add an unreviewed deletion policy that could remove user-visible exports.

**Exit criteria:** existing streaming and fencing guarantees remain covered; inline/background selection is evidence-based or explicitly left unchanged; oversized export behavior is explicit; no artifact-retention policy is introduced without approval.

**Exit criteria:** bulk updates use staging and `UPDATE FROM`; large imports avoid per-row SQL update loops; omitted/blank semantics and atomicity are tested; CPU parsing does not unnecessarily hold DB connections; upload forwarding streams safely where supported; unresolved uniqueness policy remains explicitly blocked rather than guessed.

### Phase 6 — Improve SQL query and bulk-edit performance

Use actual query shapes, representative test data, and `EXPLAIN (ANALYZE, BUFFERS)` where safe. The audit's saved query plans are historical snapshots, not proof of current latency. Do not make performance claims without measurements.

#### 6.1 High-cardinality facets

- Audit `read_plan_facets` and all facet implementations. The audit found that a special field had a database-side grouped path while other fields could materialize all distinct values in Python; one field appeared to have roughly 90,000 distinct values in the saved dataset.
- Push grouping, filtering, search, sort, pagination, and counts into PostgreSQL for high-cardinality facets.
- Preserve current behavior for case-folding, numeric-aware sorting, null/empty values, timezone/date semantics, and applying preceding filters. Verify exact UI semantics before replacing Python behavior.
- Add regression tests for ordering, pagination, search, filter combinations, nulls, and high cardinality. Avoid loading all distinct values into Python merely to return one page.

#### 6.2 Reference-field N+1 queries in bulk cell updates

- Audit `_coerce_cell`/reference resolution in bulk `update_plan_cells`.
- If each reference-valued cell triggers its own lookup, batch-prefetch mappings by reference catalog and normalized label/value, then resolve cells in memory from those bounded maps.
- Preserve current ambiguity, missing-reference, normalization, and validation behavior. Add tests with many cells referencing repeated and distinct dictionary values.

#### 6.3 Repeated row counts and aggregate cost

- Verify how often a page/window request computes both filtered and full-plan counts, and inspect current query plans on representative data.
- First consider reducing duplicate work in SQL/query structure and avoiding unnecessary exact totals where the API/UI does not require them.
- Do not add a naive time-only Valkey TTL cache for counts. A cache is acceptable only with a clear, transactionally correct invalidation/version strategy covering every mutation path.
- If a plan `data_revision`/version counter is proposed to key aggregate caches, inventory every mutation source first: imports, cell edits, Undo, formulas/recompute, plan lifecycle operations, and any scripts. Present the cross-cutting consistency design to the owner before adding it if multiple materially different designs exist.
- Measure before and after. Keep exact-count behavior if users depend on it unless the owner approves a UX/API change.

#### 6.4 Deep pagination for sorted rows

- The audit found that default-order reads could use keyset pagination while sorted reads appeared to fall back to OFFSET.
- Measure and inspect the actual frontend paging contract. If keyset pagination can be introduced without changing user-visible behavior, implement a cursor that includes all sort values, null ordering, sort direction, and a stable unique tie-breaker.
- If the current grid requires arbitrary page-number jumps or a keyset cursor would change API/UI semantics, do not silently switch it. Present options (retain OFFSET, cursor navigation, materialized ordered IDs, or another approach) and ask the owner.

#### 6.5 API result and filter limits

- Defer changing maximum page sizes, filter counts, sort counts, row-ID list sizes, facet limits, and issue-report limits to the final limits phase (Phase 9 below). Do not tighten them in this phase.

#### 6.6 Archive diff

- Do not implement full archived-plan diff here; it remains TODO under §3.4.

**Exit criteria:** confirmed high-cardinality facet and reference lookup bottlenecks are addressed; count/pagination changes are measured and preserve product semantics; any API/UX trade-off is presented to the owner before implementation.

### Phase 7 — Collaboration, Undo, and formula consistency

1. Trace the current edit transaction end-to-end, including row/project advisory locks, Valkey leases, optimistic revisions, cell journal, Undo, formula recomputation, and outbox/websocket publication.
2. Verify the semantics of project shared/exclusive locks. Do not describe a shared lock as mutually exclusive with another shared lock; it is compatible with other shared locks and only conflicts with an appropriate exclusive lock.
3. Verify every supported source-data mutation path and whether it increments the appropriate revisions and writes the expected journal/outbox records. Do not change import concurrency policy until Gate A has an explicit owner answer.
4. Verify and test the formula-after-Undo invariant from §3.7. Add a focused regression covering a formula that depends on an edited cell, then Undo, and assert the restored source value and recalculated derived value are both visible through the appropriate API/websocket state.
5. Check that Undo history cannot silently replay an operation against incompatible newer data. This is especially important for imports, but any policy that changes behavior must be coordinated with Gate A.
6. Correct inaccurate comments and `docs/COLLAB.md` descriptions of the shared project gate and import/edit interaction.
7. After the owner chooses Gate A, implement the selected conflict strategy with tests for concurrent edit/import, expired leases, worker retry, rollback, and Undo consistency. Do not implement a different strategy “for safety” without approval.

**Exit criteria:** actual lock semantics and Undo/formula behavior are proven by tests and documented accurately; import-versus-edit policy is implemented only after explicit owner approval.

### Phase 8 — Update agent-facing and developer documentation

Documentation must describe the implemented system, not aspirational architecture. Update it after the relevant code phase, rather than writing claims ahead of implementation.

1. Reconcile documented dependency versions with the actual package manifests and lockfiles. In particular, verify the documented RevoGrid version against `front` dependencies/overrides and verify FastAPI against `back/pyproject.toml` and the lockfile. Do not copy a version from a broad range without identifying the resolved version.
2. Reconcile `.mcp.json` server count/names with `AGENTS.md` and actual configuration.
3. Fix contradictory environment comments such as a stated 12-hour session default when the actual code/default says otherwise, without changing authentication behavior.
4. Remove or update stale TODO items. The audit noted that import-job UI reattachment after reload may already be implemented and tested; verify the current hook/tests before retaining that TODO.
5. Correct collaboration documentation so it does not claim that a shared project advisory lock serializes two shared-lock operations.
6. Document the approved schema field mapping and canonical frontend order. Keep one clear source of truth or document how the frontend/backend mappings are kept synchronized.
7. Improve the root `AGENTS.md` as an index to subsystem-specific instructions. Add scoped `AGENTS.md` files only where they provide genuinely useful backend/frontend/docs guidance; avoid duplicating large sections.
8. Split oversized documentation by concern where that improves discovery and reduces contradictions. A reasonable structure could include architecture/system, database, data jobs, import, export/storage, collaboration, domains, and security/operations. Adapt to the existing tree rather than mechanically creating empty folders.
9. Add concise architectural invariants, clearly distinguishing current guarantees from planned work. At minimum cover:
   - PostgreSQL is the source of truth for business state;
   - Valkey is disposable cache/coordination/pub-sub state, not unique business storage;
   - job lease/fencing rules prevent stale workers from committing/publishing;
   - import atomicity and field-update semantics;
   - export artifacts become visible only after successful completion;
   - authorization is enforced server-side;
   - collaborative edits detect stale/conflicting values according to the approved policy;
   - filtering occurs before pagination;
   - applied migrations are immutable.
10. Document the actual development/agent workflow: inspect the data path (UI → API → service → repository → database/cache/job), inspect authorization and transaction/locking/revision/outbox semantics, add a failing regression test, implement the smallest safe change, run focused and broader checks, and update the source-of-truth docs.
11. Do not state that TODO work is implemented. Mark deferred items explicitly, including S3, load testing, infra/monitoring, production runbooks, full archive diff, and final limit policy.

**Exit criteria:** version/configuration references are accurate; stale TODOs and false concurrency claims are corrected; architecture invariants distinguish implemented from planned behavior; large code and docs areas have discoverable ownership guidance.

### Phase 9 — Final-priority resource and document limits

Do this only after Phases 1–8 and the owner decisions that affect import behavior.

1. Inventory limits across workbook parsers, ZIP/XLSX/XLSB processing, upload routes, import issue reports, API pagination, filter/sort arrays, and export generation.
2. Classify each limit as:
   - a genuine safety/resource guard;
   - a product/business limit;
   - an implementation default that was never justified;
   - an infrastructure/proxy limit (deferred; do not change here).
3. Derive generous defaults from realistic corporate workbooks, observed memory/CPU use, parser behavior, and database capacity where measurable. Do not assume files are small. Do not set a value merely because it is a round number or a familiar framework default.
4. For archive-based formats, consider total uncompressed size, number of archive entries, compression ratio, worksheet count, row/column count, cell/text size, formula/XML complexity, parsing time, and issue-report growth. Keep thresholds high enough for realistic files while still preventing pathological expansion/resource exhaustion.
5. Make tunable limits explicit and document their units, defaults, and failure messages. Prefer clear actionable errors over obscure parser/database exceptions.
6. For API result sizes and issue reports, preserve the normal large-plan workflows. Introduce pagination/streaming rather than an artificially small hard cap when that is the better fit.
7. Ask the owner before enforcing any threshold for which there is no evidence of a safe business maximum. Do not change NGINX/Compose/proxy settings; those remain deferred.
8. Add boundary tests just below and above each approved threshold.

**Exit criteria:** limits are explicit, configurable where appropriate, evidence-based, permissive for realistic large corporate files, and tested. No infrastructure limit has been changed.

## 6. Deferred TODO register

The deferred items (object storage, load/stress certification, infrastructure,
Gate A/B/C policies, final limits, etc.) live in **`docs/TODO.md`**, section
«Deferred — implementation brief», with their descriptions, dependencies and
reasons. They were moved there so this brief stays an actionable implementation
sequence. Do not implement them in this task and do not duplicate them here.

## 7. Test and verification expectations

Use the repository's actual supported test commands. The audit observed a CI baseline that included PostgreSQL-backed backend tests, Valkey/MemoryStore coverage, backend coverage enforcement, frontend typecheck/lint/format/unit tests, production-image/browser smoke checks, migration tests, and secret scanning. Verify the current CI configuration rather than assuming it is unchanged.

For each phase:

- Run the narrowest tests covering the changed behavior first.
- Run relevant backend/frontend type checks, lint, formatting, and unit tests.
- Run PostgreSQL integration/migration tests for query, transaction, staging, schema, and locking changes.
- Add regression tests for every confirmed defect before or with its fix.
- Check migration upgrade from the previous schema; verify that existing data and critical indexes/constraints survive.
- For frontend-facing schema/API changes, test the actual column ordering and user state persistence, not just a type check.
- Do not claim capacity or production-readiness from ordinary unit tests. Load/stress certification remains TODO.

Specific regression targets include:

- mixed import/export/query-export job concurrency;
- durable job lease expiry, retries, fencing, and stale worker completion;
- seed-only `@` and per-column exceptions removed without breaking real import validation;
- staging updates for large batches, including omitted versus blank values and rollback;
- upload streaming, backpressure, client cancellation, and error propagation;
- SQL-side facet search/sort/pagination for high-cardinality fields;
- batched reference resolution without N+1 queries;
- frontend field mapping/order, including `col_218`'s actual displayed position;
- migration preservation of existing `r_plan_rows` data and related schema objects;
- Undo restoring source values and recalculating dependent formulas;
- concurrent import/edit behavior after the owner has selected a policy.

## 8. Completion report format

At the end of every phase, report:

1. **Status:** completed, partially completed, blocked by owner decision, or not started.
2. **Changes:** the important files/modules/migrations changed and why.
3. **Behavior:** what changed for users/data and what was intentionally kept unchanged.
4. **Tests:** exact commands and results; list skips and failures honestly.
5. **Migration/data safety:** migration behavior, compatibility handling, and any operational prerequisites (without modifying deferred infrastructure).
6. **Decision gates:** the exact questions needing the owner's answer, with options and recommendation.
7. **TODO updates:** deferred items added/updated.
8. **Next phase:** the next bounded phase and its acceptance criteria.

Do not claim that the overall project is production-ready merely because the implementation phases pass. Production capacity, deployment readiness, monitoring, backup/restore, and runbooks remain unverified/deferred until those separate workstreams are completed.

---

## 9.1 Canonical `r_plan_rows` field-name dictionary (ChatGPT-authored)

**This dictionary is complete for the supplied source snapshot** (`dev` archive commit `e806dca2a4e8c51191345ed60abfa3a89ed61a97`, exported 2026-11-23). It is an explicit implementation input, not a suggestion for the coding agent to rename independently. The implementation agent must verify that the checkout being modified still matches this schema before applying it. If the form or model has changed, stop and report the exact drift rather than silently editing this dictionary.

### Source interpretation and counts

- The authoritative form is `back/src/renovation/form_data/renovation-form.json`; its `headerRows` and `headerCells` encode **11 nested header levels**. The canonical order is the depth-first leaf order represented by `fields[].position` and used by `back/src/renovation/form_schema.py::field_header_paths()`.
- The frontend renders this renovation plan as an 11-level hierarchical header overlay (`front/src/features/renovation/renovation-grid.tsx`), rather than a flat list of independent columns. The `Full frontend header path` column below is generated using the same algorithm as `field_header_paths()`; it preserves the nested group chain for each leaf.
- The form JSON contains 217 form fields (`col_002` through `col_218`), but **`r_plan_rows` has exactly 216 physical `col_***` columns**: `col_003` through `col_218`. `col_002` (“Предприятие”) is not a physical database column. The row's persisted relation is `asset_id` (`r_assets.id`), and the grid displays the asset through its synthetic `__assetName` column. Do not create or rename a `col_002` database column; do not rename `asset_id` as part of this task.
- Every physical `col_***` field in the supplied `RenovationPlanRow` model appears exactly once below. Rows are ordered by the actual form position, **not** by sorting the legacy field suffix. In particular, `col_218` is `estimate_number` at display position 31, between `col_031` and `col_032`; the final displayed field is `col_217` (`note`).
- Position values below are the 1-based positions from the form's field layout / header number strip. `col_002` occupies source position 1 but is represented by the synthetic enterprise/asset column, so the first persisted `col_***` field is at position 2.
- `Form type` is copied from `fields[].cellType`; catalog details are shown for reference fields. It is not an instruction to change SQL types, precision, nullability, or validation.

### Naming conventions used

- English `snake_case`; names encode business meaning, not legacy ordinal, Excel column letter, group depth, or UI position.
- Reference-valued fields use `_id`; dates use `_date`; booleans use `is_`/`has_` or a clear predicate; monetary fields include currency/unit/tax treatment only when supported by the source.
- Repeated cost leaves are distinguished by their enclosing plan group, quarter/month, and component (`total`, `construction_works`, `customer_materials`).
- `СМР` is rendered as `construction_works`; `МТР Заказчика` as `customer_materials`; the `МТР` acronym is expanded in clear English names where possible. Russian business labels and nested group paths remain in the table as source evidence.
- These names are the requested proposal. Do not “improve” them locally or create a second naming scheme. Raise only a concrete contradiction between this dictionary and the actual source or a material ambiguity requiring owner input.
- **Owner decision (2026-11-24): PostgreSQL truncates identifiers to 63 bytes, and 88 of the proposed names above are longer (max 84). The owner approved a deterministic shortening rule, applied ONLY to those 88 names, with no collisions: `plan_after_proposed_adjustment` → `adj`, `construction_works` → `cw`, `customer_materials` → `cm`, `thousand_rub_ex_vat` → `k_rub`, `readiness_percent` → `pct`, `issue_description` → `issue`, `centralized_supply` → `cs`, `direct_supply` → `ds`, `equipment_materials` → `em`, `contractor_equipment_materials` → `cem`, `general_contractor` → `gc`, `subcontractor` → `sub`, `prequalification` → `preq`. The resulting final map is frozen in migration `0041_plan_rows_semantic_names.py` (`RENAME`) and the frontend decoder `front/src/shared/api/renovation-shared.ts` (`LEGACY_FIELD_NAMES`, re-exported by the `renovation.ts` barrel); those two are the implementation source of truth for the final names. The table below keeps the ORIGINAL ChatGPT proposal for audit/header-path evidence — implement the shortened names, not the long literals.**

### Complete mapping (216 physical columns)

| Form position | Legacy database column | Proposed database column | Full frontend header path (11-level tree) | Form type | Notes |
|---:|---|---|---|---|---|
| 2 | col_003 | `work_or_repair_code` | Идентификационный код работы/ремонта | text | The form JSON has legacy calculated/read-only flags, but `form_schema.py` explicitly overrides both to false; preserve this current behavior. |
| 3 | col_004 | `production_name` | 1) Техническая дирекция → Наименование производства | text |  |
| 4 | col_005 | `workshop_name` | 1) Техническая дирекция → Цех | text |  |
| 5 | col_006 | `department_or_installation_name` | 1) Техническая дирекция → Отделение/установка | text |  |
| 6 | col_007 | `equipment_class` | 1) Техническая дирекция → Класс оборудования | text |  |
| 7 | col_008 | `equipment_category` | 1) Техническая дирекция → Вид (категория) оборудования | text |  |
| 8 | col_009 | `equipment_unit_name` | 1) Техническая дирекция → Наименование ЕО | text |  |
| 9 | col_010 | `technical_location_name` | 1) Техническая дирекция → Название технического места | text |  |
| 10 | col_011 | `process_position` | 1) Техническая дирекция → Технологическая позиция | text |  |
| 11 | col_012 | `inventory_number` | 1) Техническая дирекция → Инвентарный № | text |  |
| 12 | col_013 | `functional_area` | 1) Техническая дирекция → Функциональное направление | text |  |
| 13 | col_014 | `production_impact` | 1) Техническая дирекция → Влияние на производство | text |  |
| 14 | col_015 | `equipment_criticality_group_id` | 1) Техническая дирекция → Группа критичности оборудования | reference (`criticality` catalog) |  |
| 15 | col_016 | `bpof_section` | 1) Техническая дирекция → Раздел БПОФ | text |  |
| 16 | col_017 | `work_category_id` | 1) Техническая дирекция → Категория работ | reference (`work_category` catalog) |  |
| 17 | col_018 | `work_type_id` | 1) Техническая дирекция → Вид работ | reference (`work_type` catalog) |  |
| 18 | col_019 | `cost_allocation_type_id` | 1) Техническая дирекция → Отнесение затрат (OPEX/CAPEX) | reference (`cost_allocation` catalog) |  |
| 19 | col_020 | `execution_method_id` | 1) Техническая дирекция → Способ выполнения | reference (`execution_method` catalog) |  |
| 20 | col_021 | `work_executor_name` | 1) Техническая дирекция → Наименование Исполнителя работ | text |  |
| 21 | col_022 | `work_description` | 1) Техническая дирекция → Содержание работ | text |  |
| 22 | col_023 | `defect_list_or_bill_of_quantities_number` | 1) Техническая дирекция → Дефектная ведомость/ВОР № (в случаях когда инв.№ распространяется на отделение или установку, или блок из нескольких единиц оборудования) | text |  |
| 23 | col_024 | `work_start_date` | 1) Техническая дирекция → Дата начала работ | date |  |
| 24 | col_025 | `work_end_date` | 1) Техническая дирекция → Дата окончания работ | date |  |
| 25 | col_026 | `work_duration_days` | 1) Техническая дирекция → Продолжительность, дни | number |  |
| 26 | col_027 | `labor_hours` | 1) Техническая дирекция → Трудозатраты чел.час | number |  |
| 27 | col_028 | `work_cost_rub_ex_vat` | 1) Техническая дирекция → Работа, руб. без ндс | number |  |
| 28 | col_029 | `customer_materials_cost_rub_ex_vat` | 1) Техническая дирекция → МТР Заказчика, руб. без НДС | number |  |
| 29 | col_030 | `contractor_supplied_materials_cost_rub_ex_vat` | 1) Техническая дирекция → МТР Подрядчик, руб. без НДС | number |  |
| 30 | col_031 | `total_cost_rub_ex_vat` | 1) Техническая дирекция → Стоимость итого, руб. без НДС | number |  |
| 31 | col_218 | `estimate_number` | 2) Дирекция КСиР → Номер сметы | text | Non-terminal frontend position 31; do not move to the end because of the legacy suffix. |
| 32 | col_032 | `plan_after_proposed_adjustment_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → Всего | number |  |
| 33 | col_033 | `plan_after_proposed_adjustment_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в том числе → СМР | number |  |
| 34 | col_034 | `plan_after_proposed_adjustment_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в том числе → МТР Заказчика | number |  |
| 35 | col_035 | `plan_after_proposed_adjustment_q1_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → Всего | number |  |
| 36 | col_036 | `plan_after_proposed_adjustment_q1_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → СМР | number |  |
| 37 | col_037 | `plan_after_proposed_adjustment_q1_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → МТР Заказчика | number |  |
| 38 | col_038 | `plan_after_proposed_adjustment_january_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → Всего | number |  |
| 39 | col_039 | `plan_after_proposed_adjustment_january_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → СМР | number |  |
| 40 | col_040 | `plan_after_proposed_adjustment_january_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → МТР Заказчика | number |  |
| 41 | col_041 | `plan_after_proposed_adjustment_february_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → Всего | number |  |
| 42 | col_042 | `plan_after_proposed_adjustment_february_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → СМР | number |  |
| 43 | col_043 | `plan_after_proposed_adjustment_february_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → МТР Заказчика | number |  |
| 44 | col_044 | `plan_after_proposed_adjustment_march_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → Всего | number |  |
| 45 | col_045 | `plan_after_proposed_adjustment_march_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → СМР | number |  |
| 46 | col_046 | `plan_after_proposed_adjustment_march_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → МТР Заказчика | number |  |
| 47 | col_047 | `plan_after_proposed_adjustment_q2_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → Всего | number |  |
| 48 | col_048 | `plan_after_proposed_adjustment_q2_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → СМР | number |  |
| 49 | col_049 | `plan_after_proposed_adjustment_q2_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → МТР Заказчика | number |  |
| 50 | col_050 | `plan_after_proposed_adjustment_april_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → Всего | number |  |
| 51 | col_051 | `plan_after_proposed_adjustment_april_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → СМР | number |  |
| 52 | col_052 | `plan_after_proposed_adjustment_april_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → МТР Заказчика | number |  |
| 53 | col_053 | `plan_after_proposed_adjustment_may_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → Всего | number |  |
| 54 | col_054 | `plan_after_proposed_adjustment_may_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → СМР | number |  |
| 55 | col_055 | `plan_after_proposed_adjustment_may_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → МТР Заказчика | number |  |
| 56 | col_056 | `plan_after_proposed_adjustment_june_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → Всего | number |  |
| 57 | col_057 | `plan_after_proposed_adjustment_june_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → СМР | number |  |
| 58 | col_058 | `plan_after_proposed_adjustment_june_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → МТР Заказчика | number |  |
| 59 | col_059 | `plan_after_proposed_adjustment_q3_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → Всего | number |  |
| 60 | col_060 | `plan_after_proposed_adjustment_q3_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → СМР | number |  |
| 61 | col_061 | `plan_after_proposed_adjustment_q3_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → МТР Заказчика | number |  |
| 62 | col_062 | `plan_after_proposed_adjustment_july_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → Всего | number |  |
| 63 | col_063 | `plan_after_proposed_adjustment_july_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → СМР | number |  |
| 64 | col_064 | `plan_after_proposed_adjustment_july_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → МТР Заказчика | number |  |
| 65 | col_065 | `plan_after_proposed_adjustment_august_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → Всего | number |  |
| 66 | col_066 | `plan_after_proposed_adjustment_august_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → СМР | number |  |
| 67 | col_067 | `plan_after_proposed_adjustment_august_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → МТР Заказчика | number |  |
| 68 | col_068 | `plan_after_proposed_adjustment_september_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → Всего | number |  |
| 69 | col_069 | `plan_after_proposed_adjustment_september_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → СМР | number |  |
| 70 | col_070 | `plan_after_proposed_adjustment_september_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → МТР Заказчика | number |  |
| 71 | col_071 | `plan_after_proposed_adjustment_q4_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → Всего | number |  |
| 72 | col_072 | `plan_after_proposed_adjustment_q4_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → СМР | number |  |
| 73 | col_073 | `plan_after_proposed_adjustment_q4_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → МТР Заказчика | number |  |
| 74 | col_074 | `plan_after_proposed_adjustment_october_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → Всего | number |  |
| 75 | col_075 | `plan_after_proposed_adjustment_october_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → СМР | number |  |
| 76 | col_076 | `plan_after_proposed_adjustment_october_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → МТР Заказчика | number |  |
| 77 | col_077 | `plan_after_proposed_adjustment_november_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → Всего | number |  |
| 78 | col_078 | `plan_after_proposed_adjustment_november_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → СМР | number |  |
| 79 | col_079 | `plan_after_proposed_adjustment_november_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → МТР Заказчика | number |  |
| 80 | col_080 | `plan_after_proposed_adjustment_december_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → Всего | number |  |
| 81 | col_081 | `plan_after_proposed_adjustment_december_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → СМР | number |  |
| 82 | col_082 | `plan_after_proposed_adjustment_december_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → План с учетом предлагаемой корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → МТР Заказчика | number |  |
| 83 | col_083 | `adjustment_work_volume_unit` | 2) Дирекция КСиР → Корректировка → Физические объемы работ → Ед. изм. | number | Label says “unit”; the form declares numeric type. Preserve the declared type unless the owner separately approves a type correction. |
| 84 | col_084 | `adjustment_work_volume_quantity` | 2) Дирекция КСиР → Корректировка → Физические объемы работ → Количество | number |  |
| 85 | col_085 | `plan_adjustment_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → Всего | number |  |
| 86 | col_086 | `plan_adjustment_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в том числе → СМР | number |  |
| 87 | col_087 | `plan_adjustment_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в том числе → МТР Заказчика | number |  |
| 88 | col_088 | `plan_adjustment_q1_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → Всего | number |  |
| 89 | col_089 | `plan_adjustment_q1_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → СМР | number |  |
| 90 | col_090 | `plan_adjustment_q1_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → МТР Заказчика | number |  |
| 91 | col_091 | `plan_adjustment_january_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → Всего | number |  |
| 92 | col_092 | `plan_adjustment_january_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → СМР | number |  |
| 93 | col_093 | `plan_adjustment_january_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → МТР Заказчика | number |  |
| 94 | col_094 | `plan_adjustment_february_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → Всего | number |  |
| 95 | col_095 | `plan_adjustment_february_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → СМР | number |  |
| 96 | col_096 | `plan_adjustment_february_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → МТР Заказчика | number |  |
| 97 | col_097 | `plan_adjustment_march_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → Всего | number |  |
| 98 | col_098 | `plan_adjustment_march_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → СМР | number |  |
| 99 | col_099 | `plan_adjustment_march_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → МТР Заказчика | number |  |
| 100 | col_100 | `plan_adjustment_q2_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → Всего | number |  |
| 101 | col_101 | `plan_adjustment_q2_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → СМР | number |  |
| 102 | col_102 | `plan_adjustment_q2_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → МТР Заказчика | number |  |
| 103 | col_103 | `plan_adjustment_april_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → Всего | number |  |
| 104 | col_104 | `plan_adjustment_april_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → СМР | number |  |
| 105 | col_105 | `plan_adjustment_april_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → МТР Заказчика | number |  |
| 106 | col_106 | `plan_adjustment_may_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → Всего | number |  |
| 107 | col_107 | `plan_adjustment_may_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → СМР | number |  |
| 108 | col_108 | `plan_adjustment_may_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → МТР Заказчика | number |  |
| 109 | col_109 | `plan_adjustment_june_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → Всего | number |  |
| 110 | col_110 | `plan_adjustment_june_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → СМР | number |  |
| 111 | col_111 | `plan_adjustment_june_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → МТР Заказчика | number |  |
| 112 | col_112 | `plan_adjustment_q3_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → Всего | number |  |
| 113 | col_113 | `plan_adjustment_q3_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → СМР | number |  |
| 114 | col_114 | `plan_adjustment_q3_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → МТР Заказчика | number |  |
| 115 | col_115 | `plan_adjustment_july_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → Всего | number |  |
| 116 | col_116 | `plan_adjustment_july_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → СМР | number |  |
| 117 | col_117 | `plan_adjustment_july_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → МТР Заказчика | number |  |
| 118 | col_118 | `plan_adjustment_august_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → Всего | number |  |
| 119 | col_119 | `plan_adjustment_august_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → СМР | number |  |
| 120 | col_120 | `plan_adjustment_august_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → МТР Заказчика | number |  |
| 121 | col_121 | `plan_adjustment_september_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → Всего | number |  |
| 122 | col_122 | `plan_adjustment_september_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → СМР | number |  |
| 123 | col_123 | `plan_adjustment_september_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → МТР Заказчика | number |  |
| 124 | col_124 | `plan_adjustment_q4_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → Всего | number |  |
| 125 | col_125 | `plan_adjustment_q4_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → СМР | number |  |
| 126 | col_126 | `plan_adjustment_q4_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → МТР Заказчика | number |  |
| 127 | col_127 | `plan_adjustment_october_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → Всего | number |  |
| 128 | col_128 | `plan_adjustment_october_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → СМР | number |  |
| 129 | col_129 | `plan_adjustment_october_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → МТР Заказчика | number |  |
| 130 | col_130 | `plan_adjustment_november_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → Всего | number |  |
| 131 | col_131 | `plan_adjustment_november_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → СМР | number |  |
| 132 | col_132 | `plan_adjustment_november_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → МТР Заказчика | number |  |
| 133 | col_133 | `plan_adjustment_december_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → Всего | number |  |
| 134 | col_134 | `plan_adjustment_december_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → СМР | number |  |
| 135 | col_135 | `plan_adjustment_december_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Корректировка → Стоимость работ, (+/-) тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → МТР Заказчика | number |  |
| 136 | col_136 | `plan_adjustment_type` | 2) Дирекция КСиР → Корректировка → Вид корректировки | text |  |
| 137 | col_137 | `plan_adjustment_justification_document` | 2) Дирекция КСиР → Корректировка → Причина (обосновывающий документ) | text |  |
| 138 | col_138 | `original_plan_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → Всего | number |  |
| 139 | col_139 | `original_plan_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в том числе → СМР | number |  |
| 140 | col_140 | `original_plan_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в том числе → МТР Заказчика | number |  |
| 141 | col_141 | `original_plan_q1_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → Всего | number |  |
| 142 | col_142 | `original_plan_q1_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → СМР | number |  |
| 143 | col_143 | `original_plan_q1_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в том числе → МТР Заказчика | number |  |
| 144 | col_144 | `original_plan_january_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → Всего | number |  |
| 145 | col_145 | `original_plan_january_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → СМР | number |  |
| 146 | col_146 | `original_plan_january_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Январь → в т.ч. → МТР Заказчика | number |  |
| 147 | col_147 | `original_plan_february_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → Всего | number |  |
| 148 | col_148 | `original_plan_february_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → СМР | number |  |
| 149 | col_149 | `original_plan_february_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Февраль → в т.ч. → МТР Заказчика | number |  |
| 150 | col_150 | `original_plan_march_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → Всего | number |  |
| 151 | col_151 | `original_plan_march_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → СМР | number |  |
| 152 | col_152 | `original_plan_march_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → I квартал → в т.ч. по месяцам → Март → в т.ч. → МТР Заказчика | number |  |
| 153 | col_153 | `original_plan_q2_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → Всего | number |  |
| 154 | col_154 | `original_plan_q2_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → СМР | number |  |
| 155 | col_155 | `original_plan_q2_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в том числе → МТР Заказчика | number |  |
| 156 | col_156 | `original_plan_april_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → Всего | number |  |
| 157 | col_157 | `original_plan_april_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → СМР | number |  |
| 158 | col_158 | `original_plan_april_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Апрель → в т.ч. → МТР Заказчика | number |  |
| 159 | col_159 | `original_plan_may_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → Всего | number |  |
| 160 | col_160 | `original_plan_may_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → СМР | number |  |
| 161 | col_161 | `original_plan_may_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Май → в т.ч. → МТР Заказчика | number |  |
| 162 | col_162 | `original_plan_june_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → Всего | number |  |
| 163 | col_163 | `original_plan_june_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → СМР | number |  |
| 164 | col_164 | `original_plan_june_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → II квартал → в т.ч. по месяцам → Июнь → в т.ч. → МТР Заказчика | number |  |
| 165 | col_165 | `original_plan_q3_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → Всего | number |  |
| 166 | col_166 | `original_plan_q3_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → СМР | number |  |
| 167 | col_167 | `original_plan_q3_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в том числе → МТР Заказчика | number |  |
| 168 | col_168 | `original_plan_july_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → Всего | number |  |
| 169 | col_169 | `original_plan_july_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → СМР | number |  |
| 170 | col_170 | `original_plan_july_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Июль → в т.ч. → МТР Заказчика | number |  |
| 171 | col_171 | `original_plan_august_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → Всего | number |  |
| 172 | col_172 | `original_plan_august_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → СМР | number |  |
| 173 | col_173 | `original_plan_august_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Август → в т.ч. → МТР Заказчика | number |  |
| 174 | col_174 | `original_plan_september_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → Всего | number |  |
| 175 | col_175 | `original_plan_september_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → СМР | number |  |
| 176 | col_176 | `original_plan_september_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → III квартал → в т.ч. по месяцам → Сентябрь → в т.ч. → МТР Заказчика | number |  |
| 177 | col_177 | `original_plan_q4_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → Всего | number |  |
| 178 | col_178 | `original_plan_q4_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → СМР | number |  |
| 179 | col_179 | `original_plan_q4_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в том числе → МТР Заказчика | number |  |
| 180 | col_180 | `original_plan_october_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → Всего | number |  |
| 181 | col_181 | `original_plan_october_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → СМР | number |  |
| 182 | col_182 | `original_plan_october_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Октябрь → в т.ч. → МТР Заказчика | number |  |
| 183 | col_183 | `original_plan_november_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → Всего | number |  |
| 184 | col_184 | `original_plan_november_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → СМР | number |  |
| 185 | col_185 | `original_plan_november_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Ноябрь → в т.ч. → МТР Заказчика | number |  |
| 186 | col_186 | `original_plan_december_total_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → Всего | number |  |
| 187 | col_187 | `original_plan_december_construction_works_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → СМР | number |  |
| 188 | col_188 | `original_plan_december_customer_materials_cost_thousand_rub_ex_vat` | 2) Дирекция КСиР → Первоначальный план до корректировки → Стоимость всего, тыс. руб. без НДС → в т.ч. по кварталам → IV квартал → в т.ч. по месяцам → Декабрь → в т.ч. → МТР Заказчика | number |  |
| 189 | col_189 | `lotting_activity_direction_code` | 2) Дирекция КСиР → Код направления деятельности (согласно критериев лотирования) | text |  |
| 190 | col_190 | `general_contractor_procurement_code` | 2) Дирекция КСиР → Договор генерального подряда → Код закупки (генподряд) | text |  |
| 191 | col_191 | `general_contractor_contract_date` | 2) Дирекция КСиР → Договор генерального подряда → Дата | date |  |
| 192 | col_192 | `general_contractor_contract_number` | 2) Дирекция КСиР → Договор генерального подряда → Номер | text |  |
| 193 | col_193 | `general_contractor_contract_price_thousand_rub_ex_vat` | 2) Дирекция КСиР → Договор генерального подряда → Цена работы по договору тыс. руб. без НДС | number |  |
| 194 | col_194 | `general_contractor_counterparty_name` | 2) Дирекция КСиР → Договор генерального подряда → Наименование контрагента | text |  |
| 195 | col_195 | `general_contractor_counterparty_prequalification_status` | 2) Дирекция КСиР → Договор генерального подряда → Статус предквалификации контрагента | text |  |
| 196 | col_196 | `subcontractor_engagement_requirement` | 2) Дирекция КСиР → Договор генерального подряда → Необходимость привлечения субподрядчика | text |  |
| 197 | col_197 | `general_contractor_contract_issue_description` | 2) Дирекция КСиР → Договор генерального подряда → Описание проблематики | text |  |
| 198 | col_198 | `subcontract_contract_date` | 2) Дирекция КСиР → Договор субподряда → Дата | text | Label says “Date”, but the form declares text. Do not change the type as part of a name-only migration. |
| 199 | col_199 | `subcontract_contract_number` | 2) Дирекция КСиР → Договор субподряда → Номер | date | Label says “Number”, but the form declares date. This is a source metadata inconsistency; preserve the current type and ask the owner before any type correction. |
| 200 | col_200 | `subcontractor_name` | 2) Дирекция КСиР → Договор субподряда → Наименование субподрядчика | text |  |
| 201 | col_201 | `subcontractor_prequalification_status` | 2) Дирекция КСиР → Договор субподряда → Статус предквалификации субподрядчика | text |  |
| 202 | col_202 | `has_materials_in_work_or_repair` | 3) Дирекция МТО → Наличие МТР в работах/ремонтах (Да/Нет) | boolean |  |
| 203 | col_203 | `required_materials_delivery_date` | 3) Дирекция МТО → Необходимая дата поставки МТР | date |  |
| 204 | col_204 | `total_materials_cost` | 3) Дирекция МТО → Стоимость МТР | number | The parent label is “Materials cost”; the source does not state a currency/unit here, so no unit suffix is invented. |
| 205 | col_205 | `centralized_supply_materials_cost_thousand_rub_ex_vat` | 3) Дирекция МТО → в том числе → МТР Централизованная поставка, тыс. руб. без НДС | number |  |
| 206 | col_206 | `direct_supply_materials_cost_thousand_rub_ex_vat` | 3) Дирекция МТО → в том числе → МТР Прямая поставка, тыс. руб. без НДС | number |  |
| 207 | col_207 | `contractor_supply_materials_cost_thousand_rub_ex_vat` | 3) Дирекция МТО → в том числе → МТР поставка Подрядчика, тыс. руб. без НДС | number |  |
| 208 | col_208 | `equipment_materials_contracting_centralized_supply_readiness_percent` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Контрактация оборудования и МТР → Централизованная поставка | number |  |
| 209 | col_209 | `equipment_materials_contracting_direct_supply_readiness_percent` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Контрактация оборудования и МТР → Прямая поставка | number |  |
| 210 | col_210 | `equipment_materials_delivery_centralized_supply_readiness_percent` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Поставка оборудования и МТР → Централизованная поставка | number |  |
| 211 | col_211 | `equipment_materials_delivery_direct_supply_readiness_percent` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Поставка оборудования и МТР → Прямая поставка | number |  |
| 212 | col_212 | `materials_are_critical` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Признак кртичности (да / нет) | boolean |  |
| 213 | col_213 | `materials_readiness_issue_description` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Описание проблематики | text |  |
| 214 | col_214 | `contractor_equipment_materials_supply_readiness_percent` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Поставка оборудования и МТР Подрядчика | number |  |
| 215 | col_215 | `contractor_supplied_materials_are_critical` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Признак кртичности (да / нет) | boolean |  |
| 216 | col_216 | `contractor_equipment_materials_supply_issue_description` | 3) Дирекция МТО → Статус готовности МТР к проведению ремонта (%) → Описание проблематики | text |  |
| 217 | col_217 | `note` | Примечание | text |  |

### Specific source anomalies that must not be silently “fixed” during rename

1. **`col_198` / `col_199` contract metadata mismatch:** the form labels identify `col_198` as the subcontract contract date and `col_199` as its number, while their current form types are `text` and `date`, respectively. The names above follow the business labels. Keep the current SQL/form types unchanged in the name-only migration. Ask the owner separately before proposing any type/data conversion, and describe how existing values would be interpreted.
2. **`col_083` adjustment volume unit:** the label is “Ед. изм.” while `cellType` is `number`. Its replacement name reflects the label, but its type must remain as implemented unless separately approved.
3. **`col_003` renovation code:** the legacy JSON flags it as calculated/read-only, but `form_schema.py` overrides those flags to `false`; its current application behavior is a writable typed value, not a formula-generated field. Keep that behavior unchanged during rename.
4. **`col_218` historical reuse:** the old `col_218` “Корректировка” field was retired. The current model/form reuses that name for “Номер сметы”; the old adjustment semantics must not be carried over.

### Mapping verification gate

Before writing the migration, mechanically assert:

- exactly 216 source ORM columns and exactly 216 dictionary entries;
- each legacy column appears once, each proposed name is unique, and every proposed name is valid lower-case `snake_case`;
- the set of legacy columns is exactly `col_003`…`col_218` (inclusive);
- the frontend order matches `fields[].position`, with `col_218` at position 31 and `col_217` at position 217;
- all full header paths match the current `field_header_paths()` output;
- every formula expression and dynamically constructed field reference is translated through the same old-to-new dictionary, without changing formula semantics.

If any assertion fails against the current checkout, stop Phase 3 and report the discrepancy. Do not alter names based only on a local preference.

## 10. Immediate instruction

Start with **Phase 0**, then proceed through the implementation phases in order wherever the required decisions have already been made and no unresolved owner choice blocks the work. Do not stop merely to ask permission to begin the next mechanical, independently testable phase. Stop at the relevant decision gate, present the choices, and wait for the owner's answer before changing that behavior. If the current code already differs from the audit, update the plan rather than forcing the old diagnosis onto the repository. Keep phases independently reviewable and report each phase as specified above.
