"""Write parsed technical-directorate rows into the working renovation plan."""

import asyncio
import contextlib
import pickle  # the spool is written and read by this process only
import tempfile
from collections.abc import AsyncGenerator, Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from sqlalchemy import Column, MetaData, Table, insert, text
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import TypeEngine
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.collab.locking import lock_project, lock_rows
from src.collab.persistence import JournalStore
from src.common.durable_imports import ImportValidationError
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
_RECOMPUTE_ID_BATCH = 1000
_ERR_NO_WORKING_PLAN = ValueError("Нет рабочего плана для загрузки.")

# Phase 5.1: existing rows are updated set-based from a session-local staging
# table. Its DDL is generated from the ORM column types with NO constraints and
# `ON COMMIT DROP`: no migration, no persistent state, no cleanup job; commit OR
# rollback removes it. The staging INSERT is batched like the target INSERT; the
# target UPDATE is ONE statement.
_STAGE_TABLE = "plan_import_stage"
_WRITE_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        ["work_or_repair_code", "asset_id", "updated_at", "updater_user_id"]
        + [meta.db_name for meta in field_metas() if not meta.is_calculated]
    )
)
_PLAN_TABLE = SQLModel.metadata.tables[RenovationPlanRow.__tablename__]
_STAGE_COLUMNS: tuple[str, ...] = tuple(name for name in _WRITE_FIELDS if name in _PLAN_TABLE.c)
_stage_table = Table(
    _STAGE_TABLE,
    MetaData(),
    *(Column(name, _PLAN_TABLE.c[name].type) for name in _STAGE_COLUMNS),
)
_STAGE_DDL = (
    str(CreateTable(_stage_table).compile(dialect=postgresql_dialect())).replace("CREATE TABLE", "CREATE TEMP TABLE", 1)
    + " ON COMMIT DROP"
)
_UPDATE_SQL = (
    f"UPDATE {_PLAN_TABLE.name} AS t SET "  # noqa: S608  # identifiers are ORM metadata, not user input
    + ", ".join(f"{name} = s.{name}" for name in _STAGE_COLUMNS if name != "work_or_repair_code")
    + f" FROM {_STAGE_TABLE} AS s "
    + "WHERE t.plan_id = :plan_id AND t.work_or_repair_code = s.work_or_repair_code"
)
# Defence in depth: staging keeps one row per distinct code, so a duplicate that
# slips past the parse loop fails at the database boundary too. Like the table
# itself this index is transaction-local (ON COMMIT DROP), never a migration.
_STAGE_UNIQUE_DDL = f"CREATE UNIQUE INDEX ux_{_STAGE_TABLE}_code ON {_STAGE_TABLE} (work_or_repair_code)"
_ERROR_DETAIL_LIMIT = 10


def _temp_table(name: str, *columns: tuple[str, TypeEngine], primary: str | None = None) -> tuple[Table, str]:
    """Declare a transaction-local table from ORM column types (no migration)."""
    table = Table(
        name,
        MetaData(),
        *(Column(column_name, column_type, primary_key=column_name == primary) for column_name, column_type in columns),
    )
    ddl = (
        str(CreateTable(table).compile(dialect=postgresql_dialect())).replace("CREATE TABLE", "CREATE TEMP TABLE", 1)
        + " ON COMMIT DROP"
    )
    return table, ddl


# Four tiny transaction-local sets replace O(rows) Python collections in apply:
# plan codes, codes already duplicated in the plan, workbook codes seen so far
# (with their first row) and the ids the recompute must visit.
_code_type = _PLAN_TABLE.c.work_or_repair_code.type
_id_type = _PLAN_TABLE.c.id.type
_int_type = _PLAN_TABLE.c.position.type
_existing_table, _EXISTING_DDL = _temp_table("plan_import_existing", ("code", _code_type), primary="code")
_ambiguous_table, _AMBIGUOUS_DDL = _temp_table(
    "plan_import_ambiguous", ("code", _code_type), ("occurrences", _int_type), primary="code"
)
_seen_table, _SEEN_DDL = _temp_table("plan_import_seen", ("code", _code_type), ("first_row", _int_type), primary="code")
_written_table, _WRITTEN_DDL = _temp_table("plan_import_written", ("id", _id_type), primary="id")
_TEMP_DDLS = (_STAGE_DDL, _STAGE_UNIQUE_DDL, _EXISTING_DDL, _AMBIGUOUS_DDL, _SEEN_DDL, _WRITTEN_DDL)

