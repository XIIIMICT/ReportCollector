"""
Index the user→project link tables by project_id.

`user_roles` and `r_user_roles` have a composite primary key (user_id,
project_id); the leading user_id serves per-user lookups, but the «users of a
project» queries filter by project_id alone and had no supporting index.
"""

from alembic import op

revision = "0005_user_role_project_indexes"
down_revision = "0004_block_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_user_roles_project", "user_roles", ["project_id"])
    op.create_index("ix_r_user_roles_project", "r_user_roles", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_r_user_roles_project", table_name="r_user_roles")
    op.drop_index("ix_user_roles_project", table_name="user_roles")
