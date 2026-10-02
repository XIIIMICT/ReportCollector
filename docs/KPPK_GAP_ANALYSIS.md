# КППК: what the source document requires vs what the codebase has

**Status:** research result, 2026-09-29. Basis for the further КППК work — not a plan of record yet.
**Source:** `docs/Дополнения форм.docx` — «Постановка задачи на разработку веб-приложения для ввода данных к совещаниям финансового блока АО «Росхим»» (created 2026-06-04, 10 pages, 2133 words, 185 paragraphs, 11 headings, 9 figures + the Росхим letterhead logo on the first paragraph).
**Method:** the OOXML package was parsed with ElementTree (paragraph, style, list level, image relationship; text boxes included) and all 11 PNGs were extracted and read. RegEx text extraction is unreliable on this file — it produced cross-tag matches; use ElementTree if the document is re-read.

This document is the analysis the user asked for: what the source requires, what already exists in this repository (possibly implemented or worded differently), and what is missing — with the role model and the tables called out separately, because those are the two areas that are absent in substance.

A deliberate caveat: the source is a **pre-project «Постановка задачи»** with several open points of its own («предельный срок заполнения данных: пока не знаю, уточним», an appendix with the ДЗО list «отражен в приложении» that is not part of this file). Section 7 lists what must be decided by the client rather than guessed.

---

## 1. What the source document actually describes

It is **not** a description of an "addition to two existing forms" (an earlier, garbled reading of the file suggested that — it is wrong). It is a complete task statement for a **data-entry portal** with:

- a start (login) page, a **registry of forms** ("home"), the **input form** page, a **check report** page, a **data-load monitor** for business curators, and a **logging** page for the administrator;
- three input templates, each with an explicit flat data structure (column names, types, lengths, decimals, formulas);
- a two-step lifecycle: **«Сохранить»** (save) then **«Загрузить»** (load/publish into the DB), where only the second step runs the validations (deadline, completeness, numeric format) and writes to the database;
- a rights model: read/write rights come «по предоставленным спискам» and are scoped to **templates × enterprises (ДЗО/Актив)**;
- two extra roles on top of the ordinary cabinet owner: **куратор от бизнеса** (load monitor) and **администратор системы** (logging, manual load override after an e-mail request).

### Figure inventory (all 9 are wireframe screenshots, no hidden data)

| # | Paragraph | Caption (as in the source) | What it shows |
|---|---|---|---|
| — | p001 | (no caption) | Росхим letterhead logo |
| Рис.1 | p017 | «Рис.1 Пример отображения страницы авторизации» | Card «Шаблоны ввода данных» → «Авторизация»: Логин, Пароль, «Войти» |
| Рис.2 | p025 | «Рис.2 Пример отображения страницы реестра форм» | Title «Шаблоны ввода данных»; right-hand «Отражение ФИО владельца кабинета»; left «Область доступных шаблонов ввода»: 1. Шаблон ввода данных к КППиК, 2. Шаблон ввода данных по оборотному капиталу, 3. Шаблон ввода данных к плановому комитету, 4. ----; right column «До окончания срока внесения данных осталось» → 5 / 20 / 15 / 45 дней (one deadline per template) |
| Рис.3 | p047 | «Рис.3 Пример отображения страницы шаблона ввода данных» | Header «Шаблон ввода данных: Производство продукции, план»; selectors «Наименование ДЗО», «Дата отчета»; icons save / load / home; «До окончания срока осталось 20 дней»; buttons «Проверочный отчет», «BI» |
| Рис.4 | p049–p050 | «Рис.4 Пример отображения уведомления о потере внесенных данных» | Modal «Внимание! Данные не сохранены! При переходе со страницы будут утеряны!» |
| Рис.5 | p064 | «Рис.5 Пример отображения страницы проверочного отчета» | «Проверочный отчет: Выпуск продукции»; ДЗО/Дата отчета; home + download icons; buttons «К внесению данных», «BI» |
| Рис.6 | p079 | «Рис.6 Пример отображения страницы монитора загрузки данных» | Table: № п/п · Наименование ДЗО · Дата последнее сохранения · Статус загрузки · Дата загрузки; rows = ДЗО (Крымский содовый завод, Титановые инвестиции, СНХЗ, Кучуксульфат, Метафракс Кемикалс, Метадинеа, Башкирская содовая компания, Березниковской содовый завод, Волжский оргсинтез); a not-loaded row is filled red/peach, a row whose save date is later than its load date is filled yellow (Метафракс Кемикалс) |
| Рис.7 | p129 | «Рис.7 … «Производство продукции, план»» | Table «№ п/п · Продукт · Бизнес-план (Объем валового производства / Объем товарного производства / ВЗП) · Оперативный план (same)», «Ед. изм.: т» |
| Рис.8 | p151 | «Рис.8 … «Затраты на производство продукции, план»» | Table «Бизнес-план / Оперативный план» × «Полная себестоимость / Производственная себестоимость / Переменные затраты», «Ед. изм.: руб/т» |
| Рис.9 | p184 | «Рис.9 … «Затраты на производство продукции, план»» (caption is a copy-paste error — the image is «Остатки товарной продукции, план») | Two blocks: «Остатки товарной продукции на «Дата отчета»−1 месяц» and «…на «Дата отчета»», each × «Всего / Загруженная в транспорт под клиента / На региональном складе / На складе завода», «Ед. изм.: т» |

