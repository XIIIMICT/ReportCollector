"""Фоновые задания выгрузки: очередь, дедупликация готовых файлов, лимиты."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from src.renovation.export_jobs import (
    MAX_ACTIVE_JOBS,
    MAX_ACTIVE_JOBS_PER_USER,
    ExportJob,
    ExportJobManager,
    ExportJobSpec,
    ExportQueueFullError,
)
from src.renovation.plan import RPlanState


def _spec(cache_key: str = "key") -> ExportJobSpec:
    return ExportJobSpec(
        plan_id=uuid4(),
        state=RPlanState.WORKING,
        asset_ids=[uuid4()],
        row_ids=None,
        block=None,
        file_name="plan.xlsx",
        total=10,
        cache_key=cache_key,
    )


async def _wait_done(manager: ExportJobManager, job_id: str, user_id: str = "user") -> ExportJob:
    seen: list[str] = []
    for _ in range(200):
        job = manager.get(job_id, user_id)
        seen.append(job.status)
        if job.status in ("done", "failed"):
            return job
        await asyncio.sleep(0.01)
    message = f"job did not finish: {seen[-5:]} error={job.error}"
    raise AssertionError(message)


async def test_export_job_manager_processes_and_reuses_ready_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Один и тот же scope+stamp собирается один раз, повтор отдаёт готовый файл."""
    manager = ExportJobManager()
    calls: list[str] = []

    async def fake_generate(job: ExportJob) -> None:
        calls.append(job.id)
        path = tmp_path / f"{job.id}.xlsx"
        path.write_bytes(b"PK")
        job.path = path

    monkeypatch.setattr(manager, "_generate", fake_generate)
    job = await manager.create(_spec(), "user")
    done = await _wait_done(manager, job.id)
    assert done.status == "done"
    assert len(calls) == 1

    again = await manager.create(_spec(), "user")
    assert again.status == "done"
    assert again.id != job.id
    assert len(calls) == 1, "a ready file must be reused, not regenerated"
    assert manager.file(again.id, "user").path is not None
    await manager.stop()


async def test_export_job_manager_rejects_over_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Глобальный и пер-пользовательский лимиты очереди защищают воркер."""
    manager = ExportJobManager()
    release = asyncio.Event()

    async def fake_generate(job: ExportJob) -> None:
        await release.wait()

    monkeypatch.setattr(manager, "_generate", fake_generate)

    # Пер-пользовательский лимит: два активных задания одного пользователя.
    for index in range(MAX_ACTIVE_JOBS_PER_USER):
        await manager.create(_spec(cache_key=f"user-key-{index}"), "one-user")
    with pytest.raises(ExportQueueFullError):
        await manager.create(_spec(cache_key="user-overflow"), "one-user")

    # Глобальный лимит: добираем очередь заданиями разных пользователей.
    for index in range(MAX_ACTIVE_JOBS - MAX_ACTIVE_JOBS_PER_USER):
        await manager.create(_spec(cache_key=f"global-key-{index}"), f"user-{index}")
    with pytest.raises(ExportQueueFullError):
        await manager.create(_spec(cache_key="global-overflow"), "another-user")

    release.set()
    await manager.stop()


async def test_export_job_manager_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка генерации переводит задание в failed и не роняет воркер."""
    manager = ExportJobManager()

    async def fake_generate(job: ExportJob) -> None:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(manager, "_generate", fake_generate)
    job = await manager.create(_spec(), "user")
    failed = await _wait_done(manager, job.id)
    assert failed.status == "failed"
    assert failed.error == "boom"

    # Воркер жив: следующее задание обрабатывается.
    async def ok_generate(job: ExportJob) -> None:
        job.path = Path("ok.xlsx")

    monkeypatch.setattr(manager, "_generate", ok_generate)
    next_job = await manager.create(_spec(cache_key="next"), "user")
    done = await _wait_done(manager, next_job.id)
    assert done.status == "done"
    await manager.stop()
