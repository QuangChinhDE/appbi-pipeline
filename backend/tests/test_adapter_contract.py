"""Adapter contract suite — the gate for an engine upgrade (spec section 29.3).

The 15 scenarios the spec requires, expressed against `IntegrationEngineAdapter`
rather than against Airbyte. Any adapter that passes these can back the product.

Structural checks run everywhere. The scenarios that need a live engine are
skipped unless RUN_ENGINE_CONTRACT=1, so CI can gate on the cheap half and a
release can gate on the full half.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.adapters.base import IntegrationEngineAdapter
from app.adapters.dto import (
    ConfiguredStream, ConnectorDescriptor, EngineActorRequest, EngineConnectionRequest,
    EngineSyncRequest,
)
from app.adapters.registry import bundled_by_key, get_adapter
from app.models.enums import EngineStatus, RunStatus

LIVE = os.getenv("RUN_ENGINE_CONTRACT") == "1"
live_only = pytest.mark.skipif(not LIVE, reason="set RUN_ENGINE_CONTRACT=1 to run against a live engine")

CONTRACT_OPERATIONS = [
    "health", "list_connector_metadata", "get_connector_spec",
    "create_source", "update_source", "delete_source", "check_source", "discover_source",
    "create_destination", "update_destination", "delete_destination", "check_destination",
    "create_connection", "update_connection", "delete_connection",
    "trigger_sync", "get_job", "cancel_job", "get_job_logs", "close",
    # Added for reconcile: after a restore beside a different engine
    # deployment, every mapping is a handle into a deployment that is gone.
    "resource_exists",
]

FAKER = ConnectorDescriptor("source-faker", "airbyte/source-faker", "7.2.1")
FAKER_CONFIG = {"count": 20, "seed": 1, "records_per_slice": 20, "parallelism": 1}

DEST = ConnectorDescriptor("destination-postgres", "airbyte/destination-postgres", "3.0.17")


def dest_config(schema: str) -> dict:
    return {
        "host": "postgres", "port": 5432, "database": "demo_warehouse", "schema": schema,
        "username": "demo_writer", "password": "demo_writer_pw",
        "ssl_mode": {"mode": "disable"},
        "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
    }


# ── structural: both adapters must satisfy one interface ───────────────────

def all_adapter_classes() -> list[type]:
    """Every adapter in the tree, found rather than listed.

    A hand-kept list is how a new adapter gets written and never gated: the
    suite stays green because it was never asked about the new one.
    """
    import importlib
    import pkgutil

    import app.adapters as package

    found: list[type] = []
    for module in pkgutil.iter_modules(package.__path__):
        if not module.ispkg:
            continue
        try:
            adapter_module = importlib.import_module(
                f"app.adapters.{module.name}.adapter")
        except ImportError:
            continue
        for name in dir(adapter_module):
            candidate = getattr(adapter_module, name)
            if (isinstance(candidate, type)
                    and name.endswith("Adapter")
                    and candidate.__module__ == adapter_module.__name__):
                found.append(candidate)
    return found


def test_every_adapter_implements_the_contract() -> None:
    adapters = all_adapter_classes()
    assert len(adapters) >= 3, (
        f"expected at least three adapters, found {[a.__name__ for a in adapters]}")

    for adapter_cls in adapters:
        missing = [op for op in CONTRACT_OPERATIONS if not hasattr(adapter_cls, op)]
        assert not missing, f"{adapter_cls.__name__} is missing {missing}"
        # Recorded on every adapter so an upgrade test can say which contract
        # version it certified.
        assert adapter_cls.contract_version


def test_a_non_airbyte_adapter_satisfies_the_interface() -> None:
    """The claim that this abstracts an engine, rather than abstracting Airbyte.

    Two Airbyte implementations behind one interface prove nothing about the
    interface — they share a protocol, a catalog shape and a job model. This
    one shares none of that: no connector images, no Airbyte Protocol, no
    server-side connection or job objects. If the boundary were Airbyte-shaped,
    it would not fit. See docs/ENGINE-PORTABILITY.md.
    """
    from app.adapters.base import IntegrationEngineAdapter
    from app.adapters.sql_direct.adapter import SqlDirectAdapter

    assert isinstance(SqlDirectAdapter(), IntegrationEngineAdapter)


def test_only_a_confirmed_absence_is_reported_as_missing() -> None:
    """Tightened after PM v10. The earlier rule here was itself the bug.

    This test used to require `except EngineOperationError`, which is every
    4xx -- so a 401 from a rotated credential, a 403, or a 429 all answered
    "the resource is not there". `resource_exists` returning False tells an
    operator to recreate a resource, and worker recovery reads the same
    distinction to decide whether to fail a run. Both remediations are
    destructive when the real answer was "the engine did not say".

    Only `EngineResourceGoneError` -- a 404, or Airbyte's own "could not find
    configuration" wording -- means absence.
    """
    import inspect

    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter

    source = inspect.getsource(AirbyteApiAdapter.resource_exists)
    assert "except EngineResourceGoneError" in source, (
        "only a confirmed not-found may answer False")
    assert "except EngineOperationError" not in source, (
        "a generic 4xx covers 401/403/429, none of which mean the resource is gone")
    assert "except Exception" not in source
    assert "except EngineUnavailableError" not in source


def test_the_adapter_separates_gone_from_could_not_answer() -> None:
    """The taxonomy itself, at the one place that maps HTTP onto meaning."""
    import inspect

    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter
    from app.core.errors import (
        EngineOperationError, EngineResourceGoneError, EngineUnavailableError,
    )

    # Gone is a kind of operation error, so existing handlers still work, but
    # it can be caught on its own where the distinction matters.
    assert issubclass(EngineResourceGoneError, EngineOperationError)

    source = inspect.getsource(AirbyteApiAdapter._post)
    assert "response.status_code == 429" in source, "a rate limit is not an answer"
    assert "(401, 403)" in source, "auth failure is not an answer"
    assert "EngineResourceGoneError" in source
    # 429 and 5xx both mean "ask again later", so both raise the unavailable
    # error rather than anything a caller might read as a verdict.
    rate_limit = source[source.index("response.status_code == 429"):]
    assert "EngineUnavailableError" in rate_limit.split("if response.status_code in")[0]


def test_an_engine_may_refuse_declarative_connectors() -> None:
    """Not every engine can run a manifest, and it has to be able to say so.

    Returning None makes `publish` fail with a reason instead of recording an
    image the engine will never pull and surfacing it later as a sync failure.
    """
    from app.adapters.sql_direct.adapter import SqlDirectAdapter

    assert SqlDirectAdapter().declarative_runner() is None


def test_no_layer_above_the_adapter_imports_an_engine() -> None:
    """The boundary, checked as a fact about imports rather than an intention.

    `schema_service` used to import the Airbyte protocol module for a function
    that hashes a JSON schema — nothing engine-specific, just misfiled. It is
    the kind of leak that is invisible until someone writes a second engine.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if "adapters" in path.relative_to(root).parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if "adapters.airbyte" in stripped or "adapters.sql_direct" in stripped:
                offenders.append(f"{path.relative_to(root)}: {stripped}")

    assert not offenders, (
        "these import an engine implementation directly instead of going "
        f"through the adapter registry: {offenders}")