No figure contains a DB table list or a rights matrix: **the source's role model is narrative, not tabular**, and "таблицы" in the source means the three input templates' data structures described below.

---

## 2. Verdict at a glance

Status vocabulary: `present` — есть и совпадает по смыслу · `different` — есть, но реализовано/сформулировано иначе · `partial` — есть только часть требования · `missing` — отсутствует.

| Area | Requirement (source) | Status |
|---|---|---|
| Auth | Login page, login+password, persistent browser session | `present` |
| Auth | «Разавторизация» by right-clicking the owner's name | `different` (profile modal instead) |
| Auth | Session lifetime / auto-logout (12 h) | `present` |
| Roles | Read/write rights distributed «по предоставленным спискам» | `missing` |
| Roles | Access to specific templates, scoped to enterprise (ДЗО) | `different` (per-project role + per-asset grants) |
| Roles | «Куратор от бизнеса» (business curator) | `missing` |
| Roles | «Администратор системы» separate from the project admin | `different` |
| Pages | Start page | `present` |
| Pages | **Registry of forms («home»)**: template list + FIO + deadline per template | `missing` |
| Pages | Input form page | `different` (grid workspace, not the described layout) |
| Pages | **Check report** page (per template, date/ДЗО pickers, export, «К внесению данных», BI) | `partial` |
| Pages | **Load monitor** (curator): table of ДЗО × save/load dates + colouring | `missing` |
| Pages | **Logging** page (admin): save/load audit per user | `different` (Loki/Grafana logs, no in-app page) |
| Form | «Сохранить» icon + success/error notification | `present` |
| Form | «Загрузить» icon with 4 pre-checks + SQL insert | `different` (edits persist immediately; `fix`/`submit` are the closest analog) |
| Form | Warning «Данные не сохранены» before leaving the page | `different` (edits auto-save; no `beforeunload` guard) |
| Form | «Проверочный отчет» / «BI» buttons | `partial` (report analog only) / `missing` (BI) |
| Deadlines | «Остаток дней … до окончания планового срока ввода данных» on 3 pages | `missing` |
| Deadlines | Per-format load deadline in **hours**; after it — refusal | `missing` |
| Deadlines | Manual load by the administrator after a written (e-mail) request | `missing` |
| Monitor | Columns №, ДЗО, last save date, load status, load date; red/yellow fills | `missing` |
| Logging | date-time, login, template code, template name, action, status | `missing` |
| **Tables** | «Производство продукции, план» (PRODUCTION_*, GOODS_*, SELF_CONSUMPTION_*) | `missing` |
| **Tables** | «Затраты на производство продукции, план» (FULL_COST_*, INPUT_COST_*, VAR_EXP_*) | `missing` |
| **Tables** | «Остатки товарной продукции, план» (STOCK_*) | `missing` |
| Template logic | One Актив → auto-select; product list and units depend on the Актив | `different` (equivalent asset-scoped grants + catalogs exist in «Сырьё»/«Ремонт») |
| Export | Report download as Excel or **PDF** | `partial` (Excel only) |

