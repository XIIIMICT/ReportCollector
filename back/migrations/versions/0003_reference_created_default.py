"""Preserve the reference creation timestamp default introduced by legacy 0008."""

import sqlalchemy as sa
from alembic import op

revision = "0003_reference_created_default"
down_revision = "0002_transactional_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "renovation_reference_values",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.alter_column(
        "renovation_reference_values",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
