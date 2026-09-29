"""
Recompute and store the renovation plan's calculated fields and renovation IDs.

The importer skips calculated fields and `read_plan_rows` never materializes, so
imported/adopted rows carry no derived values until they are edited. Run this
once after importing a plan and whenever the formula definitions change:

    uv run --directory back -m src.scripts.recompute_renovation_plan

Production (PostgreSQL 14) needs no schema change — this only rewrites data.
"""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import text

from src.database import async_engine, async_session_factory
from src.renovation.service import recompute_plan_rows


async def main() -> None:
    parser = argparse.ArgumentParser(description="Пересчитать и сохранить расчётные поля плана реновации.")
    parser.add_argument(
        "--plan-id",
        default=None,
        help="Идентификатор версии плана (по умолчанию — текущий рабочий план).",
    )
    args = parser.parse_args()
    plan_id = UUID(args.plan_id) if args.plan_id else None
    try:
        async with async_session_factory() as session:
            connection = await session.connection()
            await connection.execute(text("SET LOCAL lock_timeout = '30s'"))
            result = await recompute_plan_rows(session, plan_id=plan_id)
            await session.commit()
    finally:
        await async_engine.dispose()
    print(f"План {result.plan_id}: проверено строк={result.scanned}, пересчитано={result.changed}.")


if __name__ == "__main__":
    asyncio.run(main())