**Summary.** The repository already contains a good half of what the source describes in intent: authentication, a collaborative Excel-like input grid with save, per-project and per-asset access control, dictionary-driven reference data, version/archive reads, de-duplicated presence/leases, undo/redo, and Excel export. What does **not** exist in any form is: the **rights model of the source** (read/write lists × template × enterprise, plus curator and system-administrator roles), the **three data templates with their tables**, the **form registry with deadlines**, the **load monitor**, the in-app **logging page**, the **deadline machinery** (days remaining, hours per format, manual override), and **BI** links.

---

## 3. КППК as it exists in the codebase today

This matters because the project key already exists and currently *shows something else*:

- `projects` row `("kppk", "КППК")` — `back/src/scripts/seed_database.py:59`.
- URL slug mapping `kppk → kppk` — `front/src/shared/projects.ts:10-14`; the route is in the proxy matcher — `front/src/proxy.ts:22`.
- **The page renders the raw-materials workspace**: `front/src/features/report-workspace/report-workspace.tsx` switches only on `renovation`, everything else (including `kppk`) falls through to `RawMaterialsWorkspace`. The workspace fetches `/api/raw-materials/*` regardless of the project key; the key only drives the collaborator room and the header — `front/src/features/raw-materials/raw-materials-workspace.tsx:1354,1390,1488`.
- The collaboration WS path for `kppk` is deliberately mapped to the raw-materials endpoint — `front/src/shared/collab/ws-client.ts:109-113` — and the backend has **no КППК collaboration endpoint**; the room resolver rejects a client-selected КППК project — `docs/COLLAB.md:99`, `back/tests/test_collab.py:655-672`.
- There is **no `back/src/kppk/` module**, no КППК models, and no КППК API. `kppk` appears in the codebase only as a project row, a URL slug, a WS mapping and test fixtures.

So today «КППК» is an empty shell that borrows the «Сырьё» UI. Any КППК work starts from zero on the domain side.

---

## 4. The role model: what the source requires vs what exists

### 4.1 What the source requires

> «Права на чтение и запись раздаются пользователям по предоставленным спискам.» (p008)
> «В зависимости от назначенных прав пользователь имеет доступ до определенных шаблонов ввода с привязкой к предприятию (ДЗО).» (p009)
> «Поле выбора наименования ДЗО. Список соответствует правам доступа пользователя.» (p034, p059, p069)
> «Страница доступна только пользователям, наделенных правами куратора от бизнеса.» (p067)
> «Страница доступна только администратору системы.» (p082)

Decomposed:

