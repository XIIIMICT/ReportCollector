"""Persist queued operations, fenced attempts and artifact ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_durable_data_jobs"
down_revision = "0025_plan_row_facet_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("artifact", sa.Text()),
        sa.Column("size", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("request_id", sa.Text()),
    )
    op.create_index("ix_data_jobs_queue", "data_jobs", ["kind", "status", "available_at"])
    op.create_index("ix_data_jobs_owner", "data_jobs", ["user_id", "kind", "cache_key"])
    op.create_index("ix_data_jobs_expiry", "data_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_table("data_jobs")