_LOAD_EXISTING_SQL = (
    f"INSERT INTO {_existing_table.name} (code) "  # noqa: S608  # identifiers come from ORM metadata
    f"SELECT work_or_repair_code FROM {_PLAN_TABLE.name} "
    "WHERE plan_id = :plan_id AND work_or_repair_code IS NOT NULL "
    "ON CONFLICT (code) DO NOTHING"
)
_LOAD_AMBIGUOUS_SQL = (
    f"INSERT INTO {_ambiguous_table.name} (code, occurrences) "  # noqa: S608
    f"SELECT work_or_repair_code, count(*) FROM {_PLAN_TABLE.name} "
    "WHERE plan_id = :plan_id AND work_or_repair_code IS NOT NULL "
    "GROUP BY work_or_repair_code HAVING count(*) > 1"
)
# One statement per batch (not per row): `unnest` keeps the row order intact,
# and RETURNING names exactly the codes that were not already seen.
_SEEN_INSERT_SQL = (
    f"INSERT INTO {_seen_table.name} (code, first_row) "  # noqa: S608
    "SELECT value.code, value.first_row FROM "
    "unnest(CAST(:codes AS text[]), CAST(:rows AS integer[])) AS value(code, first_row) "
    "ON CONFLICT (code) DO NOTHING RETURNING code"
)
_WRITTEN_INSERT_SQL = f"INSERT INTO {_written_table.name} (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"  # noqa: S608
_WRITTEN_FROM_STAGE_SQL = (
    f"INSERT INTO {_written_table.name} (id) "  # noqa: S608
    f"SELECT t.id FROM {_PLAN_TABLE.name} AS t JOIN {_STAGE_TABLE} AS s "
    "ON t.work_or_repair_code = s.work_or_repair_code "
    "WHERE t.plan_id = :plan_id ON CONFLICT (id) DO NOTHING"
)
_AMBIGUOUS_ROWS_SQL = (
    f"SELECT id, position FROM {_PLAN_TABLE.name} "  # noqa: S608
    "WHERE plan_id = :plan_id AND work_or_repair_code = :code "
    "ORDER BY position LIMIT :detail_limit"
)


@dataclass(slots=True)
class _PendingRow:
    """One parsed file row ready for the write: new row or an update staging record."""

    parsed: ParsedPlanRow
    asset_id: UUID
    is_new: bool
    position: int | None


class _ParsedRowSpool:
    """
    Disk-backed spool of normalized rows, written before any DB session exists.

    Parsing a large workbook is CPU/IO work; holding the project gate or a write
    transaction for it keeps locks and pool connections busy for minutes. The
    worker parses into this temporary file first, then replays bounded batches
    inside one short transaction. The file is removed on close (including on
    cancellation or failure).
    """

    def __init__(self) -> None:
        # Kept open across calls; close() owns its lifetime.
        self._file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        self.rows = 0

    def write_batch(self, batch: Sequence[ParsedPlanRow]) -> None:
        for row in batch:
            pickle.dump((row.code, row.asset_key, row.asset_is_name, row.values), self._file)
        self.rows += len(batch)

    def iter_batches(self, batch_size: int = _BATCH_SIZE) -> Iterator[list[ParsedPlanRow]]:
        self._file.seek(0)
        batch: list[ParsedPlanRow] = []
        while True:
            try:
                code, asset_key, asset_is_name, values = pickle.load(self._file)  # noqa: S301
            except EOFError:
                break
            batch.append(ParsedPlanRow(code=code, asset_key=asset_key, values=values, asset_is_name=asset_is_name))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def close(self) -> None:
        self._file.close()


