"""
Short-lived in-process cache of the renovation reference catalogue.

Why only this one: the labels are a tiny, global, rarely-written table read on
nearly every plan request, so caching them is a pure win. The plan's row counts
are deliberately NOT cached — measuring showed a correctness-preserving version
token over 306k rows costs ~137 ms, i.e. MORE than the ~68 ms filtered count it
would protect, and the cheaper tokens (`r_plans.updated_at`) are not written by
every mutation path (a bulk import, an undo replay or a direct SQL fix would
silently serve a stale count). A cache that can be stale is worse than no cache.

The entry is keyed by the catalogue's own `count(*)` and `max(updated_at)`, so
renaming or adding an option misses on the next read: the invalidation cannot be
forgotten, because it follows the data instead of a call site.

`lru_cache` is per process; every worker keeps its own copy, which is fine for a
read-mostly table — the token comes from the database, so all copies agree.
"""

from collections import OrderedDict
from typing import cast

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.renovation.plan import RenovationReferenceValue

_MAX_ENTRIES = 8


def _store(key: object, value: object) -> None:
    _ENTRIES[key] = value
    while len(_ENTRIES) > _MAX_ENTRIES:
        _ENTRIES.popitem(last=False)


def clear_renovation_cache() -> None:
    """Drop the cached catalogue (tests, and a manual rescue hatch)."""
    _ENTRIES.clear()


_ENTRIES: OrderedDict[object, object] = OrderedDict()


async def reference_labels(session: AsyncSession) -> dict[object, str]:
    """
    Return `{option id: label}` of the renovation reference catalogue.

    The catalogue is small (≈80 rows) and read on nearly every plan request,
    where the round trip costs more than the labels themselves. The entry is
    keyed by the catalogue's own row count and latest `updated_at`, so a
    rename, an added option or a deletion misses on the very next read.
    """
    token = (
        await session.exec(
            select(func.count(col(RenovationReferenceValue.id)), func.max(col(RenovationReferenceValue.updated_at)))
        )
    ).one()
    key = ("labels", int(token[0]), token[1])
    cached = _ENTRIES.get(key)
    if cached is not None:
        return cast("dict[object, str]", cached)
    rows = (await session.exec(select(RenovationReferenceValue))).all()
    labels: dict[object, str] = {value.id: value.label for value in rows}
    _store(key, labels)
    return labels
