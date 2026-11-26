"""Imports persist acceptance, and commit data together with the fenced result."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Never
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.auth.models import User, utc_now
from src.common.durable_imports import DurableImportManager, ImportJob, input_path
from src.common.import_process import generate_import_in_process
from src.common.job_models import DataJob
from src.common.job_repository import JobRepository
from src.renovation.import_jobs import generate_plan_import
from src.renovation.import_rows import ParsedPlanRow
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState

if TYPE_CHECKING:
    from src.renovation.schemas import RenovationPlanImportApplyResult

pytestmark = pytest.mark.real_schema
SAMPLE = Path(__file__).resolve().parents[2] / "docs/samples/renovation_td_example.xlsb"


@pytest.fixture
async def setup_import(db_session, my_engine, monkeypatch, tmp_path):
    factory = async_sessionmaker(my_engine, class_=AsyncSession, expire_on_commit=False)
    repository = JobRepository(factory)
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path))
    monkeypatch.setattr("src.renovation.import_jobs.async_session_factory", factory)
    rows = [ParsedPlanRow(code="DURABLE_IMPORT", asset_key="BSZR", values={"workshop_name": "Loaded"})]
    monkeypatch.setattr("src.renovation.import_apply.iter_plan_row_batches", lambda *_args: (batch for batch in [rows]))
    user = (await db_session.exec(select(User).where(col(User.email) == "admin@example.ru"))).one()
    await db_session.commit()
    return repository, str(user.id), tmp_path


def manager(repository, generate=generate_plan_import):
    return DurableImportManager(kind="renovation_import", generate=generate, repository=repository)


async def accept(worker, user, tmp_path, *, digest="digest", expected_rows=0, file_name="plan.xlsb"):
    path = tmp_path / "upload.tmp"
    path.write_bytes(b"accepted workbook")
    async with worker.repository.session_factory() as session:
        plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    return await worker.create_from_path(
        source=path,
        file_name=file_name,
        digest=digest,
        size=17,
        user_id=user,
        plan_id=plan.id,
        expected_rows=expected_rows,
    )


async def imported_count(repository) -> int:
    async with repository.session_factory() as session:
        return len(
            (
                await session.exec(
                    select(RenovationPlanRow.id).where(
                        col(RenovationPlanRow.work_or_repair_code) == "DURABLE_IMPORT",
                    )
                )
            ).all()
        )


async def test_import_result_survives_restart_and_active_duplicate_does_not_reapply(setup_import) -> None:
    repository, owner, directory = setup_import
    first = manager(repository)
    job = await accept(first, owner, directory)
    again = await accept(first, owner, directory)
    assert again.id == job.id
    assert len(list(directory.glob("*.xlsb"))) == 1
    restored = manager(repository)
    await restored.execute(await repository.claim(restored.kind))
    done = await first.get(job.id, owner)
    assert done.status == "done"
    assert done.result == {"newRows": 1, "updatedRows": 0}
    assert done.total == done.progress == 1
    assert not input_path(job.input_key).exists()
    assert await imported_count(repository) == 1
    # A COMPLETED import is history, not a reusable result: uploading the same
    # bytes again must stage a NEW job and apply them, not return the old run
    # (which used to show a success toast without loading anything).
    replay = await accept(first, owner, directory)
    assert replay.id != job.id
    assert len(list(directory.glob("*.xlsb"))) == 1
    await restored.execute(await repository.claim(restored.kind))
    replay_done = await first.get(replay.id, owner)
    assert replay_done.status == "done"
    assert replay_done.result == {"newRows": 0, "updatedRows": 1}
    assert await imported_count(repository) == 1
    assert not list(directory.glob("*.xlsb"))
    with pytest.raises(KeyError):
        await restored.get(job.id, "another-owner")


@pytest.mark.parametrize("invalidation", ["cancel", "expire"])
async def test_cancelled_or_expired_import_rolls_back_all_writes(setup_import, monkeypatch, invalidation) -> None:
    from src.renovation.import_apply import apply_plan_file

    repository, owner, directory = setup_import
    worker = manager(repository)
    job = await accept(worker, owner, directory)
    claimed = await repository.claim(worker.kind)

    async def invalidate_after_write(
        session, *, source, user_id, plan_id, streaming, expected_rows, progress, source_suffix=None
    ) -> RenovationPlanImportApplyResult:
        outcome = await apply_plan_file(
            session,
            source=source,
            user_id=user_id,
            plan_id=plan_id,
            streaming=streaming,
            expected_rows=expected_rows,
            progress=progress,
            source_suffix=source_suffix,
        )
        if invalidation == "cancel":
            await worker.cancel(job.id, owner)
        else:
            async with repository.session_factory() as write_session, write_session.begin():
                record = await write_session.get(DataJob, UUID(job.id))
                record.lease_until = utc_now() - timedelta(seconds=1)
                write_session.add(record)
        return outcome

    monkeypatch.setattr("src.renovation.import_jobs.apply_plan_file", invalidate_after_write)
    await worker.execute(claimed)
    assert await imported_count(repository) == 0
    result = await worker.get(job.id, owner)
    assert result.status != "done"
    assert result.result is None
    if invalidation == "cancel":
        assert result.error == "Загрузка отменена."


async def test_duplicate_codes_fail_the_job_without_writing(setup_import, monkeypatch) -> None:
    """Apply re-checks uniqueness: a duplicate workbook never reaches completion."""
    repository, owner, directory = setup_import
    duplicate_rows = [
        ParsedPlanRow(code="DUP_JOB_CODE", asset_key="BSZR", values={"workshop_name": "первая"}),
        ParsedPlanRow(code="DUP_JOB_CODE", asset_key="BSZR", values={"workshop_name": "вторая"}),
    ]
    monkeypatch.setattr(
        "src.renovation.import_apply.iter_plan_row_batches", lambda *_: (batch for batch in [duplicate_rows])
    )
    worker = manager(repository)
    job = await accept(worker, owner, directory, digest="duplicates")
    await worker.execute(await repository.claim(worker.kind))
    result = await worker.get(job.id, owner)
    assert result.status == "failed"
    assert result.result is None
    assert result.error is not None
    assert "DUP_JOB_CODE" in result.error
    async with repository.session_factory() as session:
        written = (
            await session.exec(
                select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code) == "DUP_JOB_CODE")
            )
        ).all()
    assert written == []


async def test_lost_commit_reply_keeps_atomic_success_and_never_requeues(setup_import) -> None:
    repository, owner, directory = setup_import

    async def lost_reply(job, path) -> Never:
        await generate_plan_import(job, path)
        msg = "simulated lost reply after database commit"
        raise ConnectionError(msg)

    worker = manager(repository, lost_reply)
    job = await accept(worker, owner, directory)
    await worker.execute(await repository.claim(worker.kind))
    assert (await worker.get(job.id, owner)).status == "done"
    assert await repository.claim(worker.kind) is None
    assert await imported_count(repository) == 1
    assert not input_path(job.input_key).exists()


async def test_transient_failure_retains_input_and_shutdown_requeues(setup_import) -> None:
    repository, owner, directory = setup_import
    started = asyncio.Event()

    async def slow(_job, _path) -> None:
        started.set()
        await asyncio.Event().wait()

    worker = manager(repository, slow)
    job = await accept(worker, owner, directory)
    work = asyncio.create_task(worker.execute(await repository.claim(worker.kind)))
    await asyncio.wait_for(started.wait(), 5)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert (await worker.get(job.id, owner)).status == "pending"
    assert input_path(job.input_key).exists()


async def test_in_flight_progress_is_visible_before_the_import_commits(setup_import) -> None:
    """The bar moves while the worker holds its transaction; `total` is the preflight's 2× rows."""
    repository, owner, directory = setup_import
    released = asyncio.Event()
    reached = asyncio.Event()

    async def staged(job, _path) -> dict[str, int]:
        job.progress, job.total, job.phase = 30, 80, "write"
        reached.set()
        await released.wait()
        outcome = {"newRows": 1, "updatedRows": 1}
        async with repository.session_factory() as session, session.begin():
            await repository.complete_in_transaction(
                session,
                job_id=UUID(job.id),
                lease_token=job.lease_token,
                total=2,
                result=outcome,
            )
        return outcome

    worker = manager(repository, staged)
    job = await accept(worker, owner, directory, expected_rows=40)
    work = asyncio.create_task(worker.execute(await repository.claim(worker.kind)))
    await asyncio.wait_for(reached.wait(), 5)

    async def persisted() -> ImportJob:
        while True:
            current = await worker.get(job.id, owner)
            if (current.progress, current.total, current.phase) == (30, 80, "write"):
                return current
            await asyncio.sleep(0.05)

    seen = await asyncio.wait_for(persisted(), 5)
    assert seen.status == "running"
    released.set()
    await work
    assert (await worker.get(job.id, owner)).status == "done"


