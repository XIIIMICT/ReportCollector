"""Validate an existing current schema before replacing its legacy revision marker."""

import asyncio
from pathlib import Path
from typing import cast

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, Index, MetaData, Text, Uuid, inspect, text
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import quoted_name
from sqlmodel import SQLModel

from src import models as _models  # noqa: F401
from src.collab import models as _collab_models  # noqa: F401
from src.database import async_engine

BASELINE = "0001_baseline"
LEGACY_HEAD = "0015_plan_row_col_218"

# Revision files of the retired historical chain, deleted when the consolidated
# baseline landed. A checkout that once had them keeps them as untracked files
# (`git pull` never removes them, and the new chain reuses some numbers), which
# makes Alembic see several heads — `command.upgrade(config, "head")` then fails
# with "Multiple head revisions". The wrapper prunes exactly these names before
# reading the versions directory.
_LEGACY_REVISION_FILES = frozenset(
    {
        "0001_collab.py",
        "0002_project_key.py",
        "0003_snapshot_tables.py",
        "0004_theme_enum.py",
        "0005_plural_table_names.py",
        "0006_renovation_roles.py",
        "0007_renovation_plans.py",
        "0008_renovation_dictionary_audit.py",
        "0009_renovation_asset_unique.py",
        "0010_move_storage_to_prices.py",
        "0011_renovation_reference_payload.py",
        "0012_option_label_unique.py",
        "0013_renovation_reference_indexes.py",
        "0014_plan_row_asset_nullable.py",
        "0015_plan_row_col_218.py",
    }
)


def prune_legacy_revision_files(directory: Path) -> list[str]:
    """
    Remove retired revision files so only the current chain defines the heads.

    Returns the pruned file names (for logging/diagnostics). Only the known
    historical names are touched — a genuinely unexpected extra revision is left
    in place so it still surfaces as a conflict rather than being silently
    dropped.
    """
    versions = directory / "versions"
    if not versions.is_dir():
        return []
    pruned: list[str] = []
    for candidate in versions.iterdir():
        if candidate.is_file() and candidate.name in _LEGACY_REVISION_FILES:
            candidate.unlink()
            pruned.append(candidate.name)
    if pruned:
        # Stale bytecode would still be importable; drop the cache too.
        cache = versions / "__pycache__"
        if cache.is_dir():
            for compiled in cache.iterdir():
                if compiled.is_file() and compiled.stem.split(".")[0] + ".py" in _LEGACY_REVISION_FILES:
                    compiled.unlink()
    return pruned


# Legacy-only column the models dropped: the old `renovation_reference_values.metadata`.
# It is nullable and unused; the guard tolerates it so old installations adopt.
# `renovation_plan_rows.col_218` (the retired «Корректировка») is dropped by
# 0007_drop_plan_correction, which runs after the baseline during adoption, so
# its presence on the pre-adoption schema is expected too. `sort_order` and
# `value_key` are the renovation catalog columns dropped by
# 0019_drop_reference_sort_order / 0020_drop_reference_value_key / 0022.
_LEGACY_EXTRA_COLUMNS: dict[str, set[str]] = {
    "renovation_reference_values": {"metadata", "sort_order", "value_key", "payload"},
    "r_reference_values": {"metadata", "sort_order", "value_key", "payload"},
    "renovation_plan_rows": {"col_218"},
    "r_plan_rows": {"col_218"},
}

# 0025_plan_row_facet_indexes (after the baseline) rebuilds the plan-row facet
# indexes: ten plan_id-leading ones appear (five replacing single-column ones,
# five on text columns that had none) and the five single-column ones go away.
# Both spellings below matter — a legacy schema that still carries the pre-0013
# table names reports the legacy form (`_adoption_metadata` renames the model
# side to match).
_FACET_INDEX_COLUMNS: tuple[str, ...] = ("003", "005", "006", "007", "008", "015", "017", "018", "019", "020")
_FACET_INDEX_TABLES: tuple[str, ...] = ("r_plan_rows", "renovation_plan_rows")

