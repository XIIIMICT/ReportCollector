"""Point-in-time preflight token contract: optimistic restarts on plan mutations.

All cases use the real migrated `rc_test*` schema and separate committed
transactions: a controlled page hook commits a mutation through a second
connection while the preflight is between keyset pages. No sleeps and no fake
triggers — the revision bumps come from migration 0043 itself. The fake here is
only the workbook parser, which records what the live oracle would have said.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, BinaryIO, cast
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation import import_plan, router_import
from src.renovation.import_plan import CodeMembership, ExistingCodeOracle, PlanFileScope
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.schemas import RenovationPlanImportResult

if TYPE_CHECKING:
    from httpx2 import AsyncClient

_FIRST_ROW_ID = UUID(int=10)
_SECOND_ROW_ID = UUID(int=11)
_HIDDEN_ROW_ID = UUID(int=1)  # sorts BEFORE the first keyset page
_FIRST_CODE = "SNAP_FIRST"
_SECOND_CODE = "SNAP_SECOND"
_HIDDEN_CODE = "SNAP_HIDDEN"
_UPLOAD = {"file": ("plan.xlsb", b"opaque bytes", "application/octet-stream")}


def _headers() -> dict[str, str]:
    from conftest import signed_identity_headers

    return signed_identity_headers(email="admin@example.ru", name="Admin User")


class _Cache:
    """In-memory stand-in that records every scope it is asked about and written."""

    def __init__(self, *, always_hit: RenovationPlanImportResult | None = None) -> None:
        self.always_hit = always_hit
        self.lookups: list[str] = []
        self.stored: dict[str, RenovationPlanImportResult] = {}

    async def get_scope_digest(self, *_args: object, **_kwargs: object) -> PlanFileScope:
        return PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset())

    async def set_scope_digest(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def get_digest(
        self, _digest: str, *, scope: str = "", suffix: str | None = None
    ) -> RenovationPlanImportResult | None:
        del suffix
        self.lookups.append(scope)
        if self.always_hit is not None:
            return self.always_hit
        return self.stored.get(scope)

    async def set_digest(
        self,
        _digest: str,
        result: RenovationPlanImportResult,
        *,
        scope: str = "",
        suffix: str | None = None,
    ) -> None:
        del suffix
        self.stored[scope] = result


def _track_oracles(monkeypatch: pytest.MonkeyPatch) -> tuple[list[ExistingCodeOracle], list[list[BinaryIO]]]:
    """Record every oracle and the OS-backed handles present when it was closed."""
    created: list[ExistingCodeOracle] = []
    closed_handles: list[list[BinaryIO]] = []
    original_class = router_import.ExistingCodeOracle
    original_close = original_class.close

    def factory() -> ExistingCodeOracle:
        oracle = original_class()
        created.append(oracle)
        return oracle

    def close(self: ExistingCodeOracle) -> None:
        closed_handles.append(list(self._files))
        original_close(self)

    monkeypatch.setattr(original_class, "close", close)
    monkeypatch.setattr(router_import, "ExistingCodeOracle", factory)
    return created, closed_handles


def _inspector(probes: list[str], seen: list[dict[str, bool]]) -> Callable[..., RenovationPlanImportResult]:
    def inspect(*_args: object, existing_codes: object, **_kwargs: object) -> RenovationPlanImportResult:
        oracle = cast("CodeMembership", existing_codes)
        seen.append({code: oracle.contains(code) for code in probes})
        return RenovationPlanImportResult(status="ready", new_rows=1, rows_to_update=1)

    return inspect


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    cache: _Cache,
    inspector: Callable[..., RenovationPlanImportResult],
) -> None:
    monkeypatch.setattr(router_import, "get_inspect_cache", lambda: cache)
    monkeypatch.setattr(
        router_import,
        "scan_file_scope",
        lambda *_args, **_kwargs: PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset()),
    )
    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", inspector)
    monkeypatch.setattr(router_import, "_EXISTING_CODE_PAGE_SIZE", 2)
    # One digest per run: even these tiny fixtures build a real sorted file and
    # mmap, so cleanup assertions cover the OS-backed resources too.
    monkeypatch.setattr(import_plan, "_ORACLE_RUN_CODES", 1)


def _page_hook(monkeypatch: pytest.MonkeyPatch, hook: Callable[[int], Awaitable[None]]) -> None:
    original = router_import._fill_existing_code_oracle

    async def fill(
        session: AsyncSession,
        oracle: ExistingCodeOracle,
        plan_id: UUID,
        *,
        on_page: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        async def combined(page: int) -> None:
            if on_page is not None:
                await on_page(page)
            await hook(page)

        await original(session, oracle, plan_id, on_page=combined)

    monkeypatch.setattr(router_import, "_fill_existing_code_oracle", fill)


def _writer(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _working_plan(session: AsyncSession) -> RenovationPlan:
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    assert plan is not None
    return plan


async def _asset_id(session: AsyncSession) -> UUID:
    from src.renovation.models import RAsset

    asset = (await session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    assert asset is not None
    return asset.id


async def _revision(engine: AsyncEngine, plan_id: UUID) -> int:
    async with _writer(engine)() as session:
        value = (
            await session.exec(select(col(RenovationPlan.revision)).where(col(RenovationPlan.id) == plan_id))
        ).one()
        return int(value)


async def _seed_rows(engine: AsyncEngine, *, plan_id: UUID, asset_id: UUID) -> None:
    """Two low-id rows guarantee a deterministic first keyset page."""
    now = datetime.now(UTC)
    async with _writer(engine)() as session, session.begin():
        for position, row_id, code in ((900, _FIRST_ROW_ID, _FIRST_CODE), (901, _SECOND_ROW_ID, _SECOND_CODE)):
            session.add(
                RenovationPlanRow(
                    id=row_id,
                    plan_id=plan_id,
                    asset_id=asset_id,
                    position=position,
                    work_or_repair_code=code,
                    created_at=now,
                    updated_at=now,
                )
            )


async def _insert_row(
    engine: AsyncEngine,
    *,
    plan_id: UUID,
    asset_id: UUID,
    row_id: UUID,
    code: str,
    position: int = 902,
) -> None:
    now = datetime.now(UTC)
    async with _writer(engine)() as session, session.begin():
        session.add(
            RenovationPlanRow(
                id=row_id,
                plan_id=plan_id,
                asset_id=asset_id,
                position=position,
                work_or_repair_code=code,
                created_at=now,
                updated_at=now,
            )
        )


async def _set_code(engine: AsyncEngine, row_id: UUID, code: str) -> None:
    async with _writer(engine)() as session, session.begin():
        await session.execute(  # ty: ignore[deprecated]
            text("UPDATE r_plan_rows SET work_or_repair_code = :code WHERE id = :id"),
            {"id": row_id, "code": code},
        )


async def _move_row(engine: AsyncEngine, row_id: UUID, target: UUID) -> None:
    async with _writer(engine)() as session, session.begin():
        await session.execute(  # ty: ignore[deprecated]
            text("UPDATE r_plan_rows SET plan_id = :target WHERE id = :id"),
            {"id": row_id, "target": target},
        )


async def _delete_row(engine: AsyncEngine, row_id: UUID) -> None:
    async with _writer(engine)() as session, session.begin():
        await session.execute(text("DELETE FROM r_plan_rows WHERE id = :id"), {"id": row_id})  # ty: ignore[deprecated]


async def _make_archived(engine: AsyncEngine, *, project_id: UUID, generation: int, plan_id: UUID) -> None:
    now = datetime.now(UTC)
    async with _writer(engine)() as session, session.begin():
        session.add(
            RenovationPlan(
                id=plan_id,
                project_id=project_id,
                state=RPlanState.ARCHIVED,
                baseline_generation=generation,
                created_at=now,
                updated_at=now,
            )
        )


def _assert_closed(created: list[ExistingCodeOracle], closed_handles: list[list[BinaryIO]]) -> None:
    assert created
    assert all(oracle._closed and oracle._map is None for oracle in created)
    assert closed_handles
    assert all(handle.closed for handles in closed_handles for handle in handles)


@pytest.mark.real_schema
async def test_insert_behind_cursor_is_retried_under_a_fresh_revision(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row inserted behind the keyset cursor must not be silently omitted."""
    plan = await _working_plan(db_session)
    asset_id = await _asset_id(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=asset_id)
    before = await _revision(my_engine, plan.id)

    seen: list[dict[str, bool]] = []
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([_HIDDEN_CODE], seen))
    committed = False

    async def insert_behind_cursor(page: int) -> None:
        nonlocal committed
        if page == 1 and not committed:
            committed = True
            await _insert_row(
                my_engine,
                plan_id=plan.id,
                asset_id=asset_id,
                row_id=_HIDDEN_ROW_ID,
                code=_HIDDEN_CODE,
                position=902,
            )

    _page_hook(monkeypatch, insert_behind_cursor)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert committed
    assert await _revision(my_engine, plan.id) == before + 1
    # The mixed oracle is discarded BEFORE parsing; only the retried one runs.
    assert len(created) == 2
    assert created[0].added < created[1].added
    assert len(cache.lookups) == 2
    assert cache.lookups[0] != cache.lookups[1]
    assert len(seen) == 1
    assert seen[0][_HIDDEN_CODE] is True
    assert len(cache.stored) == 1
    stored_scope = next(iter(cache.stored))
    assert f"plan={plan.id}|rev={before + 1}|" in stored_scope
    assert f"plan={plan.id}|rev={before}|" not in stored_scope
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_code_change_and_delete_between_pages_discard_the_mixed_oracle(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first mixed oracle is never accepted under the original revision."""
    plan = await _working_plan(db_session)
    asset_id = await _asset_id(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=asset_id)
    before = await _revision(my_engine, plan.id)

    changed = "SNAP_FIRST_CHANGED"
    seen: list[dict[str, bool]] = []
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([_FIRST_CODE, changed, _SECOND_CODE], seen))
    committed = False

    async def mutate_after_first_page(page: int) -> None:
        nonlocal committed
        if page == 1 and not committed:
            committed = True
            await _set_code(my_engine, _FIRST_ROW_ID, changed)
            await _delete_row(my_engine, _SECOND_ROW_ID)

    _page_hook(monkeypatch, mutate_after_first_page)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert len(created) == 2
    assert created[0].added > created[1].added
    assert len(seen) == 1  # the mixed oracle never reached the parser
    assert seen[0][_FIRST_CODE] is False
    assert seen[0][changed] is True
    assert seen[0][_SECOND_CODE] is False
    assert await _revision(my_engine, plan.id) == before + 2  # one UPDATE + one DELETE statement
    assert len(cache.stored) == 1
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_transfer_between_plans_with_unchanged_code_is_detected(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0043 bumps BOTH plans, so the preflight restarts on A -> B."""
    plan = await _working_plan(db_session)
    asset_id = await _asset_id(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=asset_id)
    target_id = UUID("00000000-0000-0000-0000-0000000000bb")
    await _make_archived(
        my_engine, project_id=plan.project_id, generation=plan.baseline_generation + 1, plan_id=target_id
    )
    before_source = await _revision(my_engine, plan.id)
    before_target = await _revision(my_engine, target_id)

    seen: list[dict[str, bool]] = []
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([_FIRST_CODE], seen))
    committed = False

    async def transfer_after_first_page(page: int) -> None:
        nonlocal committed
        if page == 1 and not committed:
            committed = True
            await _move_row(my_engine, _FIRST_ROW_ID, target_id)

    _page_hook(monkeypatch, transfer_after_first_page)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert len(created) == 2
    assert created[0].added > created[1].added
    assert len(seen) == 1
    assert seen[0][_FIRST_CODE] is False
    assert await _revision(my_engine, plan.id) == before_source + 1
    assert await _revision(my_engine, target_id) == before_target + 1
    assert len(cache.stored) == 1
    assert f"plan={plan.id}|rev={before_source + 1}|" in next(iter(cache.stored))
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_working_plan_identity_change_is_not_presented_as_the_old_plan(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new working version must get its own report, never the old plan's."""
    plan = await _working_plan(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=await _asset_id(db_session))
    new_id = UUID("00000000-0000-0000-0000-0000000000cc")
    seen: list[dict[str, bool]] = []
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([_FIRST_CODE], seen))
    committed = False

    async def replace_working_plan(page: int) -> None:
        nonlocal committed
        if page == 1 and not committed:
            committed = True
            now = datetime.now(UTC)
            async with _writer(my_engine)() as session, session.begin():
                await session.execute(  # ty: ignore[deprecated]
                    text("UPDATE r_plans SET state = 'archived' WHERE id = :id"), {"id": plan.id}
                )
                session.add(
                    RenovationPlan(
                        id=new_id,
                        project_id=plan.project_id,
                        state=RPlanState.WORKING,
                        baseline_generation=plan.baseline_generation + 1,
                        created_at=now,
                        updated_at=now,
                    )
                )

    _page_hook(monkeypatch, replace_working_plan)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert len(created) == 2
    assert created[0].added > 0
    assert created[1].added == 0  # the new working version has no codes
    assert len(seen) == 1
    assert seen[0][_FIRST_CODE] is False
    assert len(cache.stored) == 1
    stored_scope = next(iter(cache.stored))
    assert stored_scope.startswith(f"plan={new_id}|rev=0|")
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_unchanged_plan_keeps_the_cache_hit_cheap(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hit checks the token twice and never builds an oracle or parses."""
    plan = await _working_plan(db_session)
    cached = RenovationPlanImportResult(status="ready", new_rows=7, rows_to_update=3)
    cache = _Cache(always_hit=cached)
    created, _handles = _track_oracles(monkeypatch)

    def forbidden(*_args: object, **_kwargs: object) -> RenovationPlanImportResult:
        msg = "the parser must not run on a cache hit"
        raise AssertionError(msg)

    _stub(monkeypatch, cache, forbidden)
    original_token = router_import._read_plan_token
    reads: list[object] = []

    async def counted(session: AsyncSession) -> object:
        reads.append(session)
        return await original_token(session)

    monkeypatch.setattr(router_import, "_read_plan_token", counted)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert response.json()["new_rows"] == 7
    assert len(reads) == 2
    assert len(cache.lookups) == 1
    assert cache.lookups[0].startswith(f"plan={plan.id}|")
    assert created == []
    assert cache.stored == {}


@pytest.mark.real_schema
async def test_unchanged_plan_uses_one_attempt_and_stores_one_exact_scope(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No mutation means no retry: one oracle, one parse, one exact cache key."""
    plan = await _working_plan(db_session)
    before = await _revision(my_engine, plan.id)
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    seen: list[dict[str, bool]] = []
    _stub(monkeypatch, cache, _inspector([_FIRST_CODE], seen))

    def scope_scan(*_args: object, **_kwargs: object) -> PlanFileScope:
        assert not db_session.in_transaction()  # DB released before CPU discovery
        return PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset())

    monkeypatch.setattr(router_import, "scan_file_scope", scope_scan)

    def parse_offline(*_args: object, existing_codes: object, **_kwargs: object) -> RenovationPlanImportResult:
        assert not db_session.in_transaction()  # DB released before the workbook parse
        oracle = cast("CodeMembership", existing_codes)
        seen.append({_FIRST_CODE: oracle.contains(_FIRST_CODE)})
        return RenovationPlanImportResult(status="ready")

    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", parse_offline)
    original_token = router_import._read_plan_token
    reads = 0

    async def counted(session: AsyncSession) -> object:
        nonlocal reads
        reads += 1
        return await original_token(session)

    monkeypatch.setattr(router_import, "_read_plan_token", counted)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert reads == 3  # before fill, after the final page, before cache write
    assert len(seen) == 1
    assert len(created) == 1
    assert len(cache.stored) == 1
    scope = next(iter(cache.stored))
    assert scope.startswith(f"plan={plan.id}|rev={before}|")
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_retry_exhaustion_is_bounded_explicit_and_leaves_no_cache_entry(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan that keeps changing fails with 409 after a bounded number of tries."""
    plan = await _working_plan(db_session)
    asset_id = await _asset_id(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=asset_id)
    before = await _revision(my_engine, plan.id)

    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    parses = 0

    def forbidden(*_args: object, **_kwargs: object) -> RenovationPlanImportResult:
        nonlocal parses
        parses += 1
        return RenovationPlanImportResult(status="ready")

    _stub(monkeypatch, cache, forbidden)
    original_token = router_import._read_plan_token
    reads = 0

    async def changing_token(session: AsyncSession) -> object:
        nonlocal reads
        reads += 1
        await _set_code(my_engine, _FIRST_ROW_ID, f"EXHAUST_{reads}")
        return await original_token(session)

    monkeypatch.setattr(router_import, "_read_plan_token", changing_token)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == router_import._PREFLIGHT_CONFLICT_DETAIL
    assert reads == 2 * router_import._PREFLIGHT_MAX_ATTEMPTS  # every attempt conflicts after its page read
    assert len(created) == router_import._PREFLIGHT_MAX_ATTEMPTS
    assert parses == 0
    assert cache.stored == {}
    assert await _revision(my_engine, plan.id) == before + reads
    _assert_closed(created, handles)


@pytest.mark.real_schema
async def test_only_the_stable_report_is_cached_under_the_exact_revision(
    client: AsyncClient, db_session: AsyncSession, my_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discarded mixed report is never stored; a later revision cannot hit it."""
    plan = await _working_plan(db_session)
    asset_id = await _asset_id(db_session)
    await _seed_rows(my_engine, plan_id=plan.id, asset_id=asset_id)
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([_HIDDEN_CODE], []))
    committed = False

    async def mutate_after_first_page(page: int) -> None:
        nonlocal committed
        if page == 1 and not committed:
            committed = True
            await _insert_row(
                my_engine,
                plan_id=plan.id,
                asset_id=asset_id,
                row_id=_HIDDEN_ROW_ID,
                code=_HIDDEN_CODE,
                position=902,
            )

    _page_hook(monkeypatch, mutate_after_first_page)
    response = await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())

    assert response.status_code == 200, response.text
    assert len(cache.lookups) == 2  # one discarded scope, one stable scope
    assert len(cache.stored) == 1
    scope = next(iter(cache.stored))
    revision = int(scope.split("|rev=")[1].split("|")[0])
    assert cache.lookups[0] != scope
    assert cache.lookups[1] == scope
    later_scope = scope.replace(f"|rev={revision}|", f"|rev={revision + 1}|", 1)
    assert later_scope not in cache.stored
    assert await cache.get_digest("digest", scope=later_scope) is None
    assert await cache.get_digest("digest", scope=scope) is cache.stored[scope]
    _assert_closed(created, handles)


@pytest.mark.real_schema
@pytest.mark.parametrize("failure", [RuntimeError("db failed"), asyncio.CancelledError()])
async def test_oracle_is_closed_on_db_error_and_cancellation_during_fill(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    """An exception between pages must not leak the disk-backed oracle."""
    del db_session
    cache = _Cache()
    created, handles = _track_oracles(monkeypatch)
    _stub(monkeypatch, cache, _inspector([], []))

    async def failing_fill(
        session: AsyncSession,
        oracle: ExistingCodeOracle,
        plan_id: UUID,
        *,
        on_page: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        del session, plan_id, on_page
        oracle.add(_FIRST_CODE)
        oracle.seal()
        raise failure

    monkeypatch.setattr(router_import, "_fill_existing_code_oracle", failing_fill)
    with contextlib.suppress(RuntimeError, asyncio.CancelledError):
        await client.post("/api/renovation/plan/import/inspect", files=_UPLOAD, headers=_headers())
    _assert_closed(created, handles)
    assert cache.stored == {}
