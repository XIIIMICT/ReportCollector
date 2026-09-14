"""
Rename the renovation responsibility-block codes to directorates (tech/ksir/mto).

The `r_block` Postgres enum previously stored people's surnames as its labels
(tashpaev / borodiichuk / babaeva). The three responsibility blocks are now
named after their company directorates, and the enum + any stored rows are
renamed together so values stay consistent with the application model.
"""

from alembic import op

revision = "0004_block_codes"
down_revision = "0003_reference_created_default"
branch_labels = None
depends_on = None

# Old stored label (surname block) → new label (directorate).
_TO_NEW = {
    "tashpaev": "tech",
    "borodiichuk": "ksir",
    "babaeva": "mto",
}


def _remap_case(old: bool) -> str:
    """Return a `CASE ... ::` expression remapping between the label sets."""
    mapping = _TO_NEW if old else {new: old for old, new in _TO_NEW.items()}
    when = " ".join(f"WHEN '{old_}' THEN '{new_}'" for old_, new_ in mapping.items())
    return f"CASE block::text {when} ELSE NULL END"


def upgrade() -> None:
    new = ", ".join(f"'{v}'" for v in ("tech", "ksir", "mto"))
    op.execute(f"CREATE TYPE r_block_v2 AS ENUM ({new})")
    expression = _remap_case(old=True)
    op.execute(f"ALTER TABLE r_user_roles ALTER COLUMN block TYPE r_block_v2 USING ({expression})::r_block_v2")
    op.execute("DROP TYPE r_block")
    op.execute("ALTER TYPE r_block_v2 RENAME TO r_block")


def downgrade() -> None:
    legacy = ", ".join(f"'{v}'" for v in ("tashpaev", "borodiichuk", "babaeva"))
    op.execute(f"CREATE TYPE r_block_v2 AS ENUM ({legacy})")
    expression = _remap_case(old=False)
    op.execute(f"ALTER TABLE r_user_roles ALTER COLUMN block TYPE r_block_v2 USING ({expression})::r_block_v2")
    op.execute("DROP TYPE r_block")
    op.execute("ALTER TYPE r_block_v2 RENAME TO r_block")