# Indexes the models declare that a legacy-0015 installation does not carry yet:
# they are created by migrations that run AFTER the baseline during adoption
# (0005_user_role_project_indexes), so their absence on the pre-adoption schema
# is expected, not drift. A closed list — anything else still fails closed.
_LEGACY_MISSING_INDEXES: frozenset[str] = frozenset(
    {
        "ix_user_roles_project",
        "ix_r_user_roles_project",
        "ix_kppk_cost_scope",
        "ix_kppk_production_plan_scope",
    }
) | frozenset(f"ix_{table}_plan_col_{column}" for table in _FACET_INDEX_TABLES for column in _FACET_INDEX_COLUMNS)

# Indexes a legacy-0015 installation still carries but the models dropped: they
# go away in migrations that run AFTER the baseline during adoption
# (0021_drop_reference_value_key drops the `value_key` uniqueness index, 0025
# drops the single-column facet indexes). A closed list — an unexpected
# leftover index is still drift.
_LEGACY_EXTRA_INDEXES: frozenset[str] = frozenset(
    {
        "ix_renovation_reference_values_catalog_key",
        "ix_r_reference_values_catalog_key",
    }
) | frozenset(
    f"ix_{table}_col_{column}" for table in _FACET_INDEX_TABLES for column in ("015", "017", "018", "019", "020")
)

# Tables the models declare that a legacy-0015 installation does not have yet:
# they are created by migrations running AFTER the baseline during adoption
# (0008_kppk_source_tables), so their absence on the pre-adoption schema —
# including the indexes of those tables — is expected, not drift. A closed list.
_LEGACY_MISSING_TABLES: frozenset[str] = frozenset(
    {
        "data_jobs",
        "kppk_orgs",
        "kppk_org_products",
        "kppk_periods",
        "kppk_products",
        "kppk_raws",
        "kppk_realization",
        "kppk_realization_plan",
        "kppk_cost",
        "kppk_production_plan",
        "kppk_markets",
    }
)

# Columns rewritten by a migration that runs AFTER the baseline during adoption
# (0006_price_audit_timestamps). The pre-adoption schema still holds the legacy
# TEXT values, so the type / nullability / default differences on these two
# columns are expected, not drift. A closed list — anything else fails closed.
_LEGACY_PRICE_TIMESTAMPS: frozenset[str] = frozenset({"business_plan_updated_at", "storage_standard_updated_at"})
_LEGACY_PRICE_TIMESTAMPS_TABLE = "rm_raw_material_prices"
_RENOVATION_TABLE_RENAMES = {
    "renovation_reference_values": "r_reference_values",
    "renovation_plans": "r_plans",
    "renovation_plan_rows": "r_plan_rows",
}
TABLES_WITH_UPDATER = frozenset(
    {
        "kppk_org_products",
        "kppk_orgs",
        "kppk_products",
        "kppk_raws",
        "r_assets",
        "r_plan_rows",
        "r_plans",
        "r_reference_values",
        "r_user_roles",
        "rm_asset_raw_materials",
        "rm_assets",
        "rm_delivery_bases",
        "rm_material_movements",
        "rm_raw_material_prices",
        "rm_raw_materials",
        "user_roles",
    }
)


def compare_adoption_default(
    _context: MigrationContext,
    inspected_column: Column,
    _metadata_column: Column,
    inspected_default: str | None,
    _metadata_default: object,
    _rendered_metadata_default: str | None,
) -> bool | None:
    """Allow the known pre-0003 variant; compare every other default normally."""
    # Legacy 0008 installed now(); create_all and the frozen baseline omitted it.
    # 0003 converges both variants without modifying existing timestamps.
    if (
        inspected_column.table.name in {"r_reference_values", "renovation_reference_values"}
        and inspected_column.name == "created_at"
        and inspected_default is None
    ):
        return False
    return None


def _index_diffs(diff: tuple) -> bool:
    """Detect an index add/remove diff (its kind is a plain string)."""
    return isinstance(diff, tuple) and len(diff) >= 2 and diff[0] in ("remove_index", "add_index")


def _columns_of(index: Index) -> tuple[str, ...]:
    return tuple(sorted(column.name for column in index.columns))


