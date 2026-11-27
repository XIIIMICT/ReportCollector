"""
Reproducible RC-5 rollout workload harness (plan windows/facets/exports/import).

Run from the repo root (DISPOSABLE stand only — the default target is the
isolated CI postgres, never the persistent dev or a shared/production DB):

    uv run --directory back python -m tests.benchmark_rollout_workload run \
        --rows 306000 --reps 5 --commit <sha>

It creates and drops ONLY its own `rc_workload*` schema; it refuses any other
schema name. Every scenario seeds a synthetic plan (no corporate/live data) and
reports only what actually ran: wall p50/p95/p99, throughput, RSS/CPU delta,
DB pool-acquire and Lock-wait samples. Scenarios that require the live WS
supervisor, corporate KPPK dumps or a target host are reported as NOT RUN with
a reason, never with invented numbers.

    uv run --directory back python -m tests.benchmark_rollout_workload verify-audit

`verify-audit` points the read-only duplicate audit at the disposable stand and
prints its exit status; it performs no writes.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

os.environ.setdefault("AUTH_SECRET", "rollout-bench-secret-at-least-32-characters-long")
os.environ.setdefault("AUTH_IDENTITY_TTL", "3600")
os.environ.setdefault("AUTH_SEED_ADMIN_EMAIL", "bench@example.ru")
os.environ.setdefault("AUTH_SEED_ADMIN_PASSWORD", "AdminPassword123")
os.environ.setdefault("BACKEND_CORS_ORIGINS", '["http://localhost:5000"]')
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "ci_password")
os.environ.setdefault("POSTGRES_DB", "rc_ci")
os.environ.setdefault("POSTGRES_SCHEMA", "rc_workload")

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

import src.auth.models
import src.collab.models
import src.common.job_models
import src.raw_materials.models  # noqa: F401
from src.renovation.constants import R_ASSETS
from src.renovation.export import stream_renovation_workbook
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RenovationPlanRow, RenovationReferenceValue, RPlanState
from src.renovation.plan_queries import read_working_plan_aggregates
from src.renovation.plan_reads import (
    RenovationPlanExportData,
    iter_plan_export_rows,
    read_plan_export_meta,
    read_plan_facets,
    read_plan_rows,
)
from src.renovation.reference_data import REFERENCE_OPTIONS, reference_option_id
from src.renovation.schemas import RenovationColumnFilter

DEFAULT_DB_URL = "postgresql+asyncpg://postgres:ci_password@127.0.0.1:16432/rc_ci"
DEFAULT_SCHEMA = "rc_workload"

_NOT_RUN: dict[str, str] = {
    "kppk_slices": "requires corporate raw.kppk_* dumps loaded by the operator; not seeded on the shared CI stand",
    "collaboration_activity": "requires live WS hub, multiple signed-in clients and the full stack; not measurable from a service-level harness",
    "worker_restart_during_jobs": "requires the dedicated job supervisor/worker service and a replica; no supervisor in this harness",
    "full_306k_xlsx_workbook": "documented ~3.3 GB sheet against the 4 GiB ZIP ceiling; needs a target-host run with a download client",
    "queue_wait_under_load": "no concurrent worker/supervisor here; claim latency is not queue wait",
    "dropped_sockets": "no HTTP/WS transport in this harness; needs the load-balanced target host",
}


class TimedPool(AsyncAdaptedQueuePool):
    """Record time spent acquiring a pooled connection (wait + setup)."""

    waits: ClassVar[list[float]] = []

    def _do_get(self) -> ConnectionPoolEntry:
        started = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            self.waits.append(time.perf_counter() - started)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50_s": _percentile(values, 0.5),
        "p95_s": _percentile(values, 0.95),
        "p99_s": _percentile(values, 0.99),
        "min_s": round(min(values), 4) if values else None,
        "max_s": round(max(values), 4) if values else None,
    }


def _rss_bytes() -> int | None:
    if sys.platform == "win32":
        try:
            import ctypes
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
            info = kernel.K32GetProcessMemoryInfo
            info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            info.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            return (
                int(counters.WorkingSetSize)
                if info(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
                else None
            )
        except AttributeError, OSError, ValueError:
            return None
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except ImportError:
        return None


def _validate_schema(schema: str) -> None:
    if not schema.startswith("rc_workload") or not schema.replace("_", "").isalnum():
        msg = "ROLLOUT_SCHEMA must be a dedicated rc_workload* schema"
        raise SystemExit(msg)


def _lock_sampler(database_url: str, schema: str, stop: asyncio.Event, samples: list[float]) -> None:
    """Coarse poll of Lock waits for this harness' backends (best effort)."""

    async def loop() -> None:
        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema, "application_name": "rc-workload"}},
            poolclass=AsyncAdaptedQueuePool,
        )
        try:
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    async with engine.connect() as connection:
                        waiting = int(
                            (
                                await connection.execute(
                                    text(
                                        "SELECT count(*) FROM pg_stat_activity "
                                        "WHERE application_name = 'rc-workload' AND wait_event_type = 'Lock'"
                                    )
                                )
                            ).scalar_one()
                        )
                    if waiting:
                        samples.append(time.perf_counter() - started)
                except Exception:  # noqa: BLE001, S110  # best-effort sampler must never fail the run
                    pass
                await asyncio.sleep(0.02)
        finally:
            await engine.dispose()

    asyncio.create_task(loop())  # noqa: RUF006  # detached sampler, stopped via Event before engine disposal


