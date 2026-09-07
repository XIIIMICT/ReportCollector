"""Retry committed notifications; failed delivery never reverses a saved edit."""

import asyncio
import logging

from sqlalchemy import text
from sqlmodel import col, select

from src.collab.models import OutboxEvent
from src.collab.persistence import JournalStore
from src.collab.store import get_collab_store
from src.database import async_session_factory

logger = logging.getLogger("reportcollector.outbox")


async def dispatch_once() -> None:
    async with async_session_factory() as session:
        # One dispatcher across workers preserves event order; never hold up edits.
        locked = (
            await (await session.connection()).execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || '/collab-outbox', 0))")
            )
        ).scalar_one()
        if not locked:
            return
        events = (await session.exec(select(OutboxEvent).order_by(col(OutboxEvent.id)).limit(256))).all()
        for event in events:
            await get_collab_store().publish(event.room, event.payload)
            await session.delete(event)
        await session.commit()


async def run_outbox() -> None:
    ticks = 0
    while True:
        try:
            await dispatch_once()
            if ticks % 300 == 0:
                async with async_session_factory() as session:
                    await JournalStore(session).prune_expired()
                    await session.commit()
            ticks += 1
        except Exception:
            logger.exception("Committed collaboration notifications await retry")
            await asyncio.sleep(1)
        await asyncio.sleep(0.05)
