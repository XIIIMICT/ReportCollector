"""
Фоновые задания выгрузки пообъектного плана в XLSX.

Зачем: полная выгрузка (≈306k строк × 216 столбцов) занимает минуты и сотни
мегабайт. Раньше она читалась и готовилась прямо в обработчике запроса — на
event loop — и один пользователь замораживал весь сайт, а несколько
параллельных выгрузок приводили к OOM. Теперь:

- POST создаёт задание и сразу отвечает; файл собирает один фоновый worker
  (строго последовательно, поэтому нагрузка ограничена и API остаётся живым);
- строки читаются батчами через серверный курсор — память не растёт с планом;
- готовый файл лежит на диске и переиспользуется: одинаковый запрос (тот же
  план, фильтры и метка времени) не пересобирается;
- старые задания и файлы вычищаются по TTL.

Очередь в памяти процесса: приложение запускается одним uvicorn-воркером
(см. back/Dockerfile), поэтому отдельная таблица/брокер не нужны. После
перезапуска незавершённые задания теряются — клиент создаёт новое.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

from src.app_logging import log_error
from src.database import async_session_factory

from .export import RenovationWorkbookWriter, build_plan_sheet_layout
from .service import iter_plan_export_rows, read_plan_export_meta

if TYPE_CHECKING:
    from .models import RBlock
    from .plan import RPlanState

LOGGER = logging.getLogger("reportcollector.application")

# Готовые файлы живут час: этого хватает, чтобы скачать повторно, и не даёт
# диску расти. Переопределяется EXPORT_DIR (например, на volume в prod).
JOB_TTL_SECONDS = 3600.0
# Сколько заданий может стоять в очереди/выполняться одновременно.
MAX_ACTIVE_JOBS = 8
MAX_ACTIVE_JOBS_PER_USER = 2


class ExportQueueFullError(RuntimeError):
    """Очередь выгрузок переполнена (HTTP 429)."""


def _export_dir() -> Path:
    configured = os.environ.get("EXPORT_DIR")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "reportcollector-exports"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass(slots=True)
class ExportJobSpec:
    """Всё, что нужно worker'у, чтобы собрать файл (сериализуемо в память)."""

    plan_id: UUID
    state: RPlanState
    asset_ids: list[UUID]
    row_ids: list[UUID] | None
    block: RBlock | None
    file_name: str
    total: int
    cache_key: str


@dataclass(slots=True)
class ExportJob:
    id: str
    user_id: str
    spec: ExportJobSpec
    status: Literal["pending", "running", "done", "failed"] = "pending"
    progress: int = 0
    path: Path | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)


class ExportJobManager:
    """Очередь заданий и единственный worker, собирающий файлы по очереди."""

    def __init__(self, session_factory: Callable[[], AsyncSession] | None = None) -> None:
        # Фабрика сессий инжектится тестами; в приложении — глобальная.
        self._session_factory: Callable[[], AsyncSession] = session_factory or async_session_factory
        self._jobs: dict[str, ExportJob] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run(), name="renovation-export-worker")

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        self._queue = None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    def _prune(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        for job_id in [job_id for job_id, job in self._jobs.items() if job.created_at < cutoff]:
            job = self._jobs.pop(job_id)
            for path in (job.path, self._part_path(job_id)):
                if path is not None:
                    path.unlink(missing_ok=True)

    def _part_path(self, job_id: str) -> Path:
        return _export_dir() / f"{job_id}.part"

    def _final_path(self, job_id: str) -> Path:
        return _export_dir() / f"{job_id}.xlsx"

    def _active(self) -> list[ExportJob]:
        return [job for job in self._jobs.values() if job.status in ("pending", "running")]

    async def create(self, spec: ExportJobSpec, user_id: str) -> ExportJob:
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
                    "renovation.export_job_failed",
                    "Не удалось сформировать выгрузку плана.",
                    error=str(error),
                    job_id=job.id,
                )
            finally:
                self._part_path(job_id).unlink(missing_ok=True)

    async def _generate(self, job: ExportJob) -> None:
        """Собрать XLSX: чтение батчами, запись — в отдельном потоке."""
        spec = job.spec
        async with self._session_factory() as session:
            meta = await read_plan_export_meta(
                session,
                spec.asset_ids,
                plan_id=spec.plan_id,
                state=spec.state,
                row_ids=spec.row_ids,
                block=spec.block,
            )
            partial = self._part_path(job.id)
            final = self._final_path(job.id)
            with partial.open("wb") as sink:
                writer = RenovationWorkbookWriter(
                    sink,
                    build_plan_sheet_layout(spec.block),
                    meta.reference_labels,
                )
                try:
                    async for batch in iter_plan_export_rows(
                        session,
                        plan_id=meta.plan_id,
                        asset_ids=spec.asset_ids,
                        row_ids=spec.row_ids,
                    ):
                        # Подготовка строк и сборка XML — CPU-bound: уводим из
                        # event loop, чтобы API отвечал во время выгрузки.
                        await asyncio.to_thread(writer.write_rows, batch)
                        job.progress += len(batch)
                finally:
                    await asyncio.to_thread(writer.close)
            partial.replace(final)
            job.path = final


export_jobs = ExportJobManager()
