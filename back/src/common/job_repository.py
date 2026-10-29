"""Short PostgreSQL transactions for admission, leases and fenced completion."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.export_jobs import JOB_TTL_SECONDS, MAX_ACTIVE_JOBS, MAX_ACTIVE_JOBS_PER_USER, ExportQueueFullError
from src.common.job_models import ACTIVE_STATES, QUEUE_LOCK, DataJob
from src.database import async_session_factory
from src.metrics import EXPORT_JOBS_REJECTED

LEASE_SECONDS = 30
MAX_ATTEMPTS = 3
SessionFactory = Callable[[], AsyncSession]


async def _now(session: AsyncSession) -> datetime:
    return (await session.exec(select(func.clock_timestamp()))).one()


class JobRepository:
    def __init__(self, session_factory: SessionFactory = async_session_factory) -> None:
        self.session_factory = session_factory

    async def create(self, candidate: DataJob) -> DataJob:
        async with self.session_factory() as session, session.begin():
            await session.exec(QUEUE_LOCK)
            now = await _now(session)
            existing = (
                await session.exec(
                    select(DataJob)
                    .where(
                        DataJob.kind == candidate.kind,
                        DataJob.user_id == candidate.user_id,
                        DataJob.cache_key == candidate.cache_key,
                        col(DataJob.status).in_(("pending", "running", "done")),
                        (col(DataJob.expires_at).is_(None)) | (col(DataJob.expires_at) > now),
                    )
                    .order_by(col(DataJob.created_at).desc())
                    .limit(1)
                )
            ).first()
            if existing is not None:
                return existing
            active = (await session.exec(select(DataJob.user_id).where(col(DataJob.status).in_(ACTIVE_STATES)))).all()
            reason = (
                "global_limit"
                if len(active) >= MAX_ACTIVE_JOBS
                else ("user_limit" if active.count(candidate.user_id) >= MAX_ACTIVE_JOBS_PER_USER else None)
            )
            if reason is not None:
                EXPORT_JOBS_REJECTED.labels(kind=candidate.kind, reason=reason).inc()
                msg = "Очередь выгрузок заполнена. Дождитесь завершения текущих заданий."
                raise ExportQueueFullError(msg)
            candidate.created_at = candidate.available_at = now
            session.add(candidate)
            return candidate

    async def get(self, job_id: str, user_id: str, kind: str) -> DataJob:
        try:
            identifier = UUID(job_id)
        except ValueError as error:
            raise KeyError(job_id) from error
        async with self.session_factory() as session:
            now = await _now(session)
            job = await session.get(DataJob, identifier)
            if (
                job is None
                or job.user_id != user_id
                or job.kind != kind
                or (job.expires_at is not None and job.expires_at <= now)
            ):
                raise KeyError(job_id)
            return job

    async def claim(self, kind: str, *, concurrency: int = 1) -> DataJob | None:
        async with self.session_factory() as session, session.begin():
            # The admission lock also bounds running jobs across processes.
            # It is held for metadata only, never during file generation.
            await session.exec(QUEUE_LOCK)
            now = await _now(session)
            stale = (
                await session.exec(
                    select(DataJob)
                    .where(
                        DataJob.kind == kind,
                        DataJob.status == "running",
                        col(DataJob.lease_until) <= now,
                    )
                    .with_for_update()
                )
            ).all()
            for job in stale:
                self._retry(job, now, "worker_lost", retryable=True)
                session.add(job)
            await session.flush()
            running = (
                await session.exec(
                    select(func.count())
                    .select_from(DataJob)
                    .where(
                        DataJob.kind == kind,
                        DataJob.status == "running",
                    )
                )
            ).one()
            if running >= concurrency:
                return None
            job = (
                await session.exec(
                    select(DataJob)
                    .where(
                        DataJob.kind == kind,
                        DataJob.status == "pending",
                        col(DataJob.available_at) <= now,
                    )
                    .order_by(col(DataJob.created_at))
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).first()
            if job is None:
                return None
            job.status = "running"
            job.progress = 0
            job.attempt += 1
            job.lease_token = uuid4()
            job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            job.artifact = f"{job.id}-{job.lease_token}"
            job.error = job.error_code = None
            session.add(job)
            return job

    async def heartbeat(self, job: DataJob, progress: int, total: int) -> bool:
        async with self.session_factory() as session, session.begin():
            current = await session.get(DataJob, job.id, with_for_update=True)
            now = await _now(session)
            if not self._owns(current, job, now):
                return False
            assert current is not None  # noqa: S101
            current.progress, current.total = progress, total
            current.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            session.add(current)
            return True

    async def owns(self, job: DataJob) -> bool:
        """Read-only watchdog: an orphan process must never renew its own lease."""
        async with self.session_factory() as session:
            return self._owns(await session.get(DataJob, job.id), job, await _now(session))

    @staticmethod
    def _owns(current: DataJob | None, claimed: DataJob, now: datetime) -> bool:
        return (
            current is not None
            and current.status == "running"
            and current.lease_token == claimed.lease_token
            and current.lease_until is not None
            and current.lease_until > now
        )

    @staticmethod
    def _retry(job: DataJob, now: datetime, code: str, *, retryable: bool) -> None:
        job.lease_until = None
        job.lease_token = None
        job.error_code = code
        if retryable and job.attempt < MAX_ATTEMPTS:
            job.status = "pending"
            job.progress = 0
            job.available_at = now + timedelta(seconds=2**job.attempt)
        else:
            job.status = "failed"
            job.error = "Не удалось сформировать файл. Создайте выгрузку повторно."
            job.finished_at = now
            job.expires_at = now + timedelta(seconds=JOB_TTL_SECONDS)

    async def finish(
        self, job: DataJob, *, size: int = 0, total: int = 0, error_code: str | None = None, retryable: bool = False
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            current = await session.get(DataJob, job.id, with_for_update=True)
            now = await _now(session)
            if not self._owns(current, job, now):
                return False
            assert current is not None  # noqa: S101
            if error_code is not None:
                self._retry(current, now, error_code, retryable=retryable)
            else:
                current.status = "done"
                current.progress = current.total = total
                current.size = size
                current.finished_at = now
                current.expires_at = now + timedelta(seconds=JOB_TTL_SECONDS)
                current.lease_until = None
                current.lease_token = None
            session.add(current)
            return True

    async def cancel(self, job_id: str, user_id: str, kind: str) -> DataJob:
        found = await self.get(job_id, user_id, kind)
        async with self.session_factory() as session, session.begin():
            job = await session.get(DataJob, found.id, with_for_update=True)
            if job is None:
                raise KeyError(job_id)
            if job.status in ACTIVE_STATES:
                self._retry(job, await _now(session), "cancelled", retryable=False)
                job.error = "Выгрузка отменена."
                session.add(job)
            return job

    async def missing_artifact(self, found: DataJob) -> None:
        """Invalidate a missing completed file so a repeated request can regenerate it."""
        async with self.session_factory() as session, session.begin():
            job = await session.get(DataJob, found.id, with_for_update=True)
            if job is not None and job.status == "done" and job.artifact == found.artifact:
                self._retry(job, await _now(session), "artifact_missing", retryable=False)
                session.add(job)

    async def active(self, kind: str) -> list[DataJob]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.exec(
                        select(DataJob).where(
                            DataJob.kind == kind,
                            col(DataJob.status).in_(ACTIVE_STATES),
                        )
                    )
                ).all()
            )

    async def prune(self, kind: str) -> list[UUID]:
        """List expired records; keep them until their files are actually removed."""
        async with self.session_factory() as session:
            now = await _now(session)
            expired = (
                await session.exec(
                    select(DataJob)
                    .where(
                        DataJob.kind == kind,
                        col(DataJob.expires_at) <= now,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
            ).all()
            return [job.id for job in expired]

    async def remove_expired(self, job_id: UUID) -> None:
        async with self.session_factory() as session, session.begin():
            job = await session.get(DataJob, job_id, with_for_update=True)
            now = await _now(session)
            if job is not None and job.expires_at is not None and job.expires_at <= now:
                await session.delete(job)
