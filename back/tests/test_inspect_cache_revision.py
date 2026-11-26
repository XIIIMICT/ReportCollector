"""Preflight cache invalidation by the plan's transactional work-code revision."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from conftest import signed_identity_headers
from sqlalchemy import func, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from valkey.exceptions import ValkeyError

from src.renovation import import_apply, router_import
from src.renovation.import_plan import PlanFileScope
from src.renovation.inspect_cache import InspectResultCache
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.schemas import RenovationPlanImportResult

if TYPE_CHECKING:
    from httpx2 import AsyncClient

_UPDATE_CODE = text("UPDATE r_plan_rows SET work_or_repair_code = :code WHERE id = :id")


class _Redis:
    """Minimal in-memory stand-in for the optional Valkey client."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        del ex
        self.store[key] = value

    async def aclose(self) -> None:
        return None


class _BrokenRedis:
    async def get(self, _key: str) -> str:
        msg = "valkey is down"
        raise ValkeyError(msg)

    async def set(self, _key: str, _value: str, **_kwargs: object) -> None:
        msg = "valkey is down"
        raise ValkeyError(msg)


class _Harness:
    def __init__(self, client: AsyncClient, redis: _Redis, calls: list[int]) -> None:
        self.client = client
        self.redis = redis
        self.calls = calls

    async def inspect(self) -> None:
        """Post the same workbook bytes as an admin and require a successful report."""
        response = await self.client.post(
            "/api/renovation/plan/import/inspect",
            files={"file": ("plan.xlsb", b"the same workbook bytes", "application/octet-stream")},
            headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
        )
        assert response.status_code == 200, response.text


@pytest.fixture
async def harness(client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    """Real cache class + real endpoint; only the CPU parsers are replaced."""
    del db_session
    redis = _Redis()
    cache = InspectResultCache(redis)
    calls: list[int] = []

    def fake_scope(*_args: object, **_kwargs: object) -> PlanFileScope:
        return PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset({"work_type"}))

    def fake_inspect(*_args: object, existing_codes: set[str], **_kwargs: object) -> RenovationPlanImportResult:
        calls.append(len(existing_codes))
        return RenovationPlanImportResult(status="ready", new_rows=len(existing_codes))

    monkeypatch.setattr(router_import, "get_inspect_cache", lambda: cache)
    monkeypatch.setattr(router_import, "scan_file_scope", fake_scope)
    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", fake_inspect)
    return _Harness(client, redis, calls)


async def _working_plan(session: AsyncSession) -> RenovationPlan:
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    assert plan is not None
    return plan


async def _revision(session: AsyncSession, plan_id: UUID) -> int:
    """Read the trigger-maintained counter from the DB, never the identity map."""
    value = (await session.exec(select(col(RenovationPlan.revision)).where(col(RenovationPlan.id) == plan_id))).one()
    return int(value)


async def _latest_change(session: AsyncSession, plan_id: UUID) -> datetime | None:
    return (
        await session.exec(
            select(func.max(RenovationPlanRow.updated_at)).where(col(RenovationPlanRow.plan_id) == plan_id)
        )
    ).one()


async def _asset_id(session: AsyncSession) -> UUID:
    asset = (await session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    assert asset is not None
    return asset.id


async def _last_position(session: AsyncSession, plan_id: UUID) -> int:
    value = (
        await session.exec(
            select(func.max(RenovationPlanRow.position)).where(col(RenovationPlanRow.plan_id) == plan_id)
        )
    ).one()
    return int(value or 0)


async def _insert_row(
    session: AsyncSession,
    *,
    plan_id: UUID,
    asset_id: UUID,
    position: int,
    code: str,
    updated_at: datetime | None = None,
) -> RenovationPlanRow:
    now = datetime.now(UTC)
    row = RenovationPlanRow(
        plan_id=plan_id,
        asset_id=asset_id,
        position=position,
        baseline_generation=1,
        revision=0,
        created_at=now,
        updated_at=updated_at or now,
        work_or_repair_code=code,
    )
    session.add(row)
    await session.flush()
    return row


async def test_same_file_same_plan_and_revision_is_a_cache_hit(harness: _Harness) -> None:
    await harness.inspect()
    assert harness.calls == [2]  # the seed's two working-plan codes
    await harness.inspect()
    assert len(harness.calls) == 1


async def test_work_code_edit_invalidates(harness: _Harness, db_session: AsyncSession) -> None:
    plan = await _working_plan(db_session)
    plan_id = plan.id
    before = await _revision(db_session, plan_id)
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan_id))).first()
    assert row is not None
    row_id = row.id

    await db_session.commit()
    await harness.inspect()
    assert len(harness.calls) == 1
    await db_session.execute(  # ty: ignore[deprecated]
        _UPDATE_CODE,
        {"id": row_id, "code": "EDITED_WORK_CODE"},
    )
    await db_session.flush()
    assert await _revision(db_session, plan_id) > before

    await harness.inspect()
    assert len(harness.calls) == 2


async def test_row_insert_invalidates(harness: _Harness, db_session: AsyncSession) -> None:
    plan = await _working_plan(db_session)
    plan_id = plan.id
    before = await _revision(db_session, plan_id)
    await db_session.commit()
    await harness.inspect()
    assert len(harness.calls) == 1

    await _insert_row(
        db_session,
        plan_id=plan_id,
        asset_id=await _asset_id(db_session),
        position=await _last_position(db_session, plan_id) + 1,
        code="NEW_WORK_CODE",
    )
    assert await _revision(db_session, plan_id) > before
    await harness.inspect()
    assert len(harness.calls) == 2