| ID | Requirement | Status | Evidence / comment |
|---|---|---|---|
| R-10 | **Read/write rights** per user, coming from lists, not from a fixed pair of roles | `missing` | Today: a fixed enum per project — `admin` / `asset_worker` (`back/src/auth/models.py:15-19`) and, for renovation, `admin` / `approver` / `block_owner` / `asset_worker` + responsibility block (`back/src/renovation/models.py:12-30`). There is **no read-only role** and no separate read/write grant: `asset_worker` may edit its assets, everyone else with access may read. |
| R-11 | Access scoped to **template × enterprise (ДЗО/Актив)** | `different` | Access is scoped **per project** (activation flag `user_roles.is_active`, `back/src/auth/models.py:69-98`) plus **per asset** grants (`rm_user_assets`, `r_user_assets` — `back/src/renovation/models.py:56-77`). Templates are tabs inside a project, not grantable units. In «Сырьё» the visible row set is exactly the user's assets (`resolve_raw_materials_access`, `back/src/raw_materials/service.py:66-98`); in «Ремонт» central roles see everything (`RProjectRole.ADMIN/APPROVER/BLOCK_OWNER`) and `asset_worker` sees its assets (`back/src/renovation/service.py:169-210`). |
| R-12 | **Куратор от бизнеса** — cross-ДЗО reader + load monitor | `missing` | No such role anywhere. Renovation's `approver` is a *writer-side* approval role (`submit_plan` → `approve_plan`, `back/src/renovation/service.py:1749-1800`), not a read-only cross-enterprise monitor. |
| R-13 | **Администратор системы** — logging page + manual load override | `different` | A project `admin` exists (dictionaries, fixing — `back/src/raw_materials/router.py:140-160`, `back/src/renovation/router.py:145-156`), but there is no system-level administrator, no logging page, and no "load on behalf of the user" path. |
| R-14 | Role administration UI | `different` | Both projects manage users from a «Пользователи» section: raw materials `back/src/raw_materials/router.py:351,886,940,1073`, renovation `.../dictionary/users` (`back/src/renovation/router.py:776-860`) with role/block selects in `front/src/features/renovation/renovation-dictionary-section.tsx:90-120,770`. The source's "lists of rights" would have to be reflected there or in a new matrix. |

### 4.2 What this means

The current model is **project-scoped with a coarse role pair**. The source's model is **template-scoped with a read/write split plus two supervisory roles**. A КППК implementation needs (at minimum):

1. a **read-only grant** (viewer) distinguishable from the writer grant;
2. a grant keyed by **(user, template, enterprise)**, or a documented decision to keep project-level roles and treat the source's "lists" as a per-project membership list;
3. a **curator** role with a cross-enterprise read of save/load state (the monitor);
4. a **system administrator** distinct from the project administrator, or an explicit decision that the project admin is the system admin;
5. a place to manage all of it (the existing «Пользователи» section is the natural precedent).

`user_roles` is a single composite-PK table whose `role` column is bound to the `rm_project_role` enum (`back/src/auth/models.py:69-98`), and renovation had to add its own `r_user_roles` table with its own enum for exactly this reason (`back/src/renovation/models.py:79-111`). A КППК role set would follow the same pattern (a third table + enum) unless the model is deliberately reworked — worth deciding early, because migrations 0007+ already carry the precedent.

---

## 5. The tables: what the source specifies vs what exists

The source specifies three input templates, each described as a **flat table** with a column list, lengths, decimals and formulas. **None of them exists.** The nearest existing forms are «Движение»/«Цены» (raw materials) and the per-object renovation plan (`renovation_plan_rows`, 216 `col_*` columns — `back/src/renovation/plan.py`), which are different domains.

Note a modelling difference to decide on deliberately: the source puts `CODE`/`NAME` (enterprise code and full name) **in every row** of every template, i.e. a denormalized legacy sketch. This repository's convention is normalized — an `assets`/`r_assets` table plus an FK on each row. The normalized form is the better fit here (it is what the dictionary UI, the asset grants and the presence/lease scoping all assume), but it must be a conscious decision because the source literally lists the denormalized column.

### 5.1 «Производство продукции, план» (production of products, plan)

Periodicity: 1 раз в месяц. Period limit: not defined in the source («пока не знаю, уточним»).

| Column | Type | Meaning |
|---|---|---|
| `ID` | число | unique identifier, primary key |
| `CODE` | строка, 10 | enterprise code |
| `NAME` | строка, 100 | enterprise full name |
| `REP_DATE` | дата | report date |
| `PRODUCT` | строка, 200 | finished-product name |
| `PRODUCTION_BP` | число, 3 знака | business-plan gross production for the month |
| `PRODUCTION_PLAN` | число, 3 знака | operational-plan gross production |
| `GOODS_BP` | число, 3 знака | business-plan marketable production |
| `GOODS_PLAN` | число, 3 знака | operational-plan marketable production |
| `SELF_CONSUMPTION_BP` | число, 3 знака | business-plan intra-plant consumption = `PRODUCTION_BP − GOODS_BP` |
| `SELF_CONSUMPTION_PLAN` | число, 3 знака | operational-plan intra-plant consumption = `PRODUCTION_PLAN − GOODS_PLAN` |

