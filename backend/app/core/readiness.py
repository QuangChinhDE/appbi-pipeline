"""Is this deployment configured to do what it claims?

A misconfigured engine does not announce itself. It looks like a working
platform until the first user presses "Test connection", and then fails with a
message about a connector image or a timeout — three layers away from the actual
cause, which was a missing environment variable.

So the configuration is checked once, at boot, and the result is stated plainly:
what engine this is, whether it can be reached, and if not, exactly which
setting is wrong. In production a broken configuration stops the process; there
is no useful degraded mode for a control plane that cannot reach its engine.
"""

from __future__ import annotations

import logging
import pathlib
import time
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import log_event

logger = logging.getLogger(__name__)

DOCKER_SOCKET = pathlib.Path("/var/run/docker.sock")


@dataclass(slots=True)
class Readiness:
    ok: bool
    engine_type: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "; ".join(self.problems) if self.problems else "ready"


def _check_embedded() -> list[str]:
    if DOCKER_SOCKET.exists():
        return []
    return [
        "ENGINE_TYPE=AIRBYTE_EMBEDDED needs /var/run/docker.sock, which is not mounted. "
        "Use docker-compose.embedded.yml for a local demo, or set ENGINE_TYPE=AIRBYTE_API "
        "and point AIRBYTE_API_URL at an Airbyte deployment."
    ]


def _check_api() -> list[str]:
    problems: list[str] = []

    url = (settings.airbyte_api_url or "").strip()
    if not url:
        problems.append("AIRBYTE_API_URL is empty; ENGINE_TYPE=AIRBYTE_API has nothing to call.")
    else:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            problems.append(f"AIRBYTE_API_URL is not a usable URL: {url!r}")

    if not (settings.airbyte_workspace_id or "").strip():
        if settings.is_production or not settings.airbyte_workspace_auto:
            problems.append(
                "AIRBYTE_WORKSPACE_ID is empty; every resource this product creates has to "
                "belong to an Airbyte workspace. Find the id with "
                "`python scripts/airbyte-workspace.py list`."
                + (" AIRBYTE_WORKSPACE_AUTO does not apply in production: which tenant "
                   "receives customer data is a decision, not a default."
                   if settings.is_production and settings.airbyte_workspace_auto else "")
            )

    # Credentials are optional — a deployment on a private network may not use
    # basic auth — but half a credential is always a mistake.
    user = (settings.airbyte_api_username or "").strip()
    password = (settings.airbyte_api_password or "").strip()
    if bool(user) != bool(password):
        problems.append(
            "AIRBYTE_API_USERNAME and AIRBYTE_API_PASSWORD must be set together."
        )

    client_id = (settings.airbyte_client_id or "").strip()
    client_secret = (settings.airbyte_client_secret or "").strip()
    if bool(client_id) != bool(client_secret):
        problems.append(
            "AIRBYTE_CLIENT_ID and AIRBYTE_CLIENT_SECRET must be set together."
        )

    # Production must not reach an auth-enabled engine with no credentials, and
    # must not reach one with the wrong scheme. Airbyte 1.x with auth on
    # answers HTTP Basic with 401 -- including the instance admin's own login --
    # so Basic against a 1.x deployment is a configuration error that only
    # shows up as an engine outage.
    if settings.is_production and not (client_id or user):
        problems.append(
            "APP_ENV=production with no engine credentials. An auth-enabled "
            "Airbyte refuses every call; set AIRBYTE_CLIENT_ID and "
            "AIRBYTE_CLIENT_SECRET (Airbyte 1.x) or AIRBYTE_API_USERNAME and "
            "AIRBYTE_API_PASSWORD (0.59.x)."
        )
    if client_id and user:
        problems.append(
            "Both client-credentials and Basic credentials are configured for "
            "the engine. Pick one: the adapter prefers client credentials, so "
            "the Basic pair is silently unused and will mislead whoever reads "
            "this configuration next."
        )

    return problems


def check_configuration() -> Readiness:
    """Static checks only — no network. Safe to call before anything is running."""
    engine_type = (settings.engine_type or "").upper()

    if engine_type == "AIRBYTE_EMBEDDED":
        problems = _check_embedded()
        notes = ["Embedded engine: connector images run on the host Docker daemon. "
                 "This is a local/demo path, not a production one."]
    elif engine_type == "AIRBYTE_API":
        problems = _check_api()
        workspace = (settings.airbyte_workspace_id or "").strip()
        notes = [
            f"Airbyte API engine at {settings.airbyte_api_url or '(unset)'}",
            f"workspace {workspace}" if workspace
            else "workspace resolved automatically (local only; one workspace required)",
        ]
    else:
        problems = [f"ENGINE_TYPE={engine_type!r} is not a known engine."]
        notes = []

    if settings.is_production and engine_type == "AIRBYTE_EMBEDDED":
        problems.append(
            "APP_ENV=production with ENGINE_TYPE=AIRBYTE_EMBEDDED. The embedded runner "
            "reproduces none of the job, retry, isolation or upgrade semantics an Airbyte "
            "deployment provides; production runs on ENGINE_TYPE=AIRBYTE_API."
        )

    if settings.is_production:
        # The session cookie carries the whole session. Without `Secure` a
        # browser will send it over plain HTTP, and the default was false --
        # so a production deployment behind TLS was still issuing a cookie
        # that would leak on the first downgraded request.
        if not settings.cookie_secure:
            problems.append(
                "APP_ENV=production with COOKIE_SECURE=false. The session "
                "cookie would be sent over plain HTTP. Set COOKIE_SECURE=true; "
                "if this deployment genuinely has no TLS, fix that first.")

        # Demo identities are published in this repository.
        if settings.seed_demo_data:
            problems.append(
                "APP_ENV=production with SEED_DEMO_DATA=true. That creates "
                "admin@appbi.local and three more accounts with a password "
                "published in this repository.")

    return Readiness(ok=not problems, engine_type=engine_type,
                     problems=problems, notes=notes)


