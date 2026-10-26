"""Fresh migration, roundtrip, drift rejection and lossless legacy adoption."""

from importlib import import_module

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import BACKEND_DIR, MIGRATION_SCHEMA
from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from migrations.adopt_baseline import BASELINE, LEGACY_HEAD, adopt
from migrations.upgrade import upgrade

_AUDIT_TABLES = frozenset(import_module("migrations.versions.0015_updater_user_catalog").TABLES)


def _head_revision() -> str:
    """Read the chain's head instead of pinning a literal.

    These assertions mean "at head", not "at revision X" — a new migration used
    to break them for no reason.
    """
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None, "the migration chain has no head"
    return head


HEAD_REVISION = _head_revision()


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
    "r_plan_rows": ("col_218",),
}

_RENOVATION_TABLE_RENAMES = {
    "renovation_reference_values": "r_reference_values",
    "renovation_plans": "r_plans",
    "renovation_plan_rows": "r_plan_rows",
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
            expression = f"(to_jsonb(t) || jsonb_build_object({patch}))"
        else:
            expression = "to_jsonb(t)"
        canonical_table = _RENOVATION_TABLE_RENAMES.get(table, table)
        dropped = _DROPPED_COLUMNS.get(canonical_table, ())
        if canonical_table in _AUDIT_TABLES:
            dropped += ("updated_by", "updater_user_id")
        # 0019-0022 dropped the renovation catalog order/key/payload columns and
        # added the soft-delete marker: they exist on the pre-adoption snapshot
        # but are not business values.
        if canonical_table == "r_reference_values":
            dropped += ("sort_order", "value_key", "payload", "deleted_at")
        if dropped:
            removal = " - ".join(repr(name) for name in dropped)
            expression = f"({expression} - {removal})"
        expression = f"{expression}::text"
        snapshot[_RENOVATION_TABLE_RENAMES.get(table, table)] = list(
            connection.execute(
                # Names come from the inspector and are dialect-quoted, never user input.
                text(f"SELECT {expression} FROM {quote(table)} AS t ORDER BY {expression}")  # noqa: S608
            ).scalars()
        )
    return snapshot


@pytest.mark.migration_schema
async def test_baseline_roundtrip(migration_engine) -> None:
    # Its own schema: this test goes all the way to `base`, which would make the
    # shared migration schema unusable for every test after it.
    async with migration_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{MIGRATION_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{MIGRATION_SCHEMA}"'))
        config = config_for(conn.sync_connection)
        for _ in range(2):
            await conn.run_sync(lambda _: command.upgrade(config, "head"))
            diffs = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), SQLModel.metadata))
            assert diffs == []
            await conn.run_sync(lambda _: command.downgrade(config, "base"))


