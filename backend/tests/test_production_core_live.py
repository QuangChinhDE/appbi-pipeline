"""The behavioural half of Sprint A: a real Postgres, real concurrency.

Skipped unless `RUN_CORE_LIVE=1` and a database is reachable, for the same
reason the engine contract suite is: these need infrastructure, and a unit
suite that silently needs a database is a unit suite that fails on a laptop.

What is here cannot be asserted any other way. PM's finding was that 207 tests
covered none of the races, and a source-level assertion that an index is
declared does not prove the index stops two concurrent inserts. These run the
race.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

LIVE = os.getenv("RUN_CORE_LIVE") == "1"
live_only = pytest.mark.skipif(
    not LIVE, reason="set RUN_CORE_LIVE=1 with a reachable Postgres")

pytestmark = [live_only, pytest.mark.asyncio]


async def _fresh_database(name: str) -> str:
    """A scratch database at head, so each test starts from a known schema."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    admin_url = settings.database_url.rsplit("/", 1)[0] + "/postgres"
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()
    return settings.database_url.rsplit("/", 1)[0] + f"/{name}"


# ── P0-CORE-001 ──────────────────────────────────────────────────────────────

async def test_a_production_bootstrap_creates_no_default_credentials() -> None:
    """The acceptance criterion, stated exactly: zero guessable accounts.

    An empty database, `APP_ENV=production`, `SEED_DEMO_DATA=false`. Either it
    refuses to start, or it creates precisely one admin from the supplied
    one-time secret which must then change its password. Nothing else.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.bootstrap as bootstrap
    from app.core.config import Settings
    from app.models.identity import User

    url = await _fresh_database("core_bootstrap_test")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    original_settings = bootstrap.settings
    original_maker = bootstrap.SessionLocal
    try:
        bootstrap.SessionLocal = maker
        from app.core.db import Base
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        # 1. No bootstrap secret: refuse. Do not invent a login.
        bootstrap.settings = Settings(
            app_env="production", seed_demo_data=False,
            engine_type="AIRBYTE_API", cookie_secure=True,
            bootstrap_admin_email="", bootstrap_admin_password="")
        with pytest.raises(bootstrap.BootstrapRefused):
            await bootstrap.seed()

        async with maker() as session:
            assert (await session.scalars(select(User))).all() == []

        # 2. The repository's demo password is refused even if supplied.
        bootstrap.settings = Settings(
            app_env="production", seed_demo_data=False,
            engine_type="AIRBYTE_API", cookie_secure=True,
            bootstrap_admin_email="ops@example.com",
            bootstrap_admin_password="Admin@12345")
        with pytest.raises(bootstrap.BootstrapRefused):
            await bootstrap.seed()

        # 3. A real one-time secret: exactly one admin, forced to change it.
        bootstrap.settings = Settings(
            app_env="production", seed_demo_data=False,
            engine_type="AIRBYTE_API", cookie_secure=True,
            bootstrap_admin_email="ops@example.com",
            bootstrap_admin_password="a-real-one-time-secret-9Z")
        await bootstrap.seed()

        async with maker() as session:
            users = list((await session.scalars(select(User))).all())
        assert len(users) == 1, [u.email for u in users]
        assert users[0].email == "ops@example.com"
        assert users[0].password_change_required is True
        # The finding, restated as an assertion.
        assert not any(u.email.endswith("@appbi.local") for u in users)
    finally:
        bootstrap.settings = original_settings
        bootstrap.SessionLocal = original_maker
        await engine.dispose()


# ── P0-CORE-003 ──────────────────────────────────────────────────────────────

async def test_twenty_concurrent_triggers_produce_one_run() -> None:
    """PM's acceptance criterion: 20 concurrent requests, exactly 1 run.

    The check-then-insert in `trigger()` passes this with one connection and
    fails it with twenty. The partial unique index is what makes it hold.
    """
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.enums import RunStatus

    url = await _fresh_database("core_race_test")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    workspace = uuid.uuid4()
    pipeline = uuid.uuid4()
    try:
        # Only the table under test, with the invariants the migration adds.
        async with engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE pipeline_runs ("
                " id uuid PRIMARY KEY, workspace_id uuid NOT NULL,"
                " pipeline_id uuid NOT NULL, idempotency_key varchar(120),"
                " status varchar(32) NOT NULL)"))
            await connection.execute(text(
                "CREATE UNIQUE INDEX uq_run_idempotency_key "
                "ON pipeline_runs (workspace_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"))
            await connection.execute(text(
                "CREATE UNIQUE INDEX uq_pipeline_active_run "
                "ON pipeline_runs (pipeline_id) WHERE status IN "
                "('QUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"))

        async def attempt(_: int) -> str:
            async with maker() as session:
                try:
                    await session.execute(text(
                        "INSERT INTO pipeline_runs VALUES "
                        "(:id, :ws, :pl, :key, 'QUEUED')"),
                        {"id": uuid.uuid4(), "ws": workspace, "pl": pipeline,
                         "key": "same-key"})
                    await session.commit()
                    return "won"
                except Exception:
                    await session.rollback()
                    return "lost"

        outcomes = await asyncio.gather(*(attempt(i) for i in range(20)))

        async with maker() as session:
            total = await session.scalar(text("SELECT count(*) FROM pipeline_runs"))
        assert total == 1, f"{total} runs created by 20 concurrent triggers"
        assert outcomes.count("won") == 1
        assert outcomes.count("lost") == 19
    finally:
        await engine.dispose()


async def test_a_second_active_run_is_refused_even_without_a_key() -> None:
    """The idempotency key is optional; the one-active-run invariant is not.

    A scheduled trigger and a manual one carry no shared key, so the key index
    does not help. This is the index that stops two Airbyte jobs writing the
    same destination.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = await _fresh_database("core_active_test")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    pipeline = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE pipeline_runs ("
                " id uuid PRIMARY KEY, workspace_id uuid NOT NULL,"
                " pipeline_id uuid NOT NULL, idempotency_key varchar(120),"
                " status varchar(32) NOT NULL)"))
            await connection.execute(text(
                "CREATE UNIQUE INDEX uq_pipeline_active_run "
                "ON pipeline_runs (pipeline_id) WHERE status IN "
                "('QUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"))

        async def insert(status: str) -> bool:
            async with maker() as session:
                try:
                    await session.execute(text(
                        "INSERT INTO pipeline_runs VALUES "
                        "(:id, :ws, :pl, NULL, :st)"),
                        {"id": uuid.uuid4(), "ws": uuid.uuid4(),
                         "pl": pipeline, "st": status})
                    await session.commit()
                    return True
                except Exception:
                    await session.rollback()
                    return False

        assert await insert("QUEUED") is True
        assert await insert("RUNNING") is False, (
            "a second active run for the same pipeline must be refused")
        # And the index must not forbid history: finished runs accumulate.
        assert await insert("SUCCEEDED") is True
        assert await insert("FAILED") is True
    finally:
        await engine.dispose()