Form logic: a single available Актив is auto-selected, otherwise the user must choose; the Актив determines the product list and the units (Рис.7 shows «Ед. изм.: т» and a «ВЗП» column = `SELF_CONSUMPTION_*`).

### 5.2 «Затраты на производство продукции, план» (production costs, plan)

Same identity columns (`ID`, `CODE`, `NAME`, `REP_DATE`, `PRODUCT`), then:

| Column | Type | Meaning |
|---|---|---|
| `FULL_COST_BP` / `FULL_COST_PLAN` | число, 3 знака | full cost per tonne, business plan / operational plan |
| `INPUT_COST_BP` / `INPUT_COST_PLAN` | число, 3 знака | production cost per tonne, business plan / plan |
| `VAR_EXP_BP` / `VAR_EXP_PLAN` | число, 3 знака | variable costs per tonne, business plan / plan |

Units per Рис.8: **руб/т**.

### 5.3 «Остатки товарной продукции, план» (stock of marketable products, plan)

Same identity columns, then (all «оперативный план», 3 decimals):

| Column | Meaning |
|---|---|
| `STOCK_TOTAL_BEGIN_PLAN` | total stock at the end of the previous period = `STOCK_external_BEGIN_PLAN + STOCK_PLANT_BEGIN_PLAN + STOCK_IN_TRANSPORT_BEGIN_PLAN` |
| `STOCK_IN_TRANSPORT_BEGIN_PLAN` | stock loaded into transport for a client, end of previous period |
| `STOCK_external_BEGIN_PLAN` | stock at the regional warehouse, end of previous period |
| `STOCK_PLANT_BEGIN_PLAN` | stock at the plant warehouse, end of previous period |
| `STOCK_TOTAL_END_PLAN` | total stock at the end of the planned period = `STOCK_TOTAL_BEGIN_PLAN + GOODS_PLAN − ОБЪЁМ ВЫКУПА` |
| `STOCK_IN_TRANSPORT_END_PLAN` | = `STOCK_TOTAL_END_PLAN − STOCK_external_END_PLAN − STOCK_PLANT_END_PLAN` |
| `STOCK_external_END_PLAN` | stock at the regional warehouse, end of planned period |
| `STOCK_PLANT_END_PLAN` | stock at the plant warehouse, end of planned period |

Two observations for the open-questions list: the column naming mixes cases (`STOCK_external_...`), and **«ОБЪЁМ ВЫКУПА»** appears in a formula but is **not declared in the data structure** (and note the cross-template dependency on `GOODS_PLAN` from §5.1).

### 5.4 What the codebase already gives these tables for free

If the КППК grid is built on the existing machinery, these are reusable as-is rather than re-invented:

- form schema + **computed columns** — precedent: 59 stored formula fields driven by `form_data/renovation-form.json` (`back/src/renovation/form_schema.py`, `docs/ARCHITECTURE.md`, "The renovation plan's 59 formula fields…"); the `SELF_CONSUMPTION_*`/`STOCK_*` formulas fit the same model;
- typed cells, numeric decimal handling, dictionaries (product lists, units), column filters/facets, windowed reads for large tables (`GET /renovation/plan/rows`, `docs/GRID.md`);
- collaborative editing, leases, presence, undo/redo (`docs/COLLAB.md`);
- Excel export with multi-level headers (`back/src/renovation/export.py`, `back/src/raw_materials/export.py`);
- the version/archive pattern that the "check report vs edited data" requirement resembles (`rm_report_snapshots`, renovation `working/pending_approval/archived` — `back/src/renovation/plan.py:16-21`).

