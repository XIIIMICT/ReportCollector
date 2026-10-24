"""Cross-worker transaction locks for edits and lifecycle transitions."""

from collections.abc import Callable
from functools import wraps
from hashlib import blake2b
from typing import Any

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession


async def lock_project(session: AsyncSession, project: str, *, exclusive: bool = False) -> None:
    """Edits/leases share a gate; lifecycle operations exclude all of them."""
    key = int.from_bytes(blake2b(project.encode(), digest_size=8).digest(), signed=True)
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await (await session.connection()).execute(text(f"SELECT {function}(:key)"), {"key": key})


async def lock_rows(session: AsyncSession, keys: list[str]) -> None:
    """Lock logical rows in stable order, including rows not yet inserted."""
    values = sorted({int.from_bytes(blake2b(value.encode(), digest_size=8).digest(), signed=True) for value in keys})
    if values:
        await (await session.connection()).execute(
            text(
                "SELECT pg_advisory_xact_lock(key) FROM "
                "(SELECT key FROM unnest(CAST(:keys AS bigint[])) AS locks(key) ORDER BY key) AS ordered"
            ),
            {"keys": values},
        )


def project_write(project: str, *, exclusive: bool = False) -> Callable:
    """Keep a lifecycle gate until the caller commits, including service callers."""

    def decorate(function: Callable) -> Callable:
        # The wrapper forwards whatever the decorated coroutine takes, so its own
        # signature cannot be narrower than `Any`.
        @wraps(function)
        async def wrapped(session: AsyncSession, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            await lock_project(session, project, exclusive=exclusive)
            return await function(session, *args, **kwargs)

        return wrapped

    return decorate
