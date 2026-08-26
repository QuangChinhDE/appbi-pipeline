"""Suite-wide fixtures."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="session", autouse=True)
def _drop_scratch_databases_when_the_run_ends():
    """Tests that make databases have to unmake them.

    Session-scoped and autouse so it runs once at the end regardless of which
    tests ran or whether they passed. See `scratchdb.py` for why this exists.
    """
    yield
    from scratchdb import drop_scratch_databases

    try:
        dropped = asyncio.run(drop_scratch_databases())
    except Exception as exc:                                    # noqa: BLE001
        # Never fail a green run on cleanup: report it and let the operator
        # decide. A teardown that turns a passing suite red teaches people to
        # ignore the suite.
        print(f"\nscratch databases were left behind: {exc}")
        return
    if dropped:
        print(f"\ndropped {len(dropped)} scratch database(s): {', '.join(dropped)}")