def _drop_equivalent_index_changes(diffs: list[tuple]) -> list[tuple]:
    """
    Drop equivalent index changes, keeping only genuine drift.

    Two legacy-only shapes are tolerated:

    - a pure rename — SQLModel named the index after the model class
      (`ix_rm_asset_raw_material_…`) while the models declare the table-based name;
    - a unique INDEX vs a unique CONSTRAINT plus a non-unique index on the same
      column (`r_assets.code`): both enforce the same uniqueness, so the
      structural difference is irrelevant to adoption.

    Anything unpaired or with a different column set stays in the list, so genuine
    drift (a lost index, a changed column set) is still refused.
    """
    adds_by_columns: dict[tuple[str, tuple[str, ...]], list[int]] = {}
    for position, diff in enumerate(diffs):
        if _index_diffs(diff) and diff[0] == "add_index":
            adds_by_columns.setdefault((diff[1].table.name, _columns_of(diff[1])), []).append(position)

    consumed: set[int] = set()
    matched_adds: set[int] = set()
    for position, diff in enumerate(diffs):
        if not _index_diffs(diff) or diff[0] != "remove_index":
            continue
        candidates = adds_by_columns.get((diff[1].table.name, _columns_of(diff[1])))
        if candidates:
            # An index add is only ever paired once; remember both sides so the
            # match set stays stable for the constraint check below.
            add_position = candidates[0]
            consumed.add(position)
            consumed.add(add_position)
            matched_adds.add(add_position)

    for position, diff in enumerate(diffs):
        if position in consumed:
            continue
        if isinstance(diff, tuple) and diff and diff[0] == "remove_constraint":
            # A dropped unique constraint whose column also gained a unique index
            # (the `code` case): the index add on the same (table, columns) was
            # matched above, so the constraint is the same guarantee in another
            # form.
            constraint = diff[1]
            columns = tuple(sorted(column.name for column in constraint.columns))
            if any(
                add_position in matched_adds
                for add_position in adds_by_columns.get((constraint.table.name, columns), [])
            ):
                consumed.add(position)
    return [diff for position, diff in enumerate(diffs) if position not in consumed]


def _adoption_metadata(connection: Connection) -> MetaData:
    """Compare legacy table names without ignoring structural differences."""
    live = set(inspect(connection).get_table_names())
    names = {new: old for old, new in _RENOVATION_TABLE_RENAMES.items() if old in live and new not in live}
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copied = table.to_metadata(metadata, name=names.get(table.name, table.name))
        for index in copied.indexes:
            if index.name and table.name in names:
                index.name = quoted_name(index.name.replace(table.name, names[table.name]), None)
    # to_metadata remaps self-references, but cross-table references retain
    # their original names. Rebuild only these constraints in the private copy.
    for table in list(metadata.tables.values()):
        for constraint in list(table.foreign_key_constraints):
            columns = [element.parent.name for element in constraint.elements]
            targets = []
            for element in constraint.elements:
                parts = element.target_fullname.split(".")
                parts[-2] = names.get(parts[-2], parts[-2])
                targets.append(".".join(parts))
            table.constraints.remove(constraint)
            for element in constraint.elements:
                table.foreign_keys.discard(element)
                element.parent.foreign_keys.discard(element)
            table.append_constraint(
                ForeignKeyConstraint(
                    columns,
                    targets,
                    name=constraint.name,
                    ondelete=constraint.ondelete,
                    onupdate=constraint.onupdate,
                    deferrable=constraint.deferrable,
                    initially=constraint.initially,
                    use_alter=constraint.use_alter,
                )
            )
    return metadata


def _is_legacy_price_timestamp_change(diff: object) -> bool:
    """One type/nullability/default change on a «Цены» audit timestamp column."""
    if not (isinstance(diff, tuple) and len(diff) >= 6 and diff[2] == _LEGACY_PRICE_TIMESTAMPS_TABLE):
        return False
    if diff[3] not in _LEGACY_PRICE_TIMESTAMPS:
        return False
    kind = diff[0]
    if kind == "modify_type":
        target = diff[-1]
        return isinstance(target, DateTime) and bool(target.timezone)
    if kind == "modify_nullable":
        return diff[-1] is True
    if kind == "modify_default":
        return diff[-1] is None
    return False


