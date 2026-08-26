"""Scratch databases for tests, and the promise that they go away.

Several suites need a real Postgres at a known schema rather than a mock: the
outbox tests turn on `FOR UPDATE SKIP LOCKED`, the bootstrap tests on unique
constraints, and the catalog tests on the actual column set. Each one creates a
database, uses it, and used to leave it behind — three copies of the same
twelve-line helper, none of which dropped anything.

A dev box that had run the suite a few times carried 24 abandoned databases and
211 MB, with names like `outbox_retry_limit` that nothing explained. The
leftovers are also a correctness risk: `_fresh_database` drops-then-creates, so
a test that reads a stale database still passes, and one that forgets to
re-create silently inherits the previous run's rows.

Every scratch database handed out here is recorded, and the session fixture in
`conftest.py` drops the lot when the run ends — including after a failure,
which is exactly when the old code left the most behind.
"""

from __future__ import annotations

_handed_out: set[str] = set()


def _root() -> str:
    from app.core.config import settings

    return settings.database_url.rsplit("/", 1)[0]


async def fresh_database(name: str) -> str:
    """An empty database of this name, and the URL to reach it."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"{_root()}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()
    _handed_out.add(name)
    return f"{_root()}/{name}"


async def drop_scratch_databases() -> list[str]:
    """Drop everything handed out this run. Returns what went."""
    if not _handed_out:
        return []
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"{_root()}/postgres", isolation_level="AUTOCOMMIT")
    dropped = []
    try:
        async with engine.connect() as connection:
            for name in sorted(_handed_out):
                # WITH (FORCE) because a test that failed mid-way may still
                # hold a connection, and a teardown that raises there would
                # leave the rest of the list behind.
                await connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                dropped.append(name)
    finally:
        await engine.dispose()
    _handed_out.clear()
    return dropped