---

## 6. Requirement-by-requirement gap analysis (pages, flow, deadlines)

### 6.1 Start page and session

| ID | Requirement (quoted) | Status | Where |
|---|---|---|---|
| R-01 | «На стартовой странице пользователю предлагается ввести логин и пароль» (p007) | `present` | `/api/auth/login` + local password login (`back/src/auth/router.py`), Keycloak OIDC in prod (`docs/KEYCLOAK.md`) |
| R-02 | «После однократного ввода логина и пароля браузер сохраняет данные и при загрузке страницы повторная авторизация не требуется» (p007) | `present` | `rc_session` cookie, default 12 h (`AUTH_SESSION_MAX_AGE_SECONDS`), proxy redirect (`front/src/proxy.ts`) |
| R-03 | «Через клик правой клавиши мыши пользователь может разавторизоваться» (p021 and passim) | `different` | Logout lives in the profile modal / header (`front/src/features/raw-materials/profile-modal.tsx`, `AppHeader` in `raw-materials-workspace.tsx:1818`), not a right-click on the name |

### 6.2 Registry of forms («Домашняя»)

| ID | Requirement | Status | Where |
|---|---|---|---|
| R-20 | A page listing the templates available to the user, one click → the input form | `missing` | The nearest thing is the project switcher in the header plus per-project tabs (`Brand`/`AppHeader`, `SECTION_LABELS` in `front/src/shared/grid/grid-config.ts:99-110`). There is no «Реестр форм» page and the root URL jumps straight into a project (`ARCHITECTURE.md:83`). |
| R-21 | FIO of the cabinet owner on the page | `present` | Header profile pill (`raw-materials-workspace.tsx:1830-1875`) |
| R-22 | «Остаток дней в календарных днях до окончания планового срока ввода данных», **one per template** (Рис.2: 5/20/15/45 дней) | `missing` | No deadline concept in the schema or API |

### 6.3 Input form page

| ID | Requirement | Status | Where / comment |
|---|---|---|---|
| R-30 | Template name, FIO, days remaining, the fields, date picker, ДЗО picker (p028-p034) | `different` | The grid workspace + report-month and asset filters exist; layout and naming differ; there is no deadline counter |
| R-31 | «Сохранить» icon + «Данные успешно сохранены» / «Ошибка. Данные не сохранены» (p035-p037) | `present` | Cell PATCH + toasts (`updateMovementCells`, `updateRenovationCells`; error toasts «Изменения не сохранены», e.g. `renovation-workspace.tsx:1173`) |
| R-32 | «Загрузить» icon → completeness, format and **deadline** checks, then a SQL insert, with the four exact messages (p038-p042, p099-p104) | `different` | Closest analogs: «Зафиксировать» (`POST /raw-materials/movement/fix`, `back/src/raw_materials/approve.py:73`) and the renovation submit/approve (`POST /renovation/plan/submit`, `/plan/approve`, `back/src/renovation/service.py:1749,1775`). Neither implements the source's validations: the deadline check does not exist, the completeness requirement was **removed** for «Движение» on 2026-09-04 (`approve.py:8-11`, `COMPLETENESS_FIELDS` is documented as "no longer mandatory"), the numeric-format check is enforced per-cell at edit time rather than at load time, and there is no «наименование показателей»-level error message |
| R-33 | «Вернуться на домашнюю страницу» with the unsaved-changes warning (p043, Рис.4) | `different` | There is no `beforeunload`/navigation guard; instead edits are persisted immediately, so nothing is lost — the requirement's premise does not hold in this architecture |
| R-34 | «Проверочный отчет» button with the same warning (p044) | `partial` | There is no check-report page; the warning has no analog |
| R-35 | «BI» button → BI dashboard (p045) | `missing` | No BI integration anywhere |
| R-36 | Navigation between input form ↔ check report ↔ home («Алгоритм работы с приложением», p085-p095) | `different` | Tabs + `?section=` deep links (`ARCHITECTURE.md:85`), not the described icon/button navigation |

