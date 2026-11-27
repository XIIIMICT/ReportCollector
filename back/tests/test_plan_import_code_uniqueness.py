"""Work-code uniqueness across plan versions: preflight scope and apply guards."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.durable_imports import ImportValidationError
from src.renovation.import_plan import (
    _ASSET_HEADER,
    _CODE_HEADER,
    _REQUIRED_COLUMN_LABELS,
    CodeMembership,
    inspect_plan_file,
)
from src.renovation.import_rows import ParsedPlanRow
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.router_import import _working_plan_import_scope
from src.scripts.audit_plan_work_code_duplicates import _AUDIT_SQL

if TYPE_CHECKING:
    from httpx2 import AsyncClient

_CODE = "ARCHIVED_ONLY_CODE"


async def _working_plan(session: AsyncSession) -> RenovationPlan:
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    assert plan is not None
    return plan


async def _asset(session: AsyncSession) -> RAsset:
    asset = (await session.exec(select(RAsset).where(col(RAsset.code) == "BSZR"))).first()
    if asset is None:
        pytest.skip("the seed carries no BSZR asset")
    return asset


async def _last_position(session: AsyncSession, plan_id: UUID) -> int:
    return (
        await session.exec(
            select(col(RenovationPlanRow.position))
            .where(col(RenovationPlanRow.plan_id) == plan_id)
            .order_by(col(RenovationPlanRow.position).desc())
            .limit(1)
        )
    ).first() or 0


async def _make_archived_plan_with_code(session: AsyncSession, code: str) -> RenovationPlan:
    working = await _working_plan(session)
    asset = await _asset(session)
    archived = RenovationPlan(
        project_id=working.project_id,
        state=RPlanState.ARCHIVED,
        baseline_generation=working.baseline_generation + 1,
        created_at=datetime.now(UTC),
        created_by="test",
        updated_at=datetime.now(UTC),
    )
    session.add(archived)
    await session.flush()
    session.add(
        RenovationPlanRow(
            plan_id=archived.id,
            asset_id=asset.id,
            position=1,
            work_or_repair_code=code,
            workshop_name="архивная строка",
        )
    )
    await session.flush()
    return archived


# --- Preflight: current working plan decides "new" vs "update" -----------------


async def test_preflight_compares_codes_only_in_the_current_working_plan(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archived-only code must not be reported as an update of the new plan."""
    from conftest import signed_identity_headers

    from src.renovation import router_import
    from src.renovation.schemas import RenovationPlanImportResult

    working = await _working_plan(db_session)
    await _make_archived_plan_with_code(db_session, _CODE)
    other = (
        await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == working.id))
    ).first()
    assert other is not None
    assert other.work_or_repair_code

    captured: dict[str, bool] = {}

    def fake_inspect(*_args: object, existing_codes: object, **_kwargs: object) -> RenovationPlanImportResult:
        # The endpoint closes the disk-backed oracle as soon as the parse ends,
        # so evaluate membership while it is still alive.
        oracle = cast("CodeMembership", existing_codes)
        captured["archived"] = oracle.contains(_CODE)
        captured["working"] = oracle.contains(str(other.work_or_repair_code))
        return RenovationPlanImportResult(status="ready", new_rows=1, rows_to_update=0)

    monkeypatch.setattr(router_import, "inspect_plan_file_streaming", fake_inspect)
    response = await client.post(
        "/api/renovation/plan/import/inspect",
        files={"file": ("plan.xlsb", b"opaque bytes", "application/octet-stream")},
        headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
    )
    assert response.status_code == 200, response.text
    assert captured == {"archived": False, "working": True}


def test_cache_scope_carries_the_working_plan_identity_and_revision() -> None:
    """A new plan version or a new work-code generation must not reuse a report."""
    asset = uuid4()
    first = _working_plan_import_scope(asset, 3, [asset])
    second = _working_plan_import_scope(uuid4(), 3, [asset])
    newer = _working_plan_import_scope(asset, 4, [asset])
    same = _working_plan_import_scope(asset, 3, [asset])
    assert first.startswith(f"plan={asset}|rev=3|")
    assert first == same
    assert first != second
    assert first != newer
    assert _working_plan_import_scope(None, 0, [asset]).startswith("plan=none|rev=0|")


