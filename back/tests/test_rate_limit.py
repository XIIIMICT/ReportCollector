"""Rate-limiter client-key resolution and bounded-store sweep (AUD-10)."""

import time

from starlette.requests import Request

from src import main


def _request(headers: dict[str, str], peer: str = "10.0.0.1") -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": raw_headers,
            "client": (peer, 12345),
            "query_string": b"",
        }
    )


def test_client_key_ignores_forwarded_for_by_default(monkeypatch) -> None:
    """With no trusted proxies the socket peer wins, so XFF cannot spoof."""
    monkeypatch.setattr(main.settings, "trusted_proxy_hops", 0)
    request = _request({"x-forwarded-for": "1.2.3.4"}, peer="10.0.0.1")
    assert main._client_key(request) == "10.0.0.1"


def test_client_key_uses_the_trusted_hop(monkeypatch) -> None:
    """Behind one trusted proxy the client is the rightmost XFF entry."""
    monkeypatch.setattr(main.settings, "trusted_proxy_hops", 1)
    assert main._client_key(_request({"x-forwarded-for": "1.2.3.4"})) == "1.2.3.4"
    # Two proxies: the client sits one further left, the last entry is our proxy.
    monkeypatch.setattr(main.settings, "trusted_proxy_hops", 2)
    assert main._client_key(_request({"x-forwarded-for": "1.2.3.4, 10.9.9.9"})) == "1.2.3.4"
    # Fewer entries than trusted hops — fall back to the socket peer.
    assert main._client_key(_request({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.1"


def test_sweep_drops_stale_buckets_and_respects_the_cap() -> None:
    """The store cannot grow without bound: expired buckets are swept."""
    now = time.monotonic()
    main._rate_limit_store.clear()

    main._rate_limit_store["fresh"] = [now]
    main._rate_limit_store["stale"] = [now - main._RATE_LIMIT_WINDOW - 5]
    main._sweep_rate_limit(now)
    assert "fresh" in main._rate_limit_store
    assert "stale" not in main._rate_limit_store

    # Over the cap: the oldest buckets are dropped, keeping the newest.
    main._rate_limit_store.clear()
    for index in range(main._RATE_LIMIT_MAX_KEYS + 10):
        main._rate_limit_store[f"key-{index}"] = [now - (main._RATE_LIMIT_MAX_KEYS - index)]
    main._sweep_rate_limit(now)
    assert len(main._rate_limit_store) <= main._RATE_LIMIT_MAX_KEYS
    assert "key-0" not in main._rate_limit_store  # oldest dropped
    main._rate_limit_store.clear()


async def test_memory_store_rate_limit_is_atomic_and_windowed() -> None:
    from src.collab.store import MemoryStore

    store = MemoryStore()
    assert await store.allow_rate_limit("client", limit=2, window_seconds=60)
    assert await store.allow_rate_limit("client", limit=2, window_seconds=60)
    assert not await store.allow_rate_limit("client", limit=2, window_seconds=60)