@pytest.mark.migration_schema
async def test_adopt_preserves_data_and_refuses_drift(db_session, monkeypatch, migration_engine) -> None:
    monkeypatch.chdir(BACKEND_DIR)
    await db_session.commit()
    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
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
@pytest.mark.migration_schema
async def test_adopt_refuses_unknown_or_incomplete_history(db_session, version, migration_engine) -> None:
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": version})
        with pytest.raises(ValueError, match="Неподдерживаемая ревизия"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == version


@pytest.mark.migration_schema
async def test_adopt_refuses_enum_drift(db_session, migration_engine) -> None:
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(text("ALTER TYPE user_theme ADD VALUE 'unexpected'"))
        with pytest.raises(ValueError, match="Схема отличается: значения enum"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.migration_schema
async def test_adoption_rolls_back_with_its_transaction(db_session, migration_engine) -> None:
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
    async with migration_engine.connect() as conn:
        transaction = await conn.begin()
        await conn.run_sync(adopt)
        await transaction.rollback()
    async with migration_engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.parametrize("legacy_default", [False, True])
@pytest.mark.migration_schema
async def test_upgrade_legacy_head_preserves_data_and_is_repeatable(
    db_session, legacy_default, migration_engine
) -> None:
    await db_session.close()
    async with migration_engine.begin() as conn:
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
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == HEAD_REVISION
        assert (await conn.execute(text("SELECT id, col_011 FROM r_plan_rows ORDER BY id"))).all() == before
        assert (await conn.execute(text("SELECT count(*) FROM collab_outbox"))).scalar_one() == 0
        assert (
            await conn.execute(text("SELECT id, created_at FROM r_reference_values ORDER BY id"))
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


@pytest.mark.migration_schema
async def test_upgrade_converts_price_audit_junk_to_null(db_session, migration_engine) -> None:
    """0006 must not abort on a non-datetime legacy value; it folds it to NULL."""
    await db_session.close()
    async with migration_engine.begin() as conn:
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


@pytest.mark.migration_schema
async def test_adopt_rejects_unexpected_reference_timestamp_default(db_session, migration_engine) -> None:
    await db_session.close()
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(
            text("ALTER TABLE r_reference_values ALTER COLUMN created_at SET DEFAULT '2000-01-01'::timestamptz")
        )
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(upgrade)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.migration_schema
async def test_adopt_accepts_legacy_index_names_and_dropped_column(db_session, migration_engine) -> None:
    """
    Adopt despite the legacy class-derived index names and the dropped column.

    A legacy 0015 install named some indexes after the model class and kept a
    `renovation_reference_values.metadata` column the models dropped. Adoption
    must treat those as equivalent instead of refusing.
    """
    await db_session.close()
    async with migration_engine.begin() as conn:
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
        await conn.execute(text("ALTER TABLE r_reference_values ADD COLUMN metadata TEXT"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == BASELINE
        # The legacy column is preserved, not dropped by adoption.
        assert (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'r_reference_values' "
                    "AND column_name = 'metadata'"
                )
            )
        ).scalar_one() == "metadata"


@pytest.mark.migration_schema
async def test_adopt_accepts_unique_constraint_plus_plain_index(db_session, migration_engine) -> None:
    """
    Adopt when uniqueness is enforced by a constraint plus a plain index.

    A legacy DB enforces `r_assets.code` uniqueness with a UNIQUE CONSTRAINT and a
    NON-unique index; the models declare one unique index. Adoption must see the
    same guarantee and accept the difference.
    """
    await db_session.close()
    async with migration_engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_r_assets_code"))
        await conn.execute(text("ALTER TABLE r_assets DROP CONSTRAINT IF EXISTS r_assets_code_key"))
        await conn.execute(text("ALTER TABLE r_assets ADD CONSTRAINT r_assets_code_key UNIQUE (code)"))
        await conn.execute(text("CREATE INDEX ix_r_assets_code ON r_assets (code)"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == BASELINE


@pytest.mark.migration_schema
async def test_adopt_still_refuses_a_genuine_index_mismatch(db_session, migration_engine) -> None:
    """An index removed without an equivalent replacement is real drift."""
    await db_session.close()
    async with migration_engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_app_users_email"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.migration_schema
async def test_adopt_still_refuses_a_non_audit_column_type_change(db_session, migration_engine) -> None:
    """The deferred-0006 tolerance is a closed list: any other type change is drift."""
    await db_session.close()
    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        # The grouped Alembic diff contains both a deferred comment and a real
        # type mismatch: accepting the former must never hide the latter.
        await conn.run_sync(lambda _: command.downgrade(config, "0013_rename_renovation_tables"))
        await conn.execute(text("ALTER TABLE rm_raw_material_prices ALTER COLUMN fact_2025 TYPE text"))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.migration_schema
async def test_upgrade_prunes_retired_revision_files(db_session, migration_engine) -> None:
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
        async with migration_engine.begin() as conn:
            config = config_for(conn.sync_connection)
            # Legacy pre-state: the schema is at the baseline, the marker at 0015.
            await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
            await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
            await conn.run_sync(upgrade)
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == HEAD_REVISION
        assert all(not path.exists() for path in stale)
    finally:
        for path in stale:
            path.unlink(missing_ok=True)


@pytest.mark.migration_schema
async def test_upgrade_fresh_schema(migration_engine) -> None:
    # A schema the migration chain has never touched — the fresh-install path.
    async with migration_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{MIGRATION_SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{MIGRATION_SCHEMA}"'))
        await conn.run_sync(upgrade)
        diffs = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), SQLModel.metadata))
        assert diffs == []


_KPPK_COPY_TABLES = (
    "kppk_realization",
    "kppk_realization_plan",
    "kppk_cost",
    "kppk_production_plan",
    "kppk_org_products",
    "kppk_products",
)


async def _clear_kppk_copies(conn) -> None:
    """
    Empty the КППК copies before a roundtrip that re-applies 0024.

    That migration normalizes the copies by MATCHING THEIR TEXT against the
    dictionaries and aborts on a row it cannot resolve; the shared fixture seeds
    such rows (products with no dictionary entry), and these roundtrip tests are
    about other tables. The products go last-to-first so the restricting FKs
    stay satisfied.
    """
    for table in _KPPK_COPY_TABLES:
        await conn.execute(text(f'DELETE FROM "{table}"'))  # noqa: S608 — literal table names


@pytest.mark.migration_schema
async def test_reference_columns_drop_and_roundtrip(db_session, migration_engine) -> None:
    """0019-0022 reshape the catalog table; the options themselves survive."""
    await db_session.commit()

    def columns(connection) -> set[str]:
        return {column["name"] for column in inspect(connection).get_columns("r_reference_values")}

    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, "0018_kppk_excise_on_position"))
        await _clear_kppk_copies(conn)
        before = (await conn.execute(text("SELECT catalog::text, label FROM r_reference_values ORDER BY label"))).all()
        assert before
        await conn.run_sync(lambda _: command.upgrade(config, "0023_kppk_nullable_volume_excise"))
        live = await conn.run_sync(columns)
        assert "sort_order" not in live
        assert "value_key" not in live
        assert "payload" not in live
        assert "deleted_at" in live
        assert (
            await conn.execute(text("SELECT catalog::text, label FROM r_reference_values ORDER BY label"))
        ).all() == before
        await conn.run_sync(lambda _: command.downgrade(config, "0018_kppk_excise_on_position"))
        restored = await conn.run_sync(columns)
        assert {"sort_order", "value_key", "payload"} <= restored
        assert "deleted_at" not in restored
        await conn.run_sync(lambda _: command.upgrade(config, "head"))


@pytest.mark.migration_schema
async def test_catalog_migration_only_changes_comments(db_session, migration_engine) -> None:
    await db_session.commit()
    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, "0014_data_catalog_comments"))
        comments = import_module("migrations.versions.0014_data_catalog_comments").COMMENTS
        before = await conn.run_sync(business_snapshot)
        for _ in range(2):
            await conn.run_sync(lambda _: command.downgrade(config, "0013_rename_renovation_tables"))
            assert await conn.run_sync(business_snapshot) == before

            def assert_comments(connection, *, present) -> None:
                inspector = inspect(connection)
                for name, (description, columns) in comments.items():
                    assert inspector.get_table_comment(name)["text"] == (description if present else None)
                    assert {column["name"]: column.get("comment") for column in inspector.get_columns(name)} == {
                        name: comment if present else None for name, comment in columns.items()
                    }

            await conn.run_sync(lambda c: assert_comments(c, present=False))
            await conn.run_sync(lambda _: command.upgrade(config, "0014_data_catalog_comments"))
            await conn.run_sync(lambda c: assert_comments(c, present=True))
            assert await conn.run_sync(business_snapshot) == before


