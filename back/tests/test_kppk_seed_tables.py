"""Recover application copies without changing sources or unrelated projects."""

from types import SimpleNamespace

from conftest import TEST_SCHEMA
from sqlalchemy import inspect, text

from src.kppk.constants import KPPK_SOURCE_TABLES
from src.scripts import seed_kppk
from src.scripts.seed_kppk import _ensure_copy_tables, _truncate_copies

TABLES = (*KPPK_SOURCE_TABLES.values(), "kppk_org_products", "kppk_cost", "kppk_markets")


async def test_seed_restores_all_missing_application_tables(db_session) -> None:
    connection = await db_session.connection()
    before = await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema=TEST_SCHEMA))
    for name in reversed(TABLES):
        await connection.execute(text(f'DROP TABLE IF EXISTS "{TEST_SCHEMA}"."{name}" CASCADE'))
    assert await _ensure_copy_tables(connection, TEST_SCHEMA) == len(TABLES)
    assert await _ensure_copy_tables(connection, TEST_SCHEMA) == 0
    after = await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema=TEST_SCHEMA))
    assert sorted(before) == sorted(after)
    columns = await connection.run_sync(
        lambda sync: inspect(sync).get_columns("kppk_realization_plan", schema=TEST_SCHEMA)
    )
    by_name = {column["name"]: column for column in columns}
    assert str(by_name["org_id"]["type"]) == "UUID"
    assert "period_id" not in by_name
    assert str(by_name["period_date"]["type"]) == "DATE"
    for name in ("buyout_bp_plan", "month_bp_plan", "excise_bp"):
        # Volumes and excise are nullable like every other figure of the copies:
        # "not filled" must be distinguishable from a measured zero.
        assert by_name[name]["nullable"] is True
        assert by_name[name]["default"] is None
    fks = await connection.run_sync(
        lambda sync: inspect(sync).get_foreign_keys("kppk_realization_plan", schema=TEST_SCHEMA)
    )
    assert any(fk["referred_table"] == "kppk_orgs" and fk["referred_schema"] == TEST_SCHEMA for fk in fks)
    unique = await connection.run_sync(
        lambda sync: inspect(sync).get_unique_constraints("kppk_cost", schema=TEST_SCHEMA)
    )
    assert any(item["column_names"] == ["org_id", "period_date", "product_id"] for item in unique)
    await _truncate_copies(connection, TEST_SCHEMA)


async def test_seed_creates_only_missing_table_and_preserves_existing_rows(db_session) -> None:
    connection = await db_session.connection()
    before = (
        await connection.execute(text(f'SELECT id, name FROM "{TEST_SCHEMA}".kppk_orgs ORDER BY id'))  # noqa: S608
    ).all()
    await connection.execute(text(f'DROP TABLE "{TEST_SCHEMA}".kppk_cost'))
    assert await _ensure_copy_tables(connection, TEST_SCHEMA) == 1
    after = (
        await connection.execute(text(f'SELECT id, name FROM "{TEST_SCHEMA}".kppk_orgs ORDER BY id'))  # noqa: S608
    ).all()
    assert before == after
    assert before


