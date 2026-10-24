"""Recover application copies without changing sources or unrelated projects."""

from decimal import Decimal
from types import SimpleNamespace

from conftest import TEST_SCHEMA
from sqlalchemy import inspect, text

from src.kppk.constants import KPPK_SOURCE_TABLES
from src.scripts import seed_kppk
from src.scripts.seed_kppk import _ensure_copy_tables, _truncate_copies

TABLES = (*KPPK_SOURCE_TABLES.values(), "kppk_org_products", "kppk_cost")


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
    assert any(item["column_names"] == ["org_id", "period_date", "product"] for item in unique)
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
    assert counters["kppk_realization"] == 1
    assert counters["kppk_realization_plan"] == 1
    assert counters["kppk_production_plan"] == 1
    assert counters["kppk_cost"] == 1
    assert counters["matrix_links"] == 1
    row = (
        await connection.execute(
            text(
                f'SELECT fact, excise_fact, buyout_fact FROM "{TEST_SCHEMA}".kppk_realization '  # noqa: S608 — test schema
                "WHERE product = :product AND region = 'RU'"
            ).bindparams(product="Test product")
        )
    ).one()
    assert tuple(row) == (None, None, None)  # NULLs of the source stay NULL
    # The fix exists because the copies used to coerce those NULLs to 0, which
    # invented figures on every re-seed; a real value is still copied verbatim.
    await connection.execute(
        text(
            f'UPDATE "{source_schema}".kppk_f_realization SET fact = 12.5, excise_fact = 3 '  # noqa: S608 — test schema
            "WHERE product = 'Test product'"
        )
    )
    await connection.execute(text(f'DELETE FROM "{TEST_SCHEMA}".kppk_realization'))  # noqa: S608 ? test schema
    await seed_kppk.seed(db_session)
    row = (
        await connection.execute(
            text(
                f'SELECT fact, excise_fact, buyout_fact FROM "{TEST_SCHEMA}".kppk_realization '  # noqa: S608 — test schema
                "WHERE product = :product AND region = 'RU'"
            ).bindparams(product="Test product")
        )
    ).one()
    assert tuple(row) == (Decimal("12.5"), Decimal(3), None)
    # The seed is idempotent as a whole: a second run copies nothing and adds
    # no period rows either (the generator only fills what is missing).
    assert (await seed_kppk.seed(db_session))["copy_tables_created"] == 0
