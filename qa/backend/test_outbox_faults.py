"""Kill the Product DB after the engine has already done the work.

PM v16's P0-CONSISTENCY. `actors.create()` calls the engine and the route
commits afterwards, so there is a window where the engine holds a resource --
containing the customer's credentials -- and the Product DB has not recorded
it. The old compensation only covered the engine call *itself* failing, which
is the easy half: the hard half is the engine succeeding and the database not.

An orphan created that way is invisible. It is in no list, no reconcile result
can distinguish it from a resource another deployment owns, and nobody can
delete it because nobody knows its id.

So these tests do what PM asked: fail the database at each point after the
engine call, restart the process, and then prove two things -- no orphan
survives, and the retry does not create a second resource.

Skipped without `RUN_CORE_LIVE=1`: the ledger's whole promise is that it
survives a rolled-back transaction, and only a real database can roll one back.
"""

from __future__ import annotations

import os
import uuid

import pytest

LIVE = os.getenv("RUN_CORE_LIVE") == "1"
pytestmark = [
    pytest.mark.skipif(not LIVE,
                       reason="set RUN_CORE_LIVE=1 with a reachable Postgres"),
    pytest.mark.asyncio,
]


class FakeEngine:
    """An engine that behaves like the real Airbyte Config API.

    The previous version of this double was the reason the outbox looked safe
    and was not. It minted refs as `engine-source-{product_resource_id}` and
    its delete matched on suffix, so compensating by the product UUID worked --
    a property of the double, and of nothing else.

    Airbyte does not do that. `POST /api/v1/sources/create` takes a workspace
    id, a definition id, a configuration and a name; it returns a `sourceId`
    **it** generated. The product's UUID appears nowhere the API can be queried
    by. So this double now:

      * generates its own opaque id, unrelated to anything the product sent
      * deletes strictly by that id, and ignores anything else
      * offers `find_by_product_id`, which is only answerable because the
        adapter embeds the product id in the resource *name* -- the one field
        the product controls

    A compensation path that cannot survive this double cannot survive
    production.
    """

    #: Mirrors the adapter's marker, because that is the only correlation the
    #: real API permits.
    MARKER = " [appbi:{id}]"

    def __init__(self) -> None:
        self.resources: dict[str, dict] = {}
        self.create_calls = 0
        self.delete_calls = 0
        self.delete_fails = 0
        self.find_calls = 0
        self._counter = 0

    def _name_for(self, request) -> str:
        base = getattr(request, "name", "resource")
        return f"{base}{self.MARKER.format(id=request.product_resource_id)}"

    async def create_source(self, request):
        self.create_calls += 1
        self._counter += 1
        # Opaque, engine-chosen, and deliberately nothing to do with the
        # product id -- exactly like a real `sourceId`.
        ref = f"ab-{uuid.uuid5(uuid.NAMESPACE_OID, str(self._counter)).hex}"
        self.resources[ref] = {"credentials": "CUSTOMER-SECRET",
                               "name": self._name_for(request)}
        return ref

    create_destination = create_source

    async def find_by_product_id(self, resource_type: str,
                                 product_resource_id: str) -> str | None:
        """List and match on the name marker, as the adapter does."""
        self.find_calls += 1
        marker = self.MARKER.format(id=product_resource_id)
        for ref, resource in self.resources.items():
            if marker in resource.get("name", ""):
                return ref
        return None

    async def delete_source(self, ref: str) -> None:
        self.delete_calls += 1
        if self.delete_fails > 0:
            self.delete_fails -= 1
            raise RuntimeError("engine unavailable")
        if ref not in self.resources:
            # A real API 404s on an unknown id. It does not helpfully guess.
            raise RuntimeError(f"no such resource: {ref}")
        self.resources.pop(ref)

    delete_destination = delete_source
    delete_connection = delete_source


async def _fresh_database(name: str) -> str:
    """A scratch database at a known schema; dropped when the run ends.

    The creation and the bookkeeping live in `scratchdb`, so the suite has one
    answer to "which databases did this run make" and the session fixture can
    drop them all.
    """
    from scratchdb import fresh_database

    return await fresh_database(name)