### 6.4 Check report

| ID | Requirement | Status | Where |
|---|---|---|---|
| R-40 | Page per template with «наименование проверочного отчета/отчетов», ДЗО + date/period pickers defaulting to the input page's choices (p052-p059) | `partial` | Renovation has an archive **diff** (`GET /renovation/plan/diff`, `back/src/renovation/router.py:393`); raw materials have a read-only archive version view (`/movement/snapshot/{id}`, `router.py:259`). No per-template «Проверочный отчет» page and no ДЗО/period defaulting |
| R-41 | «выгрузка отчета» as **Excel or PDF** (p061) | `partial` | Excel only (`POST /raw-materials/export`, `GET|POST /renovation/plan/export`); no PDF generator |
| R-42 | «К внесению данных» button (p062) | `different` | Tab switching |
| R-43 | «BI» button (p063) | `missing` | — |

### 6.5 Load monitor (curator)

| ID | Requirement | Status |
|---|---|---|
| R-50 | Curator-only page, report + date pickers, table with №, ДЗО, last save date, load status (загружено/не загружено), load date (Рис.6) | `missing` |
| R-51 | Cell colouring: no save → red; save date (to the second) later than the load date → yellow; not loaded → red | `missing` |

There is no per-(template, ДЗО, date) save/load state table anywhere. Raw materials has `rm_report_snapshots` (fixed versions with `fixed_at`/`fixed_by_*`, `back/src/raw_materials/models.py`) — a starting point, but it is version-oriented, not a per-ДЗО progress matrix, and it does not distinguish "saved" from "loaded" per the source's semantics.

### 6.6 Logging page (administrator)

| ID | Requirement | Status | Where |
|---|---|---|---|
| R-60 | Page with дата-время, логин, код шаблона, наименование шаблона, действие (сохранить/загрузить), статус (успешно/ошибка с признаком) (p082) | `missing` (as a page) / `different` (as data) | Structured events go to a file and Loki via Alloy — `back/src/app_logging.py` (`http_request`, `collab.ws_*`, `keycloak_identity_received`), viewed in Grafana (`docs/ARCHITECTURE.md`, "Logging"). There is no save/load audit trail exposed to the application and no admin UI |

### 6.7 Deadlines

| ID | Requirement | Status |
|---|---|---|
| R-70 | «Остаток дней … до окончания планового срока ввода данных» on the registry, the input form and the check report (p023, p031, p056) | `missing` |
| R-71 | Per-format deadline expressed in **hours**: a load later than the report date by more than that many hours is refused (p099) | `missing` — no deadline/hours field exists on any form |
| R-72 | «Загрузка данных возможна вручную администратором, после письменного (e-mail) обращения ответственного лица» (p099) | `missing` |

---

## 7. Open questions for the client (do not guess these)

1. **Deadline configuration**: the source itself writes «Предельный срок заполнения данных: пока не знаю, уточним» for all three templates, yet also speaks of a *per-format* number of hours and "days remaining" on screen. Who sets it, per template or per enterprise? Is it derived from the report date (e.g. report date + N days) or an explicit calendar?
2. **ДЗО vs Актив**: p034 says «наименование ДЗО», p126 says «если пользователю доступны данные только по 1 Активу». Are ДЗО and Актив the same list? The repository currently has **two** parallel enterprise lists — `assets` (Сырьё) and `r_assets`/«Предприятия» (Ремонт) — and the source's appendix with the ДЗО list is not part of this file.
3. **«ОБЪЁМ ВЫКУПА»** in the stock formula (p173) is not declared in the data structure. Is it a per-month manual input, and is `GOODS_PLAN` shared with template 1?
4. **Which of the three templates is КППК?** Рис.2 lists the registry as «Шаблон ввода данных к КППиК», «…по оборотному капиталу», «…к плановому комитету» — i.e. the document describes a three-template portal, while this repository has one project row `kppk`. Is КППК one template (which one?) or the whole portal with the three templates above?
5. **Read/write lists**: are the "provided lists" per user × template × enterprise (a matrix), or a per-project membership list with a viewer flag? This decides whether КППК gets a third role table (`r_user_roles`-style) or the role model is generalized.
6. **«Загрузить» semantics**: is the load a *publication/publish* of already-saved data (the current `fix`/`submit` analogy), or does the save step only keep a draft? This decides whether "Дата последнего сохранения" in the monitor means the last cell edit or the last explicit save.
7. **Periodicity**: all three templates are monthly — is `REP_DATE` the first day of the month like `rm_movement_periods`, and does a load close the period (like a final fix)?
8. **BI**: which BI system and how is the dashboard URL derived per template (a link template with template/ДЗО/date parameters)?
9. **PDF export**: is a real PDF required, or is "excel или pdf" "Excel or PDF, either one"?
10. **Logging retention**: an in-app log table with its own retention, or an application view over the existing Loki/Grafana pipeline?

