"""Record durable worker lifecycle metadata for data jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0029_data_job_runtime"
down_revision = "0028_data_job_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_jobs", sa.Column("phase", sa.Text(), nullable=True))
    op.add_column("data_jobs", sa.Column("worker_id", sa.Text(), nullable=True))
    op.add_column("data_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("data_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    for name, comment in {
        "phase": "Последняя подтверждённая фаза фоновой операции",
        "worker_id": "ID процесса, выполняющего текущую попытку",
        "started_at": "Время начала текущей попытки",
        "heartbeat_at": "Время последнего продления аренды",
    }.items():
        op.alter_column("data_jobs", name, comment=comment)


def downgrade() -> None:
    for name in ("heartbeat_at", "started_at", "worker_id", "phase"):
        op.drop_column("data_jobs", name)
