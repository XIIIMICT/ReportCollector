"""The shared in-process read cache: keyed by a data token, not by a timer."""

from collections.abc import Awaitable, Callable, Iterator

import pytest

from src.common.read_cache import cached, clear_read_cache


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    clear_read_cache()
    yield
    clear_read_cache()


async def test_a_hit_does_not_reload() -> None:
    calls = 0

    async def load() -> list[str]:
        nonlocal calls
        calls += 1
        return ["a", "b"]

    first = await cached(namespace="t", token=(1, None), loader=load)
    second = await cached(namespace="t", token=(1, None), loader=load)
    assert first == second == ["a", "b"]
    assert calls == 1


async def test_a_new_token_misses() -> None:
    """A rename/insert/delete changes the token, so the next read reloads."""
    calls = 0

    async def load() -> list[str]:
        nonlocal calls
        calls += 1
        return [f"v{calls}"]

    await cached(namespace="t", token=(1, None), loader=load)
    await cached(namespace="t", token=(2, None), loader=load)
    assert calls == 2


async def test_namespaces_never_share_an_entry() -> None:
    """Equal tokens in two caches are two different values."""
    calls = 0

    async def load() -> str:
        nonlocal calls
        calls += 1
        return f"n{calls}"

    first = await cached(namespace="a", token=(1,), loader=load)
    second = await cached(namespace="b", token=(1,), loader=load)
    assert (first, second) == ("n1", "n2")
    assert calls == 2


async def test_the_cache_is_bounded() -> None:
    """Old entries are evicted, so an unbounded key space cannot grow forever."""
    loaded: list[int] = []

    def loader_for(index: int) -> Callable[[], Awaitable[int]]:
        async def load() -> int:
            loaded.append(index)
            return index

        return load

    for index in range(12):
        await cached(namespace="t", token=(index,), loader=loader_for(index))
    # The first entries are gone: reading them again reloads.
    await cached(namespace="t", token=(0,), loader=loader_for(0))
    assert loaded.count(0) == 2