async def test_archived_only_code_is_new_for_the_working_plan(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache key of the report includes the plan identity on the wire."""
    from conftest import signed_identity_headers

    from src.renovation import router_import
    from src.renovation.import_plan import PlanFileScope
    from src.renovation.schemas import RenovationPlanImportResult

    plan = await _working_plan(db_session)

    class _Cache:
        def __init__(self) -> None:
            self.scope = ""

        async def get_scope_digest(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def set_scope_digest(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def get_digest(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def set_digest(self, *_args: object, **kwargs: object) -> None:
            self.scope = str(kwargs["scope"])

    cache = _Cache()
    monkeypatch.setattr(router_import, "get_inspect_cache", lambda: cache)
    monkeypatch.setattr(
        router_import,
        "scan_file_scope",
        lambda *_args, **_kwargs: PlanFileScope(assets=frozenset({"bszr"}), catalogs=frozenset()),
    )
    monkeypatch.setattr(
        router_import,
        "inspect_plan_file_streaming",
        lambda *_args, **_kwargs: RenovationPlanImportResult(status="ready", new_rows=1),
    )
    response = await client.post(
        "/api/renovation/plan/import/inspect",
        files={"file": ("plan.xlsb", b"opaque bytes", "application/octet-stream")},
        headers=signed_identity_headers(email="admin@example.ru", name="Admin User"),
    )
    assert response.status_code == 200, response.text
    assert cache.scope.startswith(f"plan={plan.id}|")


def test_preflight_reports_a_code_repeated_far_apart_in_the_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate detection is workbook-wide, not limited to neighbouring rows."""
    from src.renovation import import_plan

    header = [_ASSET_HEADER, _CODE_HEADER, *(label for _, label in _REQUIRED_COLUMN_LABELS)]
    values = ["BSZR", "DUP", "1", "ТОиР", "КР", "OPEX", "Собственные силы", "2027-01-01", "2027-01-31"]

    def make_row(code: str) -> list[str]:
        return [values[0], code, *values[2:]]

    rows = [header, [str(index) for index in range(len(header))], make_row("DUP")]
    rows.extend(make_row(f"UNIQUE_{index}") for index in range(150))
    rows.append(make_row("DUP"))
    monkeypatch.setattr(import_plan, "_read_rows", lambda _source, _suffix=None: rows)

    result = inspect_plan_file(
        Path("ignored.xlsb"),
        existing_codes=set(),
        asset_names_by_code={"BSZR": "АО «БСЗ»"},
    )
    assert result.status == "has_errors"
    assert any(item.startswith("DUP (строки 3, 154)") for item in result.duplicate_codes)


# --- Apply: re-checked independently of the inspect request --------------------


async def test_apply_updates_the_working_plan_code_when_exactly_one_row_matches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _asset(db_session)
    plan = await _working_plan(db_session)
    row = (await db_session.exec(select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan.id))).first()
    assert row is not None
    assert row.work_or_repair_code
    code = str(row.work_or_repair_code)

    from src.renovation import import_apply

    monkeypatch.setattr(
        import_apply,
        "parse_plan_rows",
        lambda *_args, **_kwargs: [
            ParsedPlanRow(code=code, asset_key="BSZR", values={"workshop_name": "Новое значение"})
        ],
    )
    result = await import_apply.apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (0, 1)
    # The writer is a set-based UPDATE: read the column directly instead of the
    # stale ORM instance in the identity map.
    name = (
        await db_session.exec(select(col(RenovationPlanRow.workshop_name)).where(col(RenovationPlanRow.id) == row.id))
    ).one()
    assert name == "Новое значение"


async def test_apply_inserts_an_archived_only_code_as_a_new_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _asset(db_session)
    await _make_archived_plan_with_code(db_session, _CODE)
    from src.renovation import import_apply

    monkeypatch.setattr(
        import_apply,
        "parse_plan_rows",
        lambda *_args, **_kwargs: [ParsedPlanRow(code=_CODE, asset_key="BSZR", values={"workshop_name": "Новое"})],
    )
    result = await import_apply.apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (result.new_rows, result.updated_rows) == (1, 0)
    assert (
        await db_session.exec(
            select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code) == _CODE)
        )
    ).all()


