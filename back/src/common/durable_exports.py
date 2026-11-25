"""Durable export execution with fenced attempts and bounded downloads."""

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.exc import DBAPIError, OperationalError

from src.app_logging import log_error, log_event
from src.common.export_jobs import ExportJob, ExportSpec, export_dir, final_path, part_path
from src.common.job_models import DataJob
from src.common.job_repository import LEASE_SECONDS, JobRepository
from src.metrics import (
    EXPORT_JOB_BYTES,
    EXPORT_JOB_DURATION,
    EXPORT_JOB_OLDEST,
    EXPORT_JOB_QUEUE_WAIT,
    EXPORT_JOB_ROWS,
    EXPORT_JOBS_ACTIVE,
    EXPORT_JOBS_COMPLETED_TOTAL,
    EXPORT_JOBS_FAILED_TOTAL,
    EXPORT_JOBS_QUEUE_DEPTH,
)
from src.observability import REQUEST_ID

if TYPE_CHECKING:
    from src.common.export_jobs import GenerateCoroutine

LOGGER = logging.getLogger("reportcollector.application")
POLL_SECONDS = 1.0
JOB_TIMEOUT_SECONDS = 3600
DOWNLOAD_IDLE_SECONDS = 120


class LeaseLostError(RuntimeError):
    """The worker must stop; another attempt or cancellation owns the job."""


