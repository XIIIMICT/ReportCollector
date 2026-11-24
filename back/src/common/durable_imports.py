"""Durable, bounded import jobs backed by the PostgreSQL data-job queue."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError, OperationalError

from src.app_logging import log_error, log_event
from src.common.durable_exports import LeaseLostError
from src.common.job_contracts import JOB_TTL_SECONDS, ExportQueueFullError
from src.common.job_models import DataJob
from src.common.job_repository import LEASE_SECONDS, JobRepository
from src.config import get_settings
from src.metrics import (
    EXPORT_JOB_DURATION,
    EXPORT_JOB_OLDEST,
    EXPORT_JOB_QUEUE_WAIT,
    EXPORT_JOBS_ACTIVE,
    EXPORT_JOBS_COMPLETED_TOTAL,
    EXPORT_JOBS_FAILED_TOTAL,
    EXPORT_JOBS_QUEUE_DEPTH,
)
from src.observability import REQUEST_ID

LOGGER = logging.getLogger("reportcollector.application")
POLL_SECONDS = 1.0
# The worker holds progress in memory; the heartbeat persists a change quickly
# so the client's bar moves, and renews the lease on the old cadence.
PROGRESS_POLL_SECONDS = 1.0
JOB_TIMEOUT_SECONDS = 4 * 60 * 60


def import_dir() -> Path:
    """Use a durable mounted directory for accepted uploads."""
    configured = os.environ.get("IMPORT_DIR")
    base = Path(configured) if configured else Path(get_settings().log_file).resolve().parent / "imports"
    base.mkdir(parents=True, exist_ok=True)
    return base


# The accepted workbook containers; the internal key keeps the upload's own
# suffix so the worker can pick the reader without guessing from bytes.
INPUT_SUFFIXES = frozenset({".xlsb", ".xlsx"})


def input_path(input_key: str) -> Path:
    """Resolve an internal upload key; callers never provide an arbitrary path."""
    candidate = Path(input_key)
    if candidate.name != input_key or candidate.suffix.casefold() not in INPUT_SUFFIXES:
        message = "Некорректный ключ файла импорта."
        raise ValueError(message)
    return import_dir() / candidate.name


@dataclass(slots=True)
class ImportJob:
    id: str
    user_id: str
    status: str
    progress: int
    total: int
    file_name: str
    error: str | None
    result: dict[str, Any] | None
    input_key: str
    request_id: str | None
    lease_token: UUID | None
    plan_id: UUID | None
    expected_rows: int
    phase: str | None
    attempt: int
    started_at: datetime | None
    heartbeat_at: datetime | None


GenerateImport = Callable[[ImportJob, Path], Awaitable[dict[str, Any]]]


class ImportValidationError(ValueError):
    """An explicitly safe user-facing import failure."""


class DurableImportManager:
    """Run accepted imports outside HTTP with leases and bounded retries."""

    def __init__(
        self,
        *,
        kind: str,
        generate: GenerateImport,
        repository: JobRepository | None = None,
        concurrency: int = 1,
    ) -> None:
        self.kind = kind
        self.generate = generate
        self.repository = repository or JobRepository()
        self.concurrency = concurrency
        self._workers: list[asyncio.Task[None]] = []
        self._metrics: asyncio.Task[None] | None = None
        self._last_cleanup = 0.0

    @staticmethod
    def job_from_record(record: DataJob) -> ImportJob:
        payload = record.payload
        return ImportJob(
            id=str(record.id),
            user_id=record.user_id,
            status=record.status,
            progress=record.progress,
            total=record.total,
            file_name=str(payload.get("file_name", "import.xlsb")),
            error=record.error,
            result=record.result,
            input_key=str(payload["input_key"]),
            request_id=record.request_id,
            lease_token=record.lease_token,
            plan_id=UUID(str(payload["plan_id"])) if payload.get("plan_id") else None,
            expected_rows=int(payload.get("expected_rows", 0)),
            phase=record.phase,
            attempt=record.attempt,
            started_at=record.started_at,
            heartbeat_at=record.heartbeat_at,
        )

    async def create_from_path(
        self,
        *,
        source: Path,
        file_name: str,
        digest: str,
        size: int,
        user_id: str,
        plan_id: UUID,
        expected_rows: int = 0,
    ) -> ImportJob:
        input_suffix = Path(file_name).suffix.casefold()
        if input_suffix not in INPUT_SUFFIXES:
            message = "Неподдерживаемый формат файла импорта."
            raise ValueError(message)
        input_key = f"{uuid4().hex}{input_suffix}"
        target = input_path(input_key)
        # The move is atomic and happens before the row becomes claimable.
        move = asyncio.create_task(asyncio.to_thread(source.replace, target))
        try:
            await asyncio.shield(move)
        except asyncio.CancelledError:
            await move
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        candidate = DataJob(
            kind=self.kind,
            user_id=user_id,
            cache_key=f"v1:{plan_id}:{digest}",
            payload={
                "file_name": file_name,
                "digest": digest,
                "size": size,
                "input_key": input_key,
                "plan_id": str(plan_id),
                "expected_rows": max(expected_rows, 0),
            },
            # Write + recompute: the worker reports both halves against this.
            total=max(expected_rows, 0) * 2,
            request_id=REQUEST_ID.get(),
        )
        admission = asyncio.create_task(self.repository.create(candidate, reuse_done=False))
        cancelled = False
        try:
            try:
                record = await asyncio.shield(admission)
            except asyncio.CancelledError:
                cancelled = True
                record = await admission
        except ExportQueueFullError:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        if record.id != candidate.id:
            # A request with the same digest already exists. The newly staged
            # bytes are redundant; return the durable record that won the race.
            await asyncio.to_thread(target.unlink, missing_ok=True)
        if cancelled:
            raise asyncio.CancelledError
        log_event(
            LOGGER,
            "import.accepted",
            "Файл поставлен в очередь загрузки.",
            job_id=str(record.id),
            operation=self.kind,
            request_id=record.request_id,
            bytes=size,
        )
        return self.job_from_record(record)

    async def get(self, job_id: str, user_id: str) -> ImportJob:
        return self.job_from_record(await self.repository.get(job_id, user_id, self.kind))

    async def cancel(self, job_id: str, user_id: str) -> ImportJob:
        record = await self.repository.cancel(job_id, user_id, self.kind)
        # The worker may still have the file open. Its cleanup (or TTL sweep
        # after a crashed worker) removes it once processing has stopped.
        return self.job_from_record(record)

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self.run(), name=f"{self.kind}-worker-{index}") for index in range(self.concurrency)
        ]
        self._metrics = asyncio.create_task(self._sample_metrics(), name=f"{self.kind}-metrics")

    async def stop(self) -> None:
        tasks = [task for task in (*self._workers, self._metrics) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers = []
        self._metrics = None

    async def _sample_metrics(self) -> None:
        while True:
            try:
                active = await self.repository.active(self.kind)
                EXPORT_JOBS_ACTIVE.labels(kind=self.kind).set(len(active))
                EXPORT_JOBS_QUEUE_DEPTH.labels(kind=self.kind).set(sum(job.status == "pending" for job in active))
                EXPORT_JOB_OLDEST.labels(kind=self.kind).set(
                    max((time.time() - job.created_at.timestamp() for job in active), default=0)
                )
            except Exception as error:  # noqa: BLE001 - metrics must not kill the worker
                log_error(LOGGER, "import.metrics_failed", "Не удалось прочитать очередь загрузки.", error=str(error))
            await asyncio.sleep(5)

    async def run(self) -> None:
        while True:
            try:
                if time.monotonic() - self._last_cleanup > 60:
                    await self.cleanup_orphans()
                    self._last_cleanup = time.monotonic()
                for expired in await self.repository.expired_records(self.kind):
                    await asyncio.to_thread(cleanup_expired_import, str(expired.payload["input_key"]))
                    await self.repository.remove_expired(expired.id)
                record = await self.repository.claim(
                    self.kind,
                    concurrency=self.concurrency,
                    worker_id=f"{socket.gethostname()}:{os.getpid()}:{self.kind}",
                )
                if record is not None:
                    await self.execute(record)
                    continue
            except Exception as error:  # noqa: BLE001 - dependency outages are recoverable
                log_error(LOGGER, "import.worker_error", "Ошибка обработчика загрузки.", error=str(error))
            await asyncio.sleep(POLL_SECONDS)

    async def cleanup_orphans(self) -> None:
        """Reclaim files left before admission or after an ambiguous DB response."""
        candidates = await asyncio.to_thread(_stale_inputs)
        if not candidates:
            return
        referenced = await self.repository.referenced_inputs(self.kind, [path.name for path in candidates])
        for path in candidates:
            if path.name not in referenced:
                await asyncio.to_thread(path.unlink, missing_ok=True)

    async def execute(self, record: DataJob) -> None:
        job = self.job_from_record(record)
        started = time.monotonic()
        token = REQUEST_ID.set(job.request_id)
        EXPORT_JOB_QUEUE_WAIT.labels(kind=self.kind).observe(max(0, time.time() - record.created_at.timestamp()))
        log_event(LOGGER, "import.started", "Обработка загрузки началась.", job_id=job.id, attempt=record.attempt)
        task = asyncio.ensure_future(self.generate(job, input_path(job.input_key)))
        heartbeat = asyncio.create_task(self._heartbeat(record, job))
        committed = False
        error_code = "worker_stopped"
        retryable = True
        failure_message: str | None = None
        try:
            async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
                done, _ = await asyncio.wait((task, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
                await task
                # The generator commits writes and job completion atomically.
                # Never acknowledge in a second transaction after writing data.
                current = await self.repository.get(job.id, job.user_id, self.kind)
                committed = current.status == "done"
                if not committed:
                    msg = "Import did not commit its result"
                    raise LeaseLostError(msg)  # noqa: TRY301 - classified with other attempt failures
                if committed:
                    EXPORT_JOBS_COMPLETED_TOTAL.labels(kind=self.kind).inc()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - API receives a stable classification
            retryable = isinstance(error, (ConnectionError, OperationalError)) or (
                isinstance(error, DBAPIError) and error.connection_invalidated
            )
            error_code = "dependency_unavailable" if retryable else "generation_failed"
            failure_message = (
                str(error)[:2000]
                if isinstance(error, ImportValidationError)
                else ("Не удалось загрузить файл. План не изменён. Повторите загрузку.")
            )
            EXPORT_JOBS_FAILED_TOTAL.labels(kind=self.kind).inc()
            log_error(LOGGER, "import.failed", "Не удалось загрузить файл.", job_id=job.id, error=str(error))
        finally:
            task.cancel()
            heartbeat.cancel()
            await asyncio.gather(task, heartbeat, return_exceptions=True)
            try:
                if not committed:
                    await self.repository.finish(
                        record,
                        error_code=error_code,
                        error=failure_message,
                        retryable=retryable,
                    )
                current = await self.repository.get(job.id, job.user_id, self.kind)
                committed = current.status == "done"
                if current.status in {"done", "failed"}:
                    await asyncio.to_thread(input_path(job.input_key).unlink, missing_ok=True)
            finally:
                EXPORT_JOB_DURATION.labels(kind=self.kind).observe(time.monotonic() - started)
                log_event(
                    LOGGER,
                    "import.finished",
                    "Обработка загрузки завершена.",
                    job_id=job.id,
                    outcome="done" if committed else "interrupted",
                    rows=job.progress,
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                REQUEST_ID.reset(token)

    async def _heartbeat(self, record: DataJob, job: ImportJob) -> None:
        """
        Persist the child's progress and keep the lease alive.

        The child reports progress into `job` as it works (stdout events in the
        subprocess runner, a direct callback in-process), so the loop watches
        that state and writes a change within a second. The lease is still
        renewed only every `LEASE_SECONDS / 3`.
        """
        # `None` forces the first pass to persist whatever the worker already
        # reported; the child usually reports `parse` before this task runs.
        last: tuple[int, int, str | None] | None = None
        renewed_at = time.monotonic()
        while True:
            await asyncio.sleep(PROGRESS_POLL_SECONDS)
            state = (job.progress, job.total, job.phase)
            lease_due = time.monotonic() - renewed_at >= LEASE_SECONDS / 3
            if state == last and not lease_due:
                continue
            if not await self.repository.heartbeat(record, job.progress, job.total, job.phase):
                if (await self.repository.get(job.id, job.user_id, self.kind)).status == "done":
                    return
                message = "Import lease is no longer owned by this worker"
                raise LeaseLostError(message)
            last = state
            renewed_at = time.monotonic()


def cleanup_expired_import(input_key: str) -> None:
    """Delete an accepted upload after its durable job TTL expires."""
    input_path(input_key).unlink(missing_ok=True)


def _stale_inputs() -> list[Path]:
    cutoff = time.time() - JOB_TIMEOUT_SECONDS - JOB_TTL_SECONDS
    candidates: list[Path] = []
    with os.scandir(import_dir()) as entries:
        for entry in entries:
            path = Path(entry.path)
            if path.suffix not in {".upload", ".xlsb", ".xlsx"} or len(path.stem) != 32:
                continue
            if any(char not in "0123456789abcdef" for char in path.stem):
                continue
            try:
                if entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
                    candidates.append(path)
            except FileNotFoundError:
                continue
            if len(candidates) >= 100:
                break
    return candidates
