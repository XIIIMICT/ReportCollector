"""Cross-connection job persistence, fencing, admission and artifact regressions."""

import asyncio
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import utc_now
from src.common.durable_exports import DurableExportManager
from src.common.export_jobs import ExportQueueFullError, final_path, part_path
from src.common.job_models import DataJob
from src.common.job_repository import JobRepository
from src.renovation.export_jobs import ExportJobSpec
from src.renovation.plan import RPlanState
from src.renovation.schemas import RenovationColumnFilter

pytestmark = pytest.mark.real_schema


@pytest.fixture
def repository(db_session, my_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    return JobRepository(async_sessionmaker(my_engine, class_=AsyncSession, expire_on_commit=False))


def _spec(key="scope") -> ExportJobSpec:
    return ExportJobSpec(
        plan_id=uuid4(),
        state=RPlanState.WORKING,
        asset_ids=[uuid4()],
        row_ids=None,
        block=None,
        file_name="plan.xlsx",
        total=2,
        cache_key=key,
        filters=[RenovationColumnFilter(column="updatedAt", **{"from": "2026-10-14T00:00:00+03:00"})],
    )


async def _write(job) -> None:
    part_path(job.file_id).write_bytes(b"PK" + b"x" * 200_000)
    part_path(job.file_id).replace(final_path(job.file_id))
    job.path = final_path(job.file_id)
    job.progress = job.spec.total


def _manager(repository, generate=_write) -> DurableExportManager[ExportJobSpec]:
    return DurableExportManager(ExportJobSpec, generate, kind="test_export", repository=repository)


async def _expire(repository, job_id, *, attempt=None) -> None:
    async with repository.session_factory() as session, session.begin():
        job = await session.get(DataJob, UUID(job_id))
        job.lease_until = utc_now() - timedelta(seconds=60)
        job.available_at = utc_now() - timedelta(seconds=60)
        if attempt is not None:
            job.attempt = attempt
        session.add(job)


async def test_persisted_query_and_download_survive_manager_restart(repository) -> None:
    first = _manager(repository)
    spec = _spec()
    job = await first.create(spec, "owner")
    other = _manager(repository)
    restored = await other.get(job.id, "owner")
    assert restored.spec == spec
    assert isinstance(restored.spec, ExportJobSpec)
    assert restored.spec.filters is not None
    assert restored.spec.filters[0].from_ == "2026-10-14T00:00:00+03:00"
    claimed = await repository.claim("test_export", worker_id="test-host:123")
    assert claimed.phase == "running"
    assert claimed.worker_id == "test-host:123"
    assert claimed.started_at is not None
    assert claimed.heartbeat_at is not None
    await other.execute(claimed)
    done = await first.get(job.id, "owner")
    assert done.status == "done"
    assert done.phase == "done"
    assert done.progress == done.spec.total == 2
    chunks = [chunk async for chunk in first.stream_growing(job.id, "owner")]
    assert max(map(len, chunks)) <= 64 * 1024
    assert done.path is not None
    assert b"".join(chunks) == done.path.read_bytes()
    assert (await first.create(spec, "owner")).id == job.id
    with pytest.raises(KeyError):
        await other.get(job.id, "another-owner")


async def test_admission_and_claim_are_atomic_across_connections(repository) -> None:
    first, other = _manager(repository), _manager(repository)
    spec = _spec()
    duplicates = await asyncio.gather(*(manager.create(spec, "owner") for manager in (first, other) * 3))
    assert len({job.id for job in duplicates}) == 1
    await first.create(_spec("second"), "owner")
    with pytest.raises(ExportQueueFullError):
        await other.create(_spec("third"), "owner")
    claimed = await asyncio.gather(repository.claim("test_export"), repository.claim("test_export"))
    assert sum(item is not None for item in claimed) == 1
    second = await repository.claim("test_export", concurrency=2)
    assert second is not None
    assert await repository.claim("test_export", concurrency=2) is None


async def test_per_kind_concurrency_ignores_other_running_kinds(repository) -> None:
    """One kind's running jobs must not consume another kind's worker slots.

    Regression for the claim() count: it used to count ALL running jobs, so a
    single running export blocked an import (and vice versa) even though each
    manager declares its own per-kind concurrency. The global cap is separate
    and lives in create() via MAX_ACTIVE_JOBS.
    """

    async def insert(kind: str, status: Literal["pending", "running"], key: str) -> None:
        async with repository.session_factory() as session, session.begin():
            session.add(
                DataJob(
                    kind=kind,
                    user_id="owner",
                    cache_key=key,
                    payload={},
                    status=status,
                    lease_until=utc_now() + timedelta(seconds=60),
                    lease_token=uuid4(),
                )
            )

    # A running export of another kind must not block an import claim.
    await insert("test_export", "running", "export-1")
    await insert("renovation_import", "pending", "import-1")
    claimed = await repository.claim("renovation_import", concurrency=1)
    assert claimed is not None
    assert claimed.kind == "renovation_import"

    # ...and once the import is running, the export kind still obeys ONLY its
    # own slots: a second export claim with concurrency=2 succeeds, with 1 fails.
    await insert("test_export", "pending", "export-2")
    assert await repository.claim("test_export", concurrency=2) is not None
    assert await repository.claim("test_export", concurrency=2) is None
    assert await repository.claim("renovation_import", concurrency=1) is None


async def test_stale_attempt_cannot_publish_or_renew(repository) -> None:
    manager = _manager(repository)
    job = await manager.create(_spec(), "owner")
    first = await repository.claim("test_export")
    await _expire(repository, job.id)
    assert await repository.claim("test_export") is None  # retry backoff
    await _expire(repository, job.id)
    second = await repository.claim("test_export")
    assert second.attempt == 2
    assert second.artifact != first.artifact
    assert not await repository.heartbeat(first, 1, 2)
    assert not await repository.finish(first, total=2)
    await manager.execute(second)
    assert (await manager.get(job.id, "owner")).status == "done"


async def test_cancel_fences_writer_and_is_owner_scoped(repository) -> None:
    manager = _manager(repository)
    job = await manager.create(_spec(), "owner")
    claimed = await repository.claim("test_export")
    with pytest.raises(KeyError):
        await manager.cancel(job.id, "intruder")
    cancelled = await manager.cancel(job.id, "owner")
    assert cancelled.status == "failed"
    assert cancelled.error == "Выгрузка отменена."
    assert not await repository.finish(claimed, total=2)
    assert not await repository.heartbeat(claimed, 1, 2)
    assert await repository.claim("test_export") is None


async def test_failure_retry_budget_and_redacted_errors(repository) -> None:
    async def unavailable(job) -> None:
        msg = "private connection details"
        raise ConnectionError(msg)

    manager = _manager(repository, unavailable)
    job = await manager.create(_spec(), "owner")
    claimed = await repository.claim("test_export")
    await manager.execute(claimed)
    assert (await manager.get(job.id, "owner")).status == "pending"
    await _expire(repository, job.id, attempt=2)
    claimed = await repository.claim("test_export")
    await manager.execute(claimed)
    failed = await manager.get(job.id, "owner")
    assert failed.status == "failed"
    assert failed.error is not None
    assert "private" not in failed.error


async def test_permanent_failure_does_not_retry(repository) -> None:
    async def invalid(job) -> None:
        msg = "invalid row"
        raise ValueError(msg)

    manager = _manager(repository, invalid)
    job = await manager.create(_spec(), "owner")
    await manager.execute(await repository.claim("test_export"))
    assert (await manager.get(job.id, "owner")).status == "failed"
    assert await repository.claim("test_export") is None


async def test_failed_acknowledgement_restores_log_context(repository, monkeypatch) -> None:
    from src.observability import REQUEST_ID

    async def unavailable(*args: object, **kwargs: object) -> None:
        message = "Database unavailable"
        raise ConnectionError(message)

    manager = _manager(repository)
    await manager.create(_spec(), "owner")
    record = await repository.claim("test_export")
    monkeypatch.setattr(repository, "finish", unavailable)
    token = REQUEST_ID.set("outer-request")
    try:
        with pytest.raises(ConnectionError):
            await manager.execute(record)
        assert REQUEST_ID.get() == "outer-request"
    finally:
        REQUEST_ID.reset(token)


async def test_selected_rows_keep_order_across_bounded_batches(db_session) -> None:
    from sqlmodel import select

    from src.renovation.plan import RenovationPlan, RenovationPlanRow
    from src.renovation.service import iter_plan_export_rows

    plan = (await db_session.exec(select(RenovationPlan).where(RenovationPlan.state == RPlanState.WORKING))).one()
    rows = (await db_session.exec(select(RenovationPlanRow).where(RenovationPlanRow.plan_id == plan.id).limit(4))).all()
    assert len(rows) >= 2
    rows = list(reversed(rows))
    batches = [
        batch
        async for batch in iter_plan_export_rows(
            db_session,
            plan_id=plan.id,
            asset_ids=list({row.asset_id for row in rows if row.asset_id}),
            row_ids=[row.id for row in rows],
            include_unassigned=True,
            batch_size=1,
        )
    ]
    assert all(len(batch) == 1 for batch in batches)
    assert [batch[0][0] for batch in batches] == [row.position for row in rows]


async def test_lost_artifact_can_be_regenerated_and_ttl_cleans_attempts(repository, tmp_path) -> None:
    from src.common.durable_exports import _remove_artifacts

    manager = _manager(repository)
    spec = _spec()
    job = await manager.create(spec, "owner")
    await manager.execute(await repository.claim("test_export"))
    done = await manager.get(job.id, "owner")
    assert done.path is not None
    done.path.unlink()
    again = await manager.create(spec, "owner")
    assert again.id != job.id
    assert (await manager.get(job.id, "owner")).status == "failed"
    await manager.execute(await repository.claim("test_export"))
    async with repository.session_factory() as session, session.begin():
        record = await session.get(DataJob, UUID(again.id))
        record.expires_at = utc_now() - timedelta(seconds=1)
        session.add(record)
    with pytest.raises(KeyError):
        await manager.get(again.id, "owner")
    assert UUID(again.id) in await repository.prune("test_export")
    _remove_artifacts(again.id)
    await repository.remove_expired(UUID(again.id))
    assert not list(tmp_path.glob(f"{again.id}-*"))


async def test_shutdown_stops_generation_then_requeues(repository) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    async def slow(job) -> None:
        try:
            part_path(job.file_id).write_bytes(b"partial")
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    manager = _manager(repository, slow)
    job = await manager.create(_spec(), "owner")
    claimed = await repository.claim("test_export")
    task = asyncio.create_task(manager.execute(claimed))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert (await manager.get(job.id, "owner")).status == "pending"
    assert not part_path(claimed.artifact).exists()


async def test_download_refuses_to_join_attempts(repository) -> None:
    manager = _manager(repository)
    job = await manager.create(_spec(), "owner")
    first = await repository.claim("test_export")
    part_path(first.artifact).write_bytes(b"first ZIP")
    stream = manager.stream_growing(job.id, "owner")
    assert await anext(stream) == b""
    assert await anext(stream) == b"first ZIP"
    await _expire(repository, job.id)
    await repository.claim("test_export")
    with pytest.raises(RuntimeError, match="attempt interrupted"):
        await anext(stream)


async def test_actual_subprocess_generates_valid_plan(repository, db_session, monkeypatch, tmp_path) -> None:
    from io import BytesIO
    from zipfile import ZipFile

    from conftest import TEST_DATABASE_URL, TEST_SCHEMA
    from sqlmodel import select

    from src.common.export_process import generate_in_process
    from src.renovation.plan import RenovationPlan

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("POSTGRES_SCHEMA", TEST_SCHEMA)
    plan = (await db_session.exec(select(RenovationPlan).where(RenovationPlan.state == RPlanState.WORKING))).first()
    assert plan is not None
    spec = _spec()
    spec.plan_id = plan.id
    spec.asset_ids = []
    spec.include_unassigned = True
    spec.filters = None
    manager = DurableExportManager(ExportJobSpec, generate_in_process, kind="renovation_plan", repository=repository)
    job = await manager.create(spec, "owner")
    claimed = await repository.claim("renovation_plan")
    await manager.execute(claimed)
    restored = await manager.get(job.id, "owner")
    assert restored.status == "done", restored.error
    payload = b"".join([chunk async for chunk in manager.stream_growing(job.id, "owner")])
    assert ZipFile(BytesIO(payload)).testzip() is None
    assert restored.progress == restored.spec.total
