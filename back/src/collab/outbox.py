"""Retry committed notifications; failed delivery never reverses a saved edit."""

import asyncio
import logging
from time import monotonic

from sqlalchemy import text
from sqlmodel import col, select

from src.collab.models import OutboxEvent
from src.collab.persistence import JournalStore
from src.collab.store import get_collab_store
from src.database import async_session_factory

logger = logging.getLogger("reportcollector.outbox")


async def dispatch_once() -> bool:
    """Publish one bounded batch and report whether work was found."""
    async with async_session_factory() as session:
        # One dispatcher across workers preserves event order; never hold up edits.
        locked = (
            await (await session.connection()).execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || '/collab-outbox', 0))")
            )
        ).scalar_one()
        if not locked:
            return False
        events = (await session.exec(select(OutboxEvent).order_by(col(OutboxEvent.id)).limit(256))).all()
        for event in events:
            await get_collab_store().publish(event.room, event.payload)
            await session.delete(event)
        await session.commit()
        return bool(events)


async def run_outbox() -> None:
    next_prune = 0.0
    idle_delay = 0.05
    while True:
        try:
            had_work = await dispatch_once()
            if monotonic() >= next_prune:
                async with async_session_factory() as session:
                    await JournalStore(session).prune_expired()
                    await session.commit()
                next_prune = monotonic() + 15
        except Exception:
            logger.exception("Committed collaboration notifications await retry")
            idle_delay = 0.05
            await asyncio.sleep(1)
            continue
        # Avoid a database round-trip every 50 ms while retaining a bounded
        # retry so a notification cannot remain stuck indefinitely.
        idle_delay = 0.05 if had_work else min(idle_delay * 2, 1.0)
        await asyncio.sleep(idle_delay)