class _Deployment:
    def __init__(self, name: str) -> None:
        self._name = name

    async def __aenter__(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import app.core.db as db
        import app.models  # noqa: F401  -- register the tables
        from app.core.db import Base

        url = await _fresh_database(self._name)
        self._engine = create_async_engine(url)
        self._maker = async_sessionmaker(self._engine, expire_on_commit=False)
        self._saved = (db._engine, db._session_factory)
        db._engine, db._session_factory = self._engine, self._maker

        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        return self

    async def __aexit__(self, *exc) -> None:
        import app.core.db as db

        db._engine, db._session_factory = self._saved
        await self._engine.dispose()

    def session(self):
        return self._maker()


async def _ledger_rows():
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models.outbox import EngineOperation

    async with SessionLocal() as session:
        return list((await session.scalars(select(EngineOperation))).all())


async def test_the_intent_is_durable_before_the_engine_is_called() -> None:
    """The premise everything else rests on.

    If `begin()` shared the request transaction, a rollback would erase the
    evidence along with the work -- and the orphan would be invisible again.
    """
    from app.core.db import SessionLocal
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_durable"):
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        # An uncommitted, later-rolled-back session running concurrently.
        async with SessionLocal() as request_session:
            await request_session.execute(__import__("sqlalchemy").text("SELECT 1"))
            await outbox.begin(workspace_id, "SOURCE", "CREATE", resource_id)
            await request_session.rollback()

        rows = await _ledger_rows()
        assert len(rows) == 1, rows
        assert rows[0].state == EngineOperationState.PENDING
        assert rows[0].product_resource_id == resource_id


async def test_a_rollback_after_the_engine_call_leaves_no_orphan() -> None:
    """The failure PM described, start to finish.

    Engine succeeds, Product DB does not commit, process moves on. The engine
    is holding a resource with customer credentials in it and the product has
    no row. The sweeper has to find and remove it.
    """
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_orphan"):
        engine = FakeEngine()
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)

        class _Request:
            product_resource_id = resource_id
            name = "Probe"
        ref = await engine.create_source(_Request())
        await outbox.engine_created(operation_id, ref)

        # ... and here the Product DB transaction rolls back. Nothing is
        # written. The engine still holds the resource.
        assert engine.resources, "precondition: the engine holds something"

        # The sweeper, run as the worker runs it, with the SLO already elapsed.
        counts = await outbox.sweep(engine, stale_seconds=0)

        assert counts["compensated"] == 1, counts
        assert engine.resources == {}, "the orphan survived the sweep"
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATED


async def test_a_crash_before_the_outcome_was_recorded_is_still_cleaned_up() -> None:
    """The worst window: engine called, process dies before `engine_created()`.

    No ref was ever recorded, so compensation cannot address the resource by
    ref. It is addressable by the product resource id because that is the
    external id the engine was given -- which is the reason the id is generated
    before the call rather than after.
    """
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_crash"):
        engine = FakeEngine()
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        await outbox.begin(workspace_id, "SOURCE", "CREATE", resource_id)

        class _Request:
            product_resource_id = resource_id
            name = "Probe"
        await engine.create_source(_Request())
        # <- process dies here; the ledger row is still PENDING

        counts = await outbox.sweep(engine, stale_seconds=0)

        assert counts["compensated"] == 1, counts
        assert engine.resources == {}, "an unrecorded resource was left behind"
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATED


async def test_a_committed_create_is_closed_and_never_compensated() -> None:
    """The other direction, and the more dangerous mistake.

    If only the final ledger update is lost, the product row exists and is in
    use. Compensating that would delete a working source out from under a
    customer -- so the sweeper checks for the row before it deletes anything.
    """
    from app.core.db import SessionLocal
    from app.core.permissions import Role
    from app.models.identity import Membership, User, Workspace
    from app.models.integration import Source
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_committed") as deployment:
        engine = FakeEngine()
        resource_id = uuid.uuid4()

        async with deployment.session() as session:
            workspace = Workspace(name="Acme", slug="acme", timezone="Asia/Bangkok")
            session.add(workspace)
            await session.flush()
            user = User(email="ops@acme.io", full_name="Ops", password_hash="x")
            session.add(user)
            await session.flush()
            session.add(Membership(workspace_id=workspace.id, user_id=user.id,
                                   role=Role.PLATFORM_ADMIN))
            workspace_id = workspace.id
            await session.commit()

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)

        class _Request:
            product_resource_id = resource_id
            name = "Probe"
        ref = await engine.create_source(_Request())
        await outbox.engine_created(operation_id, ref)

        # The product row DID commit; only the ledger's closing write was lost.
        async with deployment.session() as session:
            session.add(Source(id=resource_id, workspace_id=workspace_id,
                               name="Committed source",
                               connector_key="source-postgres"))
            await session.commit()

        counts = await outbox.sweep(engine, stale_seconds=0)

        assert counts["closed"] == 1, counts
        assert counts["compensated"] == 0, "a live resource was compensated"
        assert engine.resources, "the sweeper deleted a source that was in use"
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMMITTED


async def test_a_retry_reuses_the_saga_instead_of_duplicating_it() -> None:
    """Idempotency, keyed on the product resource id.

    Without this, every retry of a failing create leaves another engine
    resource behind and the cleanup problem multiplies.
    """
    from app.services import outbox

    async with _Deployment("outbox_retry"):
        engine = FakeEngine()
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        class _Request:
            product_resource_id = resource_id
            name = "Probe"

        first, was_retry = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)
        assert was_retry is False
        await engine.create_source(_Request())

        # Second attempt. `begin` says this is a retry, which is the signal the
        # caller needs: the engine has no idempotency key, so calling create
        # again would make a second resource and strand the first.
        second, was_retry = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)
        assert first == second, "a retry started a second saga"
        assert was_retry is True

        existing = await engine.find_by_product_id("SOURCE", str(resource_id))
        assert existing is not None, "the first attempt's resource must be findable"
        # ...so the caller reuses it rather than creating another.
        assert engine.create_calls == 1
        assert len(engine.resources) == 1

        rows = await _ledger_rows()
        assert len(rows) == 1, [r.state for r in rows]
        assert rows[0].attempts >= 1

        await outbox.sweep(engine, stale_seconds=0)
        assert engine.resources == {}


async def test_compensation_retries_and_then_asks_for_a_human() -> None:
    """An engine that is down must not turn into an infinite retry log.

    It must also not give up on the first failure, because 'engine briefly
    unreachable' is the common case and is self-healing.
    """
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_retry_limit"):
        engine = FakeEngine()
        engine.delete_fails = 2
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)

        class _Request:
            product_resource_id = resource_id
            name = "Probe"
        ref = await engine.create_source(_Request())
        await outbox.engine_created(operation_id, ref)

        # Two sweeps fail, the third succeeds.
        await outbox.sweep(engine, stale_seconds=0)
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATION_REQUIRED
        assert "engine unavailable" in (rows[0].last_error or "")

        await outbox.sweep(engine, stale_seconds=0)
        await outbox.sweep(engine, stale_seconds=0)

        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATED
        assert engine.resources == {}


async def test_an_engine_that_never_recovers_is_escalated_not_retried_forever() -> None:
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_exhausted"):
        engine = FakeEngine()
        engine.delete_fails = 10_000
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)

        class _Request:
            product_resource_id = resource_id
            name = "Probe"
        await outbox.engine_created(
            operation_id, await engine.create_source(_Request()))

        for _ in range(outbox.MAX_COMPENSATION_ATTEMPTS + 2):
            await outbox.sweep(engine, stale_seconds=0)

        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.FAILED
        # And it is on the list an alert fires from.
        assert [r.id for r in await outbox.overdue(stale_seconds=0)] == [rows[0].id]


async def test_the_engine_call_failing_is_not_treated_as_an_orphan() -> None:
    """Nothing was created, so there is nothing to compensate.

    Sweeping this would issue a pointless delete against every failed create,
    which on a flaky engine is a lot of noise hiding the real orphans.
    """
    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_engine_failed"):
        engine = FakeEngine()
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)
        await outbox.failed(operation_id, "ENGINE_UNAVAILABLE: connection refused")

        counts = await outbox.sweep(engine, stale_seconds=0)

        assert counts["checked"] == 0, counts
        assert engine.delete_calls == 0
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATED
        assert "ENGINE_UNAVAILABLE" in (rows[0].last_error or "")


async def test_the_double_does_not_flatter_the_implementation() -> None:
    """A guard on the test double itself.

    The previous double minted `engine-source-{product_resource_id}` and its
    delete matched on suffix, so compensating by the product UUID passed. That
    is not a property Airbyte has, and the outbox shipped believing it did.

    If somebody makes this double convenient again, the suite goes green and
    the product goes back to being wrong -- so the double's realism is itself
    asserted.
    """
    engine = FakeEngine()
    resource_id = uuid.uuid4()

    class _Request:
        product_resource_id = resource_id
        name = "Probe"

    ref = await engine.create_source(_Request())

    assert str(resource_id) not in ref, (
        "the engine ref must not be derivable from the product id")
    # Deleting by the product id must fail, exactly as a real 404 would.
    with pytest.raises(RuntimeError):
        await engine.delete_source(str(resource_id))
    assert engine.resources, "the resource must survive a wrong-id delete"
    # The only way back to it is the marker the adapter puts in the name.
    assert await engine.find_by_product_id("SOURCE", str(resource_id)) == ref