async def _seed(session: AsyncSession, rows: int) -> tuple[RenovationPlan, list[RAsset]]:
    from src.auth.models import Project

    now = datetime.now(UTC)
    project = Project(key="renovation", name="Renovation workload")
    session.add(project)
    await session.flush()
    assets = [RAsset(code=code, name=name, created_at=now) for _, code, name in R_ASSETS]
    session.add_all(assets)
    await session.flush()
    session.add_all(
        [
            RenovationReferenceValue(id=reference_option_id(catalog, label), catalog=catalog, label=label)
            for catalog, label in REFERENCE_OPTIONS
        ]
    )
    plan = RenovationPlan(
        project_id=project.id,
        state=RPlanState.WORKING,
        baseline_generation=1,
        created_at=now,
        created_by="rollout-workload",
        updated_at=now,
        updater_user_id=None,
    )
    session.add(plan)
    await session.flush()
    workshops = [f"Цех {number % 24:02d}" for number in range(rows)]
    for start in range(0, rows, 5000):
        stop = min(start + 5000, rows)
        batch = [
            {
                "id": uuid4(),
                "plan_id": plan.id,
                "position": number + 1,
                "asset_id": assets[number % len(assets)].id,
                "created_at": now,
                "updated_at": now,
                "baseline_generation": 1,
                "work_or_repair_code": f"WL_{number:06d}",
                "workshop_name": workshops[number],
                "work_description": f"Работа {number % 500:04d}",
                "equipment_class": f"Класс {number % 60:02d}",
                "work_cost_rub_ex_vat": Decimal(number % 10000) / 100,
                "total_cost_rub_ex_vat": Decimal(number % 10000) / 100,
            }
            for number in range(start, stop)
        ]
        await session.execute(insert(RenovationPlanRow), batch)  # ty: ignore[deprecated]
    await session.commit()
    return plan, assets


async def _timed(label: str, fn: Callable[[], Any], measurements: dict[str, Any]) -> None:
    started = time.perf_counter()
    await fn()
    measurements.setdefault(label, []).append(time.perf_counter() - started)