def _is_legacy_equivalent(diff: tuple) -> bool:
    """Match a single known legacy-only difference (a dropped column or index)."""
    # 0014 adds the catalog after adoption. Only absent comments whose target
    # matches a documented application object are deferred, never structural edits.
    if isinstance(diff, tuple) and len(diff) == 3 and diff[0] == "add_table_comment":
        table = diff[1]
        name = _RENOVATION_TABLE_RENAMES.get(table.name, table.name)
        expected = SQLModel.metadata.tables.get(name)
        return expected is not None and diff[2] is None and table.comment == expected.comment
    if isinstance(diff, tuple) and len(diff) == 7 and diff[0] == "modify_comment":
        name = _RENOVATION_TABLE_RENAMES.get(diff[2], diff[2])
        table = SQLModel.metadata.tables.get(name)
        column = table.c.get(diff[3]) if table is not None else None
        if name == "r_plan_rows" and diff[3].startswith("col_"):
            old_number = int(diff[3][4:])
            legacy_column = table.c.get(f"col_{old_number - 1:03d}") if table is not None else None
            if legacy_column is not None and diff[-1] == legacy_column.comment and diff[-2] is None:
                return True
        return column is not None and diff[-2] is None and diff[-1] == column.comment
    # 0015 intentionally replaces denormalized text audit with a nullable FK;
    # adoption validates legacy structure before that migration runs.
    if isinstance(diff, tuple) and diff[0] in {"add_column", "remove_column", "add_fk", "remove_fk"}:
        if diff[0] == "add_column":
            column = diff[3]
            name = _RENOVATION_TABLE_RENAMES.get(diff[2], diff[2])
            if name in TABLES_WITH_UPDATER and column.name == "updater_user_id":
                return column.nullable and isinstance(column.type, Uuid)
            # 0022 adds the forward-compatible soft-delete marker.
            if name == "r_reference_values" and column.name == "deleted_at":
                return column.nullable and isinstance(column.type, DateTime)
        if diff[0] == "remove_column":
            column = diff[3]
            name = _RENOVATION_TABLE_RENAMES.get(diff[2], diff[2])
            if name in TABLES_WITH_UPDATER and column.name == "updated_by":
                return isinstance(column.type, Text) and not column.nullable
        if diff[0] == "add_fk":
            constraint = diff[1]
            name = _RENOVATION_TABLE_RENAMES.get(constraint.table.name, constraint.table.name)
            return (
                name in TABLES_WITH_UPDATER
                and [(element.parent.name, element.target_fullname) for element in constraint.elements]
                == [("updater_user_id", "app_users.id")]
                and constraint.ondelete == "SET NULL"
            )
    if isinstance(diff, tuple) and len(diff) >= 4 and diff[0] == "remove_column":
        _schema, table, column = diff[1], diff[2], diff[3]
        return table in _LEGACY_EXTRA_COLUMNS and column.name in _LEGACY_EXTRA_COLUMNS[table]
    if isinstance(diff, tuple) and len(diff) >= 2 and diff[0] == "add_index":
        # An index the migration chain creates after the baseline; absent on a
        # pre-adoption legacy schema — either by name (0005) or together with
        # the whole post-baseline table it belongs to (0008).
        return diff[1].name in _LEGACY_MISSING_INDEXES or diff[1].table.name in _LEGACY_MISSING_TABLES
    if isinstance(diff, tuple) and len(diff) >= 2 and diff[0] == "remove_index":
        # An index the migration chain drops after the baseline; still present
        # on a pre-adoption legacy schema (the `value_key` uniqueness).
        return diff[1].name in _LEGACY_EXTRA_INDEXES
    if isinstance(diff, tuple) and len(diff) >= 2 and diff[0] == "add_table":
        return diff[1].name in _LEGACY_MISSING_TABLES
    if isinstance(diff, list):
        # Alembic groups the changes on one column into a nested list; tolerate
        # it only when every change is a known deferred timestamp/comment edit.
        return bool(diff) and all(_is_legacy_equivalent(item) for item in diff)
    return _is_legacy_price_timestamp_change(diff)


def _describe_diffs(diffs: list[tuple]) -> str:
    """Render a compact, readable summary of the remaining schema differences."""
    lines: list[str] = []
    for diff in diffs:
        if isinstance(diff, list):
            # Alembic groups several changes on one column into a nested list.
            lines.append(_describe_diffs(diff))
            continue
        kind = diff[0] if isinstance(diff, tuple) and diff else type(diff).__name__
        if kind in ("remove_index", "add_index"):
            index = diff[1]
            columns = ", ".join(column.name for column in index.columns)
            lines.append(f"{kind} {index.table.name}({columns}) [{index.name}]")
        elif kind in ("remove_column", "add_column"):
            _schema, table, column = diff[1], diff[2], diff[3]
            name = column.name if column is not None else "?"
            lines.append(f"{kind} {table}.{name}")
        elif kind in ("remove_table", "add_table"):
            lines.append(f"{kind} {diff[1].name}")
        elif kind == "remove_constraint":
            constraint = diff[1]
            columns = ", ".join(column.name for column in constraint.columns)
            lines.append(f"{kind} {constraint.table.name}({columns})")
        else:
            lines.append(str(kind))
    return "; ".join(lines)