async def test_seed_recopies_sources_after_application_tables_were_deleted(db_session, monkeypatch) -> None:
    connection = await db_session.connection()
    source_schema = f"{TEST_SCHEMA}_kppk_source"
    await connection.execute(text(f'CREATE SCHEMA "{source_schema}"'))
    for statement in seed_kppk.split_sql_statements(seed_kppk._read_sql_file(seed_kppk.SOURCE_SCHEMA_PATH)):
        target = seed_kppk._statement_target(statement)
        if target is not None and target[0] == "raw":
            await connection.execute(text(statement.replace("raw.", f'"{source_schema}".')))
    # Sources live in a disposable test schema; shared raw/dict are never reset.
    inserts = (
        "INSERT INTO kppk_d_org VALUES (1, 'TEST', 'Test org', 0)",
        "INSERT INTO kppk_d_period VALUES (1, 'Июнь', 2026, '2026-06-01', '2026-06-30')",
        "INSERT INTO kppk_d_product_seq VALUES (1, 'Test product')",
        "INSERT INTO kppk_d_raw_seq VALUES (1, 'Test raw')",
        (
            "INSERT INTO kppk_f_realization (org_id, period_id, rep_date, product, region) "
            "VALUES (1, 1, '2026-06-30', 'Test product', 'RU')"
        ),
        (
            "INSERT INTO kppk_f_realization_plan (org_id, period_id, rep_date, product, region) "
            "VALUES (1, 1, '2026-06-30', 'Test product', 'RU')"
        ),
        (
            "INSERT INTO kppk_f_production_plan (org_id, period_id, rep_date, product, full_cost_bp) "
            "VALUES (1, 1, '2026-06-30', 'Test product', 125)"
        ),
    )
    for statement in inserts:
        await connection.execute(text(statement.replace("INSERT INTO ", f'INSERT INTO "{source_schema}".')))
    for name in reversed(TABLES):
        await connection.execute(text(f'DROP TABLE IF EXISTS "{TEST_SCHEMA}"."{name}" CASCADE'))

    # `_ensure_copy_tables` recreates `kppk_markets` EMPTY and the seed fills it
    # with the source markets («РФ», «ДЗ», «БЗ») — this test's source rows use
    # «RU», so that market is added right after the tables are restored and
    # before the copies resolve their references.
    real_ensure_copy_tables = seed_kppk._ensure_copy_tables

    async def ensure_with_source_market(conn, schema: str) -> int:
        created = await real_ensure_copy_tables(conn, schema)
        await conn.execute(
            text(
                f'INSERT INTO "{schema}".kppk_markets (id, code, name, created_at, updated_at) '  # noqa: S608 — test schema
                "VALUES (gen_random_uuid(), 'RU', 'Российская Федерация (RU)', now(), now()) "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
        return created

    monkeypatch.setattr(seed_kppk, "_ensure_copy_tables", ensure_with_source_market)

    async def source_schemas(_connection) -> list[str]:
        return []

    async def source_tables(_connection) -> int:
        return 0

    monkeypatch.setattr(seed_kppk, "KPPK_SOURCE_SCHEMA_RAW", source_schema)
    monkeypatch.setattr(seed_kppk, "get_settings", lambda: SimpleNamespace(postgres_schema=TEST_SCHEMA))
    monkeypatch.setattr(seed_kppk, "_ensure_schemas", source_schemas)
    monkeypatch.setattr(seed_kppk, "_ensure_source_tables", source_tables)
    monkeypatch.setattr(
        seed_kppk,
        "_matrix_matches",
        lambda: [{"org": "Test org", "product": "Test product", "hasExcise": False}],
    )
    counters = await seed_kppk.seed(db_session)
    assert counters["copy_tables_created"] == len(TABLES)
    # The period tables are REBUILT by the seed: the counter is the generated
    # row count (one matrix pair × the whole window), not the copied rows.
    assert counters["periods_realization_plan"] > 0
    assert counters["periods_realization"] > 0
    # The pure copies keep the source rows verbatim.
    assert counters["kppk_production_plan"] == 1
    assert counters["matrix_links"] == 1
    # A source figure in a PERIOD table is replaced by the empty period rows:
    # those three tables are the seed's scaffolding, filled by the business.
    period_rows = (
        await connection.execute(
            text(
                f'SELECT count(*) FROM "{TEST_SCHEMA}".kppk_realization r '  # noqa: S608 — test schema
                f'JOIN "{TEST_SCHEMA}".kppk_products p ON p.id = r.product_id '
                f'JOIN "{TEST_SCHEMA}".kppk_markets m ON m.id = r.market_id '
                "WHERE p.name = :product AND m.code = 'RU' AND r.fact IS NOT NULL"
            ).bindparams(product="Test product")
        )
    ).scalar_one()
    assert period_rows == 0, "the seed rebuilds the period tables, source figures included"
    # The build order carries the period window (September 2023 → next December).
    span = (
        await connection.execute(
            text(f'SELECT min(period_date), count(DISTINCT period_date) FROM "{TEST_SCHEMA}".kppk_cost')  # noqa: S608
        )
    ).one()
    assert str(span[0]) == "2023-09-01"
    assert span[1] == len(seed_kppk.target_periods())
    # The seed is idempotent as a whole: a second run copies nothing and the
    # generator produces the same rows again (it truncates, it does not append).
    again = await seed_kppk.seed(db_session)
    assert again["copy_tables_created"] == 0
    assert again["periods_realization_plan"] == counters["periods_realization_plan"]
    duplicates = (
        await connection.execute(
            text(
                f"SELECT count(*) - count(DISTINCT (org_id, period_date, product_id, market_id)) "  # noqa: S608
                f'FROM "{TEST_SCHEMA}".kppk_realization_plan'
            )
        )
    ).scalar_one()
    assert duplicates == 0, "the period rebuild must not duplicate rows"


async def test_normalized_sql_matches_python_normalization(db_session) -> None:
    """The SQL twin of `normalize_text` must produce the same string, always.

    It is duplicated on purpose (the copy runs server-side), so a rule added to
    one side and forgotten on the other silently breaks the copy — exactly how a
    `;`/`,` mismatch once made the seed abort on «неизвестный продукт».
    """
    connection = await db_session.connection()
    expression = seed_kppk._normalized_sql("product")
    samples = (
        "Прочая продукция (некондиция; восстановленные; РКО)",
        "Прочая продукция (некондиция, восстановленные, РКО)",
        'Средство "Белизна"; тыс. шт',
        '  Средство  "Белизна" ,  тыс.\u00a0шт ',
        "Строка\nс переносом",
        "двойной  пробел",
        "",
    )
    for sample in samples:
        sql_value = (
            await connection.execute(
                # The expression is built from server-owned column names only.
                text(f"SELECT {expression} FROM (SELECT CAST(:value AS text) AS product) s").bindparams(  # noqa: S608
                    value=sample
                )
            )
        ).scalar_one()
        assert sql_value == seed_kppk.normalize_text(sample), f"SQL and Python disagree on {sample!r}"


async def test_copy_resolves_product_whose_punctuation_differs(db_session, monkeypatch) -> None:
    """A dump spelled with `;` matches its dictionary entry spelled with `,`."""
    connection = await db_session.connection()
    source_schema = f"{TEST_SCHEMA}_kppk_punct"
    await connection.execute(text(f'CREATE SCHEMA "{source_schema}"'))
    for statement in seed_kppk.split_sql_statements(seed_kppk._read_sql_file(seed_kppk.SOURCE_SCHEMA_PATH)):
        target = seed_kppk._statement_target(statement)
        if target is not None and target[0] == "raw":
            await connection.execute(text(statement.replace("raw.", f'"{source_schema}".')))
    # The DICTIONARY keeps commas; the FACT rows carry semicolons.
    inserts = (
        "INSERT INTO kppk_d_org VALUES (1, 'TEST', 'Test org', 0)",
        "INSERT INTO kppk_d_period VALUES (1, 'Июнь', 2026, '2026-06-01', '2026-06-30')",
        "INSERT INTO kppk_d_product_seq VALUES (1, 'Прочая продукция (некондиция, восстановленные, РКО)')",
        "INSERT INTO kppk_d_raw_seq VALUES (1, 'Test raw')",
        (
            "INSERT INTO kppk_f_realization (org_id, period_id, rep_date, product, region) "
            "VALUES (1, 1, '2026-06-30', 'Прочая продукция (некондиция; восстановленные; РКО)', 'РФ')"
        ),
        (
            "INSERT INTO kppk_f_realization_plan (org_id, period_id, rep_date, product, region) "
            "VALUES (1, 1, '2026-06-30', 'Прочая продукция (некондиция; восстановленные; РКО)', 'РФ')"
        ),
    )
    for statement in inserts:
        await connection.execute(text(statement.replace("INSERT INTO ", f'INSERT INTO "{source_schema}".')))
    for name in reversed(TABLES):
        await connection.execute(text(f'DROP TABLE IF EXISTS "{TEST_SCHEMA}"."{name}" CASCADE'))

    async def source_schemas(_connection) -> list[str]:
        return []

    async def source_tables(_connection) -> int:
        return 0

    monkeypatch.setattr(seed_kppk, "KPPK_SOURCE_SCHEMA_RAW", source_schema)
    monkeypatch.setattr(seed_kppk, "get_settings", lambda: SimpleNamespace(postgres_schema=TEST_SCHEMA))
    monkeypatch.setattr(seed_kppk, "_ensure_schemas", source_schemas)
    monkeypatch.setattr(seed_kppk, "_ensure_source_tables", source_tables)
    monkeypatch.setattr(seed_kppk, "_matrix_matches", list)
    counters = await seed_kppk.seed(db_session)
    # The row resolved instead of aborting the whole seed.
    assert counters["kppk_products"] == 1
    # What got stored is the NORMALIZED name, so it matches the dictionary row.
    stored = (
        (
            await connection.execute(
                text(f'SELECT name FROM "{TEST_SCHEMA}".kppk_products')  # noqa: S608 — test schema
            )
        )
        .scalars()
        .all()
    )
    assert stored == ["Прочая продукция (некондиция, восстановленные, РКО)"]


async def test_copy_creates_product_missing_from_the_source_dictionary(db_session, monkeypatch) -> None:
    """A plan row naming an unknown product seeds under «@» instead of aborting.

    The source dumps are not self-consistent: prod's production plan references
    «Абсорбент А-2/Л/очищенный», which `raw.kppk_d_product_seq` never carried.
    """
    connection = await db_session.connection()
    source_schema = f"{TEST_SCHEMA}_kppk_absent"
    await connection.execute(text(f'CREATE SCHEMA "{source_schema}"'))
    for statement in seed_kppk.split_sql_statements(seed_kppk._read_sql_file(seed_kppk.SOURCE_SCHEMA_PATH)):
        target = seed_kppk._statement_target(statement)
        if target is not None and target[0] == "raw":
            await connection.execute(text(statement.replace("raw.", f'"{source_schema}".')))
    inserts = (
        "INSERT INTO kppk_d_org VALUES (1, 'TEST', 'Test org', 0)",
        "INSERT INTO kppk_d_period VALUES (1, 'Июнь', 2026, '2026-06-01', '2026-06-30')",
        # The dictionary knows only this product …
        "INSERT INTO kppk_d_product_seq VALUES (1, 'Known product')",
        "INSERT INTO kppk_d_raw_seq VALUES (1, 'Test raw')",
        # … while the production plan also references one it never carried.
        (
            "INSERT INTO kppk_f_production_plan (org_id, period_id, rep_date, product) "
            "VALUES (1, 1, '2026-06-30', 'Абсорбент А-2')"
        ),
    )
    for statement in inserts:
        await connection.execute(text(statement.replace("INSERT INTO ", f'INSERT INTO "{source_schema}".')))
    for name in reversed(TABLES):
        await connection.execute(text(f'DROP TABLE IF EXISTS "{TEST_SCHEMA}"."{name}" CASCADE'))

    async def source_schemas(_connection) -> list[str]:
        return []

    async def source_tables(_connection) -> int:
        return 0

    monkeypatch.setattr(seed_kppk, "KPPK_SOURCE_SCHEMA_RAW", source_schema)
    monkeypatch.setattr(seed_kppk, "get_settings", lambda: SimpleNamespace(postgres_schema=TEST_SCHEMA))
    monkeypatch.setattr(seed_kppk, "_ensure_schemas", source_schemas)
    monkeypatch.setattr(seed_kppk, "_ensure_source_tables", source_tables)
    monkeypatch.setattr(seed_kppk, "_matrix_matches", list)
    counters = await seed_kppk.seed(db_session)
    # The plan row loaded instead of aborting the whole seed.
    assert counters["kppk_production_plan"] == 1
    # It is a real product row, without a source num, under the «@» marker.
    created = (
        await connection.execute(
            text(
                f'SELECT name, product_num FROM "{TEST_SCHEMA}".kppk_products '  # noqa: S608 — test schema
                "WHERE name LIKE '@%'"
            )
        )
    ).all()
    assert [(name, num) for name, num in created] == [("@Абсорбент А-2", None)]
    # The copy resolved the plan row to that product rather than skipping it.
    linked = (
        (
            await connection.execute(
                text(
                    f'SELECT p.name FROM "{TEST_SCHEMA}".kppk_production_plan pp '  # noqa: S608 — test schema
                    f'JOIN "{TEST_SCHEMA}".kppk_products p ON p.id = pp.product_id'
                )
            )
        )
        .scalars()
        .all()
    )
    assert linked == ["@Абсорбент А-2"]

    # A second run reuses the product: no duplicate, no extra counter.
    again = await seed_kppk.seed(db_session)
    assert again["kppk_production_plan"] == 1
    duplicates = (
        await connection.execute(
            text(
                f'SELECT count(*) FROM "{TEST_SCHEMA}".kppk_products '  # noqa: S608 — test schema
                "WHERE name LIKE '@%'"
            )
        )
    ).scalar_one()
    assert duplicates == 1, "the auto-created product must not be duplicated on re-seed"
