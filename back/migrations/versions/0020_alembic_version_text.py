"""
Widen `alembic_version.version_num` so a long revision id can't fail a deploy.

Alembic creates the table with `version_num VARCHAR(32)`. Revision ids carry the
sequence number AND a descriptive slug (`0018_kppk_excise_on_position`), and a
descriptive-enough name is what an operator and a code reviewer need — but past
32 characters the upgrade dies at the very END, on the bookkeeping `UPDATE`, with
`StringDataTruncationError: value too long for type character varying(32)`.
The schema change is already applied by then, so the failure is pure noise.

`TEXT` removes the limit for good; the table holds one row.

Revision ID: 0020_alembic_version_text
Revises: 0019_drop_reference_sort_order
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0020_alembic_version_text"
down_revision: str | None = "0019_drop_reference_sort_order"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Make the stored revision id a TEXT column."""
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """
    Restore VARCHAR(32).

    Safe only while every stored id fits; the cast raises otherwise, which is
    the honest outcome — silently truncating the current revision would leave
    the marker pointing at a revision that does not exist.
    """
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.Text(),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