def spool_plan_rows(source: BinaryIO, suffix: str | None = None, batch_size: int = _BATCH_SIZE) -> _ParsedRowSpool:
    """Parse the whole workbook into a temporary spool; no DB access involved."""
    spool = _ParsedRowSpool()
    try:
        for batch in iter_plan_row_batches(source, batch_size, suffix):
            spool.write_batch(batch)
    except BaseException:
        spool.close()
        raise
    else:
        return spool


def _duplicate_in_file_error(code: str, first_row: int, next_row: int) -> ImportValidationError:
    """Report a workbook code repeated across rows (possibly across batch boundaries)."""
    return ImportValidationError(
        f"Код работы «{code}» встречается дважды в загружаемом файле (строки {first_row} и {next_row}). "
        "Загрузка отклонена: код должен быть уникален в одном плане. План не изменён."
    )


def _ambiguous_existing_error(
    code: str, rows: list[tuple[UUID, int]], occurrences: int | None = None
) -> ImportValidationError:
    """Report a file code matching several working-plan rows and refuse the overwrite."""
    total = len(rows) if occurrences is None else occurrences
    shown = ", ".join(f"id={row_id}, №{position}" for row_id, position in rows[:_ERROR_DETAIL_LIMIT])
    extra = "" if total <= _ERROR_DETAIL_LIMIT else f" и ещё {total - _ERROR_DETAIL_LIMIT}"
    return ImportValidationError(
        f"Код работы «{code}» встречается в {total} строках рабочего плана ({shown}{extra}). "
        "Загрузка отклонена: неоднозначный код нельзя обновлять. "
        "Удалите дубли в плане или исправьте код в файле. План не изменён."
    )


