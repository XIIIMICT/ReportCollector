"""
Renovation formula materialization and the plan recompute backfill.

Moved verbatim from ``service.py`` (Phase 4). Pure computation over plan rows:
no HTTP, no authorization, no transaction ownership beyond the caller's
session. ``service.py`` re-exports ``recompute_plan_rows`` for its historical
importers (import_apply, seed, maintenance script, tests).
"""

"""
Renovation plan WRITE surface and façade.

Cell mutations, row lifecycle, formula recomputation and submit/approve stay
here (Phase 4 decomposition); the READ surface lives in ``plan_reads`` and the
shared primitives in ``service_common``. The public names below are re-exported
from those modules so ``from src.renovation.service import X`` keeps working.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import update as sa_update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation.form_schema import (
    RenovationFieldMeta,
    field_metas,
    formula_engine,
)
from src.renovation.models import RAsset
from src.renovation.plan import (
    RenovationPlanRow,
    RPlanState,
)
from src.renovation.renovation_id import (
    RENOVATION_ID_FORMULA_ENABLED,
)
from src.renovation.service_common import (
    _ERR_NO_WORKING_PLAN,
    _VIRTUAL_FIELDS,
    _reference_labels,
    _renovation_id_input,
    _safe_renovation_id,
)

from .plan_queries import _plan_by_state

_CALCULATED_METAS: tuple[RenovationFieldMeta, ...] = tuple(meta for meta in field_metas() if meta.is_calculated)
# work_or_repair_code (the renovation ID) is derived by `_renovation_id_input`, not by the
# formula engine; `_materialize_row` leaves it to the caller.
_FORMULA_METAS: tuple[RenovationFieldMeta, ...] = tuple(
    meta for meta in _CALCULATED_METAS if meta.db_name != "work_or_repair_code"
)
_CALCULATED_COLUMNS: tuple[str, ...] = tuple(meta.db_name for meta in _FORMULA_METAS)
# Every formula input (neither calculated nor virtual) — the full-recompute sweep.
_INPUT_FIELD_NAMES: tuple[str, ...] = tuple(
    meta.db_name for meta in field_metas() if not meta.is_calculated and meta.db_name not in _VIRTUAL_FIELDS
)


def _materialize_row(row: RenovationPlanRow, only: set[str] | None = None) -> tuple[dict[str, object], bool]:
    """
    Compute the formula fields of one row without touching the row object.

    `only` restricts the recompute to the dependency closure of those changed
    fields (AUD-28); `None` recomputes every formula field (the backfill). The
    values are keyed by field name, so a calculated dependency inside the
    closure is produced in the same pass and one outside is read from the row.
    Returns every calculated field (recomputed or stored) and whether any
    recomputed one differs from the stored value.
    """
    engine = formula_engine()
    if only is None:
        target_names: set[str] | None = None
        input_names = _INPUT_FIELD_NAMES
    else:
        target_names = engine.affected_fields(only)
        input_names = tuple(engine.inputs_of(target_names))
    values: dict[str, object] = {}
    for name in input_names:
        value = getattr(row, name)
        if value is not None:
            values[name] = value
    if target_names is None:
        engine.materialize(values)
    elif target_names:
        engine.materialize_fields(values, target_names)
    calculated: dict[str, object] = {}
    changed = False
    for meta in _FORMULA_METAS:
        if target_names is None or meta.db_name in target_names:
            new_value = values.get(meta.db_name)
            changed = changed or getattr(row, meta.db_name) != new_value
        else:
            # Not part of the closure: unchanged, so the stored value stands.
            new_value = getattr(row, meta.db_name)
        calculated[meta.db_name] = new_value
    return calculated, changed


def _recalculate_row(
    session: AsyncSession, row: RenovationPlanRow, changed_fields: set[str] | None = None
) -> dict[str, object]:
    """Materialize formula fields into the row (in place) and register the change."""
    calculated, changed = _materialize_row(row, changed_fields)
    if changed:
        for name, value in calculated.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
        session.add(row)
    return calculated


@dataclass(frozen=True, slots=True)
class RenovationRecomputeResult:
    """Outcome of `recompute_plan_rows` (scanned/changed row counts)."""

    plan_id: UUID
    scanned: int
    changed: int


async def recompute_plan_rows(
    session: AsyncSession,
    *,
    plan_id: UUID | None = None,
    row_ids: Sequence[UUID] | None = None,
    batch_size: int = 1000,
    progress: Callable[[int], None] | None = None,
) -> RenovationRecomputeResult:
    """
    Materialize the calculated fields and the renovation ID of plan rows.

    The importer skips calculated fields (the app is expected to recompute them)
    and `read_plan_rows` never materializes, so imported/adopted rows carry no
    derived values until they are edited. This recomputes and stores them; run it
    once after importing a plan, and whenever the formula definitions change.

    `row_ids` narrows the sweep to the rows an import just wrote (~20k instead of
    the whole ~306k-row plan); `None` keeps the full backfill used by
    `just recompute-renovation-plan`. `progress` receives the number of rows
    scanned so far, after every batch.

    Rows stream by primary key (bounded memory) and only rows whose derived
    values actually changed are written, in one batched `UPDATE` per batch (a
    per-row ORM flush costs ~2 ms/row on a 300k-row plan). The row
    `revision`/`updated_at` are left alone: this is a data fix, not an edit, and
    bumping the revision would invalidate in-flight undo/redo.
    """
    if plan_id is None:
        plan = await _plan_by_state(session, RPlanState.WORKING)
        if plan is None:
            raise _ERR_NO_WORKING_PLAN
        plan_id = plan.id
    # The renovation-ID generator is off (`RENOVATION_ID_FORMULA_ENABLED`), so
    # `work_or_repair_code` keeps its stored value and neither label map nor assets are
    # needed; skip both reads on the hot import path.
    if RENOVATION_ID_FORMULA_ENABLED:
        label_map = await _reference_labels(session)
        assets_by_id = {asset.id: asset for asset in (await session.exec(select(RAsset))).all()}
    else:
        label_map = {}
        assets_by_id = {}
    statement = (
        sa_update(RenovationPlanRow)
        .where(col(RenovationPlanRow.id) == bindparam("row_id"))
        .values({name: bindparam(name) for name in (*_CALCULATED_COLUMNS, "work_or_repair_code")})
        .execution_options(synchronize_session=False)
    )
    connection = await session.connection()
    scanned = 0
    changed = 0

    async def write_batch(rows: Sequence[RenovationPlanRow]) -> None:
        nonlocal scanned, changed
        parameters: list[dict[str, object]] = []
        for row in rows:
            calculated, row_changed = _materialize_row(row)
            asset = assets_by_id.get(row.asset_id)
            new_id = row.work_or_repair_code
            if asset is not None and RENOVATION_ID_FORMULA_ENABLED:
                new_id = _safe_renovation_id(_renovation_id_input(row, asset, label_map)).value
            row_changed = row_changed or new_id != row.work_or_repair_code
            changed += row_changed
            scanned += 1
            if row_changed:
                parameters.append({"row_id": row.id, **calculated, "work_or_repair_code": new_id})
        if parameters:
            await connection.execute(statement, parameters)
        for row in rows:
            session.expunge(row)
        if progress is not None:
            progress(scanned)

    if row_ids is not None:
        # Deduplicate while keeping the caller's order; an import may name the
        # same row twice only through a duplicate work code, which the preflight
        # already refuses — the guard costs nothing.
        pending = list(dict.fromkeys(row_ids))
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            query = select(RenovationPlanRow).where(
                col(RenovationPlanRow.plan_id) == plan_id,
                col(RenovationPlanRow.id).in_(chunk),
            )
            rows = list((await session.exec(query.order_by(col(RenovationPlanRow.id)))).all())
            await write_batch(rows)
        return RenovationRecomputeResult(plan_id=plan_id, scanned=scanned, changed=changed)

    last_id: UUID | None = None
    while True:
        query = select(RenovationPlanRow).where(col(RenovationPlanRow.plan_id) == plan_id)
        if last_id is not None:
            query = query.where(col(RenovationPlanRow.id) > last_id)
        rows = list((await session.exec(query.order_by(col(RenovationPlanRow.id)).limit(batch_size))).all())
        if not rows:
            break
        await write_batch(rows)
        last_id = rows[-1].id
    return RenovationRecomputeResult(plan_id=plan_id, scanned=scanned, changed=changed)
