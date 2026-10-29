"""Store bounded results of durable data operations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_data_job_result"
down_revision = "0027_data_job_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_jobs", sa.Column("result", JSONB()))
    op.alter_column(
        "data_jobs",
        "result",
        comment="Результат фоновой операции без больших строковых данных",
    )


def downgrade() -> None:
    op.drop_column("data_jobs", "result")