async def apply_plan_file(
    session: AsyncSession,
    *,
    source: BinaryIO | _ParsedRowSpool,
    user_id: UUID | None,
    plan_id: UUID | None = None,
    streaming: bool = False,
    expected_rows: int = 0,
    progress: Callable[[int, int, str], None] | None = None,
    source_suffix: str | None = None,
) -> RenovationPlanImportApplyResult:
    """
    Load a validated technical-directorate file into the working plan.

    The workbook is parsed into a local spool BEFORE the project gate or any
    write transaction is acquired (the durable worker does this even earlier,
    before opening a session). The spool is replayed in bounded batches inside
    ONE transaction, so the plan is never seen half-loaded. The shared project
    advisory gate is compatible with other shared locks: it does NOT serialize
    this load against normal cell edits (those use row locks/leases/revisions).
    Gate A's chosen policy blocks the form while the import job runs.

    Rows are matched by the work code: a code already in the TARGET plan updates
    its single matching row, a new code appends a row to the end of the plan.
    A code repeated in the workbook or duplicated in the target plan refuses the
    whole file — this is re-checked here, not inherited from the inspect request.

    `progress` receives `(done, total, phase)`: the write phase counts processed
    rows, the recompute phase counts rescanned rows, and `total` is twice the
    preflight's row count (the file is written, then those rows are recomputed).
    `expected_rows=0` means the client did not send a preflight count — the
    counters still advance, but the caller treats `total=0` as indeterminate.
    `source_suffix` names the container when `source` is an already-open stream;
    a caller that already prepared a spool passes it as `source` instead.
    """
    spool: _ParsedRowSpool
    if isinstance(source, _ParsedRowSpool):
        spool = source
    elif streaming:
        with observe_phase("import", "parse"):
            spool = await asyncio.to_thread(spool_plan_rows, source, source_suffix, _BATCH_SIZE)
    else:
        with observe_phase("import", "parse"):
            parsed = await asyncio.to_thread(parse_plan_rows, source, source_suffix)
            spool = _ParsedRowSpool()
            for start in range(0, len(parsed), _BATCH_SIZE):
                spool.write_batch(parsed[start : start + _BATCH_SIZE])
    try:
        batches = _stream_batches(spool.iter_batches())
        with observe_phase("import", "project_gate_wait"):
            await lock_project(session, RENOVATION_PROJECT_KEY)
        with observe_phase("import", "project_gate_hold"):
            plan_query = select(RenovationPlan).where(col(RenovationPlan.state) == RPlanState.WORKING)
            if plan_id is not None:
                plan_query = plan_query.where(col(RenovationPlan.id) == plan_id)
            plan = (await session.exec(plan_query)).first()
            if plan is None:
                raise _ERR_NO_WORKING_PLAN
            # The project gate is SHARED (it admits normal edits and several imports).
            # A second, plan-scoped exclusive lock serializes applies of the same plan.
            await lock_rows(session, [f"renovation_import_plan:{plan.id}"])
            # Transaction-local sets: DROP IF EXISTS also handles nested test
            # savepoints where COMMIT does not happen between two loads.
            for table_name in (
                _STAGE_TABLE,
                _seen_table.name,
                _written_table.name,
                _existing_table.name,
                _ambiguous_table.name,
            ):
                await session.execute(text(f"DROP TABLE IF EXISTS {table_name}"))  # ty: ignore[deprecated]
            with observe_phase("import", "staging"):
                for ddl in _TEMP_DDLS:
                    await session.execute(text(ddl))  # ty: ignore[deprecated]
                await session.execute(text(_LOAD_EXISTING_SQL), {"plan_id": plan.id})  # ty: ignore[deprecated]
                await session.execute(text(_LOAD_AMBIGUOUS_SQL), {"plan_id": plan.id})  # ty: ignore[deprecated]
                reference_ids = await _reference_ids(session)
                assets = (await session.exec(select(RAsset))).all()
                asset_ids = {asset.code.upper(): asset.id for asset in assets}
                # A drop without «Наименование ДО» names the entity in «Предприятие»:
                # the same dictionary, matched by name (case/quote/space-insensitive).
                asset_ids_by_name = {_asset_name_key(asset.name): asset.id for asset in assets}
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

            def report(phase: str, done: int) -> None:
                if progress is None:
                    return
                # The file can differ from the preflight count; never let a
                # stale estimate push the bar past 100 %.
                progress(min(done, total) if total else done, total, phase)

            report("parse", 0)
            inserted = 0
            updated = 0
            batch: list[_PendingRow] = []
            file_row = 0
            with observe_phase("import", "apply"):
                async with contextlib.aclosing(batches):
                    async for parsed_batch in batches:
                        codes = [row.code for row in parsed_batch]
                        first_rows = [file_row + index + 1 for index in range(len(parsed_batch))]
                        # One statement per batch: codes already seen (in this or an
                        # earlier batch) conflict on the temp table's primary key.
                        fresh: dict[str, int] = {}
                        for code in (
                            (
                                await session.execute(  # ty: ignore[deprecated]
                                    text(_SEEN_INSERT_SQL), {"codes": codes, "rows": first_rows}
                                )
                            )
                            .scalars()
                            .all()
                        ):
                            fresh[code] = fresh.get(code, 0) + 1
                        ambiguous = set(
                            (
                                await session.exec(
                                    select(col(_ambiguous_table.c.code)).where(col(_ambiguous_table.c.code).in_(codes))
                                )
                            ).all()
                        )
                        existing = set(
                            (
                                await session.exec(
                                    select(col(_existing_table.c.code)).where(col(_existing_table.c.code).in_(codes))
                                )
                            ).all()
                        )
                        for row, number in zip(parsed_batch, first_rows, strict=True):
                            file_row = number
                            if fresh.get(row.code, 0) == 0:
                                first = cast(
                                    "int",
                                    (
                                        await session.exec(
                                            select(col(_seen_table.c.first_row)).where(
                                                col(_seen_table.c.code) == row.code
                                            )
                                        )
                                    ).one(),
                                )
                                raise _duplicate_in_file_error(row.code, first, number)
                            fresh[row.code] -= 1
                            if row.code in ambiguous:
                                occurrences = cast(
                                    "int",
                                    (
                                        await session.exec(
                                            select(col(_ambiguous_table.c.occurrences)).where(
                                                col(_ambiguous_table.c.code) == row.code
                                            )
                                        )
                                    ).one(),
                                )
                                details = [
                                    (row_id, position)
                                    for row_id, position in (
                                        await session.execute(  # ty: ignore[deprecated]
                                            text(_AMBIGUOUS_ROWS_SQL),
                                            {
                                                "plan_id": plan.id,
                                                "code": row.code,
                                                "detail_limit": _ERROR_DETAIL_LIMIT,
                                            },
                                        )
                                    ).all()
                                ]
                                raise _ambiguous_existing_error(row.code, details, occurrences)
                            if row.asset_is_name:
                                asset_id = asset_ids_by_name.get(_asset_name_key(row.asset_key))
                            else:
                                asset_id = asset_ids.get(row.asset_key)
                            if asset_id is None:
                                # A reference value like any catalog label: an asset the
                                # project's dictionary does not have refuses the whole
                                # file (the preflight reports it) instead of dropping
                                # its rows from the plan.
                                raise _unknown_asset(row.code, row.asset_key)
                            is_new = row.code not in existing
                            position: int | None = None
                            if not is_new:
                                updated += 1
                            else:
                                last_position += 1
                                position = last_position
                                inserted += 1
                            batch.append(_PendingRow(parsed=row, asset_id=asset_id, is_new=is_new, position=position))
                            if len(batch) >= _BATCH_SIZE:
                                with observe_phase("import", "write_batch"):
                                    await _flush_batch(
                                        session,
                                        plan_id=plan.id,
                                        batch=batch,
                                        reference_ids=reference_ids,
                                        user_id=user_id,
                                    )
                                batch = []
                                processed = inserted + updated
                                report("write", min(processed, expected) if total else processed)
                if batch:
                    with observe_phase("import", "write_batch"):
                        await _flush_batch(
                            session, plan_id=plan.id, batch=batch, reference_ids=reference_ids, user_id=user_id
                        )
                    processed = inserted + updated
                    report("write", min(processed, expected) if total else processed)

                # ONE set-based UPDATE for all staged rows. No per-row UPDATE round
                # trip remains, and the written ids move into a temp table instead
                # of an O(rows) Python list.
                await session.execute(text(_UPDATE_SQL), {"plan_id": plan.id})  # ty: ignore[deprecated]
                await session.execute(text(_WRITTEN_FROM_STAGE_SQL), {"plan_id": plan.id})  # ty: ignore[deprecated]

            # Only the rows this load wrote need their derived values: the full
            # sweep is the backfill. Ids are read from the temp table in bounded
            # batches, so neither apply nor recompute holds an O(N) UUID list.
            async def written_id_batches() -> AsyncGenerator[list[UUID]]:
                last_id: UUID | None = None
                while True:
                    query = select(col(_written_table.c.id)).order_by(col(_written_table.c.id))
                    if last_id is not None:
                        query = query.where(col(_written_table.c.id) > last_id)
                    ids = cast("list[UUID]", list((await session.exec(query.limit(_RECOMPUTE_ID_BATCH))).all()))
                    if not ids:
                        return
                    yield ids
                    last_id = ids[-1]

            with observe_phase("import", "recompute"):
                written = min(processed, expected) if total else 0
                report("recompute", written)
                await recompute_plan_rows(
                    session,
                    plan_id=plan.id,
                    id_batches=written_id_batches(),
                    progress=lambda scanned: report("recompute", written + scanned if total else scanned),
                )
            # Gate A follow-up: the import overwrites whole rows, so any edit stack
            # made before it would replay obsolete values over the imported ones.
            await JournalStore(session).clear_undo_state_for_scope("renovationPlan/")
            return RenovationPlanImportApplyResult(new_rows=inserted, updated_rows=updated)
    finally:
        spool.close()


