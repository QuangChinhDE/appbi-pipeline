"""The bootstrap account has to be able to get out of its own starting state.

PM v16's P0-AUTH, and the shape of it is worth writing down: every individual
piece worked. The backend set `password_change_required`, the guard refused
product routes, and `/auth/change-password` was correctly mounted on `UserDep`
so the gate could not lock out the one action that clears it.

What did not work was the join. `/auth/me` took `CtxDep`, so reading the
session -- the first thing the app does on load -- returned
`403 PASSWORD_CHANGE_REQUIRED`. The frontend saw no user and rendered nothing.
A fresh production deployment signed in as its only account and got a white
screen, with no way to discover why.

So these drive the real app over HTTP against a real database, because the
defect lived in the seam between correct parts and nothing that tests the parts
would have found it.

Skipped without `RUN_CORE_LIVE=1`, like the rest of the live suite: a unit
suite that silently needs Postgres is a unit suite that fails on a laptop.
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


async def _fresh_database(name: str) -> str:
    """A scratch database at a known schema; dropped when the run ends.

    The creation and the bookkeeping live in `scratchdb`, so the suite has one
    answer to "which databases did this run make" and the session fixture can
    drop them all.
    """
    from scratchdb import fresh_database

    return await fresh_database(name)


class _Deployment:
    """A running app on a scratch database, reachable over HTTP."""

    def __init__(self, name: str) -> None:
        self._name = name

    async def __aenter__(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import app.core.db as db
        from app.core.db import Base
        # `create_all` only knows the tables whose modules have been imported.
        # Importing them lazily inside the helpers meant the first test in a
        # session created an empty schema and every later one worked, which is
        # the most confusing possible version of this bug.
        import app.models  # noqa: F401
        import app.models.identity  # noqa: F401

        url = await _fresh_database(self._name)
        self._engine = create_async_engine(url)
        self._maker = async_sessionmaker(self._engine, expire_on_commit=False)

        # The lazy proxies resolve through these globals on first use, so
        # replacing them points the whole app at the scratch database.
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

    def client(self):
        """A separate cookie jar per call, so two of them are two browsers."""
        import httpx

        from app.main import app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def account(self, email: str, password: str, *,
                      change_required: bool = True) -> uuid.UUID:
        from app.core.permissions import Role
        from app.core.security import hash_password
        from app.models.identity import Membership, User, Workspace

        async with self.session() as session:
            workspace = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}",
                                  timezone="Asia/Bangkok")
            session.add(workspace)
            await session.flush()
            user = User(email=email, full_name="Bootstrap Admin",
                        password_hash=hash_password(password),
                        is_platform_admin=True,
                        password_change_required=change_required)
            session.add(user)
            await session.flush()
            session.add(Membership(workspace_id=workspace.id, user_id=user.id,
                                   role=Role.PLATFORM_ADMIN))
            await session.commit()
            return user.id


PASSWORD = "Bootstrap@12345"
CHOSEN = "Chosen@Password9"


async def test_the_account_can_read_its_own_session_while_blocked() -> None:
    """`/auth/me` is what the app calls first, so it has to answer.

    This is the white screen, reduced to one assertion.
    """
    async with _Deployment("auth_me_blocked") as deployment:
        await deployment.account("ops@acme.io", PASSWORD)
        async with deployment.client() as client:
            login = await client.post("/api/v1/auth/login",
                                      json={"email": "ops@acme.io", "password": PASSWORD})
            assert login.status_code == 200, login.text
            assert login.json()["password_change_required"] is True, (
                "login has to tell the client where to send the user")

            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 200, (
                f"the session must stay readable while blocked: "
                f"{me.status_code} {me.text[:200]}")
            assert me.json()["password_change_required"] is True


async def test_every_product_route_is_still_refused() -> None:
    """Reading the session must not become a way around the gate."""
    async with _Deployment("auth_gate_holds") as deployment:
        await deployment.account("ops@acme.io", PASSWORD)
        async with deployment.client() as client:
            await client.post("/api/v1/auth/login",
                              json={"email": "ops@acme.io", "password": PASSWORD})
            for path in ("/api/v1/sources", "/api/v1/destinations",
                         "/api/v1/pipelines", "/api/v1/runs",
                         "/api/v1/connectors", "/api/v1/workspace/members"):
                response = await client.get(path)
                assert response.status_code == 403, (path, response.status_code)
                assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED", path


async def test_changing_the_password_opens_the_product() -> None:
    """Blocked, change, in -- with the same client throughout.

    The new cookie has to come back on the change response, or the browser that
    just succeeded gets a 401 on its next request.
    """
    async with _Deployment("auth_change_opens") as deployment:
        await deployment.account("ops@acme.io", PASSWORD)
        async with deployment.client() as client:
            await client.post("/api/v1/auth/login",
                              json={"email": "ops@acme.io", "password": PASSWORD})
            assert (await client.get("/api/v1/pipelines")).status_code == 403

            changed = await client.post("/api/v1/auth/change-password", json={
                "current_password": PASSWORD, "new_password": CHOSEN})
            assert changed.status_code == 200, changed.text
            assert changed.json()["password_change_required"] is False

            assert (await client.get("/api/v1/auth/me")).status_code == 200
            assert (await client.get("/api/v1/pipelines")).status_code == 200


async def test_the_chosen_password_must_meet_the_policy() -> None:
    async with _Deployment("auth_policy") as deployment:
        await deployment.account("ops@acme.io", PASSWORD)
        async with deployment.client() as client:
            await client.post("/api/v1/auth/login",
                              json={"email": "ops@acme.io", "password": PASSWORD})
            for weak in ("Short1A", "alllowercase12345", "ALLUPPERCASE12345",
                         "NoDigitsHereAtAll"):
                response = await client.post("/api/v1/auth/change-password", json={
                    "current_password": PASSWORD, "new_password": weak})
                assert response.status_code in (400, 422), (weak, response.status_code)
            # And the gate is still closed after all that.
            assert (await client.get("/api/v1/pipelines")).status_code == 403


async def test_a_second_holder_of_the_temporary_password_is_signed_out() -> None:
    """Whoever handed over the bootstrap secret also knows it.

    Clearing the flag has to end their session too, not merely mint a new one
    for the caller.
    """
    async with _Deployment("auth_two_holders") as deployment:
        await deployment.account("ops@acme.io", PASSWORD)
        async with deployment.client() as first, deployment.client() as second:
            await first.post("/api/v1/auth/login",
                             json={"email": "ops@acme.io", "password": PASSWORD})
            await second.post("/api/v1/auth/login",
                              json={"email": "ops@acme.io", "password": PASSWORD})
            assert (await second.get("/api/v1/auth/me")).status_code == 200

            changed = await first.post("/api/v1/auth/change-password", json={
                "current_password": PASSWORD, "new_password": CHOSEN})
            assert changed.status_code == 200, changed.text

            stale = await second.get("/api/v1/auth/me")
            assert stale.status_code == 401, stale.text
            assert stale.json()["error"]["code"] == "SESSION_REVOKED"


async def test_an_invited_member_starts_in_the_same_state() -> None:
    """An invite password is a handover secret, not a credential.

    The route hashed whatever it was given against a schema bound of 8
    characters while the policy everywhere else is 12 -- so the one path that
    creates accounts *for other people* was the weakest in the product, and the
    account it created never had to replace the password a stranger chose.
    """
    async with _Deployment("auth_invite") as deployment:
        await deployment.account("ops@acme.io", PASSWORD, change_required=False)
        async with deployment.client() as admin:
            await admin.post("/api/v1/auth/login",
                             json={"email": "ops@acme.io", "password": PASSWORD})

            weak = await admin.post("/api/v1/workspace/members", json={
                "email": "invited-weak@acme.io", "full_name": "Invited",
                "role": "ANALYST", "password": "Short1A"})
            assert weak.status_code in (400, 422), weak.text

            created = await admin.post("/api/v1/workspace/members", json={
                "email": "invited@acme.io", "full_name": "Invited",
                "role": "ANALYST", "password": "Handover@12345"})
            assert created.status_code == 201, created.text

        async with deployment.client() as invited:
            login = await invited.post("/api/v1/auth/login", json={
                "email": "invited@acme.io", "password": "Handover@12345"})
            assert login.status_code == 200, login.text
            assert login.json()["password_change_required"] is True, (
                "an account whose password somebody else chose must replace it")
            assert (await invited.get("/api/v1/auth/me")).status_code == 200
            assert (await invited.get("/api/v1/pipelines")).status_code == 403