def adopt(connection: Connection) -> None:
    """Only the known previous head and a matching schema can be adopted."""
    directory = Path(__file__).resolve().parent
    # A checkout that once carried the retired chain keeps its revision files;
    # prune them so the baseline lookup (and the upgrade that follows) sees a
    # single head instead of failing with "Multiple head revisions".
    prune_legacy_revision_files(directory)
    config = Config(str(directory.parent / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    if ScriptDirectory.from_config(config).get_revision(BASELINE) is None:
        message = "Переход выполняется только из релиза с единой baseline-миграцией."
        raise ValueError(message)
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        message = "Нет alembic_version. Для новой базы используйте alembic upgrade head."
        raise ValueError(message)
    connection.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
    versions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if versions not in ([LEGACY_HEAD], [BASELINE]):
        message = f"Неподдерживаемая ревизия базы данных: {versions}."
        raise ValueError(message)

    def include_object(_obj: object, name: str | None, type_: str, reflected: bool, _compare_to: object) -> bool:
        # Obsolete journal tables may remain in legacy installations. Preserve them.
        if type_ == "table" and name in {"collab_edit_stacks", "collab_cell_states", "collab_outbox"}:
            return False
        return not (type_ == "table" and reflected and name in {"rm_cell_state", "rm_edit_log"})

    metadata = _adoption_metadata(connection)
    diffs = compare_metadata(
        MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": compare_adoption_default,
                "include_object": include_object,
            },
        ),
        metadata,
    )
    # A legacy DB names some indexes after the model class instead of the table;
    # treat those pure renames as equivalent, then keep only the genuine drift.
    remaining = _drop_equivalent_index_changes(diffs)
    remaining = [diff for diff in remaining if not _is_legacy_equivalent(diff)]
    if remaining:
        # Print only the object names: a full `!r` of hundreds of reflected
        # tables is unreadable and hides the actual drift.
        message = f"Схема отличается от актуальных моделей; переход отменён: {_describe_diffs(remaining)}"
        raise ValueError(message)
    # Alembic does not detect enum label or primary-key changes automatically.
    enums = {item["name"]: item["labels"] for item in cast("PGInspector", inspector).get_enums()}
    # The responsibility-block enum (`r_block`) is renamed from people's surnames
    # to directorates by migration 0004_block_codes. Adopting a legacy-0015 DB
    # (which still carries the surname labels) must not be refused here — the
    # migration that follows adopt converges it to the model's labels.
    block_legacy = {"tashpaev", "borodiichuk", "babaeva"}
    block_new = {"tech", "ksir", "mto"}
    live_tables = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        # Absent by design before the upgrade: obsolete journal tables and the
        # tables created by migrations that run after the baseline.
        if table.name in {"collab_edit_stacks", "collab_cell_states", "collab_outbox"} or table.name not in live_tables:
            continue
        expected_pk = [column.name for column in table.primary_key.columns]
        if inspector.get_pk_constraint(table.name)["constrained_columns"] != expected_pk:
            message = f"Схема отличается: первичный ключ {table.name}."
            raise ValueError(message)
        for column in table.columns:
            if not isinstance(column.type, Enum):
                continue
            if column.type.name != "r_block":
                if enums.get(column.type.name) != column.type.enums:
                    message = f"Схема отличается: значения enum {column.type.name}."
                    raise ValueError(message)
                continue
            # Allow the known in-flight rename (legacy labels → directorates);
            # any other label difference on r_block is still a schema drift.
            live = set(enums.get("r_block") or ())
            if live == block_legacy and set(column.type.enums) == block_new:
                continue
            if enums.get("r_block") != column.type.enums:
                message = "Схема отличается: значения enum r_block."
                raise ValueError(message)
    config.attributes["connection"] = connection
    command.stamp(config, BASELINE, purge=True)


async def main() -> None:
    try:
        async with async_engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '30s'"))
            await connection.run_sync(adopt)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
