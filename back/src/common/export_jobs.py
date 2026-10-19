"""
Background export jobs: the shared queue every project's big exports run on.

Generalized from the renovation plan export: a full-table export can be
minutes of CPU and hundreds of megabytes (the plan is ~306k rows × 216
columns ≈ 520 MB), which must not happen inside a request handler — one user
would freeze the whole event loop and several parallel exports would OOM the
container. Instead:

- ``ExportJobManager.create`` registers a job and answers at once; a single
  sequential worker (started in the app lifespan, in-process — the app runs
  one uvicorn worker) generates the files one by one through a
  project-provided ``generate`` coroutine, so the load stays bounded and the
  API stays responsive;
- the coroutine writes the XLSX to ``{EXPORT_DIR}/{job_id}.part`` and renames
  it to ``{job_id}.xlsx`` on success, so ``stream_growing`` can tail the
  growing file and serve the bytes WHILE they are generated (the browser's
  own download UI shows progress from the first byte);
- a finished file is reused: the same cache key (the project hashes its
  scope + data stamp into ``spec.cache_key``) is not regenerated;
- jobs and files are pruned after ``JOB_TTL_SECONDS``.

The project-defined spec is opaque to the manager (it only reads
``file_name``/``total``/``cache_key`` through duck-typed attributes... see
``ExportJobSpecFields``); the queue lives in process memory: after a restart
pending jobs are lost and the client simply creates a new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.app_logging import log_error
from src.common.export import INLINE_EXPORT_MAX_ROWS
from src.common.xlsx_table import TableWorkbookWriter
from src.downloads import attachment_response

if TYPE_CHECKING:
    from src.common.export import ExportColumnLike

LOGGER = logging.getLogger("reportcollector.application")

# Готовые файлы живут час: этого хватает, чтобы скачать повторно, и не даёт
# диску расти. Переопределяется EXPORT_DIR (например, на volume в prod).
JOB_TTL_SECONDS = 3600.0
# Сколько заданий может стоять в очереди/выполняться одновременно.
MAX_ACTIVE_JOBS = 8
MAX_ACTIVE_JOBS_PER_USER = 2


class ExportQueueFullError(RuntimeError):
    """Очередь выгрузок переполнена (HTTP 429)."""


def export_dir() -> Path:
    """Return the directory export files are written to (EXPORT_DIR overrides the temp dir)."""
    configured = os.environ.get("EXPORT_DIR")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "reportcollector-exports"
    base.mkdir(parents=True, exist_ok=True)
    return base


def part_path(job_id: str) -> Path:
    """Return the growing file a job's generator writes (renamed to final on success)."""
    return export_dir() / f"{job_id}.part"


def final_path(job_id: str) -> Path:
    """Return the finished, downloadable file of a job."""
    return export_dir() / f"{job_id}.xlsx"


class ExportJobSpecLike(Protocol):
    """The three fields the manager reads from a project's spec."""

    @property
    def file_name(self) -> str: ...

    @property
    def total(self) -> int: ...

    @property
    def cache_key(self) -> str: ...


@dataclass(slots=True)
class ExportJob:
    id: str
    user_id: str
    spec: ExportJobSpecLike
    status: Literal["pending", "running", "done", "failed"] = "pending"
    progress: int = 0
    path: Path | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    # Final file size in bytes (known only once done; a live download that
    # streamed the growing file does not need it).
    size: int | None = None


GenerateCoroutine = Callable[[ExportJob], Awaitable[None]]


