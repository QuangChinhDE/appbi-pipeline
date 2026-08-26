"""The Base.vn connectors this product ships.

Native, in the sense that matters: they are Python in this repository, they
compile to Airbyte declarative manifests at import time, and they are seeded
into the catalogue like any other connector. Nothing to import, nothing to copy,
nothing to configure per machine. Move the repository to another laptop and they
are there.

    Project  ->  BaseConnector  ->  Stream  ->  manifest  ->  engine

The shape that makes multi-workspace work:

* the connector definition and its API logic are shared — one row in
  `connector_definitions`, one manifest, recompiled from this code on every
  deploy
* the token is per workspace, held in the encrypted secret store like every
  other credential, and reaches the connector as `access_token_v2`

So fixing a Base API change means editing this package once. Every workspace
using that connector picks it up on the next deploy, because `seed_catalog()`
overwrites the stored manifest from here rather than treating it as
user-owned data.

Why not keep the YAML
---------------------

The ten manifests in `docs/base-api/` worked, and they are the origin of
everything here. They were also ten copies of the same conventions, which is how
they drifted: eight of twenty-five HRM streams paginated, one application
required `domain` and `version` config to reach a fixed host, one had a
colleague's project id in the request body, two had a stream declared twice, and
all ten sent the credential under a field name Base no longer accepts. Those are
not YAML's fault, but a single Python definition with a shared compiler makes
each of them a one-line fix instead of ten.
"""

from __future__ import annotations

from typing import Any

from ._shared import (
    RUNNER_REPOSITORY, RUNNER_VERSION, TOKEN_FIELD, BaseConnector,
    ConfigField, Incremental, Parent, Stream, compile_manifest,
    connection_specification,
)
from .core import ACCOUNT, REQUEST, TIMEOFF, WORKFLOW
from .finance import INCOME
from .hr import HIRING, HRM
from .work import PAYROLL, SERVICE, WEWORK

#: Every Base connector, in the order the catalogue should show them.
CONNECTORS: tuple[BaseConnector, ...] = (
    ACCOUNT,
    HRM,
    HIRING,
    WORKFLOW,
    REQUEST,
    SERVICE,
    WEWORK,
    TIMEOFF,
    PAYROLL,
    INCOME,
)

# Checked at import, so a broken definition fails the process rather than
# reaching a customer's catalogue and failing at sync time.
for _connector in CONNECTORS:
    _connector.validate()

BY_KEY: dict[str, BaseConnector] = {c.connector_key: c for c in CONNECTORS}


def manifests() -> dict[str, dict[str, Any]]:
    """Compiled manifest per connector key."""
    return {c.connector_key: compile_manifest(c) for c in CONNECTORS}


def catalogue_entries() -> list[dict[str, Any]]:
    """Registry rows, in the same shape as the bundled connector registry.

    Returned as plain dicts so `adapters.registry` can build
    `ConnectorMetadata` from these and from the JSON file through one code path.
    """
    entries: list[dict[str, Any]] = []
    for connector in CONNECTORS:
        streams = connector.streams
        entries.append({
            "connector_key": connector.connector_key,
            "display_name": connector.title,
            "connector_type": "SOURCE",
            "category": "Base.vn",
            "description": connector.summary,
            "icon": connector.connector_key,
            "icon_url": "",
            "documentation_url": connector.docs_url,
            # A declarative connector runs on the generic runner; the manifest
            # is the connector. Pinning the runner is what pins behaviour.
            "docker_repository": RUNNER_REPOSITORY,
            "version": RUNNER_VERSION,
            "release_stage": "beta",
            "support_level": "certified",
            "supports_oauth": False,
            "supports_incremental": any(s.incremental for s in streams),
            "supports_cdc": False,
            "supports_namespaces": False,
            "supported_destination_sync_modes": [],
            "spec_schema": connection_specification(connector),
            "declarative_manifest": compile_manifest(connector),
            # Shipped and supported: this product wrote them and tests them.
            "certification": "SUPPORTED",
            "stream_count": len(streams),
        })
    return entries


def stream_inventory() -> list[dict[str, Any]]:
    """Stream → endpoint, for the handover document and for tests.

    Generated rather than written down, so the documentation cannot drift from
    what the connectors actually do.
    """
    rows: list[dict[str, Any]] = []
    for connector in CONNECTORS:
        for stream in connector.streams:
            rows.append({
                "connector": connector.connector_key,
                "app": connector.app,
                "stream": stream.name,
                "endpoint": connector.url_base + stream.path,
                "collection": ".".join(stream.collection),
                "primary_key": list(stream.primary_key),
                "incremental": stream.incremental.field if stream.incremental else None,
                "filter_param": stream.incremental.param if stream.incremental else None,
                "paginated": stream.paginate,
                "parent": stream.parent.stream if stream.parent else None,
                "parent_field": stream.parent.inject if stream.parent else None,
                "note": stream.note,
            })
    return rows


__all__ = [
    "ACCOUNT", "BY_KEY", "CONNECTORS", "HIRING", "HRM", "INCOME", "PAYROLL",
    "REQUEST", "SERVICE", "TIMEOFF", "WEWORK", "WORKFLOW",
    "BaseConnector", "ConfigField", "Incremental", "Parent", "Stream",
    "RUNNER_REPOSITORY", "RUNNER_VERSION", "TOKEN_FIELD",
    "catalogue_entries", "compile_manifest", "manifests", "stream_inventory",
]
