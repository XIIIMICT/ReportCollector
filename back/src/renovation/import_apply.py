"""Write parsed technical-directorate rows into the working renovation plan."""

import asyncio
import contextlib
import queue
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import insert, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import lock_project
from src.renovation.constants import RENOVATION_PROJECT_KEY
from src.renovation.form_schema import field_meta
from src.renovation.import_rows import ParsedPlanRow, iter_plan_row_batches, parse_plan_rows
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RPlanState
from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id
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
    streaming: bool = False,
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
    """
    if streaming:
        batches = _stream_parsed_batches(source)
    else:
        parsed = await asyncio.to_thread(parse_plan_rows, source)

        async def one_batch() -> AsyncIterator[list[ParsedPlanRow]]:
            yield parsed

        batches = one_batch()
    await lock_project(session, RENOVATION_PROJECT_KEY)
    plan = (await session.exec(select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING))).first()
    if plan is None:
        raise _ERR_NO_WORKING_PLAN
    reference_ids = _reference_ids()
    asset_ids = {asset.repair_code.upper(): asset.id for asset in (await session.exec(select(RAsset))).all()}

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

    inserted = 0
    updated = 0
    batch: list[_PendingRow] = []
    async for parsed_batch in batches:
        for row in parsed_batch:
            asset_id = asset_ids.get(row.asset_code)
            if asset_id is None:
                # A reference value like any catalog label: an asset the project's
                # dictionary does not have refuses the whole file (the preflight
                # reports it) instead of dropping its rows from the plan.
                raise _unknown_asset(row.code, row.asset_code)
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
                await _flush_batch(session, plan_id=plan.id, batch=batch, reference_ids=reference_ids, user_id=user_id)
                batch = []
    if batch:
        await _flush_batch(session, plan_id=plan.id, batch=batch, reference_ids=reference_ids, user_id=user_id)

    # Calculated fields and the renovation ID of every row of the plan.
    await recompute_plan_rows(session, plan_id=plan.id)
    return RenovationPlanImportApplyResult(new_rows=inserted, updated_rows=updated)


async def _stream_parsed_batches(source: BinaryIO) -> AsyncIterator[list[ParsedPlanRow]]:
    """Bridge the CPU parser thread to async writes with a bounded queue."""
    batches: queue.Queue[list[ParsedPlanRow] | BaseException | None] = queue.Queue(maxsize=2)

    def produce() -> None:
        try:
            for batch in iter_plan_row_batches(source, _BATCH_SIZE):
                batches.put(batch)
        except BaseException as error:  # noqa: BLE001
            batches.put(error)
        finally:
            batches.put(None)

    producer = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            item = await asyncio.to_thread(batches.get)
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not producer.done():
            producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer


async def _flush_batch(
    session: AsyncSession,
    *,
    plan_id: UUID,
    batch: Sequence[_PendingRow],
    reference_ids: dict[str, dict[str, str]],
    user_id: UUID | None,
) -> None:
    """Write one batch as bulk INSERT/UPDATE statements (no per-row round trip)."""
    now = datetime.now(UTC)
    new_rows: list[dict[str, object]] = []
    updates: list[tuple[UUID, dict[str, object]]] = []
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
            else:
                values[field] = value
        if pending.row_id is None:
            new_rows.append(
                {
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
            continue
        # Values absent from the file keep their current plan values.
        updates.append(
            (
                pending.row_id,
                {"asset_id": pending.asset_id, "updated_at": now, "updater_user_id": user_id, **values},
            )
        )
    if new_rows:
        await session.exec(insert(RenovationPlanRow).values(new_rows))
    for row_id, changes in updates:
        await session.exec(update(RenovationPlanRow).where(col(RenovationPlanRow.id) == row_id).values(**changes))


def _label(field: str) -> str:
    """Return the form label of a column (for user-facing messages)."""
    meta = field_meta(field)
    return meta.label if meta is not None else field


def _reference_ids() -> dict[str, dict[str, str]]:
    """Catalog id → {casefolded label: option id} for the form's own options."""
    catalog_labels = {catalog for catalog, _label in REFERENCE_OPTIONS}
    ids: dict[str, dict[str, str]] = {catalog: {} for catalog in catalog_labels}
    for catalog, label in REFERENCE_OPTIONS:
        ids[catalog][label.casefold()] = str(reference_option_id(catalog, label))
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


def _unknown_asset(code: str, asset_code: str) -> ValueError:
    """Abort on an asset the project's dictionary does not have (nothing written)."""
    return ValueError(
        f"Строка {code}: актив «{asset_code}» не найден в справочнике активов. "
        "План не изменён — добавьте актив в справочник или исправьте файл."
    )


_CATALOG_BY_FIELD: dict[str, str] = {
    "col_015": "criticality",
    "col_017": "work_category",
    "col_018": "work_type",
    "col_019": "cost_allocation",
    "col_020": "execution_method",
}