async def check_engine_reachable() -> list[str]:
    """Ask the engine whether it is actually there.

    Separate from the static checks because it needs the network: a deployment
    can be configured correctly and still be starting up, and that is worth
    distinguishing from a configuration error.
    """
    from app.adapters.registry import get_adapter

    try:
        health = await get_adapter().health()
    except Exception as exc:  # noqa: BLE001 - any failure here is "not reachable"
        return [f"engine did not answer a health check: {type(exc).__name__}: {str(exc)[:200]}"]

    if not health.reachable:
        return [f"engine reported itself unreachable: {health.detail or health.status}"]
    return []


# ── live dependency probe ────────────────────────────────────────────────────
# Distinct from the checks above, which only read configuration. This one asks
# each dependency whether it is actually answering, and it is what /readyz is
# built on.


@dataclass(slots=True)
class DependencyState:
    name: str
    ok: bool
    detail: str | None = None
    required: bool = True

    def as_dict(self) -> dict:
        out: dict = {"ok": self.ok, "required": self.required}
        if self.detail:
            out["detail"] = self.detail
        return out


async def _probe_database() -> DependencyState:
    from sqlalchemy import text

    from app.core.db import SessionLocal

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return DependencyState("database", False,
                               f"{type(exc).__name__}: {str(exc)[:200]}")
    return DependencyState("database", True)


# A load balancer polls /readyz every few seconds. Asking Airbyte over HTTP each
# time turns a health check into a traffic source, so the shallow answer is
# reused for a few seconds. A deep probe always asks for real, because the whole
# point of it is to find out what is true right now.
_ENGINE_CACHE_SECONDS = 5.0
_engine_cache: tuple[float, DependencyState] | None = None


async def _probe_engine(*, required: bool, fresh: bool) -> DependencyState:
    global _engine_cache

    if not fresh and _engine_cache is not None:
        cached_at, cached = _engine_cache
        if (time.monotonic() - cached_at) < _ENGINE_CACHE_SECONDS:
            return replace(cached, required=required)

    problems = await check_engine_reachable()
    state = (DependencyState("engine", False, problems[0], required=required)
             if problems else DependencyState("engine", True, required=required))
    _engine_cache = (time.monotonic(), state)
    return state


async def probe(*, deep: bool) -> tuple[bool, dict]:
    """Whether this instance should be sent traffic, and why.

    Two different questions get asked here, and conflating them is the mistake
    this signature exists to prevent.

    Shallow (`deep=False`) is what a load balancer asks: can this process serve
    requests at all? The database is required, because without it every request
    fails. The engine is reported but not required, because taking the control
    plane out of rotation while Airbyte is down converts a partial outage into a
    total one — nobody could then read run history, see why the engine is down,
    or acknowledge the alert saying so. An operator who genuinely wants the
    strict behaviour sets READINESS_REQUIRE_ENGINE.

    Deep (`deep=True`) is what a deploy gate or smoke test asks: is the whole
    dependency chain healthy? Everything is required. This is the one to point a
    release check at, and the one that must never be wired to a load balancer.
    """
    engine_required = deep or settings.readiness_require_engine

    database = await _probe_database()
    engine = await _probe_engine(required=engine_required, fresh=deep)

    states = [database, engine]
    ok = all(state.ok for state in states if state.required)

    return ok, {
        "status": "ready" if ok else "not_ready",
        "mode": "deep" if deep else "shallow",
        "engine_type": (settings.engine_type or "").upper(),
        "dependencies": {state.name: state.as_dict() for state in states},
    }


# ── database separation ──────────────────────────────────────────────────────
# Guardrail 2 says the product never reads Airbyte's metadata database. Until
# now that was enforced by discipline: on the staging stack the product's role
# could read all 47 of Airbyte's tables, and nothing would have noticed a
# service quietly starting to do so.
#
# Tables that exist in an Airbyte configuration database and in no schema this
# product owns. Matching several of them, rather than one, avoids tripping over
# a product table that happens to share a name.
AIRBYTE_TABLES = frozenset({
    "actor", "actor_definition", "actor_catalog", "connection",
    "workspace", "attempts", "state", "stream_reset",
})


