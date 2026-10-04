"""
Drop the obsolete «Корректировка» plan column (`col_218`).

It was used as a fallback object code for the renovation ID when the defect
statement was absent. The specification (`docs/seed/id_spec.docx`) defines a
composite object code for that case instead (technology position * work type *
month), so the field is neither read nor written anymore. The form JSON keeps
its slot (hidden via `form_schema.HIDDEN_FIELDS`) to preserve the legacy
column positions of every later field.
"""

from alembic import op

revision = "0007_drop_plan_correction"
down_revision = "0006_price_audit_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE renovation_plan_rows DROP COLUMN IF EXISTS col_218")


def downgrade() -> None:
    op.execute("ALTER TABLE renovation_plan_rows ADD COLUMN IF NOT EXISTS col_218 text")