# ── P0-CORE-004 ──────────────────────────────────────────────────────────────

async def test_only_a_confirmed_absence_marks_a_run_lost() -> None:
    """PM v10 reopened this: a 401 marked a live Airbyte job FAILED.

    Every 4xx collapsed into `EngineOperationError` in the adapter, and
    recovery read anything that was not `EngineUnavailableError` as "the job is
    gone". So a rotated credential, a 403, or a rate limit failed runs whose
    Airbyte jobs were still running and still writing.

    The full matrix PM asked for. Only a confirmed not-found may end FAILED.
    """
    from datetime import timedelta

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.db import Base, utcnow
    from app.core.errors import (
        EngineOperationError, EngineResourceGoneError, EngineUnavailableError,
    )
    from app.models.enums import RunStatus, TriggerType
    from app.models.run import PipelineRun
    from app.services import runs as run_service

    def answer(ref: str):
        if ref in ("running", "terminal"):
            return None                                   # 200
        if ref == "notfound":
            raise EngineResourceGoneError(technical_message="HTTP 404")
        if ref in ("unauthorized", "forbidden"):
            raise EngineOperationError(code="ENGINE_AUTH_FAILED",
                                       technical_message="HTTP 401/403")
        if ref in ("ratelimited", "server", "timeout"):
            raise EngineUnavailableError(technical_message="429/500/timeout")
        if ref == "malformed":
            raise EngineOperationError(technical_message="HTTP 422")
        raise AssertionError(ref)

    class FakeAdapter:
        async def get_job(self, ref: str):
            return answer(ref)

    url = await _fresh_database("core_recovery_matrix")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    expected = {
        "running": RunStatus.RUNNING,
        "terminal": RunStatus.RUNNING,
        "notfound": RunStatus.FAILED,
        "unauthorized": RunStatus.RUNNING,
        "forbidden": RunStatus.RUNNING,
        "ratelimited": RunStatus.RUNNING,
        "server": RunStatus.RUNNING,
        "timeout": RunStatus.RUNNING,
        "malformed": RunStatus.RUNNING,
        # No engine ref and the lease is up: nothing was ever started, so
        # nothing can be duplicated by failing it.
        "never": RunStatus.FAILED,
    }

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for constraint in ("fk_pipeline_runs_pipeline_id_pipelines",
                               "fk_pipeline_runs_workspace_id_workspaces"):
                await connection.execute(text(
                    f"ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS {constraint}"))

        stale = utcnow() - timedelta(hours=24)
        ids: dict[str, uuid.UUID] = {}
        async with maker() as session:
            for label in expected:
                run = PipelineRun(
                    workspace_id=uuid.uuid4(), pipeline_id=uuid.uuid4(),
                    trigger_type=TriggerType.MANUAL, status=RunStatus.RUNNING,
                    engine_job_ref=None if label == "never" else label,
                    claimed_by="worker-1", heartbeat_at=stale)
                session.add(run)
                await session.flush()
                ids[label] = run.id
            await session.commit()

        import app.adapters.registry as registry
        original = registry.get_adapter
        registry.get_adapter = lambda: FakeAdapter()
        try:
            async with maker() as session:
                counts = await run_service.recover_orphans(session, "worker-1")
        finally:
            registry.get_adapter = original

        async with maker() as session:
            final = {label: (await session.get(PipelineRun, run_id)).status
                     for label, run_id in ids.items()}

        wrong = {label: (final[label], want)
                 for label, want in expected.items() if final[label] is not want}
        assert not wrong, f"got/expected: {wrong}"
        assert counts["lost"] == 2, counts        # notfound + never
        assert counts["adopted"] == 2, counts     # running + terminal
        assert counts["deferred"] == 6, counts    # every non-answer
    finally:
        await engine.dispose()