async def check_database_separation() -> tuple[list[str], list[str]]:
    """Is the product's database its own?  Returns (problems, warnings).

    Two different mistakes, with different severities.

    *Same database* — the product's DATABASE_URL points at the schema Airbyte
    owns. There is no legitimate reason for this: Airbyte migrates that schema
    on its own upgrade schedule, the product's migrations would collide with
    it, and the guardrail would be violated by construction. Fatal.

    *Same instance, different database* — a cost decision someone may make
    knowingly. It is still worth saying out loud, because it leaves the
    guardrail one connection string away from being broken, couples two
    upgrade schedules, and lets Airbyte's job history fill a disk the control
    plane needs. Warned, not refused.
    """
    from sqlalchemy import text

    from app.core.db import SessionLocal

    problems: list[str] = []
    warnings: list[str] = []

    try:
        async with SessionLocal() as session:
            # Every non-system schema, not just `public`. Airbyte's bootloader
            # puts its schema in `public` by default, but `search_path` is a
            # connection setting and a deployment that moved it would have
            # slipped past a public-only scan -- the check would have passed on
            # exactly the database it exists to refuse.
            present = set((await session.scalars(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN "
                "('pg_catalog', 'information_schema') "
                "AND table_schema NOT LIKE 'pg_toast%' "
                "AND table_schema NOT LIKE 'pg_temp%'"
            ))).all())

            overlap = AIRBYTE_TABLES & present
            if len(overlap) >= 3:
                problems.append(
                    "DATABASE_URL points at a database that contains Airbyte's "
                    f"own schema ({', '.join(sorted(overlap)[:4])}, ...). The "
                    "product must never share a database with the engine: "
                    "Airbyte migrates that schema on its own schedule and this "
                    "product's migrations would collide with it. Give the "
                    "product a database of its own."
                )

            # Same instance is visible from pg_database when the role may see it.
            try:
                neighbours = set((await session.scalars(text(
                    "SELECT datname FROM pg_database WHERE datname NOT IN "
                    "('postgres', 'template0', 'template1')"
                ))).all())
            except Exception:  # noqa: BLE001 - not every deployment permits this
                neighbours = set()

            if "airbyte" in neighbours and settings.is_production:
                warnings.append(
                    "Airbyte's database is on the same Postgres instance as the "
                    "product's, and the product's role can see it. That is a "
                    "cost decision, not an error, but it couples two upgrade "
                    "schedules and leaves guardrail 2 one connection string "
                    "away from being broken. Separate instances in production; "
                    "see docs/ADR-001-database-topology.md."
                )
    except Exception as exc:  # noqa: BLE001 - the DB probe reports its own faults
        log_event(logger, logging.WARNING, "startup.separation_check_skipped",
                  error=str(exc)[:200])

    return problems, warnings


async def enforce_at_startup() -> None:
    """Run the checks and act on them.

    Production stops on a broken configuration. Everywhere else it logs loudly
    and keeps going, because a developer editing settings should not have to
    fight the process to get back to a working state.
    """
    readiness = check_configuration()

    for note in readiness.notes:
        log_event(logger, logging.INFO, "startup.engine", detail=note)

    # Whether the product's database is its own. Checked here rather than in
    # check_configuration because it needs a connection.
    separation_problems, separation_warnings = await check_database_separation()
    for warning in separation_warnings:
        log_event(logger, logging.WARNING, "startup.database_shared", detail=warning)
    readiness.problems.extend(separation_problems)
    readiness.ok = not readiness.problems

    if readiness.ok:
        return

    for problem in readiness.problems:
        log_event(logger, logging.ERROR, "startup.misconfigured",
                  engine_type=readiness.engine_type, detail=problem)

    if separation_problems:
        raise RuntimeError(
            "Refusing to start: " + separation_problems[0]
        )

    if settings.is_production:
        raise RuntimeError(
            "Refusing to start with an unusable engine configuration: " + readiness.summary
        )


async def probe_engine_at_startup() -> None:
    """Say, at boot, whether the engine is answering.

    Deliberately not fatal by default, even in production. A configuration
    error can never fix itself, so refusing to start is right. Unreachability
    is different: on a fresh deployment the engine is usually still booting
    alongside this process, and dying on that produces a crash loop whose cause
    looks like the product rather than the ordering. The state is logged, it is
    visible on /readyz, and STARTUP_REQUIRE_ENGINE turns it into a hard failure
    for deployments that would rather not start at all.
    """
    problems = await check_engine_reachable()
    if not problems:
        log_event(logger, logging.INFO, "startup.engine_reachable",
                  engine_type=(settings.engine_type or "").upper())
        return

    for problem in problems:
        log_event(logger, logging.ERROR, "startup.engine_unreachable",
                  engine_type=(settings.engine_type or "").upper(), detail=problem)

    if settings.startup_require_engine:
        raise RuntimeError("STARTUP_REQUIRE_ENGINE is set and the engine did not answer: "
                           + problems[0])
