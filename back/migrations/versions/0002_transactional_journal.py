"""Commit undo history and cell revisions atomically with business data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_transactional_journal"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("collab_action_id_seq")))
    op.create_table(
        "collab_edit_stacks",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "session_id", "scope"),
    )
    op.create_index("ix_collab_edit_stacks_expires_at", "collab_edit_stacks", ["expires_at"])
    op.create_table(
        "collab_cell_states",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "collab_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("room", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("collab_outbox")
    op.drop_table("collab_cell_states")
    op.drop_table("collab_edit_stacks")
    op.execute(sa.schema.DropSequence(sa.Sequence("collab_action_id_seq")))
