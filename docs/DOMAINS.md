# Project domains: КППК and Логистика

Data-model detail moved from `ARCHITECTURE.md` (Phase 8 split). The system
overview, environment and API contract stay in `ARCHITECTURE.md`.

## Data model (project «КППК», key `kppk`)

Phase 1 copies the source tables of the corporate КППК database into the program
schema and exposes the four dictionaries the «Таблицы.xlsx» phase builds on.

The **original data is not in the repository**: `raw.kppk_*` ships as SQL dumps
the operator loads into the database. The seeder only needs the database — plus
three small files of its own in `back/src/kppk/data/`:

- `source_schema.sql` — the structure of the source tables (a fresh or CI
  install creates them from it);
- `demo_source_rows.sql` — a few rows (13 ДЗО, 137 products, one period, one
  row per form) used **only** when the source tables are absent, so every form
  works end to end on a demo install. A real installation loads the actual
  dumps instead and this file is never merged with them;
- `product_matrix.sql` — the product × ДЗО matrix **and** its «Заполняемый
  акциз» flags. This is the one input the source dumps never carried (they have
  products and ДЗО as separate dictionaries); it replaced «Матрица
  продуктов.xlsx» and is the reason no spreadsheet is needed at seed time.

The `dict` schema belongs to another project and is never created or touched.

- **Source schemas** `raw` (`raw.kppk_d_org`, `raw.kppk_d_period`,
  `raw.kppk_d_product_seq`, `raw.kppk_d_raw_seq`, `raw.kppk_f_realization`,
  `raw.kppk_f_realization_plan`, `raw.kppk_f_production_plan`) — created from
  `source_schema.sql` and filled with the demo rows **only when missing**; on
  prod installations they already hold the real data and are only read. The
  `dict` schema is another project's and is never touched.
- **Program copies** (`back/src/kppk/models.py`, prefix `kppk_`): `kppk_orgs`,
  `kppk_periods`, `kppk_products`, `kppk_raws`, `kppk_markets` (the
  dictionaries), `kppk_org_products` («Продуктовые позиции»: product × ДЗО links
  from the matrix), and the read-only `kppk_realization` /
  `kppk_realization_plan` copies for the next phase. Every copy is uuid-keyed;
  the source integer id stays as a unique column (`org_id`, `period_id`,
  `product_num`, `raw_num`) — the seeder's re-copy key and the join key the
  fact/plan copies reference. `product_num`/`raw_num` are admin-editable in the
  UI and stay unique; the dictionary names are unique as well
  (`kppk_orgs.code`/`name`, `kppk_products.name`, `kppk_raws.name`,
  `kppk_markets.code`/`name`, `kppk_periods` by `(month_name, year_num)`);
  several NULL source numbers stay allowed (PostgreSQL treats NULLs as distinct)
  — the matrix brings in products without a number.
