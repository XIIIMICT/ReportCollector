"""Durable data-operation state; payloads contain query specifications, never rows."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, select

from src.auth.models import utc_now
from src.common.data_catalog import apply_table_comments


class DataJob(SQLModel, table=True):
    __tablename__ = "data_jobs"
    __table_args__ = (
        Index("ix_data_jobs_queue", "kind", "status", "available_at"),
        Index("ix_data_jobs_owner", "user_id", "kind", "cache_key"),
        Index("ix_data_jobs_expiry", "expires_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kind: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    cache_key: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict = Field(sa_column=Column(JSONB, nullable=False))
    status: Literal["pending", "running", "done", "failed"] = Field(
        default="pending", sa_column=Column(Text, nullable=False)
    )
    progress: int = 0
    total: int = 0
    attempt: int = 0
    lease_token: UUID | None = None
    lease_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    available_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    artifact: str | None = Field(default=None, sa_column=Column(Text))
    size: int | None = Field(default=None, sa_column=Column(BigInteger))
    error: str | None = Field(default=None, sa_column=Column(Text))
    error_code: str | None = Field(default=None, sa_column=Column(Text))
    request_id: str | None = Field(default=None, sa_column=Column(Text))


# Only active states count against admission/worker limits. Terminal records
# remain addressable for their TTL, including cancelled jobs (status=failed).
apply_table_comments(SQLModel.metadata.tables[DataJob.__tablename__])

ACTIVE_STATES = ("pending", "running")
QUEUE_LOCK = select(func.pg_advisory_xact_lock(17642, 1))