@pytest.mark.migration_schema
async def test_adopt_refuses_unexpected_existing_comment(db_session, migration_engine) -> None:
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        await conn.execute(text("COMMENT ON COLUMN rm_assets.name IS 'unexpected legacy comment'"))
        with pytest.raises(ValueError, match="Схема отличается"):
            await conn.run_sync(adopt)


@pytest.mark.migration_schema
async def test_rename_migration_preserves_renovation_collab_state(db_session, migration_engine) -> None:
    await db_session.commit()
    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, "0012_kppk_calendar_cost"))
        await _clear_kppk_copies(conn)
        await conn.execute(
            text(
                "INSERT INTO collab_cell_states (key, state) VALUES "
                "('renovation_plan_rows/00000000-0000-0000-0000-000000000001/col_011', '{\"revision\": 1}')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO collab_edit_stacks (user_id, session_id, scope, actions, expires_at) VALUES "
                "('user', 'session', 'renovationPlan/2026-01-01', "
                "'[{\"targetTable\":\"renovation_plan_rows\"}]', now() + interval '1 hour')"
            )
        )
        await conn.run_sync(lambda _: command.upgrade(config, "head"))
        assert (
            (await conn.execute(text("SELECT key FROM collab_cell_states WHERE key LIKE 'r_plan_rows/%'")))
            .scalar_one()
            .startswith("r_plan_rows/")
        )
        action = (
            await conn.execute(text("SELECT actions::text FROM collab_edit_stacks WHERE user_id = 'user'"))
        ).scalar_one()
        assert "r_plan_rows" in action


@pytest.mark.migration_schema
async def test_upgrade_failure_rolls_back_legacy_marker(db_session, monkeypatch, migration_engine) -> None:
    from migrations import upgrade as module

    await db_session.close()
    async with migration_engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})

    def fail(*_args: object) -> None:
        message = "injected migration failure"
        raise RuntimeError(message)

    monkeypatch.setattr(module.command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        async with migration_engine.begin() as conn:
            await conn.run_sync(upgrade)
    async with migration_engine.connect() as conn:
        assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == LEGACY_HEAD


@pytest.mark.migration_schema
async def test_completed_upgrade_rolls_back_data_ddl_and_marker(db_session, migration_engine) -> None:
    await db_session.close()
    async with migration_engine.begin() as conn:
        config = config_for(conn.sync_connection)
        await conn.run_sync(lambda _: command.downgrade(config, BASELINE))
        await conn.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": LEGACY_HEAD})
        before = await conn.run_sync(business_snapshot)
    async with migration_engine.connect() as conn:
        transaction = await conn.begin()
        await conn.run_sync(upgrade)
        await transaction.rollback()
    async with migration_engine.connect() as conn:
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