---

## 8. Suggested order of work (proposal, not a commitment)

1. **Decide the model items in §7** (1, 2, 4, 5, 6) — they block schema design.
2. **КППК data model + migration** for the three templates, normalized onto a КППК enterprise table, in the renovation-plan style (typed columns + stored computed columns), with `(template, enterprise, report_date)` uniqueness.
3. **КППК API module** (`back/src/kppk/`) with its own roles, reusing `collab` for editing; a КППК WS endpoint (currently missing — `docs/COLLAB.md:99`).
4. **Form registry + deadlines** — the smallest visible piece that the source puts first, and the one that gives «одно окно» context to everything else.
5. **Save/load lifecycle + the four validation messages**, on top of the existing lease/period machinery.
6. **Load monitor** (curator) and **logging page** — both read the lifecycle state produced in step 5.
7. **Check report + Excel/PDF export**, reusing the renovation export/report rendering.
8. Only then the shell polish: right-click logout, unsaved-changes guards, BI links.

---

## 9. Evidence index (what was checked)

- Source parse: `docs/Дополнения форм.docx` → 185 body paragraphs (43 plain, 131 list items, 11 headings), 9 figures + letterhead logo, ~14 190 characters of body text (the images carry the layout, not hidden data).
- Roles: `back/src/auth/models.py:15-19,69-98`; `back/src/renovation/models.py:12-30,56-77,79-111`; `back/src/renovation/service.py:169-210,1749-1800`; `back/src/raw_materials/service.py:66-98`; `back/src/raw_materials/router.py:140-160`; `back/src/renovation/router.py:127-156`.
- Project wiring: `back/src/scripts/seed_database.py:56-60`; `front/src/shared/projects.ts:10-14`; `front/src/proxy.ts:22`; `front/src/features/report-workspace/report-workspace.tsx`; `front/src/shared/collab/ws-client.ts:109-113`; `back/tests/test_collab.py:655-672`; `docs/COLLAB.md:99`.
- Lifecycle: `back/src/raw_materials/approve.py:1-30,73-232`; `back/src/renovation/plan.py:16-21`; `back/src/renovation/service.py:1749,1775`; `back/src/raw_materials/router.py:233-320,372`; `back/src/renovation/router.py:393,408,422,473,489`.
- Forms/grid/export: `back/src/renovation/form_schema.py`; `back/src/renovation/export.py`; `back/src/raw_materials/export.py`; `front/src/shared/grid/grid-config.ts`; `docs/GRID.md`.
- Logging: `back/src/app_logging.py`; `docs/ARCHITECTURE.md` ("Logging"); `docs/KEYCLOAK.md`.
- Session/auth: `back/src/auth/router.py`, `back/src/auth/service.py`, `front/src/proxy.ts`, `front/src/features/raw-materials/profile-modal.tsx`, `docs/ARCHITECTURE.md` ("Authentication").
- Conventions: `AGENTS.md` (docs in English, UI strings in Russian, migrations only forward, `just` commands).
