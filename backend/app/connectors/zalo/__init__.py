"""Zalo, as a connector group of its own.

Its own package and its own category rather than a stream bolted onto something
else: Zalo Ads has its own credentials, its own host and its own dialect, and
the catalogue groups by who maintains a connector. Adding it required no edit
to `adapters.registry` -- `app.connectors` discovers any package that exports
`PROVIDER`, and refuses one that would claim a category or a connector key
another group already owns.

One connector today. The tuple is the seam for Zalo OA or Zalo Shop later
without changing anything that reads this module.
"""

from __future__ import annotations

from typing import Any

from app.connectors import ConnectorProvider

from ._shared import Stream, ZaloConnector, compile_manifest, connection_specification
from .catalog import ZALO_ADS

CONNECTORS: tuple[ZaloConnector, ...] = (ZALO_ADS,)

# Checked at import, so a broken definition fails the process rather than
# reaching a customer's catalogue and failing at sync time.
for _connector in CONNECTORS:
    _connector.validate()

BY_KEY: dict[str, ZaloConnector] = {c.connector_key: c for c in CONNECTORS}


def manifests() -> dict[str, dict[str, Any]]:
    return {c.connector_key: compile_manifest(c) for c in CONNECTORS}


def catalogue_entries() -> list[dict[str, Any]]:
    """Registry rows, in the same shape as the bundled connector registry."""
    # Imported here rather than at module scope: `base_vn` owns the runner pin,
    # and a top-level import would make the packages import-cyclic.
    from app.connectors.base_vn._shared import RUNNER_REPOSITORY, RUNNER_VERSION

    entries: list[dict[str, Any]] = []
    for connector in CONNECTORS:
        entries.append({
            "connector_key": connector.connector_key,
            "display_name": connector.title,
            "connector_type": "SOURCE",
            "category": "Zalo",
            "description": connector.summary,
            "icon": connector.connector_key,
            "icon_url": "",
            "documentation_url": connector.docs_url,
            "docker_repository": RUNNER_REPOSITORY,
            "version": RUNNER_VERSION,
            "release_stage": "beta",
            "support_level": "certified",
            # The connector performs its own token exchange from a client id
            # and secret; there is no consent screen for the product to drive.
            "supports_oauth": False,
            # Honestly false: `date` is an input the user types, not a cursor.
            # See `catalog.py` for why it was not promoted to one.
            "supports_incremental": False,
            "supports_cdc": False,
            "supports_namespaces": False,
            "supported_destination_sync_modes": [],
            "spec_schema": connection_specification(connector),
            "declarative_manifest": compile_manifest(connector),
            # BETA: the token endpoint and the ads endpoint were probed and
            # exist, and nothing else could be verified without a Zalo Ads
            # application. See `compatibility.yaml`.
            "certification": "BETA",
            "stream_count": len(connector.streams),
        })
    return entries


def stream_inventory() -> list[dict[str, Any]]:
    return [{
        "connector": connector.connector_key,
        "app": connector.app,
        "stream": stream.name,
        "endpoint": stream.path,
        "collection": ".".join(stream.collection) or "(toàn bộ phản hồi)",
        "primary_key": list(stream.primary_key),
        "incremental": None,
        "paginated": False,
        "note": stream.note,
    } for connector in CONNECTORS for stream in connector.streams]


#: How `app.connectors` finds this group.
PROVIDER = ConnectorProvider(
    key="zalo",
    category="Zalo",
    title="Zalo",
    entries=catalogue_entries,
)


__all__ = [
    "BY_KEY", "CONNECTORS", "PROVIDER", "ZALO_ADS", "Stream", "ZaloConnector",
    "catalogue_entries", "compile_manifest", "connection_specification",
    "manifests", "stream_inventory",
]