async def test_lost_admission_reply_preserves_accepted_bytes(setup_import, monkeypatch) -> None:
    repository, owner, directory = setup_import
    create = repository.create
    record = None

    async def lost_reply(candidate, *, reuse_done: bool = True) -> Never:
        nonlocal record
        record = await create(candidate, reuse_done=reuse_done)
        msg = "database committed before connection was lost"
        raise ConnectionError(msg)

    monkeypatch.setattr(repository, "create", lost_reply)
    worker = manager(repository)
    with pytest.raises(ConnectionError):
        await accept(worker, owner, directory)
    assert record is not None
    assert input_path(record.payload["input_key"]).exists()
    await worker.execute(await repository.claim(worker.kind))
    assert (await worker.get(str(record.id), owner)).status == "done"


async def test_empty_expiry_list_does_not_block_worker(setup_import) -> None:
    repository, owner, directory = setup_import
    worker = manager(repository)
    job = await accept(worker, owner, directory)
    await worker.start()
    try:
        async with asyncio.timeout(15):
            while (await worker.get(job.id, owner)).status in {"pending", "running"}:  # noqa: ASYNC110
                await asyncio.sleep(0.1)
        assert (await worker.get(job.id, owner)).status == "done"
    finally:
        await worker.stop()


async def test_xlsx_upload_keeps_its_suffix_for_the_worker(setup_import, client, monkeypatch) -> None:
    """The internal key keeps the upload's container so the worker reads the right format."""
    from conftest import signed_identity_headers

    repository, owner, directory = setup_import
    worker = manager(repository)
    monkeypatch.setattr("src.renovation.router_import.import_jobs", worker)
    headers = signed_identity_headers(email="admin@example.ru", name="Admin User")
    response = await client.post(
        "/api/renovation/plan/import/apply",
        files={"file": ("plan.xlsx", b"bytes")},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["file_name"] == "plan.xlsx"
    assert len(list(directory.glob("*.xlsx"))) == 1
    assert not list(directory.glob("*.upload"))
    queued = await repository.get(accepted["id"], owner, worker.kind)
    assert input_path(queued.payload["input_key"]).suffix == ".xlsx"
    await worker.execute(await repository.claim(worker.kind))
    assert (await worker.get(accepted["id"], owner)).status == "done"
    assert await imported_count(repository) == 1


async def test_active_import_is_visible_to_every_member(setup_import, client, db_session, monkeypatch) -> None:
    from conftest import signed_identity_headers

    repository, _owner, _directory = setup_import
    worker = manager(repository)
    monkeypatch.setattr("src.renovation.router_import.import_jobs", worker)
    monkeypatch.setattr("src.renovation.router_import.async_session_factory", repository.session_factory)
    headers = signed_identity_headers(email="admin@example.ru", name="Admin User")
    assert (await client.get("/api/renovation/plan/import/active", headers=headers)).json() is None

    accepted = await client.post(
        "/api/renovation/plan/import/apply",
        files={"file": ("plan.xlsb", b"bytes")},
        headers=headers,
    )
    assert accepted.status_code == 202
    active = await client.get("/api/renovation/plan/import/active", headers=headers)
    assert active.status_code == 200, active.text
    body = active.json()
    assert body["id"] == accepted.json()["id"]
    assert body["status"] == "pending"
    assert body["user_name"] == "Admin User"

    # Gate A is enforced server-side too: a stale client cannot edit behind it.
    row = (await db_session.exec(select(RenovationPlanRow).limit(1))).first()
    denied = await client.patch(
        "/api/renovation/plan",
        headers=headers,
        params={"session_id": "gate-a"},
        json={"updates": [{"rowId": str(row.id), "field": "workshop_name", "value": "x"}]},
    )
    assert denied.status_code == 409, denied.text

    cancelled = await client.delete(f"/api/renovation/plan/import/jobs/{body['id']}", headers=headers)
    assert cancelled.status_code == 200
    assert (await client.get("/api/renovation/plan/import/active", headers=headers)).json() is None


async def test_http_acceptance_queues_without_parsing_and_enforces_owner(setup_import, client, monkeypatch) -> None:
    from conftest import signed_identity_headers

    repository, owner, directory = setup_import
    worker = manager(repository)
    monkeypatch.setattr("src.renovation.router_import.import_jobs", worker)
    headers = signed_identity_headers(email="admin@example.ru", name="Admin User")
    response = await client.post(
        "/api/renovation/plan/import/apply",
        files={"file": ("plan.xlsb", b"bytes")},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["status"] == "pending"
    assert await imported_count(repository) == 0
    assert len(list(directory.glob("*.xlsb"))) == 1
    assert not list(directory.glob("*.upload"))
    status = await client.get(f"/api/renovation/plan/import/jobs/{accepted['id']}", headers=headers)
    assert status.json() == accepted
    await accept(worker, owner, directory, digest="second")
    rejected = await client.post(
        "/api/renovation/plan/import/apply",
        files={"file": ("plan.xlsb", b"other")},
        headers=headers,
    )
    assert rejected.status_code == 429, rejected.text
    assert len(list(directory.glob("*.xlsb"))) == 2
    assert not list(directory.glob("*.upload"))


async def test_actual_subprocess_refuses_bad_file_without_writing(setup_import, monkeypatch) -> None:
    from conftest import TEST_DATABASE_URL, TEST_SCHEMA

    repository, owner, directory = setup_import
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("POSTGRES_SCHEMA", TEST_SCHEMA)
    worker = manager(repository, generate_import_in_process)
    path = directory / "sample.tmp"
    path.write_bytes(SAMPLE.read_bytes())
    async with repository.session_factory() as session:
        plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).one()
    job = await worker.create_from_path(
        source=path, file_name=SAMPLE.name, digest="sample", size=path.stat().st_size, user_id=owner, plan_id=plan.id
    )
    await worker.execute(await repository.claim(worker.kind))
    result = await worker.get(job.id, owner)
    assert result.status == "failed", result
    assert result.error is not None
    assert "справочнике" in result.error
    assert await imported_count(repository) == 0
    assert not input_path(job.input_key).exists()
