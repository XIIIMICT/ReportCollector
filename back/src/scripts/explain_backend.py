"""
Capture repeatable read-only query plans from the real grid/export paths.

Run with --analyze to execute SELECTs; otherwise only estimate plans. Output
contains aggregate plan statistics, never row data, SQL literals or identity.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.database import async_engine
from src.renovation.models import RAsset
from src.renovation.plan import RenovationPlan, RPlanState
from src.renovation.service import read_plan_export_meta, read_plan_facets, read_plan_rows


def summarize(node: dict[str, Any]) -> dict[str, Any]:
    """Keep costs/rows/buffers and index names; omit predicates/data literals."""
    keys = (
        "Node Type",
        "Relation Name",
        "Index Name",
        "Plan Rows",
        "Plan Width",
        "Actual Rows",
        "Actual Loops",
        "Actual Total Time",
        "Total Cost",
        "Shared Hit Blocks",
        "Shared Read Blocks",
        "Temp Read Blocks",
        "Temp Written Blocks",
        "Rows Removed by Filter",
        "Sort Method",
        "Sort Space Used",
        "Sort Space Type",
    )
    result = {key: node[key] for key in keys if key in node}
    if "Plans" in node:
        result["Plans"] = [summarize(child) for child in node["Plans"]]
    return result


async def capture(*, analyze: bool, output: Path) -> None:
    statements: list[tuple[str, str, tuple[Any, ...] | dict[str, Any] | None]] = []
    operation = "window"

    def record(
        _conn: object,
        _cursor: object,
        statement: str,
        parameters: tuple[Any, ...] | dict[str, Any] | None,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((operation, statement, parameters))

    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text("SET LOCAL statement_timeout = '60s'"))
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                assets = list((await session.exec(select(RAsset))).all())
                plan = (
                    await session.exec(select(RenovationPlan).where(RenovationPlan.state == RPlanState.WORKING))
                ).one()
                event.listen(conn.sync_connection, "before_cursor_execute", record)
                try:
                    window = await read_plan_rows(session, assets, plan_id=plan.id, limit=100)
                    operation = "window_deep"
                    await read_plan_rows(session, assets, plan_id=plan.id, offset=max(0, window.total // 2), limit=100)
                    operation = "facet_text"
                    await read_plan_facets(session, assets, plan_id=plan.id, column="col_004", limit=50)
                    operation = "facet_date"
                    await read_plan_facets(session, assets, plan_id=plan.id, column="col_024", limit=50)
                    operation = "export_metadata"
                    await read_plan_export_meta(session, [asset.id for asset in assets], plan_id=plan.id)
                finally:
                    event.remove(conn.sync_connection, "before_cursor_execute", record)
                plans = []
                # Metadata queries repeat between workflows. Preserve counts per
                # operation while explaining each distinct SQL statement once.
                counts: dict[str, int] = {}
                seen: set[str] = set()
                for name, sql, params in statements:
                    counts[name] = counts.get(name, 0) + 1
                    signature = f"{name}:{sql}"
                    if signature in seen:
                        continue
                    seen.add(signature)
                    options = "ANALYZE, BUFFERS, FORMAT JSON" if analyze else "FORMAT JSON"
                    raw = (await conn.exec_driver_sql(f"EXPLAIN ({options}) {sql}", params)).scalar_one()
                    explained = json.loads(raw) if isinstance(raw, str) else raw
                    root = explained[0]
                    plans.append(
                        {
                            "operation": name,
                            "planning_ms": root.get("Planning Time"),
                            "execution_ms": root.get("Execution Time"),
                            "plan": summarize(root["Plan"]),
                        }
                    )
                report = {
                    "captured_at": datetime.now(UTC).isoformat(),
                    "analyze": analyze,
                    "working_plan_rows": window.total,
                    "assets": len(assets),
                    "query_counts": counts,
                    "plans": plans,
                }
                await asyncio.to_thread(
                    output.write_text,
                    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                print(f"Captured {len(plans)} distinct plans; working rows: {window.total}; output: {output}")
            await conn.rollback()
    finally:
        await async_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", action="store_true", help="Execute SELECTs to measure rows/time/buffers")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(capture(analyze=args.analyze, output=args.output))


if __name__ == "__main__":
    main()