async def test_deleting_a_non_latest_row_invalidates(harness: _Harness, db_session: AsyncSession) -> None:
    """A delete must re-key even when max(updated_at) of the plan is unchanged."""
    plan = await _working_plan(db_session)
    plan_id = plan.id
    asset_id = await _asset_id(db_session)
    position = await _last_position(db_session, plan_id)
    old = await _insert_row(
        db_session,
        plan_id=plan_id,
        asset_id=asset_id,
        position=position + 1,
        code="OLD_DELETED",
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await _insert_row(db_session, plan_id=plan_id, asset_id=asset_id, position=position + 2, code="NEWEST_KEPT")
    await db_session.commit()
    await harness.inspect()
    count_after_inserts = len(harness.calls)

    max_before = await _latest_change(db_session, plan_id)
    revision_before = await _revision(db_session, plan_id)
    await db_session.delete(old)
    await db_session.flush()
    assert await _latest_change(db_session, plan_id) == max_before
    assert await _revision(db_session, plan_id) > revision_before

    await harness.inspect()
    assert len(harness.calls) == count_after_inserts + 1


async def test_direct_apply_invalidates(
    harness: _Harness, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.renovation.import_rows import ParsedPlanRow

    plan = await _working_plan(db_session)
    plan_id = plan.id
    before = await _revision(db_session, plan_id)
    await harness.inspect()
    assert len(harness.calls) == 1

    monkeypatch.setattr(
        import_apply,
        "parse_plan_rows",
        lambda *_args, **_kwargs: [
            ParsedPlanRow(code="APPLIED_CODE", asset_key="BSZR", values={"workshop_name": "загружено"})
        ],
    )
    result = await import_apply.apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (1, 0)
    assert await _revision(db_session, plan_id) > before

    await harness.inspect()
    assert len(harness.calls) == 2


async def test_switching_working_plan_invalidates(harness: _Harness, db_session: AsyncSession) -> None:
    working = await _working_plan(db_session)
    project_id, generation = working.project_id, working.baseline_generation
    await db_session.commit()
    await harness.inspect()
    assert harness.calls == [2]

    working.state = RPlanState.ARCHIVED
    db_session.add(working)
    next_plan = RenovationPlan(
        project_id=project_id,
        state=RPlanState.WORKING,
        baseline_generation=generation + 1,
        created_at=datetime.now(UTC),
        created_by="test",
        updated_at=datetime.now(UTC),
    )
    db_session.add(next_plan)
    await db_session.flush()

    await harness.inspect()
    assert len(harness.calls) == 2
    assert harness.calls[-1] == 0  # the new plan has no codes yet
    assert any(f"plan={next_plan.id}|rev=" in key for key in harness.redis.store)


async def test_archived_plan_changes_do_not_invalidate(harness: _Harness, db_session: AsyncSession) -> None:
    working = await _working_plan(db_session)
    working_id = working.id
    archived = RenovationPlan(
        project_id=working.project_id,
        state=RPlanState.ARCHIVED,
        baseline_generation=working.baseline_generation + 1,
        created_at=datetime.now(UTC),
        created_by="test",
        updated_at=datetime.now(UTC),
    )
    db_session.add(archived)
    await db_session.flush()
    row = await _insert_row(
        db_session,
        plan_id=archived.id,
        asset_id=await _asset_id(db_session),
        position=1,
        code="ARCHIVED_CODE",
    )
    archived_before = await _revision(db_session, archived.id)
    working_before = await _revision(db_session, working_id)
    assert archived_before > 0
    # The endpoint releases its DB connection mid-request; commit the fixture's
    # savepoint so these rows stay visible to later statements in this test.
    await db_session.commit()

    await harness.inspect()
    assert len(harness.calls) == 1

    await db_session.execute(  # ty: ignore[deprecated]
        _UPDATE_CODE,
        {"id": row.id, "code": "ARCHIVED_EDITED"},
    )
    await _insert_row(
        db_session,
        plan_id=archived.id,
        asset_id=await _asset_id(db_session),
        position=99,
        code="ARCHIVED_NEW",
    )
    await db_session.flush()
    assert await _revision(db_session, archived.id) > archived_before
    assert await _revision(db_session, working_id) == working_before

    await harness.inspect()
    assert len(harness.calls) == 1


async def test_broken_valkey_still_analyses_the_file(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_session
    calls: list[int] = []
    monkeypatch.setattr(router_import, "get_inspect_cache", lambda: InspectResultCache(_BrokenRedis()))
    monkeypatch.setattr(
        router_import,
        "scan_file_scope",
        lambda *_args, **_kwargs: PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset()),
    )

    def fake_inspect(*_args: object, existing_codes: set[str], **_kwargs: object) -> RenovationPlanImportResult:
        calls.append(len(existing_codes))
        return RenovationPlanImportResult(status="ready", new_rows=len(existing_codes))

    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", fake_inspect)
    response = await client.post(
        "/api/renovation/plan/import/inspect",
        files={"file": ("plan.xlsb", b"the same workbook bytes", "application/octet-stream")},
        headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