class DurableExportManager[Spec: ExportSpec]:
    def __init__(
        self,
        model: type[Spec],
        generate: GenerateCoroutine,
        *,
        kind: str,
        repository: JobRepository | None = None,
        concurrency: int = 1,
    ) -> None:
        self.model = model
        self.generate = generate
        self.kind = kind
        self.repository = repository or JobRepository()
        self.concurrency = concurrency
        self._workers: list[asyncio.Task[None]] = []
        self._metrics: asyncio.Task[None] | None = None

    def _job(self, record: DataJob) -> ExportJob:
        spec = self.model.model_validate({**record.payload, "total": record.total})
        job = ExportJob(
            id=str(record.id),
            user_id=record.user_id,
            spec=spec,
            status=record.status,
            progress=record.progress,
            path=final_path(record.artifact) if record.artifact else None,
            error=record.error,
            size=record.size,
            request_id=record.request_id,
            artifact_key=record.artifact,
            phase=record.phase,
            attempt=record.attempt,
            started_at=record.started_at,
            heartbeat_at=record.heartbeat_at,
        )
        return job

    async def create(self, spec: Spec, user_id: str) -> ExportJob:
        payload = spec.model_dump(mode="json", by_alias=True)
        record = await self.repository.create(
            DataJob(
                kind=self.kind,
                user_id=user_id,
                payload=payload,
                cache_key=payload["cache_key"],
                total=payload["total"],
                request_id=REQUEST_ID.get(),
            )
        )
        if record.status == "done" and not await asyncio.to_thread(_file_exists, record):
            await self.repository.missing_artifact(record)
            return await self.create(spec, user_id)
        log_event(
            LOGGER,
            "export.accepted",
            "Выгрузка поставлена в очередь.",
            job_id=str(record.id),
            operation=self.kind,
            request_id=record.request_id,
        )
        return self._job(record)

    async def get(self, job_id: str, user_id: str) -> ExportJob:
        record = await self.repository.get(job_id, user_id, self.kind)
        if record.status == "done" and not await asyncio.to_thread(_file_exists, record):
            await self.repository.missing_artifact(record)
            record = await self.repository.get(job_id, user_id, self.kind)
        return self._job(record)

    async def cancel(self, job_id: str, user_id: str) -> ExportJob:
        return self._job(await self.repository.cancel(job_id, user_id, self.kind))

    async def start(self) -> None:
        """Start bounded supervisors; their heavy generators run in child processes."""
        if not self._workers:
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
        """Queue gauges must keep advancing while a long attempt is running."""
        while True:
            try:
                active = await self.repository.active(self.kind)
                EXPORT_JOBS_ACTIVE.labels(kind=self.kind).set(len(active))
                EXPORT_JOBS_QUEUE_DEPTH.labels(kind=self.kind).set(sum(j.status == "pending" for j in active))
                EXPORT_JOB_OLDEST.labels(kind=self.kind).set(
                    max((time.time() - j.created_at.timestamp() for j in active), default=0)
                )
            except Exception as error:  # noqa: BLE001 - dependency outages are recoverable
                log_error(LOGGER, "export.metrics_failed", "Не удалось прочитать состояние очереди.", error=str(error))
            await asyncio.sleep(5)

    async def run(self) -> None:
        while True:
            try:
                for job_id in await self.repository.prune(self.kind):
                    await asyncio.to_thread(_remove_artifacts, str(job_id))
                    await self.repository.remove_expired(job_id)
                record = await self.repository.claim(
                    self.kind,
                    concurrency=self.concurrency,
                    worker_id=f"{socket.gethostname()}:{os.getpid()}:{self.kind}",
                )
                if record is not None:
                    await self.execute(record)
                    continue
            except Exception as error:  # noqa: BLE001 - a dependency outage must not kill the consumer
                log_error(LOGGER, "export.worker_error", "Ошибка фонового обработчика выгрузок.", error=str(error))
            await asyncio.sleep(POLL_SECONDS)

    async def execute(self, record: DataJob) -> None:
        job = self._job(record)
        started = time.monotonic()
        token = REQUEST_ID.set(job.request_id)
        EXPORT_JOB_QUEUE_WAIT.labels(kind=self.kind).observe(max(0, time.time() - record.created_at.timestamp()))
        log_event(
            LOGGER,
            "export.started",
            "Формирование выгрузки началось.",
            job_id=job.id,
            operation=self.kind,
            attempt=record.attempt,
            request_id=job.request_id,
        )
        generate = asyncio.ensure_future(self.generate(job))
        heartbeat = asyncio.create_task(self._heartbeat(record, job))
        committed = False
        error_code = "worker_stopped"
        retryable = True
        try:
            async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
                done, _ = await asyncio.wait((generate, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                # A successful heartbeat task never completes on its own.
                await generate
                size = _artifact_size(job)
                committed = await self.repository.finish(record, size=size, total=job.progress)
                if committed:
                    EXPORT_JOBS_COMPLETED_TOTAL.labels(kind=self.kind).inc()
                    EXPORT_JOB_ROWS.labels(kind=self.kind).observe(job.progress)
                    EXPORT_JOB_BYTES.labels(kind=self.kind).observe(size)
        except asyncio.CancelledError:
            # Do not acknowledge a cancelled writer until its thread has stopped.
            # On a hard kill the lease expires and the next worker recovers it.
            raise
        except Exception as error:  # noqa: BLE001 - classify and keep internal details out of the API
            retryable = isinstance(error, (ConnectionError, OperationalError)) or (
                isinstance(error, DBAPIError) and error.connection_invalidated
            )
            error_code = (
                "dependency_unavailable"
                if retryable
                else ("lease_lost" if isinstance(error, LeaseLostError) else "generation_failed")
            )
            EXPORT_JOBS_FAILED_TOTAL.labels(kind=self.kind).inc()
            log_error(
                LOGGER,
                "export.export_job_failed",
                "Не удалось сформировать выгрузку.",
                job_id=job.id,
                error=str(error),
                error_code=error_code,
                request_id=job.request_id,
            )
        finally:
            for task in (generate, heartbeat):
                task.cancel()
            await asyncio.gather(generate, heartbeat, return_exceptions=True)
            try:
                part_path(job.file_id).unlink(missing_ok=True)
                if not committed:
                    # Keep any finished artifact until TTL cleanup: a lost DB reply
                    # can mean completion committed even though this worker saw an
                    # exception. Release only after the writer has stopped.
                    await self.repository.finish(record, error_code=error_code, retryable=retryable)
            finally:
                EXPORT_JOB_DURATION.labels(kind=self.kind).observe(time.monotonic() - started)
                log_event(
                    LOGGER,
                    "export.finished",
                    "Обработка выгрузки завершена.",
                    job_id=job.id,
                    operation=self.kind,
                    outcome="done" if committed else "interrupted",
                    rows=job.progress,
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                REQUEST_ID.reset(token)

    async def _heartbeat(self, record: DataJob, job: ExportJob) -> None:
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            if not await self.repository.heartbeat(record, job.progress, job.spec.total):
                msg = "Export lease is no longer owned by this worker"
                raise LeaseLostError(msg)

    async def stream_growing(self, job_id: str, user_id: str) -> AsyncIterator[bytes]:
        """Pin a download to one attempt; never concatenate retried ZIP archives."""
        offset = 0
        artifact: str | None = None
        last_data = time.monotonic()
        job = await self.get(job_id, user_id)
        last_status = time.monotonic()
        yield b""
        while True:
            if job.status != "done" and time.monotonic() - last_status >= 0.5:
                job = await self.get(job_id, user_id)
                last_status = time.monotonic()
            if job.status == "failed":
                msg = "Export failed or was cancelled"
                raise RuntimeError(msg)
            if artifact is not None and (job.artifact_key != artifact or job.status == "pending"):
                msg = "Export attempt interrupted; download the completed job again"
                raise RuntimeError(msg)
            if job.status in ("running", "done") and job.artifact_key is not None:
                artifact = job.artifact_key
                path = final_path(artifact) if job.status == "done" else part_path(artifact)
                data = await asyncio.to_thread(_read_chunk, path, offset)
                # A rename can happen between reading the status and opening the file.
                if not data:
                    data = await asyncio.to_thread(_read_chunk, final_path(artifact), offset)
                if data:
                    offset += len(data)
                    last_data = time.monotonic()
                    yield data
                    continue
                if job.status == "done":
                    if offset != job.size:
                        msg = "Export artifact is unavailable or incomplete"
                        raise RuntimeError(msg)
                    return
            if time.monotonic() - last_data > DOWNLOAD_IDLE_SECONDS:
                msg = "Export download made no progress"
                raise TimeoutError(msg)
            await asyncio.sleep(0.25)


def _read_chunk(path: Path, offset: int) -> bytes:
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(64 * 1024)
    except FileNotFoundError:
        return b""


def _artifact_size(job: ExportJob) -> int:
    if job.path is None:
        message = "Export generator did not publish an artifact"
        raise FileNotFoundError(message)
    return job.path.stat().st_size


def _file_exists(record: DataJob) -> bool:
    if record.artifact is None:
        return False
    try:
        return final_path(record.artifact).stat().st_size == record.size
    except FileNotFoundError:
        return False


def _remove_artifacts(job_id: str) -> None:
    # IDs originate in the UUID database column; no arbitrary path is accepted.
    for suffix in ("part", "xlsx"):
        for path in export_dir().glob(f"{job_id}-*.{suffix}"):
            path.unlink(missing_ok=True)
