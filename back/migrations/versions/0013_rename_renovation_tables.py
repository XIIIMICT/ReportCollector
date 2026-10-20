"""Use the r_ prefix for renovation application tables."""

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0013_rename_renovation_tables"
down_revision: str | None = "0012_kppk_calendar_cost"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


TABLES = (
    ("renovation_reference_values", "r_reference_values"),
    ("renovation_plans", "r_plans"),
    ("renovation_plan_rows", "r_plan_rows"),
)


def _rename_if_exists(old: str, new: str) -> None:
    op.execute(f'ALTER TABLE IF EXISTS "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    for old, new in TABLES:
        if op.get_bind().dialect.has_table(op.get_bind(), old):
            _rename_if_exists(old, new)

    op.execute(
        "UPDATE collab_cell_states SET key = replace(key, 'renovation_plan_rows/', 'r_plan_rows/') "
        "WHERE key LIKE 'renovation_plan_rows/%'"
    )
    op.execute(
        "UPDATE collab_edit_stacks SET actions = replace(actions::text, 'renovation_plan_rows', 'r_plan_rows')::jsonb "
        "WHERE actions::text LIKE '%renovation_plan_rows%'"
    )

    for old, new in (
        ("ix_renovation_reference_values_catalog_key", "ix_r_reference_values_catalog_key"),
        ("uq_renovation_reference_values_catalog_label", "uq_r_reference_values_catalog_label"),
        ("uq_renovation_plans_one_working", "uq_r_plans_one_working"),
        ("uq_renovation_plans_one_pending", "uq_r_plans_one_pending"),
        ("ix_renovation_plan_rows_asset", "ix_r_plan_rows_asset"),
        ("ix_renovation_plan_rows_col_015", "ix_r_plan_rows_col_015"),
        ("ix_renovation_plan_rows_col_017", "ix_r_plan_rows_col_017"),
        ("ix_renovation_plan_rows_col_018", "ix_r_plan_rows_col_018"),
        ("ix_renovation_plan_rows_col_019", "ix_r_plan_rows_col_019"),
        ("ix_renovation_plan_rows_col_020", "ix_r_plan_rows_col_020"),
        ("uq_renovation_plan_rows_position", "uq_r_plan_rows_position"),
    ):
        op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')
    for old, new in (
        ("renovation_plan_rows_plan_id_fkey", "r_plan_rows_plan_id_fkey"),
        ("renovation_plan_rows_col_015_fkey", "r_plan_rows_col_015_fkey"),
        ("renovation_plan_rows_col_017_fkey", "r_plan_rows_col_017_fkey"),
        ("renovation_plan_rows_col_018_fkey", "r_plan_rows_col_018_fkey"),
        ("renovation_plan_rows_col_019_fkey", "r_plan_rows_col_019_fkey"),
        ("renovation_plan_rows_col_020_fkey", "r_plan_rows_col_020_fkey"),
    ):
        op.execute(f'ALTER TABLE "r_plan_rows" RENAME CONSTRAINT "{old}" TO "{new}"')


def downgrade() -> None:
    op.execute(
        "UPDATE collab_cell_states SET key = replace(key, 'r_plan_rows/', 'renovation_plan_rows/') "
        "WHERE key LIKE 'r_plan_rows/%'"
    )
    op.execute(
        "UPDATE collab_edit_stacks SET actions = replace(actions::text, 'r_plan_rows', 'renovation_plan_rows')::jsonb "
        "WHERE actions::text LIKE '%r_plan_rows%'"
    )
    for old, new in (
        ("r_plan_rows_plan_id_fkey", "renovation_plan_rows_plan_id_fkey"),
        ("r_plan_rows_col_015_fkey", "renovation_plan_rows_col_015_fkey"),
        ("r_plan_rows_col_017_fkey", "renovation_plan_rows_col_017_fkey"),
        ("r_plan_rows_col_018_fkey", "renovation_plan_rows_col_018_fkey"),
        ("r_plan_rows_col_019_fkey", "renovation_plan_rows_col_019_fkey"),
        ("r_plan_rows_col_020_fkey", "renovation_plan_rows_col_020_fkey"),
    ):
        op.execute(f'ALTER TABLE "r_plan_rows" RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in (
        ("ix_r_reference_values_catalog_key", "ix_renovation_reference_values_catalog_key"),
        ("uq_r_reference_values_catalog_label", "uq_renovation_reference_values_catalog_label"),
        ("uq_r_plans_one_working", "uq_renovation_plans_one_working"),
        ("uq_r_plans_one_pending", "uq_renovation_plans_one_pending"),
        ("ix_r_plan_rows_asset", "ix_renovation_plan_rows_asset"),
        ("ix_r_plan_rows_col_015", "ix_renovation_plan_rows_col_015"),
        ("ix_r_plan_rows_col_017", "ix_renovation_plan_rows_col_017"),
        ("ix_r_plan_rows_col_018", "ix_renovation_plan_rows_col_018"),
        ("ix_r_plan_rows_col_019", "ix_renovation_plan_rows_col_019"),
        ("ix_r_plan_rows_col_020", "ix_renovation_plan_rows_col_020"),
        ("uq_r_plan_rows_position", "uq_renovation_plan_rows_position"),
    ):
        op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')
    for old, new in reversed(TABLES):
        if op.get_bind().dialect.has_table(op.get_bind(), new):
            _rename_if_exists(new, old)
