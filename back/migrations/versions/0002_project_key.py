"""
Проект в журнале правок совместного редактирования.

Revision ID: 0002_project_key
Revises: 0001_collab
Create Date: 2026-08-13

Добавляет project_key в rm_edit_log: комната/стек undo/redo проекта
(правки разных проектов больше не смешиваются). Существующие записи
относятся к единственному на тот момент проекту «Сырьё» — бэкфил
производится до установки NOT NULL. Операции идемпотентны.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_project_key"
down_revision: str | None = "0001_collab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "rm_edit_log" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("rm_edit_log")}
    if "project_key" not in columns:
        op.execute("ALTER TABLE rm_edit_log ADD COLUMN project_key TEXT")
    op.execute("UPDATE rm_edit_log SET project_key = 'raw_materials' WHERE project_key IS NULL")
    op.alter_column("rm_edit_log", "project_key", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rm_edit_log" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("rm_edit_log")}
    if "project_key" in columns:
        op.drop_column("rm_edit_log", "project_key")