async def test_duplicate_new_code_is_rejected_across_batch_boundaries(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code repeated in batch 1 and batch 2 is still a duplicate in apply."""
    await _asset(db_session)
    from src.renovation import import_apply

    rows = [
        ParsedPlanRow(code=f"UNIQ_{index}", asset_key="BSZR", values={"workshop_name": str(index)})
        for index in range(200)
    ]
    rows.append(ParsedPlanRow(code="UNIQ_0", asset_key="BSZR", values={"workshop_name": "дубль"}))
    batches = [rows[:200], rows[200:]]
    # A generator (not list_iterator) so `_stream_parsed_batches` can close it.
    monkeypatch.setattr(import_apply, "iter_plan_row_batches", lambda *_args: (batch for batch in batches))

    with pytest.raises(ImportValidationError, match="UNIQ_0") as error:
        async with db_session.begin_nested():
            await import_apply.apply_plan_file(
                db_session,
                source=None,  # ty: ignore[invalid-argument-type]
                user_id=None,
                streaming=True,
                source_suffix=".xlsb",
            )
    assert "1" in str(error.value)
    assert "201" in str(error.value)
    assert (
        await db_session.exec(
            select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code).like("UNIQ_%"))
        )
    ).all() == []


async def test_duplicate_new_code_in_one_batch_is_rejected(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _asset(db_session)
    from src.renovation import import_apply

    rows = [
        ParsedPlanRow(code="SAME_BATCH", asset_key="BSZR", values={"workshop_name": "первая"}),
        ParsedPlanRow(code="SAME_BATCH", asset_key="BSZR", values={"workshop_name": "вторая"}),
    ]
    monkeypatch.setattr(import_apply, "parse_plan_rows", lambda *_args, **_kwargs: rows)
    with pytest.raises(ImportValidationError, match="SAME_BATCH"):
        async with db_session.begin_nested():
            await import_apply.apply_plan_file(db_session, source=None, user_id=None)  # ty: ignore[invalid-argument-type]
    assert (
        await db_session.exec(
            select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code) == "SAME_BATCH")
        )
    ).all() == []


# --- Read-only audit -----------------------------------------------------------


async def test_audit_reports_within_plan_duplicates_but_not_cross_version_repeats(
    db_session: AsyncSession,
) -> None:
    """The audit names the plan, code, row ids, positions and duplicate count."""
    await _asset(db_session)
    plan = await _working_plan(db_session)
    # The same code in two plan versions is expected: the audit groups by plan.
    await _make_archived_plan_with_code(db_session, _CODE)
    assert (await db_session.execute(text(_AUDIT_SQL), {"plan_id": plan.id})).all() == []  # ty: ignore[deprecated]

    # Dropped inside this test transaction only; the fixture rollback restores it.
    await db_session.execute(text("DROP INDEX uq_r_plan_rows_plan_work_or_repair_code"))  # ty: ignore[deprecated]
    last = await _last_position(db_session, plan.id)
    row_ids: list[str] = []
    for offset in (1, 2):
        row = RenovationPlanRow(
            plan_id=plan.id,
            asset_id=(await _asset(db_session)).id,
            position=last + offset,
            work_or_repair_code="AUDIT_DUP",
        )
        db_session.add(row)
        await db_session.flush()
        row_ids.append(str(row.id))
    found = (await db_session.execute(text(_AUDIT_SQL), {"plan_id": plan.id})).all()  # ty: ignore[deprecated]
    assert len(found) == 1
    plan_id, code, state, count, ids, positions = found[0]
    assert str(plan_id) == str(plan.id)
    assert code == "AUDIT_DUP"
    assert state == RPlanState.WORKING.value
    assert count == 2
    assert [str(value) for value in ids] == row_ids
    assert [int(value) for value in positions] == [last + 1, last + 2]


# --- Concurrency: two applies cannot both insert the same code -----------------


@pytest.mark.real_schema
async def test_concurrent_applies_cannot_create_duplicates(
    db_session: AsyncSession,
    my_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db_session  # fixture seeds the real schema used by both workers
    factory = async_sessionmaker(my_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _asset(session)
    from src.renovation import import_apply

    rows = [ParsedPlanRow(code="CONCURRENT_CODE", asset_key="BSZR", values={"workshop_name": "загружено"})]
    monkeypatch.setattr(import_apply, "iter_plan_row_batches", lambda *_args: (batch for batch in [rows]))

    async def run() -> None:
        async with factory() as session, session.begin():
            await import_apply.apply_plan_file(
                session,
                source=None,  # ty: ignore[invalid-argument-type]
                user_id=None,
                streaming=True,
                source_suffix=".xlsb",
            )

    await asyncio.gather(run(), run())
    async with factory() as session:
        found = (
            await session.exec(
                select(col(RenovationPlanRow.id)).where(col(RenovationPlanRow.work_or_repair_code) == "CONCURRENT_CODE")
            )
        ).all()
    assert len(found) == 1