def test_adapter_satisfies_the_runtime_protocol() -> None:
    assert isinstance(get_adapter(), IntegrationEngineAdapter)


def test_bundled_registry_declares_pinned_versions() -> None:
    for key in ("source-postgres", "source-faker", "destination-postgres"):
        metadata = bundled_by_key(key)
        assert metadata is not None, key
        assert metadata.version != "latest", "guardrail 6: never deploy `latest`"
        assert metadata.spec_schema.get("properties"), key


def test_no_engine_vocabulary_leaks_above_the_adapter() -> None:
    """Guardrail 5: only the adapter package may mention the engine."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("adapters/") or relative == "bootstrap.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        # `airbyte` may appear in a comment explaining the boundary, but never
        # as an import of engine internals.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "airbyte" in stripped and "import" in stripped:
                offenders.append(f"{relative}: {stripped[:90]}")
    allowed = {"resources", "airbyte_protocol", "airbyte_api"}
    real = [o for o in offenders if not any(token in o for token in allowed)]
    assert not real, f"engine imports outside the adapter: {real}"


# ── 1-2: engine reachable, catalog readable ────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_01_engine_health() -> None:
    health = await get_adapter().health()
    assert health.reachable is True
    assert health.status is EngineStatus.HEALTHY


@live_only
@pytest.mark.asyncio
async def test_02_list_connector_metadata() -> None:
    metadata = await get_adapter().list_connector_metadata()
    assert metadata
    assert all(item.docker_repository and item.version for item in metadata)


@live_only
@pytest.mark.asyncio
async def test_03_read_connector_spec_from_the_image() -> None:
    spec = await get_adapter().get_connector_spec(FAKER)
    assert spec.spec_schema.get("properties")


# ── 4-5: source create + check ─────────────────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_04_create_source() -> None:
    ref = await get_adapter().create_source(EngineActorRequest(
        workspace_id=uuid.uuid4(), product_resource_id=uuid.uuid4(),
        name="contract-source", connector=FAKER, configuration=FAKER_CONFIG,
    ))
    assert ref.ref


@live_only
@pytest.mark.asyncio
async def test_05_check_source_succeeds() -> None:
    result = await get_adapter().check_source(FAKER, FAKER_CONFIG)
    assert result.succeeded, result.technical_message


# ── 6: discover ────────────────────────────────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_06_discover_returns_capabilities() -> None:
    catalog = await get_adapter().discover_source(FAKER, FAKER_CONFIG)
    assert catalog.streams
    assert catalog.catalog_hash
    users = next(s for s in catalog.streams if s.name == "users")
    assert "full_refresh" in users.supported_sync_modes
    assert users.json_schema.get("properties")


# ── 7-8: destination create + check ────────────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_07_create_destination() -> None:
    ref = await get_adapter().create_destination(EngineActorRequest(
        workspace_id=uuid.uuid4(), product_resource_id=uuid.uuid4(),
        name="contract-destination", connector=DEST,
        configuration=dest_config("contract_probe"),
    ))
    assert ref.ref


@live_only
@pytest.mark.asyncio
async def test_08_check_destination_succeeds() -> None:
    result = await get_adapter().check_destination(DEST, dest_config("contract_probe"))
    assert result.succeeded, result.technical_message


# ── 9-12: connection + a real sync to a terminal state ─────────────────────

@live_only
@pytest.mark.asyncio
async def test_09_to_12_connection_and_sync() -> None:
    import asyncio

    adapter = get_adapter()
    pipeline_id = uuid.uuid4()
    schema = f"contract_{uuid.uuid4().hex[:8]}"

    catalog = await adapter.discover_source(FAKER, FAKER_CONFIG)
    users = next(s for s in catalog.streams if s.name == "users")
    streams = [ConfiguredStream(
        name=users.name, json_schema=users.json_schema,
        sync_mode="full_refresh", destination_sync_mode="overwrite",
    )]

    connection = await adapter.create_connection(EngineConnectionRequest(
        workspace_id=uuid.uuid4(), product_resource_id=pipeline_id,
        name="contract-connection", source_ref="s", destination_ref="d", streams=streams,
    ))
    assert connection.ref

    job = await adapter.trigger_sync(EngineSyncRequest(
        workspace_id=uuid.uuid4(), pipeline_id=pipeline_id, run_id=uuid.uuid4(),
        connection_ref=connection.ref, source=FAKER, destination=DEST,
        source_config=FAKER_CONFIG, destination_config=dest_config(schema),
        streams=streams, timeout_seconds=900,
    ))
    assert job.ref

    # 11: a running job reports a non-terminal product status.
    running = await adapter.get_job(job.ref)
    assert running.status in (RunStatus.STARTING, RunStatus.RUNNING, RunStatus.SUCCEEDED)

    # 12: it reaches a terminal status with counts.
    for _ in range(180):
        status = await adapter.get_job(job.ref)
        if status.status.is_terminal:
            break
        await asyncio.sleep(2)
    assert status.status is RunStatus.SUCCEEDED, (
        status.failure.technical_message if status.failure else status.raw_status
    )
    assert (status.records_synced or 0) > 0

    # Logs are readable and chunked.
    logs = await adapter.get_job_logs(job.ref, cursor=0, limit=50)
    assert logs.lines

    await adapter.delete_connection(connection.ref)


# ── 13: cancel is idempotent ───────────────────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_13_cancel_is_idempotent() -> None:
    adapter = get_adapter()
    unknown = "embedded://job/does-not-exist"
    first = await adapter.cancel_job(unknown)
    second = await adapter.cancel_job(unknown)
    assert first.status is second.status  # never raises, never flip-flops


# ── 14-15: failure mapping ─────────────────────────────────────────────────

@live_only
@pytest.mark.asyncio
async def test_14_bad_credentials_map_to_authentication() -> None:
    from app.core.errors import ErrorCategory

    postgres = ConnectorDescriptor("source-postgres", "airbyte/source-postgres", "3.8.5")
    result = await get_adapter().check_source(postgres, {
        "host": "postgres", "port": 5432, "database": "demo_source",
        "schemas": ["shop"], "username": "demo_reader", "password": "wrong-password",
        "ssl_mode": {"mode": "disable"},
        "replication_method": {"method": "Standard"},
        "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
    })
    assert result.succeeded is False
    assert result.category is ErrorCategory.AUTHENTICATION
    assert result.error_code == "SOURCE_AUTHENTICATION_FAILED"


@live_only
@pytest.mark.asyncio
async def test_15_unreachable_host_maps_to_network() -> None:
    from app.core.errors import ErrorCategory

    postgres = ConnectorDescriptor("source-postgres", "airbyte/source-postgres", "3.8.5")
    result = await get_adapter().check_source(postgres, {
        "host": "no-such-host.invalid", "port": 5432, "database": "demo_source",
        "schemas": ["shop"], "username": "demo_reader", "password": "x",
        "ssl_mode": {"mode": "disable"},
        "replication_method": {"method": "Standard"},
        "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
    })
    assert result.succeeded is False
    assert result.category in (ErrorCategory.NETWORK, ErrorCategory.CONFIGURATION)


def test_both_log_shapes_are_read() -> None:
    """0.59.1 returns `logLines`; 1.8.5 returns `events` and leaves it empty.

    Reading only the first is not an error anywhere. It is a job with no logs
    — which is how the Kubernetes certification run reported every sync, and
    how an operator debugging a failure would find the log view blank with
    nothing to say why. Silent emptiness is the worst possible failure for a
    log view, so both shapes are read and neither is switched on a version
    number.
    """
    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter

    legacy = AirbyteApiAdapter._log_lines({"logLines": ["one", "two"]})
    assert legacy == ["one", "two"]

    structured = AirbyteApiAdapter._log_lines({
        "version": 1,
        "logLines": [],
        "events": [
            {"timestamp": 1787501553837, "message": "APPLY Stage: BUILD",
             "level": "info", "logSource": "platform"},
            {"message": "no timestamp here"},
        ],
    })
    assert len(structured) == 2
    assert "APPLY Stage: BUILD" in structured[0]
    assert "INFO" in structured[0] and "platform" in structured[0]
    assert "2026-" in structured[0], "the timestamp should be rendered, not dropped"
    # An event missing every optional field still produces its message rather
    # than a line of empty prefixes.
    assert structured[1] == "no timestamp here"

    assert AirbyteApiAdapter._log_lines({}) == []
