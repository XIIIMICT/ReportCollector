"""
In-process cache for small, rarely-written tables that sit on a hot read path.

The rule this module exists to enforce: **cache only what is read on nearly
every request AND has a cheap, correct token of its own change.** Both halves
matter:

- caching a table read once per page load buys nothing and costs a staleness
  risk — the asset/delivery-base/КППК dictionaries are exactly that case, so
  they are deliberately NOT cached here;
- a cache whose invalidation is wired by hand at every call site gets forgotten.
  The renovation plan tried to cache row counts and gave them up: a token that
  stays correct across every write path (bulk import, undo replay, direct SQL)
  cost more than the count it protected, and the cheap timer it replaced was not
  written by all those paths — a stale count reached the UI.

So entries are keyed by a TOKEN the caller computes from the data itself
(`count(*)` + `max(updated_at)` of the table being cached): rename, insert or
delete changes the token, the next read misses, and no call site has to
remember to invalidate anything. A timer would silently serve stale rows; a
token cannot.

The cache is per PROCESS (one dict per worker). That is correct rather than
merely acceptable: the token comes from the database, so every worker's copy
holds the same data, and a write in one worker misses in the others on their
next read.
"""

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import cast

# Enough for every cacheable table of a project plus headroom for a token
# change racing a read; entries are tiny (a few dozen rows each).
_MAX_ENTRIES = 8

_ENTRIES: OrderedDict[tuple[object, ...], object] = OrderedDict()


async def cached[Value](
    *,
    namespace: str,
    token: tuple[object, ...],
    loader: Callable[[], Awaitable[Value]],
) -> Value:
    """
    Return the value for `token`, loading it on a miss.

    `namespace` separates independent caches (one per table or query shape), so
    two callers with equal tokens never share an entry. `loader` runs only on a
    miss and must return the value the token describes.
    """
    key = (namespace, *token)
    hit = _ENTRIES.get(key)
    if hit is not None:
        # Refresh the LRU position: a hot entry must not be evicted by a cold one.
        _ENTRIES.move_to_end(key)
        return cast("Value", hit)
    value = await loader()
    _ENTRIES[key] = value
    while len(_ENTRIES) > _MAX_ENTRIES:
        _ENTRIES.popitem(last=False)
    return value


def clear_read_cache() -> None:
    """Drop every cached entry (tests, and a manual rescue hatch)."""
    _ENTRIES.clear()


def clear_read_cache_for(namespace: str) -> None:
    """Drop the entries of one namespace (a targeted reset from a test)."""
    for entry in [entry for entry in _ENTRIES if entry[0] == namespace]:
        del _ENTRIES[entry]