async def _stream_parsed_batches(source: BinaryIO, suffix: str | None = None) -> AsyncGenerator[list[ParsedPlanRow]]:
    """Stream a workbook's parsed batches (kept for callers/tests that bypass the spool)."""
    batches = cast("Generator[list[ParsedPlanRow]]", iter_plan_row_batches(source, _BATCH_SIZE, suffix))
    async with contextlib.aclosing(_stream_batches(batches)) as streamed:
        async for item in streamed:
            yield item


async def _stream_batches(batches: Iterator[list[ParsedPlanRow]]) -> AsyncGenerator[list[ParsedPlanRow]]:
    """Bridge a bounded sync iterator to the async writer without abandoning a producer."""
    iterator = iter(batches)
    try:
        while True:

            def read_next() -> list[ParsedPlanRow] | None:
                return next(iterator, None)

            read = asyncio.create_task(asyncio.to_thread(read_next))
            try:
                with observe_phase("import", "parse_batch"):
                    item = await asyncio.shield(read)
            except asyncio.CancelledError:
                # Cancellation cannot interrupt a Python thread. Wait for this
                # bounded batch before closing the spool underneath it.
                await read
                raise
            if item is None:
                break
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await asyncio.to_thread(close)


async def _flush_batch(
    session: AsyncSession,
    *,
    plan_id: UUID,
    batch: Sequence[_PendingRow],
    reference_ids: dict[str, dict[str, str]],
    user_id: UUID | None,
) -> None:
    """
    Write one batch: new rows in one INSERT, existing rows staged for one UPDATE.

    The set-based UPDATE runs after the last batch. Inserted ids are recorded in
    a transaction-local table for the bounded recompute; no list is returned.
    """
    now = datetime.now(UTC)
    new_rows: list[dict[str, object]] = []
    stage_rows: list[dict[str, object]] = []
    inserted_ids: list[UUID] = []
    for pending in batch:
        # The parsed values are already coerced to the column types, so they go
        # to `values()` as-is; reference labels become catalog ids here.
        values: dict[str, object] = {}
        for field, value in pending.parsed.values.items():
            # The asset is a foreign key on the row, not a plan column.
            if field not in _STAGE_COLUMNS:
                continue
            if value is None:
                # Explicit empty cell: overwrite the old value with NULL.
                values[field] = None
            elif field in _CATALOG_BY_FIELD:
                resolved = _resolve_reference(field, value, reference_ids)
                if resolved is value:
                    raise _unknown_reference(pending.parsed.code, field, value)
                values[field] = resolved
            elif field in _DATE_FIELDS:
                # The parser's contract is ISO text; the column is DATE. An
                # empty cell stays NULL.
                values[field] = date.fromisoformat(str(value)) if value else None
            else:
                values[field] = value
        if pending.is_new:
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
                    "work_or_repair_code": pending.parsed.code,
                    **values,
                }
            )
            inserted_ids.append(row_id)
            continue
        # Values absent from the FILE HEADER keep their current plan values;
        # blank cells inside the row are present as None and overwrite.
        stage_rows.append(
            {
                "work_or_repair_code": pending.parsed.code,
                "asset_id": pending.asset_id,
                "updated_at": now,
                "updater_user_id": user_id,
                **values,
            }
        )
    if new_rows:
        # One multi-row VALUES clause, so every dict must carry the SAME keys:
        # the parser drops blank cells, which makes real rows ragged. Pad them
        # to the batch's union — an omitted cell is a NULL on a created row.
        columns = sorted(set().union(*(row.keys() for row in new_rows)))
        await session.exec(
            insert(RenovationPlanRow).values([{column: row.get(column) for column in columns} for row in new_rows])
        )
        await session.execute(text(_WRITTEN_INSERT_SQL), [{"id": row_id} for row_id in inserted_ids])  # ty: ignore[deprecated]
    if stage_rows:
        # One batched INSERT into the transaction-local staging table; the
        # target rows are updated by ONE UPDATE ... FROM after the last batch.
        await session.execute(insert(_stage_table), stage_rows)  # ty: ignore[deprecated]


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

    These are the very rows the preflight checks the file against. Reading the
    static seed list instead meant every option the operator added after the
    seed was unusable: the preflight said «ready» and the load then refused the
    whole file with "значение не найдено в справочнике".
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
    "equipment_criticality_group_id": "criticality",
    "work_category_id": "work_category",
    "work_type_id": "work_type",
    "cost_allocation_type_id": "cost_allocation",
    "execution_method_id": "execution_method",
}