- **Normalized references (0024).** The source dumps carry the product NAME and
  the market CODE as free text in every realization row. The copies reference
  them by uuid instead: `product_id` → `kppk_products.id` and `market_id` →
  `kppk_markets.id` (`ON DELETE RESTRICT`) on `kppk_realization`,
  `kppk_realization_plan`, `kppk_cost` and `kppk_production_plan`; the text
  columns are gone. The one and only name comparison happens in the migration
  back-fill (and in the seeder's copy step), and both **abort on an unmatched
  row** rather than skipping it. `kppk_markets` holds «РФ»/«ДЗ»/«БЗ» (code +
  full name), seeded by the migration; the seeder tops it up with
  `ON CONFLICT DO NOTHING`. `uq_kppk_cost_scope` keys on
  `(org_id, period_date, product_id)`. The API contract is unchanged
  (`product`/`region` stay strings) — the backend joins the dictionaries.
- **Seeder** `just seed-kppk` (`back/src/scripts/seed_kppk.py`, one
  transaction): re-copies the source tables on every run (truncate + re-insert,
  audit stamps on the four dictionaries) and rebuilds `kppk_org_products` from
  the matrix; products and ДЗО are matched by exact display name — no
  normalization (spaces stay significant). The realization/production copies
  resolve that text into `product_id`/`market_id` on the way in, and the seed
  FAILS on a source row whose product or market is not in the dictionaries
  instead of writing an unresolvable copy. The ДЗО copy keeps the source id,
  code and name only — `raw.kppk_d_org.time_zone` is not copied. A matrix
  product missing in the source dictionary is created without a `product_num`
  under an `@`-prefixed name; a dictionary product missing in the matrix keeps
  an empty ДЗО. The market dictionary is topped up to the source codes («РФ»,
  «ДЗ», «БЗ») when a restored schema left it empty (`markets_created` in the
  counters). The `kppk` project row and its user links are owned by
  `just seed` / self-provisioning, not by this seeder.
- **Workspace** `front/src/features/kppk/kppk-workspace.tsx`: three static tabs
  over the copies («Продуктовые позиции» follow the «Сырьевые позиции» form —
  the source number «№» leads (editable, unique server-side), then the product
  with its ДЗО multi-select column, values comma-separated; «ДЗО» keeps the
  code and the full name only — ordered by the name, no number, no time
  zone). The «Периоды» dictionary is backend-only in this phase — its tab is
  hidden until «Таблицы.xlsx». Tabs are visible to every project member,
  editing is admin-only (mirrored by a read-only grid). No realtime
  collaboration (no WS room) and no fixing in this phase, but the shared
  action-bar mechanics are in place: undo/redo through the server-side
  collaboration journal (page `session_id`, history per tab) and Excel export
  of the whole/visible table through the common dictionary renderer.

## Data model (project «Логистика», key `logistics`)

Phase 1 is the dictionaries of the corporate logistics database plus the main
«Ставки» table. The dictionaries are not copied: the tables live in the external
**`dict`** schema (`back/dicts.sql`) and the API only reads them — no migration
creates them, no seeder writes them, and no endpoint mutates them. The DB role
needs SELECT on `dict.countries`, `dict.transport_types`, `dict.vehicle_types`,
`dict.transport_types_vehicle_types` and `dict.orgs`, and REFERENCES on them for
migration `0035` (the rates table's foreign keys).

- **Rows** (`back/src/logistics/dictionaries.py`): every read filters
  `deleted_at IS NULL` on every table it touches. «Страны» returns `shortname`
  and the computed `market` (`alpha2="RU"` → «РФ»; `ts` or `sng` → «БЗ»;
  otherwise «ДЗ»); «Активы» returns all `prefix_ru` ordered by it; «Типы ТС и
  перевозки» is one transport-type × vehicle-type matrix ordered by transport
  type, then vehicle. The transport types and vehicle types are never exposed
  as separate dictionaries. A type whose mappings are absent (or all point at
  deleted vehicles) keeps one row with an empty vehicle cell.
- **Fallback**: when a table does not exist, the reader returns a short built-in
  demo list and the payload says `source="fallback"`; the grid shows a warning
  banner. An existing but EMPTY table is a real empty dictionary
  (`source="external"`, no rows) — the fallback never hides it. The existence
  check is `to_regclass`, so a missing `dict` schema is safe.
- **«Ставки»** (`l_rates`, model `back/src/logistics/models.py`, read model
  `back/src/logistics/rates.py`): the project's main table, one row per
  asset × product/route/transport per month. `period_date` is the FIRST day of
  the selected month (the toolbar's month/year selects filter the grid by it and
  stamp a new row). The reference columns (`org_id`, `country_from_id`,
  `country_to_id`, `transport_type_id`, `vehicle_type_id`) store the source
  UUIDs and carry FOREIGN KEYs into `dict` (`ondelete="RESTRICT"`), so a
  physically deleted dictionary row cannot be removed while a rate references
  it. A soft-deleted one (`deleted_at`) stays valid and the read model prefixes
  its label with «УДАЛЕНО: ». Migration `0035_logistics_rates` creates the table
  and the FK constraints; the model itself keeps plain UUID columns, so
  `SQLModel.metadata` can be built without the external schema (the metadata
  comparison in `test_migrations.py` ignores those DB-only FKs). The schema name
  is configurable — `LOGISTICS_DICT_SCHEMA`, default `dict`; the tests point it
  at their own schema. The migration FAILS with a Russian message when the
  external tables are absent: the rates table cannot exist without them. The
  rates endpoints check the same tables at request time and answer `503` with
  «Справочники «Логистики» недоступны …» instead of a raw SQL error, so a
  half-provisioned installation is diagnosable from the UI. A developer machine
  without the corporate dumps can fill the schema with `just seed-dev-dict`
  (`back/src/scripts/seed_dev_dict.py`, local only: it creates the five tables
  only when missing and inserts fixed-id demo rows — it never drops or alters
  anything, and the tables it created must be dropped before the real dumps are
  loaded).
- **What is computed, not stored** (`rates.py`): «Направление» (departure country
  market «РФ» → the DESTINATION market «РФ»/«БЗ»/«ДЗ», so Россия → Беларусь is
  «БЗ»; any other departure → destination «РФ» is «Импорт», else «Экспорт»),
  «Опасный» (`hazard_class_value IS NOT NULL`), the dollar
  equivalents of the three rub rates (`rub / USD_RATE`, the constant `83.90`
  until the corporate source provides it) and the month-over-month deviations:
  each row read carries the previous month's net rates for the SAME asset ×
  route × transport × vehicle, «руб. без НДС»/«дол. без НДС» are net − previous
  net, and «%» is temporarily the same rub difference (the division arrives
  with the real formula). «Продукт», «Пункт отправления (А)» and «Пункт назначения (Б)» are
  nullable placeholders that stay NULL and read-only.
- **Validation** (`rates.py`): the four rub fields and «Загрузка в ТС, т» are
  non-negative decimals with at most two places; the vehicle type must belong to
  the selected transport type through
  `dict.transport_types_vehicle_types` and may be NULL only when that transport
  type has no mappings. The row stores `transport_type_id` + `vehicle_type_id`
  themselves — never the mapping row's id, so that table can key on the
  `(transport_type_id, vehicle_type_id)` PAIR. A row may be deleted (no
  dependents yet): the API journals the delete as an existence action, so undo
  restores the row (with its original `created_at`, keeping the period order)
  and redo removes it again.
- **Roles and access** (`service.py`, `users.py`): the membership role is one of
  `admin` (everything), `reviewer` (reads every rate, never writes, no
  dictionary tabs), `asset_worker` (reads and writes only the rates of the
  assets granted in `l_user_assets`; the «Актив» column is hidden and the
  server stamps the granted asset on insert) and `blocked` (no access).
  `user_roles.is_active` is DERIVED from the role (blocked → false), so every
  existing resolver keeps working; an asset worker without a single grant is
  denied at access time — equivalent to blocked, but the admin may save the
  role first and pick the asset afterwards. The users dictionary stays
  admin-only; the reference reads (countries/transport types/orgs/hazard
  classes) are open to every member because the rates form needs their labels.
  A worker's undo replays only actions on their own assets (the journal is
  already per user).
- **Project/role**: migration `0032_logistics` inserts the `logistics` project
  row (idempotent) and creates the `logistics_price_kind` enum (`INDICATIVE`,
  `PRICE`) the rates table uses. Migrations `0033_logistics_hazard_classes`
  / `0034_logistics_table_prefix` create and seed the application-owned
  «Класс опасности» table (`l_hazard_classes`, values 1..9); it has an API but
  no tab yet. `0035_logistics_rates` creates `l_rates`; `0036_project_roles`
  appends the shared `reviewer`/`blocked` roles to `rm_project_role`;
  `0037_logistics_user_assets` creates `l_user_assets` (the asset grants, with
  an external FK into `dict.orgs`). Application-owned
  logistics tables use the `l_` prefix.
  `just seed-logistics` (idempotent) ensures the project, the nine classes and
  Евтушенко's active admin link — and nothing else; the external `dict` schema
  is never touched. Every logistics endpoint requires a membership; the users
  dictionary and the rate delete require the administrator.
- **Workspace** `front/src/features/logistics/logistics-workspace.tsx`: the main
  «Ставки» tab first, then the read-only dictionaries — «Страны», «Типы ТС и
  перевозки», «Активы» — and the admin-only «Пользователи» tab (add a member with
  a role — blocked by default — and, for an asset worker, the asset grants; edit
  the full name and the role; delete after blocking; email is immutable and the
  full name follows Keycloak when it is enabled). The tab strip is admin-only:
  a reviewer or an asset worker sees the «Ставки» tab alone (the month/year
  filter and the view strip stay). «Ставки» has month/year selects in the toolbar
  (2025–2028, no visible labels), a collaboration room, undo/redo scoped to the
  selected period, and a trailing draft row owned by the section (the generic
  placeholder action is off; the row delete is an explicit action with a
  confirmation dialog). The dictionaries have no collaboration room, no
  undo and no export; their grids keep header filters, sorting, copy and the
  remembered position. The transport tab merges the type cell over its vehicle
  types and the countries tab merges the market cell over its alphabetically
  sorted countries (`CellMergePlugin`); a user sorting or any active column
  filter disables the merge. The computed country market is highlighted like a
  calculated column but rendered as text.