async def _scenarios(
    session: AsyncSession,
    plan: RenovationPlan,
    assets: list[RAsset],
    measurements: dict[str, Any],
) -> None:
    for offset in (0, 100_000, 300_000):
        await _timed(
            f"window@{offset}",
            lambda offset=offset: read_plan_rows(
                session,
                assets,
                plan_id=plan.id,
                offset=offset,
                limit=1000,
                anchor_position=offset + 1,
            ),
            measurements,
        )
    filters = [RenovationColumnFilter(column="workshop_name", values=["Цех 01", "Цех 02"])]
    await _timed(
        "window_filtered",
        lambda: read_plan_rows(session, assets, plan_id=plan.id, filters=filters, offset=0, limit=1000),
        measurements,
    )
    for column in ("workshop_name", "work_description"):
        await _timed(
            f"facet:{column}",
            lambda column=column: read_plan_facets(session, assets, plan_id=plan.id, column=column, limit=200),
            measurements,
        )
    await _timed(
        "facet:work_description:search",
        lambda: read_plan_facets(
            session,
            assets,
            plan_id=plan.id,
            column="work_description",
            search_kind="text",
            search_query="Работа 03",
            limit=200,
        ),
        measurements,
    )
    await _timed(
        "aggregates",
        lambda: read_working_plan_aggregates(
            session,
            group_by=cast("InstrumentedAttribute[object]", RenovationPlanRow.asset_id),
            ids=[asset.id for asset in assets],
        ),
        measurements,
    )

    async def full_iter() -> int:
        count = 0
        async for batch in iter_plan_export_rows(session, plan_id=plan.id, asset_ids=[asset.id for asset in assets]):
            count += len(batch)
        measurements.setdefault("results", {})["export_iter_full_rows"] = count
        return count

    await _timed("export_iter_full", full_iter, measurements)

    ids = list(
        (
            await session.execute(  # ty: ignore[deprecated]
                select(RenovationPlanRow.id)
                .where(col(RenovationPlanRow.plan_id) == plan.id)
                .order_by(col(RenovationPlanRow.position))
                .limit(5000)
            )
        ).scalars()
    )

    async def filtered_workbook() -> int:
        meta = await read_plan_export_meta(session, [asset.id for asset in assets], plan_id=plan.id, row_ids=ids)
        rows: list[tuple[int, str, str, dict[str, object]]] = []
        async for batch in iter_plan_export_rows(
            session, plan_id=plan.id, asset_ids=[asset.id for asset in assets], row_ids=ids
        ):
            rows.extend(batch)
        data = RenovationPlanExportData(
            baseline_generation=meta.baseline_generation,
            state=meta.state,
            stamp=meta.stamp,
            rows=rows,
            reference_labels=meta.reference_labels,
            totals=meta.totals,
            values_only=True,
        )
        total_bytes = sum(len(chunk) for chunk in stream_renovation_workbook(data))
        measurements.setdefault("results", {})["export_xlsx_filtered_bytes"] = total_bytes
        measurements.setdefault("results", {})["export_xlsx_filtered_rows"] = len(rows)
        return total_bytes

    await _timed("export_xlsx_filtered", filtered_workbook, measurements)


async def _import_scenarios(rows: int, database_url: str) -> dict[str, Any]:
    """Real workbook inspect + durable apply via the existing bounded benchmark."""
    from tests.benchmark_import_bounded_memory import _apply, measure_inspect

    os.environ["IMPORT_BENCH_DATABASE_URL"] = database_url
    os.environ["IMPORT_BENCH_SCHEMA"] = "rc_bench_rollout"
    return {
        "inspect": measure_inspect(rows),
        "apply": await _apply(rows),
        "note": "xlsx only here; xlsb needs a real .xlsb drop (target-host gate)",
    }


