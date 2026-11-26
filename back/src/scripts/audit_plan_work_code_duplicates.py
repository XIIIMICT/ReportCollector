"""
Report duplicate work/repair codes inside renovation plan versions.

Read-only by construction: the session is put into a PostgreSQL READ ONLY
transaction and the script issues SELECT statements only. It never deletes,
updates, seeds or runs a migration. Default output is a plain-text report;
`--format json` emits the same data for automation and `--dry-run` adds commented
remediation SQL that is printed, NEVER executed.

    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates
    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates --format json
    uv run --directory back -m src.scripts.audit_plan_work_code_duplicates --dry-run

Exit status 0 = no duplicates (safe to apply migration 0042), 1 = duplicates
found (migration would fail). Backup and remediation procedure: docs/DATA-JOBS.md,
«Work-code uniqueness rollout».
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import text

from src.database import async_engine, async_session_factory

# Grouped on the TARGET plan only: a code repeated across different versions is
# expected and must not be reported. NULL codes are excluded (draft rows).
_AUDIT_SQL = """
SELECT r.plan_id,
       r.work_or_repair_code,
       p.state AS plan_state,
       count(*) AS duplicate_count,
       array_agg(r.id ORDER BY r.position, r.id) AS row_ids,
       array_agg(r.position ORDER BY r.position, r.id) AS positions
FROM r_plan_rows AS r
JOIN r_plans AS p ON p.id = r.plan_id
WHERE r.work_or_repair_code IS NOT NULL
  AND (CAST(:plan_id AS uuid) IS NULL OR r.plan_id = CAST(:plan_id AS uuid))
GROUP BY r.plan_id, r.work_or_repair_code, p.state
HAVING count(*) > 1
ORDER BY r.plan_id, r.work_or_repair_code
"""


@dataclass(frozen=True)
class DuplicateGroup:
    """One (plan, code) with more than one row, in position order."""

    plan_id: str
    plan_state: str
    work_or_repair_code: str
    duplicate_count: int
    row_ids: list[str]
    positions: list[int]


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def _remediation_sql(groups: list[DuplicateGroup]) -> list[str]:
    """Printable SQL TEMPLATE: the surviving row is a decision for a human."""
    lines = [
        "-- DRY RUN ONLY — not executed. Back up r_plan_rows first.",
        "-- The first id below is the lowest position, NOT an automatic winner.",
        "-- Choose the surviving row from the real data before running anything.",
    ]
    for group in groups:
        losers = ", ".join(f"'{row_id}'" for row_id in group.row_ids[1:])
        code = _escape_literal(group.work_or_repair_code)
        lines.append(
            # Printed COMMENT only — never executed; ids/codes come from the DB and are escaped.
            f"-- plan {group.plan_id}, code {group.work_or_repair_code}: keep row {group.row_ids[0]}; "  # noqa: S608
            f"DELETE FROM r_plan_rows WHERE id IN ({losers}) "
            f"AND plan_id = '{group.plan_id}' AND work_or_repair_code = '{code}';"
        )
    return lines


def _text_report(groups: list[DuplicateGroup]) -> list[str]:
    lines = [
        f"duplicate groups: {len(groups)}",
        f"rows involved: {sum(group.duplicate_count for group in groups)}",
        "read_only: True",
        f"migration 0042: {'BLOCKED — clean the duplicates first' if groups else 'ready'}",
    ]
    for group in groups:
        lines.append(
            f"plan={group.plan_id} state={group.plan_state} code={group.work_or_repair_code!r} "
            f"count={group.duplicate_count}"
        )
        lines.append(f"    row_ids={group.row_ids}")
        lines.append(f"    positions={group.positions}")
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Только для чтения: дубли кодов работ внутри одной версии плана реновации."
    )
    parser.add_argument("--plan-id", default=None, help="Ограничить отчёт одной версией плана (UUID).")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Формат отчёта.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Добавить ЗАКОММЕНТИРОВАННЫЕ SQL-подсказки для исправления (не выполняются).",
    )
    args = parser.parse_args()
    plan_id = UUID(args.plan_id) if args.plan_id else None

    try:
        async with async_session_factory() as session:
            # Enforced server-side: a stray write in this script fails loudly.
            await session.execute(text("SET TRANSACTION READ ONLY"))  # ty: ignore[deprecated]
            rows = (await session.execute(text(_AUDIT_SQL), {"plan_id": plan_id})).all()  # ty: ignore[deprecated]
    finally:
        await async_engine.dispose()

    groups = [
        DuplicateGroup(
            plan_id=str(row_plan_id),
            plan_state=str(state),
            work_or_repair_code=str(code),
            duplicate_count=int(count),
            row_ids=[str(value) for value in row_ids],
            positions=[int(value) for value in positions],
        )
        for row_plan_id, code, state, count, row_ids, positions in rows
    ]
    if args.format == "json":
        report = {
            "read_only": True,
            "migration_0042": "blocked" if groups else "ready",
            "duplicates": [asdict(group) for group in groups],
            "duplicate_groups": len(groups),
            "rows_involved": sum(group.duplicate_count for group in groups),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for line in _text_report(groups):
            print(line)
    if args.dry_run and groups:
        print()
        for line in _remediation_sql(groups):
            print(line)
    raise SystemExit(1 if groups else 0)


if __name__ == "__main__":
    asyncio.run(main())
