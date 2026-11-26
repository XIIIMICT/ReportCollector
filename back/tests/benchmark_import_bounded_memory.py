"""
Before/after measurements for the bounded renovation import.

Run from the repo root:

    uv run --directory back python -m tests.benchmark_import_bounded_memory inspect --rows 20000
    uv run --directory back python -m tests.benchmark_import_bounded_memory apply --rows 5000

`inspect` needs no database. `apply` uses a DEDICATED throwaway schema
(`IMPORT_BENCH_SCHEMA`, default `rc_bench`) on `IMPORT_BENCH_DATABASE_URL`
(default `postgresql+asyncpg://postgres:strong_password@127.0.0.1:5432/dwh`).
It never touches production data and drops/recreates only that schema.

Reported numbers are from a SYNTHETIC workbook/plan on one developer machine,
not production measurements. Only the phases actually exercised are reported;
phases that need a real worker supervisor are explicitly listed as not measured.
"""

import argparse
import asyncio
import ctypes
import json
import os
import sys
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

os.environ.setdefault("AUTH_SECRET", "bench-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("AUTH_IDENTITY_TTL", "3600")
os.environ.setdefault("AUTH_SEED_ADMIN_EMAIL", "admin@example.ru")
os.environ.setdefault("AUTH_SEED_ADMIN_PASSWORD", "AdminPassword123")
os.environ.setdefault("BACKEND_CORS_ORIGINS", '["http://localhost:5000"]')
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation import import_apply, import_plan
from src.renovation.constants import R_ASSETS
from src.renovation.import_rows import ParsedPlanRow
from src.renovation.plan import RenovationPlan, RenovationReferenceValue, RPlanState
from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id


def _columns() -> list[str]:
    meta = next(meta for meta in import_plan.field_metas() if meta.db_name == "work_cost_rub_ex_vat")
    return [
        import_plan._ASSET_HEADER,
        import_plan._CODE_HEADER,
        *(label for _, label in import_plan._REQUIRED_COLUMN_LABELS),
        meta.label,
    ]


def _row(number: int, *, invalid: bool, repeat: int | None = None) -> list[object]:
    index = number if repeat is None else number % repeat
    return [
        "BSZR",
        f"BENCH_{index:06d}",
        "1",
        "ТОиР",
        "КР",
        "OPEX",
        "Собственные силы",
        "2027-01-01",
        "2027-01-31",
        "не число" if invalid else "1",
    ]


def make_workbook(rows: int, path: Path, *, repeat: int | None = None) -> None:
    """Synthetic plan-shaped workbook with invalid numeric cells and repeats."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet("РБ ПОФ")
    sheet.append(_columns())
    for number in range(rows):
        sheet.append(_row(number, invalid=True, repeat=repeat))
    book.save(path)
    book.close()


def peak_rss_bytes() -> int | None:
    """Peak process RSS, or None when the platform cannot report it."""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            memory_info = kernel.K32GetProcessMemoryInfo
            memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            memory_info.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            process = kernel.GetCurrentProcess()
            if memory_info(process, ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        except AttributeError, OSError, ValueError:
            return None
        return None
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except ImportError:
        return None


def measure_inspect(rows: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.xlsx"
        generation = time.perf_counter()
        make_workbook(rows, path, repeat=1000)
        generated = time.perf_counter() - generation
        tracemalloc.start()
        started = time.perf_counter()
        result = import_plan.inspect_plan_file_streaming(
            path,
            existing_codes=set(),
            asset_names_by_code={"BSZR": "АО «БСЗ»"},
            reference_values=None,
            suffix=".xlsx",
            issue_limit=1000,
        )
        wall = time.perf_counter() - started
        _current, heap_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return {
        "mode": "inspect",
        "rows": rows,
        "workbook_bytes": path.stat().st_size if path.exists() else None,
        "generation_s": round(generated, 3),
        "wall_s": round(wall, 3),
        "rows_per_second": round(rows / wall, 1),
        "peak_traced_heap_bytes": heap_peak,
        "peak_rss_bytes": peak_rss_bytes(),
        "issues": result.issue_counts,
        "examples_retained": {key: len(getattr(result, key)) for key in result.issue_counts if hasattr(result, key)},
    }


async def _seed(session: AsyncSession) -> UUID:
    from src.auth.models import Project
    from src.renovation.models import RAsset

    now = datetime.now(UTC)
    project = Project(key="renovation", name="Renovation")
    session.add(project)
    await session.flush()
    for _, repair_code, name in R_ASSETS:
        session.add(RAsset(code=repair_code, name=name, created_at=now))
    for catalog, label in REFERENCE_OPTIONS:
        session.add(RenovationReferenceValue(id=reference_option_id(catalog, label), catalog=catalog, label=label))
    await session.flush()
    plan = RenovationPlan(
        project_id=project.id,
        state=RPlanState.WORKING,
        baseline_generation=1,
        created_at=now,
        created_by="bench",
        updated_at=now,
        updater_user_id=None,
    )
    session.add(plan)
    await session.flush()
    return plan.id


async def _apply(rows: int) -> dict[str, Any]:
    database_url = os.environ.get(
        "IMPORT_BENCH_DATABASE_URL", "postgresql+asyncpg://postgres:strong_password@127.0.0.1:5432/dwh"
    )
    schema = os.environ.get("IMPORT_BENCH_SCHEMA", "rc_bench")
    if not schema.startswith("rc_bench") or not schema.replace("_", "").isalnum():
        msg = "IMPORT_BENCH_SCHEMA must be a dedicated rc_bench* schema"
        raise SystemExit(msg)
    bootstrap = create_async_engine(database_url, poolclass=NullPool)
    async with bootstrap.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    await bootstrap.dispose()

    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}, pool_pre_ping=True
    )
    # Import every model module so SQLModel.metadata covers the FKs JournalStore uses.
    import src.auth.models
    import src.collab.models
    import src.common.job_models
    import src.raw_materials.models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def batches(*_args: object) -> object:
        for start in range(0, rows, import_apply._BATCH_SIZE):
            yield [
                ParsedPlanRow(
                    code=f"BENCH_{number:06d}",
                    asset_key="BSZR",
                    values={"workshop_name": f"row {number}"},
                )
                for number in range(start, min(start + import_apply._BATCH_SIZE, rows))
            ]

    original_batches = import_apply.iter_plan_row_batches
    original_lock = import_apply.lock_project
    marks: dict[str, float] = {}

    async def timed_lock(session: AsyncSession, project: str, *, exclusive: bool = False) -> None:
        wait_start = time.perf_counter()
        await original_lock(session, project, exclusive=exclusive)
        marks["project_lock_wait_s"] = time.perf_counter() - wait_start
        marks["project_lock_taken_at"] = time.perf_counter()

    import_apply.iter_plan_row_batches = batches  # ty: ignore[invalid-assignment]
    import_apply.lock_project = timed_lock  # ty: ignore[invalid-assignment]
    tracemalloc.start()
    started = time.perf_counter()
    try:
        async with factory() as session, session.begin():
            plan_id = await _seed(session)
            result = await import_apply.apply_plan_file(
                session,
                source=BytesIO(),
                user_id=None,
                plan_id=plan_id,
                streaming=True,
                expected_rows=rows,
            )
    finally:
        commit_done = time.perf_counter()
        import_apply.iter_plan_row_batches = original_batches
        import_apply.lock_project = original_lock
        _current, heap_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss = peak_rss_bytes()
        await engine.dispose()
        boot = create_async_engine(database_url, poolclass=NullPool)
        async with boot.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await boot.dispose()
    wall = commit_done - started
    return {
        "mode": "apply",
        "rows": rows,
        "wall_s": round(wall, 3),
        "rows_per_second": round(rows / wall, 1),
        "db_connection_hold_s": round(wall, 3),
        "project_lock_wait_s": round(marks.get("project_lock_wait_s", 0), 4),
        "project_lock_hold_s": round(commit_done - marks.get("project_lock_taken_at", commit_done), 3),
        "peak_traced_heap_bytes": heap_peak,
        "peak_rss_bytes": rss,
        "result": {"new": result.new_rows, "updated": result.updated_rows},
        "not_measured": [
            "worker supervisor heartbeat/lease renewal (no durable job manager in this harness)",
            "real XLSB/XLSX bytes in apply mode (rows are fed through the parser seam)",
            "production data, concurrency, network latency, multi-worker contention",
        ],
    }


async def _worker(rows: int) -> dict[str, Any]:
    """Full durable-worker path: parse a real workbook, then mutate under a lease."""
    from src.common.durable_imports import DurableImportManager, input_path
    from src.common.job_repository import JobRepository
    from src.renovation import import_jobs

    database_url = os.environ.get(
        "IMPORT_BENCH_DATABASE_URL", "postgresql+asyncpg://postgres:strong_password@127.0.0.1:5432/dwh"
    )
    schema = os.environ.get("IMPORT_BENCH_SCHEMA", "rc_worker_bench")
    if not schema.startswith("rc_worker_bench") or not schema.replace("_", "").isalnum():
        msg = "IMPORT_BENCH_SCHEMA must be a dedicated rc_worker_bench* schema"
        raise SystemExit(msg)
    boot = create_async_engine(database_url, poolclass=NullPool)
    async with boot.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    await boot.dispose()

    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}, pool_pre_ping=True
    )
    import src.auth.models
    import src.collab.models
    import src.common.job_models
    import src.raw_materials.models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner_id = uuid4()
    async with factory() as session, session.begin():
        plan_id = await _seed(session)
        from src.auth.models import User

        session.add(User(id=owner_id, name="Benchmark"))

    with tempfile.TemporaryDirectory() as directory:
        os.environ["IMPORT_DIR"] = directory
        workbook = Path(directory) / "synthetic.xlsx"
        make_workbook(rows, workbook)
        repository = JobRepository(factory)
        manager = DurableImportManager(
            kind="renovation_import", generate=import_jobs.generate_plan_import, repository=repository
        )
        await manager.create_from_path(
            source=workbook,
            file_name="synthetic.xlsx",
            digest="benchmark",
            size=workbook.stat().st_size,
            user_id=str(owner_id),
            plan_id=plan_id,
            expected_rows=rows,
        )
        claimed = await repository.claim(manager.kind)
        if claimed is None:
            msg = "benchmark job was not claimed"
            raise SystemExit(msg)
        import_job = manager.job_from_record(claimed)
        # No supervisor heartbeat in this harness; keep the lease alive for the
        # whole synthetic run so fencing does not compete with the measurement.
        from src.auth.models import utc_now
        from src.common.job_models import DataJob

        async with factory() as write_session, write_session.begin():
            record = await write_session.get(DataJob, claimed.id)
            if record is not None:
                record.lease_until = utc_now() + timedelta(hours=1)
                write_session.add(record)
        opened: dict[str, float] = {}

        def tracked_factory() -> object:
            opened.setdefault("at", time.perf_counter())
            return factory()

        original_factory = import_jobs.async_session_factory
        original_lock = import_apply.lock_project
        marks: dict[str, float] = {}

        async def timed_lock(session: AsyncSession, project: str, *, exclusive: bool = False) -> None:
            wait_start = time.perf_counter()
            await original_lock(session, project, exclusive=exclusive)
            marks["wait"] = time.perf_counter() - wait_start
            marks["taken"] = time.perf_counter()

        import_jobs.async_session_factory = tracked_factory  # ty: ignore[invalid-assignment]
        import_apply.lock_project = timed_lock  # ty: ignore[invalid-assignment]
        tracemalloc.start()
        started = time.perf_counter()
        try:
            result = await import_jobs.generate_plan_import(import_job, input_path(import_job.input_key))
        finally:
            commit_done = time.perf_counter()
            import_jobs.async_session_factory = original_factory
            import_apply.lock_project = original_lock
            _current, heap_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            rss = peak_rss_bytes()

    await engine.dispose()
    boot = create_async_engine(database_url, poolclass=NullPool)
    async with boot.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    await boot.dispose()
    wall = commit_done - started
    return {
        "mode": "worker",
        "rows": rows,
        "wall_s": round(wall, 3),
        "rows_per_second": round(rows / wall, 1),
        "db_connection_hold_s": round(commit_done - opened.get("at", commit_done), 3),
        "project_lock_wait_s": round(marks.get("wait", 0), 4),
        "project_lock_hold_s": round(commit_done - marks.get("taken", commit_done), 3),
        "peak_traced_heap_bytes": heap_peak,
        "peak_rss_bytes": rss,
        "result": result,
        "not_measured": [
            "worker supervisor heartbeat/lease renewal (generate_plan_import is called directly)",
            "production data, concurrency, network latency, multi-worker contention",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "apply", "worker"))
    parser.add_argument("--rows", type=int, default=20_000)
    args = parser.parse_args()
    if args.mode == "inspect":
        payload = measure_inspect(args.rows)
    else:
        payload = asyncio.run(_apply(args.rows) if args.mode == "apply" else _worker(args.rows))
    print(json.dumps(payload, ensure_ascii=False, indent=2))  # noqa: T201  # benchmark output


if __name__ == "__main__":
    main()
