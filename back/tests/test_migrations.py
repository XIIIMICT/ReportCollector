"""Fresh migration, roundtrip, drift rejection and lossless legacy adoption."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import BACKEND_DIR
from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from migrations.adopt_baseline import BASELINE, LEGACY_HEAD, adopt
from migrations.upgrade import upgrade


def config_for(connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["connection"] = connection
    return config


# `''` and NULL are the same "never updated" state for the «Цены» audit
# timestamps: migration 0006 converts the legacy empty strings to NULL. The
# snapshot normalizes both to `''` so a downgrade→upgrade round-trip compares
# equal; the dedicated assertions below still pin the actual type change.
_EMPTY_AS_NULL_COLUMNS: dict[str, tuple[str, ...]] = {
    "rm_raw_material_prices": ("business_plan_updated_at", "storage_standard_updated_at"),
}

# Columns the migration chain legitimately drops (0007_drop_plan_correction):
# they exist on the pre-adoption snapshot but not after the upgrade, so the data
# comparison excludes them.
_DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "renovation_plan_rows": ("col_218",),
}


def business_snapshot(connection) -> dict[str, list[str]]:
    """Compare every stored business column, not just selected example fields."""
    tables = inspect(connection).get_table_names()
    quote = connection.dialect.identifier_preparer.quote
    snapshot: dict[str, list[str]] = {}
    for table in tables:
        if table == "alembic_version" or table.startswith("collab_"):
            continue
        normalized = _EMPTY_AS_NULL_COLUMNS.get(table)
        if normalized:
            patch = ", ".join(f"'{name}', coalesce({quote(name)}::text, '')" for name in normalized)
            expression = f"(to_jsonb(t) || jsonb_build_object({patch}))::text"
        else:
            expression = "row_to_json(t)::text"
        dropped = _DROPPED_COLUMNS.get(table)
        if dropped:
            removal = " - ".join(repr(name) for name in dropped)
            expression = f"(row_to_json(t)::jsonb - {removal})::text"
        snapshot[table] = list(
            connection.execute(
                # Names come from the inspector and are dialect-quoted, never user input.
                text(f"SELECT {expression} FROM {quote(table)} AS t ORDER BY {expression}")  # noqa: S608
            ).scalars()
        )
    return snapshot


async def test_baseline_roundtrip(test_engine) -> None:
    from conftest import TEST_SCHEMA

    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        config = config_for(conn.sync_connection)
        for _ in range(2):
            await conn.run_sync(lambda _: command.upgrade(config, "head"))
            diffs = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), SQLModel.metadata))
            assert diffs == []
            await conn.run_sync(lambda _: command.downgrade(config, "base"))


async def test_adopt_preserves_data_and_refuses_drift(db_session, test_engine, monkeypatch) -> None:
    monkeypatch.chdir(BACKEND_DIR)
    await db_session.commit()
    async with test_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.stamp(config, BASELINE))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(text("CREATE TABLE rm_edit_log (id integer PRIMARY KEY, payload text)"))
        await conn.execute(text("INSERT INTO rm_edit_log VALUES (1, 'legacy journal')"))
        before = (await conn.execute(text("SELECT id, col_011 FROM renovation_plan_rows ORDER BY id"))).all()
        await conn.run_sync(adopt)
        await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT payload FROM rm_edit_log"))).scalar_one() == "legacy journal"
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == BASELINE
        assert (await conn.execute(text("SELECT id, col_011 FROM renovation_plan_rows ORDER BY id"))).all() == before
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        # Drop a tracked (non-legacy-only) column to force real drift; col_218
        # is on the tolerated list now (0007 drops it during the upgrade).
        await conn.execute(text("ALTER TABLE renovation_plan_rows DROP COLUMN col_013"))
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.parametrize("version", ["unknown", "0014_plan_row_asset_nullable"])
async def test_adopt_refuses_unknown_or_incomplete_history(db_session, test_engine, version) -> None:
    async with test_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": version})
        with pytest.raises(ValueError, match="Неподдерживаемая ревизия"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == version


async def test_adopt_refuses_enum_drift(db_session, test_engine) -> None:
    async with test_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(text("ALTER TYPE user_theme ADD VALUE 'unexpected'"))
        with pytest.raises(ValueError, match="Схема отличается: значения enum"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


async def test_adoption_rolls_back_with_its_transaction(db_session, test_engine) -> None:
    async with test_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
    async with test_engine.connect() as conn:
        transaction = await conn.begin()
        await conn.run_sync(adopt)
        await transaction.rollback()
    async with test_engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.parametrize("legacy_default", [False, True])
async def test_upgrade_legacy_head_preserves_data_and_is_repeatable(db_session, test_engine, legacy_default) -> None:
    await db_session.close()
    async with test_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
        if legacy_default:
            # Legacy 0008 adds this default; metadata.create_all did not.
            await conn.execute(
                text("ALTER TABLE renovation_reference_values ALTER COLUMN created_at SET DEFAULT now()")
            )
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        all_business_data = await conn.run_sync(business_snapshot)
        timestamps = (
            await conn.execute(text("SELECT id, created_at FROM renovation_reference_values ORDER BY id"))
        ).all()
        before = (await conn.execute(text("SELECT id, col_011 FROM renovation_plan_rows ORDER BY id"))).all()
        await conn.run_sync(upgrade)
        await conn.run_sync(upgrade)
        # Tables created by migrations that run after the baseline
        # (since 0008_kppk_source_tables) do not exist on the legacy schema;
        # the pre-existing tables must keep their data unchanged.
        after = await conn.run_sync(business_snapshot)
        assert {table: after[table] for table in all_business_data} == all_business_data
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0011_kppk_realization_buyout"
        assert (await conn.execute(text("SELECT id, col_011 FROM renovation_plan_rows ORDER BY id"))).all() == before
        assert (await conn.execute(text("SELECT count(*) FROM collab_outbox"))).scalar_one() == 0
        assert (
            await conn.execute(text("SELECT id, created_at FROM renovation_reference_values ORDER BY id"))
        ).all() == timestamps
        # 0006: the legacy empty-string audit timestamps are real timestamptz
        # columns now, with '' folded to NULL.
        audit_columns = (
            await conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'rm_raw_material_prices' "
                    "AND column_name IN ('business_plan_updated_at', 'storage_standard_updated_at') "
                    "ORDER BY column_name"
                )
            )
        ).all()
        assert audit_columns == [("timestamp with time zone", "YES"), ("timestamp with time zone", "YES")]
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM rm_raw_material_prices "
                    "WHERE business_plan_updated_at IS NOT NULL OR storage_standard_updated_at IS NOT NULL"
                )
            )
        ).scalar_one() == 0
        diffs = await conn.run_sync(
            lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"compare_server_default": True}), SQLModel.metadata
            )
        )
        assert diffs == []


async def test_upgrade_converts_price_audit_junk_to_null(db_session, test_engine) -> None:
    """0006 must not abort on a non-datetime legacy value; it folds it to NULL."""
    await db_session.close()
    async with test_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
        row_id = (await conn.execute(text("SELECT id FROM rm_raw_material_prices ORDER BY id LIMIT 1"))).scalar_one()
        await conn.execute(
            text(
                "UPDATE rm_raw_material_prices SET business_plan_updated_at = '<УГАДАЙТЕ>', "
                "storage_standard_updated_at = '2026-09-28T06:12:52.123456+00:00' WHERE id = :id"
            ),
            {"id": row_id},
        )
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.run_sync(upgrade)
        business_plan, storage_standard = (
            await conn.execute(
                text(
                    "SELECT business_plan_updated_at, storage_standard_updated_at "
                    "FROM rm_raw_material_prices WHERE id = :id"
                ),
                {"id": row_id},
            )
        ).one()
    assert business_plan is None
    assert storage_standard is not None


async def test_adopt_rejects_unexpected_reference_timestamp_default(db_session, test_engine) -> None:
    await db_session.close()
    async with test_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(
            text(
                "ALTER TABLE renovation_reference_values ALTER COLUMN created_at SET DEFAULT '2000-01-01'::timestamptz"
            )
        )
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(upgrade)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


async def test_adopt_accepts_legacy_index_names_and_dropped_column(db_session, test_engine) -> None:
    """
    Adopt despite the legacy class-derived index names and the dropped column.

    A legacy 0015 install named some indexes after the model class and kept a
    `renovation_reference_values.metadata` column the models dropped. Adoption
    must treat those as equivalent instead of refusing.
    """
    await db_session.close()
    async with test_engine.begin() as conn:
        # Reproduce the legacy naming: rename the canonical indexes to the
        # class-derived ones and re-add the extra column.
        await conn.execute(text("ALTER INDEX ix_app_users_email RENAME TO ix_app_user_email"))
        await conn.execute(text("ALTER INDEX ix_projects_key RENAME TO ix_project_key"))
        await conn.execute(
            text(
                "ALTER INDEX ix_rm_asset_raw_materials_raw_material_id RENAME TO ix_rm_asset_raw_material_raw_material_id"
            )
        )
        await conn.execute(
            text("ALTER INDEX ix_rm_material_movements_report_month RENAME TO ix_rm_material_movement_report_month")
        )
        await conn.execute(text("ALTER INDEX ix_rm_report_snapshots_fixed_at RENAME TO ix_rm_report_snapshot_fixed_at"))
        await conn.execute(
            text("ALTER INDEX ix_rm_report_snapshots_report_month RENAME TO ix_rm_report_snapshot_report_month")
        )
        await conn.execute(text("ALTER INDEX ix_rm_user_assets_asset_id RENAME TO ix_rm_user_asset_asset_id"))
        await conn.execute(text("ALTER TABLE renovation_reference_values ADD COLUMN metadata TEXT"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == BASELINE
        # The legacy column is preserved, not dropped by adoption.
        assert (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'renovation_reference_values' "
                    "AND column_name = 'metadata'"
                )
            )
        ).scalar_one() == "metadata"


async def test_adopt_accepts_unique_constraint_plus_plain_index(db_session, test_engine) -> None:
    """
    Adopt when uniqueness is enforced by a constraint plus a plain index.

    A legacy DB enforces `r_assets.code` uniqueness with a UNIQUE CONSTRAINT and a
    NON-unique index; the models declare one unique index. Adoption must see the
    same guarantee and accept the difference.
    """
    await db_session.close()
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_r_assets_code"))
        await conn.execute(text("ALTER TABLE r_assets DROP CONSTRAINT IF EXISTS r_assets_code_key"))
        await conn.execute(text("ALTER TABLE r_assets ADD CONSTRAINT r_assets_code_key UNIQUE (code)"))
        await conn.execute(text("CREATE INDEX ix_r_assets_code ON r_assets (code)"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == BASELINE


async def test_adopt_still_refuses_a_genuine_index_mismatch(db_session, test_engine) -> None:
    """An index removed without an equivalent replacement is real drift."""
    await db_session.close()
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_app_users_email"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


async def test_adopt_still_refuses_a_non_audit_column_type_change(db_session, test_engine) -> None:
    """The deferred-0006 tolerance is a closed list: any other type change is drift."""
    await db_session.close()
    async with test_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE rm_raw_material_prices ALTER COLUMN fact_2025 TYPE text"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


async def test_upgrade_prunes_retired_revision_files(db_session, test_engine) -> None:
    """
    Upgrade despite leftover files from the retired historical chain.

    A checkout updated in place keeps the deleted revisions as untracked files;
    because the new chain reuses some numbers, Alembic then sees several heads and
    "upgrade head" fails. The wrapper prunes exactly the known legacy names.
    """
    from conftest import BACKEND_DIR

    await db_session.close()
    versions = BACKEND_DIR / "migrations" / "versions"
    stale = [versions / name for name in ("0001_collab.py", "0003_snapshot_tables.py", "0015_plan_row_col_218.py")]
    for path in stale:
        path.write_text(
            '"""stale retired revision."""\n\n'
            f'revision = "stale_{path.stem}"\n'
            "down_revision = None\n"
            "branch_labels = None\n"
            "depends_on = None\n\n\n"
            "def upgrade() -> None: ...\n\n\n"
            "def downgrade() -> None: ...\n",
            encoding="utf-8",
        )
    try:
        async with test_engine.begin() as conn:
            config = config_for(conn.sync_connection)
            # Legacy pre-state: the schema is at the baseline, the marker at 0015.
            await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
            await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
            await conn.run_sync(upgrade)
            assert (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "0011_kppk_realization_buyout"
        assert all(not path.exists() for path in stale)
    finally:
        for path in stale:
            path.unlink(missing_ok=True)


async def test_upgrade_fresh_schema(test_engine) -> None:
    from conftest import TEST_SCHEMA

    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
        await conn.run_sync(upgrade)
        diffs = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), SQLModel.metadata))
        assert diffs == []


async def test_upgrade_failure_rolls_back_legacy_marker(db_session, test_engine, monkeypatch) -> None:
    from migrations import upgrade as module

    await db_session.close()
    async with test_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})

    def fail(*_args: object) -> None:
        message = "injected migration failure"
        raise RuntimeError(message)

    monkeypatch.setattr(module.command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        async with test_engine.begin() as conn:
            await conn.run_sync(upgrade)
    async with test_engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


async def test_completed_upgrade_rolls_back_data_ddl_and_marker(db_session, test_engine) -> None:
    await db_session.close()
    async with test_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        before = await conn.run_sync(business_snapshot)
    async with test_engine.connect() as conn:
        transaction = await conn.begin()
        await conn.run_sync(upgrade)
        await transaction.rollback()
    async with test_engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD
        assert await conn.run_sync(business_snapshot) == before
        assert (await conn.execute(text("SELECT to_regclass('collab_outbox')"))).scalar_one() is None
        assert (await conn.execute(text("SELECT to_regclass('collab_action_id_seq')"))).scalar_one() is None
        default = await conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'renovation_reference_values' "
                "AND column_name = 'created_at'"
            )
        )
        assert default.scalar_one() is None