class ExportJobManager:
    """Очередь заданий и единственный worker, собирающий файлы по очереди."""

    def __init__(self, generate: GenerateCoroutine | None = None) -> None:
        """
        Hold the project's generator and own the queue.

        ``generate`` builds one job's file: it receives the job (with the
        project spec and the ``progress`` counter to advance) and must write
        the XLSX to ``part_path(job.id)`` and rename it to
        ``final_path(job.id)``. The renovation manager's generator is the
        canonical implementation: read rows in batches, write through a
        streaming writer, push CPU-heavy parts off the event loop. Tests may
        omit it and monkeypatch ``_generate`` instead.
        """
        self._generate_callback = generate
        self._jobs: dict[str, ExportJob] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task[None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run(), name="export-jobs-worker")

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        self._queue = None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    # ── registry ─────────────────────────────────────────────────────────

    def _prune(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        for job_id in [job_id for job_id, job in self._jobs.items() if job.created_at < cutoff]:
            job = self._jobs.pop(job_id)
            for path in (job.path, part_path(job_id)):
                if path is not None:
                    path.unlink(missing_ok=True)

    def _active(self) -> list[ExportJob]:
        return [job for job in self._jobs.values() if job.status in ("pending", "running")]

    async def create(self, spec: ExportJobSpecLike, user_id: str) -> ExportJob:
        """Создать задание; одинаковый готовый файл переиспользуется."""
        self._prune()
        # Готовый файл для того же scope+версии: отдаём его же, не собирая
        # заново. Заводим новую запись, чтобы проверка владельца работала.
        for job in self._jobs.values():
            if (
                job.status == "done"
                and job.spec.cache_key == spec.cache_key
                and job.path is not None
                and job.path.exists()
            ):
                reused = ExportJob(
                    id=str(uuid4()),
                    user_id=user_id,
                    spec=spec,
                    status="done",
                    progress=spec.total,
                    path=job.path,
                )
                self._jobs[reused.id] = reused
                return reused
        active = self._active()
        if len(active) >= MAX_ACTIVE_JOBS:
            message = "Очередь выгрузок заполнена. Попробуйте через минуту."
            raise ExportQueueFullError(message)
        if sum(1 for job in active if job.user_id == user_id) >= MAX_ACTIVE_JOBS_PER_USER:
            message = "У вас уже формируются выгрузки. Дождитесь их завершения."
            raise ExportQueueFullError(message)
        job = ExportJob(id=str(uuid4()), user_id=user_id, spec=spec)
        self._jobs[job.id] = job
        await self.start()
        assert self._queue is not None  # noqa: S101 - start() создал очередь
        await self._queue.put(job.id)
        return job

    def get(self, job_id: str, user_id: str) -> ExportJob:
        self._prune()
        job = self._jobs.get(job_id)
        if job is None or job.user_id != user_id:
            raise KeyError(job_id)
        return job

    def file(self, job_id: str, user_id: str) -> ExportJob:
        job = self.get(job_id, user_id)
        if job.status != "done" or job.path is None or not job.path.exists():
            raise KeyError(job_id)
        return job

    # ── worker ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        assert self._queue is not None  # noqa: S101
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status != "pending":
                continue
            job.status = "running"
            try:
                await self._generate(job)
                job.status = "done"
            except asyncio.CancelledError:
                raise
            except BaseException as error:  # noqa: BLE001
                job.status = "failed"
                job.error = str(error) or "Не удалось сформировать файл."
                log_error(
                    LOGGER,
                    "export.export_job_failed",
                    "Не удалось сформировать выгрузку.",
                    error=str(error),
                    job_id=job.id,
                )
            finally:
                part_path(job_id).unlink(missing_ok=True)

    async def _generate(self, job: ExportJob) -> None:
        """Delegate to the project's generator (a method so tests can patch it)."""
        if self._generate_callback is None:
            message = "generate coroutine is not configured"
            raise RuntimeError(message)
        await self._generate_callback(job)

    # ── download streamer ────────────────────────────────────────────────

    async def stream_growing(self, job_id: str, user_id: str) -> AsyncIterator[bytes]:
        """
        Yield the export file's bytes AS it is being written by the worker.

        The browser downloads this URL directly, so its native download UI
        shows progress from the first byte (not only once generation
        finishes, which for a 300k-row plan is minutes of silence). We tail
        the growing `.part` file the worker writes, flush it to the client,
        and finish when the job is done (the `.part` is then renamed to the
        final `.xlsx`). If the job fails mid-stream we cannot retract the
        bytes already sent, so the connection just ends (the browser keeps
        the partial file); the client surfaces a toast when it later reads
        the failed status. A finished (cached) job is streamed in one shot.
        """
        job = self.get(job_id, user_id)
        # Fast path: already done (a reused cache hit) - just stream the file.
        if job.status == "done" and job.path is not None:
            with job.path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    yield chunk
            return
        part = part_path(job_id)
        offset = 0
        idle = 0.0
        # Open the response immediately (Starlette sends the headers on the
        # first yielded chunk). While the job is merely QUEUED behind another
        # export the worker has not written a single byte, so without this
        # the browser would sit on "waiting for the server" with no download
        # at all; a zero-length flush makes the download appear at once.
        yield b""
        while True:
            job = self.get(job_id, user_id)
            if job.status == "failed":
                # Nothing more will be written; end the stream.
                return
            written = part.stat().st_size if part.exists() else 0
            if written > offset:
                idle = 0.0
                with part.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read(written - offset)
                offset = written
                if data:
                    yield data
            elif job.status == "done":
                # Renamed: the final file carries the tail (the zip end record).
                final = final_path(job_id)
                with final.open("rb") as handle:
                    handle.seek(offset)
                    while tail := handle.read(64 * 1024):
                        yield tail
                return
            else:
                idle += 1.0
                if idle > 120.0:
                    # Nothing written for two minutes: the worker is stuck or died.
                    return
            await asyncio.sleep(0.25)


# ─── The shared table exporter (every project's dictionaries and forms) ────
#
# The plan export keeps its specialized manager (a scope-specific generator);
# the OTHER tables — «Сырьё» forms and dictionaries, КППК, «Ремонт»
# dictionaries — share one manager and one generic generator built on
# ``TableWorkbookWriter``. Small tables render straight into the response
# (200 + bytes, exactly like before); anything past
# ``INLINE_EXPORT_MAX_ROWS`` rows becomes a background job (202 + the job
# description; the front then native-downloads the file URL, which streams
# the growing file).

# Rows per write batch of the background table generator.
TABLE_JOB_BATCH_ROWS = 5_000


@dataclass(slots=True)
class TableExportSpec:
    """A table export's whole scope: the writer config plus the rows themselves."""

    sheet_title: str
    columns: Sequence[ExportColumnLike]
    header_pinned: bool
    file_name: str
    total: int
    cache_key: str
    lists: Mapping[str, Sequence[str]] | None
    column_lists: Mapping[str, str]
    calculated: Mapping[str, tuple[str, ...]] | None
    totals_row: Mapping[str, float] | None
    # The materialized rows (the request handler already read them to count;
    # the worker streams them into the writer in batches, then drops them).
    rows: list[BaseModel]


class ExportJobRead(BaseModel):
    """The job status the front polls / downloads by."""

    id: str
    status: Literal["pending", "running", "done", "failed"]
    progress: int
    total: int
    fileName: str
    error: str | None = None


async def _generate_table_file(job: ExportJob) -> None:
    """Write one table export through the shared streaming writer, in batches."""
    spec = job.spec
    assert isinstance(spec, TableExportSpec)  # noqa: S101
    partial = part_path(job.id)
    final = final_path(job.id)
    with partial.open("wb") as sink:
        writer = TableWorkbookWriter(
            sink,
            spec.sheet_title,
            spec.columns,
            header_pinned=spec.header_pinned,
            lists=spec.lists,
            column_lists=spec.column_lists,
            calculated=spec.calculated,
            row_total=spec.total,
            totals_row=spec.totals_row,
        )
        try:
            for start in range(0, len(spec.rows), TABLE_JOB_BATCH_ROWS):
                batch = spec.rows[start : start + TABLE_JOB_BATCH_ROWS]
                await asyncio.to_thread(writer.write_rows, batch)
                job.progress += len(batch)
        finally:
            await asyncio.to_thread(writer.close)
    partial.replace(final)
    job.path = final
    job.size = final.stat().st_size
    # The rows are no longer needed (the file is ready for re-download); drop
    # the reference so a finished job does not pin the whole table in memory
    # for its TTL hour.
    spec.rows = []


table_export_jobs = ExportJobManager(_generate_table_file)


async def export_table_or_job(
    *,
    sheet_title: str,
    columns: Sequence[ExportColumnLike],
    rows: Sequence[BaseModel],
    header_pinned: bool,
    file_name: str,
    user_id: str,
    lists: Mapping[str, Sequence[str]] | None = None,
    column_lists: Mapping[str, str] | None = None,
    calculated: Mapping[str, tuple[str, ...]] | None = None,
    totals_row: Mapping[str, float] | None = None,
) -> Response:
    """
    Render a table export — inline for small tables, as a job for big ones.

    The single dual-mode entry every project's table endpoint calls: up to
    ``INLINE_EXPORT_MAX_ROWS`` rows the workbook renders right in the request
    and returns as an attachment (the bytes are byte-identical to the job's);
    past the threshold a background job builds the same workbook and the
    response is 202 + ``ExportJobRead`` (the front hands the file URL to the
    browser, which downloads the file while it is still being generated).
    ``totals_row`` (column key → aggregate) mirrors the grid's pinned summary
    row when the exported table carries one.
    """
    file_name = file_name.strip()
    if len(rows) <= INLINE_EXPORT_MAX_ROWS:
        sink = BytesIO()
        writer = TableWorkbookWriter(
            sink,
            sheet_title,
            columns,
            header_pinned=header_pinned,
            lists=lists,
            column_lists=column_lists,
            calculated=calculated,
            row_total=len(rows),
            totals_row=totals_row,
        )
        writer.write_rows(rows)
        writer.close()
        return attachment_response(sink.getvalue(), filename=file_name)
    spec = TableExportSpec(
        sheet_title=sheet_title,
        columns=columns,
        header_pinned=header_pinned,
        file_name=file_name,
        total=len(rows),
        cache_key=uuid4().hex,
        lists=lists,
        column_lists=dict(column_lists or {}),
        calculated=dict(calculated or {}),
        totals_row=dict(totals_row) if totals_row else None,
        rows=list(rows),
    )
    job = await table_export_jobs.create(spec, user_id)
    read = ExportJobRead(
        id=job.id,
        status=job.status,
        progress=job.progress,
        total=job.spec.total,
        fileName=job.spec.file_name,
        error=job.error,
    )
    return JSONResponse(read.model_dump(), status_code=202)
