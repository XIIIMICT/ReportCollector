"""Canonical cell keys and durable PostgreSQL collaboration tables."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Column, DateTime, Sequence
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

ACTION_SEQUENCE = Sequence("collab_action_id_seq", metadata=SQLModel.metadata)


class EditStack(SQLModel, table=True):
    """Session history committed atomically with business rows."""

    __tablename__ = "collab_edit_stacks"
    user_id: str = Field(primary_key=True)
    session_id: str = Field(primary_key=True)
    scope: str = Field(primary_key=True)
    actions: list[dict] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))


class CellState(SQLModel, table=True):
    """Durable revision and last writer for undo conflict checks."""

    __tablename__ = "collab_cell_states"
    key: str = Field(primary_key=True)
    state: dict = Field(sa_column=Column(JSONB, nullable=False))


class OutboxEvent(SQLModel, table=True):
    """A committed notification awaiting at-least-once delivery."""

    __tablename__ = "collab_outbox"
    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    room: str
    payload: dict = Field(sa_column=Column(JSONB, nullable=False))


MOVEMENT_TABLE = "rm_material_movements"
PRICE_TABLE = "rm_raw_material_prices"
RENOVATION_PLAN_ROWS_TABLE = "renovation_plan_rows"


def canonical_key(target_table: str, row_id: UUID, field: str) -> str:
    """Return the single cell key: table/row/field."""
    return f"{target_table}/{row_id}/{field}"
