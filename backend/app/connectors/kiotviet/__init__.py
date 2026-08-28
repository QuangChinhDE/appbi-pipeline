"""KiotViet Retail, shipped the way the Base.vn connectors are shipped.

Same mechanism, different dialect. The connector is Python in this package,
compiled to a declarative manifest at import, and joined into the bundled
catalogue by `adapters.registry` -- so it is seeded, listed, permissioned and
synced through exactly the same path as an Airbyte connector. There is no
second kind of connector for the rest of the product to know about.

What is *not* shared with `base_vn` is the request dialect: KiotViet is GET with
query parameters, a bearer token the connector fetches for itself, and one host
for every shop with a `Retailer` header choosing between them. See `_shared.py`
for why that gets its own compiler rather than another set of flags on Base's.

Fixing a KiotViet API change means editing this package once. Every workspace
picks it up on the next deploy: `seed_catalog()` overwrites the stored manifest
from here, and `actors.republish_manifests()` pushes it to the sources that
already exist.
"""

from __future__ import annotations

from typing import Any

from app.connectors import ConnectorProvider

from ._shared import (
    Incremental, KiotVietConnector, Stream, compile_manifest,
    connection_specification,
)
from .catalog import KIOTVIET

#: Every KiotViet connector. One today; the tuple is the seam for KiotViet
#: FnB or Retail-specific variants without changing anything that reads it.
CONNECTORS: tuple[KiotVietConnector, ...] = (KIOTVIET,)

# Checked at import, so a broken definition fails the process rather than
# reaching a customer's catalogue and failing at sync time.
for _connector in CONNECTORS:
    _connector.validate()

BY_KEY: dict[str, KiotVietConnector] = {c.connector_key: c for c in CONNECTORS}


def manifests() -> dict[str, dict[str, Any]]:
    """Compiled manifest per connector key."""
    return {c.connector_key: compile_manifest(c) for c in CONNECTORS}


def catalogue_entries() -> list[dict[str, Any]]:
    """Registry rows, in the same shape as the bundled connector registry."""
    # Imported here rather than at module scope: `base_vn` owns the runner pin,
    # and importing it at the top would make the two packages import-cyclic the
    # first time either needs something from the other.
    from app.connectors.base_vn._shared import RUNNER_REPOSITORY, RUNNER_VERSION

    entries: list[dict[str, Any]] = []
    for connector in CONNECTORS:
        streams = connector.streams
        entries.append({
            "connector_key": connector.connector_key,
            "display_name": connector.title,
            "connector_type": "SOURCE",
            "category": "KiotViet",
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
            # The connector performs the OAuth exchange itself from a client id
            # and secret. There is no consent screen and nothing for the
            # product's OAuth machinery to do, so this stays false.
            "supports_oauth": False,
            "supports_incremental": any(s.incremental for s in streams),
            "supports_cdc": False,
            "supports_namespaces": False,
            "supported_destination_sync_modes": [],
            "spec_schema": connection_specification(connector),
            "declarative_manifest": compile_manifest(connector),
            # BETA until a shop with data has run it end to end. The request
            # side is measured; record parsing on the commercial collections is
            # not, because the shop available here is empty. See `catalog.py`.
            "certification": "BETA",
            "stream_count": len(streams),
        })
    return entries


def stream_inventory() -> list[dict[str, Any]]:
    """Stream → endpoint, generated so documentation cannot drift from code."""
    rows: list[dict[str, Any]] = []
    for connector in CONNECTORS:
        for stream in connector.streams:
            rows.append({
                "connector": connector.connector_key,
                "app": connector.app,
                "stream": stream.name,
                "endpoint": stream.path,
                "collection": ".".join(stream.collection) or "(toàn bộ phản hồi)",
                "primary_key": list(stream.primary_key),
                "incremental": stream.incremental.field if stream.incremental else None,
                "paginated": stream.paginate,
                "note": stream.note,
            })
    return rows


#: How `app.connectors` finds this group.
PROVIDER = ConnectorProvider(
    key="kiotviet",
    category="KiotViet",
    title="KiotViet",
    entries=catalogue_entries,
)


__all__ = [
    "BY_KEY", "CONNECTORS", "KIOTVIET", "PROVIDER", "Incremental", "KiotVietConnector",
    "Stream", "catalogue_entries", "compile_manifest",
    "connection_specification", "manifests", "stream_inventory",
]
