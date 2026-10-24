"""
The renovation reference catalogue, cached in-process.

The labels are a tiny (~80 rows), global, rarely-written table read on nearly
every plan request, so caching them is a pure win; the general mechanism (and
why only such tables qualify) lives in `src/common/read_cache.py`.

The entry is keyed by the catalogue's own `count(*)` and `max(updated_at)`, so
renaming or adding an option misses on the next read: the invalidation cannot be
forgotten, because it follows the data instead of a call site.
"""

from uuid import UUID

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.read_cache import cached
from src.common.read_cache import clear_read_cache_for as _clear_namespace
from src.renovation.plan import RenovationReferenceValue

_NAMESPACE = "renovation.reference_labels"


def clear_renovation_cache() -> None:
    """Drop the cached catalogue (tests, and a manual rescue hatch)."""
    _clear_namespace(_NAMESPACE)


async def reference_labels(session: AsyncSession) -> dict[UUID, str]:
    """Return `{option id: label}` of the renovation reference catalogue."""
    token = (
        await session.exec(
            select(
                func.count(col(RenovationReferenceValue.id)),
                func.max(col(RenovationReferenceValue.updated_at)),
            )
        )
    ).one()

    async def load() -> dict[UUID, str]:
        rows = (await session.exec(select(RenovationReferenceValue))).all()
        return {value.id: value.label for value in rows}

    return await cached(namespace=_NAMESPACE, token=(int(token[0]), token[1]), loader=load)
