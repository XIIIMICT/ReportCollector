"""Write parsed technical-directorate rows into the working renovation plan."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from sqlalchemy import insert, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import lock_project
from src.observability import observe_phase
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import field_meta, field_metas
from src.renovation.import_plan import _asset_name_key
from src.renovation.import_rows import ParsedPlanRow, iter_plan_row_batches, parse_plan_rows
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.schemas import RenovationPlanImportApplyResult
from src.renovation.service import recompute_plan_rows

# Rows per INSERT/UPDATE batch: short statements (the row is ~216 columns wide)
# without paying a round trip per row.
_BATCH_SIZE = 200
_ERR_NO_WORKING_PLAN = ValueError("Нет рабочего плана для загрузки.")


@dataclass(slots=True)
class _PendingRow:
    """One parsed file row ready for the write: new row or an update of one."""

    parsed: ParsedPlanRow
    asset_id: UUID
    row_id: UUID | None
    position: int | None


async def apply_plan_file(
    session: AsyncSession,
    *,
    source: BinaryIO,
    user_id: UUID | None,
    plan_id: UUID | None = None,
    streaming: bool = False,
    expected_rows: int = 0,
    progress: Callable[[int, int, str], None] | None = None,
    source_suffix: str | None = None,
) -> RenovationPlanImportApplyResult:
    """
    Load a validated technical-directorate file into the working plan.

    The workbook is parsed in a worker thread (CPU-bound: tens of thousands of
    rows × 216 columns), then written in short batches inside ONE transaction
    under the shared project gate, so the plan is never seen half-loaded and a
    concurrent edit waits instead of racing. Reference labels resolve to the
    form's catalog ids here; a label the catalog does not know aborts the whole
    load (the preflight reports them before the button is enabled).

    Rows are matched by the work code: a code already in the plan updates that
    row in place, a new code appends a row to the end of the plan.

    `progress` receives `(done, total, phase)`: the write phase counts processed
    rows, the recompute phase counts rescanned rows, and `total` is twice the
    preflight's row count (the file is written, then those rows are recomputed).
    `expected_rows=0` means the client did not send a preflight count — the
    counters still advance, but the caller treats `total=0` as indeterminate.
    `source_suffix` names the container when `source` is an already-open stream
    (the durable job reads a path whose suffix may be `.xlsb` or `.xlsx`).
    """
    if streaming:
        batches = _stream_parsed_batches(source, source_suffix)
    else:
        parsed = await asyncio.to_thread(parse_plan_rows, source, source_suffix)

        async def one_batch() -> AsyncGenerator[list[ParsedPlanRow]]:
            yield parsed

        batches = one_batch()
    with observe_phase("import", "project_lock"):
        await lock_project(session, RENOVATION_PROJECT_KEY)
    plan_query = select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING)
    if plan_id is not None:
        plan_query = plan_query.where(col(RenovationPlan.id) == plan_id)
    plan = (await session.exec(plan_query)).first()
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    reference_ids = await _reference_ids(session)
    assets = (await session.exec(select(RAsset))).all()
    asset_ids = {asset.code.upper(): asset.id for asset in assets}
    # A drop without «Наименование ДО» names the entity in «Предприятие»: the
    # same dictionary, matched by name (case/quote/space-insensitive).
    asset_ids_by_name = {_asset_name_key(asset.name): asset.id for asset in assets}

    existing: dict[str, UUID] = {
        code: row_id
        for row_id, code in (
            await session.exec(
                select(col(RenovationPlanRow.id), col(RenovationPlanRow.col_003)).where(
                    col(RenovationPlanRow.plan_id) == plan.id
                )
            )
        ).all()
        if code
    }
    last_position = (
        await session.exec(
            select(RenovationPlanRow.position)
            .where(col(RenovationPlanRow.plan_id) == plan.id)
            .order_by(col(RenovationPlanRow.position).desc())
            .limit(1)
        )
    ).first() or 0

    expected = max(expected_rows, 0)
    total = expected * 2
    processed = 0
    affected: list[UUID] = []

    def report(phase: str, done: int) -> None:
        if progress is None:
            return
        # The file can differ from the preflight count (it was checked, then
        # edited); never let a stale estimate push the bar past 100 %.
        progress(min(done, total) if total else done, total, phase)

    report("parse", 0)
    inserted = 0
    updated = 0
    batch: list[_PendingRow] = []
    async with contextlib.aclosing(batches):
        async for parsed_batch in batches:
            for row in parsed_batch:
                if row.asset_is_name:
                    asset_id = asset_ids_by_name.get(_asset_name_key(row.asset_key))
                else:
                    asset_id = asset_ids.get(row.asset_key)
                if asset_id is None:
                    # A reference value like any catalog label: an asset the project's
                    # dictionary does not have refuses the whole file (the preflight
                    # reports it) instead of dropping its rows from the plan.
                    raise _unknown_asset(row.code, row.asset_key)
                row_id = existing.get(row.code)
                position: int | None = None
                if row_id is not None:
                    updated += 1
                else:
                    last_position += 1
                    position = last_position
                    inserted += 1
                batch.append(_PendingRow(parsed=row, asset_id=asset_id, row_id=row_id, position=position))
                if len(batch) >= _BATCH_SIZE:
                    with observe_phase("import", "write_batch"):
                        affected.extend(
                            await _flush_batch(
                                session, plan_id=plan.id, batch=batch, reference_ids=reference_ids, user_id=user_id
                            )
                        )
                    batch = []
                    processed = inserted + updated
                    report("write", min(processed, expected) if total else processed)
    if batch:
        with observe_phase("import", "write_batch"):
            affected.extend(
                await _flush_batch(session, plan_id=plan.id, batch=batch, reference_ids=reference_ids, user_id=user_id)
            )
        processed = inserted + updated
        report("write", min(processed, expected) if total else processed)

    # Only the rows this load wrote need their derived values: the full sweep
    # (`just recompute-renovation-plan`) is the backfill, and rewriting all
    # ~306k rows was where most of the former ~340-second import went.
    with observe_phase("import", "recompute"):
        written = min(processed, expected) if total else 0
        report("recompute", written)
        await recompute_plan_rows(
            session,
            plan_id=plan.id,
            row_ids=affected,
            progress=lambda scanned: report("recompute", written + scanned if total else scanned),
        )
    return RenovationPlanImportApplyResult(new_rows=inserted, updated_rows=updated)


async def _stream_parsed_batches(source: BinaryIO, suffix: str | None = None) -> AsyncGenerator[list[ParsedPlanRow]]:
    """Pull one batch at a time; never leave a producer blocked after a failed write."""
    batches = cast("Generator[list[ParsedPlanRow]]", iter_plan_row_batches(source, _BATCH_SIZE, suffix))
    try:
        while True:

            def read_next() -> list[ParsedPlanRow] | None:
                return next(batches, None)

            read = asyncio.create_task(asyncio.to_thread(read_next))
            try:
                with observe_phase("import", "parse_batch"):
                    item = await asyncio.shield(read)
            except asyncio.CancelledError:
                # Cancellation cannot interrupt a Python thread. Wait for this
                # bounded batch before closing the workbook/upload underneath it.
                await read
                raise
            if item is None:
                break
            yield item
    finally:
        await asyncio.to_thread(batches.close)


async def _flush_batch(
    session: AsyncSession,
    *,
    plan_id: UUID,
    batch: Sequence[_PendingRow],
    reference_ids: dict[str, dict[str, str]],
    user_id: UUID | None,
) -> list[UUID]:
    """
    Write one batch as bulk INSERT/UPDATE statements (no per-row round trip).

    Returns the ids of every row the batch touched: an INSERT needs its id
    generated here so the narrowed recompute can name the new rows without a
    second lookup by work code.
    """
    now = datetime.now(UTC)
    new_rows: list[dict[str, object]] = []
    updates: list[tuple[UUID, dict[str, object]]] = []
    affected: list[UUID] = []
    for pending in batch:
        # The parsed values are already coerced to the column types, so they go
        # to `values()` as-is; reference labels become catalog ids here.
        values: dict[str, object] = {}
        for field, value in pending.parsed.values.items():
            # The asset is a foreign key on the row, not a plan column.
            if field == "col_002" or not hasattr(RenovationPlanRow, field):
                continue
            if field in _CATALOG_BY_FIELD:
                resolved = _resolve_reference(field, value, reference_ids)
                if resolved is value:
                    raise _unknown_reference(pending.parsed.code, field, value)
                values[field] = resolved
            elif field in _DATE_FIELDS:
                # The parser's contract is ISO text (see _DATE_FIELDS); the
                # column is DATE. An empty cell stays NULL.
                values[field] = date.fromisoformat(str(value)) if value else None
            else:
                values[field] = value
        if pending.row_id is None:
            row_id = uuid4()
            new_rows.append(
                {
                    "id": row_id,
                    "plan_id": plan_id,
                    "asset_id": pending.asset_id,
                    "position": pending.position or 0,
                    "baseline_generation": 1,
                    "revision": 0,
                    "created_at": now,
                    "updated_at": now,
                    "updater_user_id": user_id,
                    "col_003": pending.parsed.code,
                    **values,
                }
            )
            affected.append(row_id)
            continue
        # Values absent from the file keep their current plan values.
        updates.append(
            (
                pending.row_id,
                {"asset_id": pending.asset_id, "updated_at": now, "updater_user_id": user_id, **values},
            )
        )
        affected.append(pending.row_id)
    if new_rows:
        # One multi-row VALUES clause, so every dict must carry the SAME keys:
        # the parser drops blank cells, which makes real rows ragged (and made
        # SQLAlchemy raise "explicitly rendered as a boundparameter"). Pad them
        # to the batch's union — an omitted cell is a NULL on a created row.
        columns = sorted(set().union(*(row.keys() for row in new_rows)))
        await session.exec(
            insert(RenovationPlanRow).values([{column: row.get(column) for column in columns} for row in new_rows])
        )
    for row_id, changes in updates:
        await session.exec(update(RenovationPlanRow).where(col(RenovationPlanRow.id) == row_id).values(**changes))
    return affected


def _label(field: str) -> str:
    """Return the form label of a column (for user-facing messages)."""
    meta = field_meta(field)
    return meta.label if meta is not None else field


# Columns the form renders as dates. The parser hands them over as ISO text —
# it also feeds the renovation-ID calculation, which wants text — while the
# column is a real DATE: asyncpg refuses a string ("'str' object has no
# attribute 'toordinal'"), so the write boundary converts them.
_DATE_FIELDS: frozenset[str] = frozenset(meta.db_name for meta in field_metas() if meta.cell_type == "date")


async def _reference_ids(session: AsyncSession) -> dict[str, dict[str, str]]:
    """
    Catalog id → {casefolded label: option id}, from the LIVE dictionary.

    These are the very rows the preflight (`/plan/import/inspect`) checks the
    file against. Reading the static seed list instead meant every option the
    operator added after the seed was unusable: the preflight said «ready» and
    the load then refused the whole file with "значение не найдено в
    справочнике" — the «Собственными силами» case. The dictionary is the
    authority, in both passes.
    """
    ids: dict[str, dict[str, str]] = {}
    for option in (await session.exec(select(RenovationReferenceValue))).all():
        # `RReferenceCatalog` is a StrEnum, but a row added in THIS session still
        # carries the raw string it was constructed with — `str()` covers both.
        ids.setdefault(str(option.catalog), {})[option.label.casefold()] = str(option.id)
    return ids


def _resolve_reference(field: str, value: object, reference_ids: dict[str, dict[str, str]]) -> object:
    """Map a catalog label to its option id (unknown labels stay as text → FK fails)."""
    catalog = _CATALOG_BY_FIELD.get(field)
    if catalog is None:
        return value
    # Excel hands a numeric option over as "3.0"; the catalog label is "3".
    text = " ".join(str(value).replace("\u00a0", " ").split())
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        text = text[:-2]
    label = text.casefold()
    return reference_ids.get(catalog, {}).get(label, value)


def _unknown_reference(code: str, field: str, value: object) -> ValueError:
    """Abort on a label the catalog has no option for (the whole load rolls back)."""
    return ValueError(
        f"Строка {code}: значение «{value}» не найдено в справочнике поля «{_label(field)}». "
        "План не изменён — сообщите о значении ответственному за справочник."
    )


def _unknown_asset(code: str, asset_key: str) -> ValueError:
    """Abort on an asset the project's dictionary does not have (nothing written)."""
    return ValueError(
        f"Строка {code}: актив «{asset_key}» не найден в справочнике активов. "
        "План не изменён — добавьте актив в справочник или исправьте файл."
    )


_CATALOG_BY_FIELD: dict[str, str] = {
    "col_015": "criticality",
    "col_017": "work_category",
    "col_018": "work_type",
    "col_019": "cost_allocation",
    "col_020": "execution_method",
}