async def test_two_workers_sweeping_at_once_compensate_once() -> None:
    """The sweeper runs in every worker, and they overlap.

    Without row locking, two workers read the same stale row and both issue the
    delete: one succeeds, the other gets a 404 it will record as a compensation
    failure and retry until it gives up -- an operation escalated to FAILED
    when nothing was ever wrong.
    """
    import asyncio

    from app.models.outbox import EngineOperationState
    from app.services import outbox

    async with _Deployment("outbox_concurrent"):
        engine = FakeEngine()
        workspace_id, resource_id = uuid.uuid4(), uuid.uuid4()

        class _Request:
            product_resource_id = resource_id
            name = "Probe"

        operation_id, _ = await outbox.begin(
            workspace_id, "SOURCE", "CREATE", resource_id)
        await outbox.engine_created(operation_id, await engine.create_source(_Request()))

        results = await asyncio.gather(
            outbox.sweep(engine, stale_seconds=0),
            outbox.sweep(engine, stale_seconds=0),
            outbox.sweep(engine, stale_seconds=0),
        )

        assert sum(r["compensated"] for r in results) == 1, results
        assert engine.delete_calls == 1, engine.delete_calls
        assert engine.resources == {}
        rows = await _ledger_rows()
        assert rows[0].state == EngineOperationState.COMPENSATED


# ── OAuth grant lifecycle ────────────────────────────────────────────────────
#
# In this file because it needs the same real database: a grant is a row, and
# every property that matters about it is a property of that row.

async def _grant(deployment, workspace_id, connector_key="source-google-sheets"):
    from app.core.db import SessionLocal
    from app.services import oauth

    async with SessionLocal() as session:
        grant = await oauth.store_grant(
            session, workspace_id=workspace_id, user_id=uuid.uuid4(),
            connector_key=connector_key, provider=oauth.PROVIDERS["google"],
            credentials={"credentials": {"auth_type": "Client",
                                         "refresh_token": "SENTINEL-REFRESH"}},
            account_label="someone@example.com")
        await session.commit()
        return grant.id


async def test_a_grant_can_be_redeemed_once_and_only_once() -> None:
    """Replay would attach one person's Google account to a second resource."""
    from app.core.db import SessionLocal
    from app.core.errors import ValidationError
    from app.services import oauth

    async with _Deployment("oauth_single_use") as deployment:
        workspace_id = uuid.uuid4()
        grant_id = await _grant(deployment, workspace_id)

        async with SessionLocal() as session:
            credentials = await oauth.consume_grant(
                session, grant_id, workspace_id=workspace_id,
                connector_key="source-google-sheets")
            await session.commit()
        assert credentials["credentials"]["refresh_token"] == "SENTINEL-REFRESH"

        async with SessionLocal() as session:
            with pytest.raises(ValidationError):
                await oauth.consume_grant(
                    session, grant_id, workspace_id=workspace_id,
                    connector_key="source-google-sheets")


async def test_a_grant_belongs_to_one_workspace_and_one_connector() -> None:
    """A handle leaked from one form must not work anywhere else."""
    from app.core.db import SessionLocal
    from app.core.errors import ValidationError
    from app.services import oauth

    async with _Deployment("oauth_scoped") as deployment:
        workspace_id = uuid.uuid4()
        grant_id = await _grant(deployment, workspace_id)

        async with SessionLocal() as session:
            with pytest.raises(ValidationError):
                await oauth.consume_grant(
                    session, grant_id, workspace_id=uuid.uuid4(),
                    connector_key="source-google-sheets")
            with pytest.raises(ValidationError):
                await oauth.consume_grant(
                    session, grant_id, workspace_id=workspace_id,
                    connector_key="source-microsoft-onedrive")


async def test_an_abandoned_grant_expires_and_is_purged() -> None:
    """An unconsumed grant is a live refresh token pointing at nothing."""
    from datetime import timedelta

    from app.core.db import SessionLocal, utcnow
    from app.core.errors import ValidationError
    from app.models.oauth import OAuthGrant
    from app.services import oauth

    async with _Deployment("oauth_expiry") as deployment:
        workspace_id = uuid.uuid4()
        grant_id = await _grant(deployment, workspace_id)

        async with SessionLocal() as session:
            row = await session.get(OAuthGrant, grant_id)
            row.expires_at = utcnow() - timedelta(minutes=1)
            await session.commit()

        async with SessionLocal() as session:
            with pytest.raises(ValidationError):
                await oauth.consume_grant(
                    session, grant_id, workspace_id=workspace_id,
                    connector_key="source-google-sheets")

        async with SessionLocal() as session:
            assert await oauth.purge_expired(session) == 1
            await session.commit()

        async with SessionLocal() as session:
            assert await session.get(OAuthGrant, grant_id) is None