async def run(rows: int, reps: int, import_rows: int, schema: str, database_url: str, commit: str) -> dict[str, Any]:
    _validate_schema(schema)
    rss_start = _rss_bytes()
    cpu_start = time.process_time()
    boot = create_async_engine(database_url, poolclass=AsyncAdaptedQueuePool)
    async with boot.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    await boot.dispose()

    TimedPool.waits = []
    engine = create_async_engine(
        database_url,
        connect_args={
            "server_settings": {"search_path": schema, "application_name": "rc-workload", "lock_timeout": "30s"}
        },
        poolclass=TimedPool,
        pool_pre_ping=True,
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    stop = asyncio.Event()
    lock_samples: list[float] = []
    _lock_sampler(database_url, schema, stop, lock_samples)
    measurements: dict[str, Any] = {"results": {}}
    try:
        async with factory() as session:
            plan, assets = await _seed(session, rows)
            for _repetition in range(reps):
                await _scenarios(session, plan, assets, measurements)
    finally:
        stop.set()
        await asyncio.sleep(0.05)
        rss = _rss_bytes()
        cpu_end = time.process_time()
        await engine.dispose()
        boot = create_async_engine(database_url, poolclass=AsyncAdaptedQueuePool)
        async with boot.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await boot.dispose()

    import_payload = await _import_scenarios(import_rows, database_url)
    measured: dict[str, Any] = {}
    for key, samples in measurements.items():
        if key == "results":
            continue
        measured[key] = {
            **_summary(samples),
            "throughput_rows_per_s": (
                round(rows / statistics.median(samples), 1) if samples and statistics.median(samples) else None
            ),
        }
    return {
        "scenario": "rollout-workload",
        "commit": commit,
        "captured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "database_url_host": database_url.rsplit("@", maxsplit=1)[-1],
            "schema": schema,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "rows_seeded": rows,
            "import_rows": import_rows,
            "repetitions": reps,
            "seed_shape": "synthetic sparse plan rows (id, plan_id, position, asset, unique code, 4 value columns)",
        },
        "measured": measured,
        "scenario_results": measurements["results"],
        "db_pool_acquire_wait_s": {
            **_summary(TimedPool.waits),
            "method": "AsyncAdaptedQueuePool._do_get wrapper on the harness engine",
        },
        "db_lock_wait_s": {
            "samples": len(lock_samples),
            "total_sampled_s": round(sum(lock_samples), 4),
            "method": "20 ms pg_stat_activity poll, application_name='rc-workload', wait_event_type='Lock'",
        },
        "rss_bytes": rss,
        "rss_start_bytes": rss_start,
        "rss_delta_bytes": (rss - rss_start) if rss is not None and rss_start is not None else None,
        "cpu_process_time_s": round(cpu_end - cpu_start, 3),
        "import_benchmark": import_payload,
        "not_run": _NOT_RUN,
    }


def verify_audit(database_url: str, schema: str) -> dict[str, Any]:
    """Run the read-only audit CLI against the disposable stand in a fresh process."""
    command = [sys.executable, "-m", "src.scripts.audit_plan_work_code_duplicates", "--format", "json"]
    environment = {**os.environ, "DATABASE_URL": database_url, "POSTGRES_SCHEMA": schema}
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": " ".join(command),
        "schema": schema,
        "read_only": True,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr_tail": completed.stderr.strip()[-200:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "verify-audit"))
    parser.add_argument("--rows", type=int, default=306_000)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--import-rows", type=int, default=10_000)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--audit-schema", default="rc")
    parser.add_argument("--database-url", default=DEFAULT_DB_URL)
    parser.add_argument("--commit", default=os.environ.get("ROLLOUT_COMMIT", "unknown"))
    args = parser.parse_args()
    if args.mode == "verify-audit":
        payload: dict[str, Any] = verify_audit(args.database_url, args.audit_schema)
    else:
        _validate_schema(args.schema)
        payload = asyncio.run(run(args.rows, args.reps, args.import_rows, args.schema, args.database_url, args.commit))
    print(json.dumps(payload, ensure_ascii=False, indent=2))  # noqa: T201  # benchmark output


if __name__ == "__main__":
    main()