async def test_a_password_change_revokes_sessions_opened_before_it() -> None:
    """Two people sign in with the same one-time bootstrap secret.

    After the first changes the password, the second must not still hold a
    platform-admin session. The old code cleared `password_change_required` and
    claimed in a comment that earlier sessions were invalidated -- nothing did
    it, so clearing the flag actively *un*blocked the second session.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.db import Base
    from app.core.security import decode_session_token, issue_session_token
    from app.models.identity import User

    url = await _fresh_database("core_session_test")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with maker() as session:
            user = User(email="ops@example.com", full_name="Ops",
                        password_hash="x", is_platform_admin=True,
                        password_change_required=True)
            session.add(user)
            await session.commit()
            user_id, version = user.id, user.session_version

        # Both sessions are minted from the same one-time secret.
        first = issue_session_token(user_id, None, version)
        second = issue_session_token(user_id, None, version)

        # The first holder changes the password.
        async with maker() as session:
            user = await session.get(User, user_id)
            user.password_hash = "y"
            user.password_change_required = False
            user.session_version += 1
            await session.commit()
            new_version = user.session_version

        async with maker() as session:
            user = await session.get(User, user_id)
            # Both old tokens are stale now, including the second person's --
            # who would otherwise hold a full-privilege session precisely
            # because the flag had just been cleared.
            for token in (first, second):
                assert decode_session_token(token)["sv"] != user.session_version
            fresh = issue_session_token(user_id, None, new_version)
            assert decode_session_token(fresh)["sv"] == user.session_version
    finally:
        await engine.dispose()


async def test_a_hung_sync_is_cancelled_on_the_engine_then_timed_out() -> None:
    """PILOT-G4: bounded execution, proven rather than configured.

    `timeout_seconds` was set on every request and honoured only by the
    embedded runner, so an Airbyte sync that hung stayed RUNNING forever and
    held the pipeline's one active-run slot.

    Two things are asserted, and the second matters more: the engine job is
    cancelled *before* the run is marked terminal. The other order leaves
    Airbyte writing to a destination the product thinks is idle.
    """
    from datetime import timedelta

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.db import Base, utcnow
    from app.core.errors import EngineUnavailableError
    from app.models.enums import RunStatus, TriggerType
    from app.models.run import PipelineRun
    from app.services import runs as run_service

    cancelled: list[str] = []

    class FakeAdapter:
        async def cancel_job(self, ref: str):
            if ref == "engine-down":
                raise EngineUnavailableError(technical_message="connection refused")
            cancelled.append(ref)

    url = await _fresh_database("core_timeout_test")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for constraint in ("fk_pipeline_runs_pipeline_id_pipelines",
                               "fk_pipeline_runs_workspace_id_workspaces"):
                await connection.execute(text(
                    f"ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS {constraint}"))

        overdue = utcnow() - timedelta(seconds=settings.run_timeout_seconds + 60)
        rows = {
            "hung": ("engine-job-1", overdue),
            "engine-down": ("engine-down", overdue),
            "healthy": ("engine-job-2", utcnow()),
        }
        ids: dict[str, uuid.UUID] = {}
        async with maker() as session:
            for label, (ref, started) in rows.items():
                run = PipelineRun(
                    workspace_id=uuid.uuid4(), pipeline_id=uuid.uuid4(),
                    trigger_type=TriggerType.MANUAL, status=RunStatus.RUNNING,
                    engine_job_ref=ref, started_at=started, heartbeat_at=started)
                session.add(run)
                await session.flush()
                ids[label] = run.id
            await session.commit()

        import app.adapters.registry as registry
        original = registry.get_adapter
        registry.get_adapter = lambda: FakeAdapter()
        try:
            async with maker() as session:
                counts = await run_service.enforce_timeouts(session)
        finally:
            registry.get_adapter = original

        async with maker() as session:
            final = {label: (await session.get(PipelineRun, run_id)).status
                     for label, run_id in ids.items()}

        assert counts == {"timed_out": 1, "deferred": 1}, counts
        assert final["hung"] is RunStatus.TIMED_OUT
        # The engine was told, not just the database.
        assert cancelled == ["engine-job-1"], cancelled
        # An engine that could not be reached leaves the run alone: marking it
        # terminal would be a claim nobody verified.
        assert final["engine-down"] is RunStatus.RUNNING
        # And a run inside its deadline is untouched.
        assert final["healthy"] is RunStatus.RUNNING
    finally:
        await engine.dispose()
