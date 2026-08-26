"""Bundled connector registry + engine adapter factory.

The registry file is the product-owned normalized catalog (section 11.4): a
registry or daemon outage must never stop the Sources page from rendering, so
the catalog is seeded from here and refreshed out of band.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from app.adapters.base import IntegrationEngineAdapter
from app.adapters.dto import ConnectorMetadata
from app.core.config import settings
from app.models.enums import EngineType

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "resources" / "connector_registry.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _registry_entries() -> list[dict]:
    """The JSON catalogue plus the connectors this product writes itself.

    Base.vn connectors are Python in `app/connectors/base_vn`, compiled to a
    declarative manifest at import. Joining them here means they are seeded,
    listed, permissioned and synced through exactly the same path as an Airbyte
    connector -- there is no second kind of connector for the rest of the
    product to know about.
    """
    from app.connectors.base_vn import catalogue_entries

    return list(_load()["connectors"]) + catalogue_entries()


@lru_cache(maxsize=1)
def bundled_connectors() -> list[ConnectorMetadata]:
    entries = _registry_entries()
    return [
        ConnectorMetadata(
            connector_key=entry["connector_key"],
            display_name=entry["display_name"],
            connector_type=entry["connector_type"],
            docker_repository=entry["docker_repository"],
            version=entry["version"],
            spec_schema=entry["spec_schema"],
            category=entry.get("category", "Database"),
            description=entry.get("description"),
            icon=entry.get("icon"),
            icon_url=entry.get("icon_url") or None,
            documentation_url=entry.get("documentation_url") or None,
            release_stage=entry.get("release_stage", "generally_available"),
            support_level=entry.get("support_level", "community"),
            supports_oauth=entry.get("supports_oauth", False),
            supports_incremental=entry.get("supports_incremental", True),
            supports_cdc=entry.get("supports_cdc", False),
            supports_namespaces=entry.get("supports_namespaces", True),
            supported_destination_sync_modes=entry.get("supported_destination_sync_modes", []),
            declarative_manifest=entry.get("declarative_manifest"),
        )
        for entry in entries
    ]


def bundled_certifications() -> dict[str, str]:
    return {e["connector_key"]: e.get("certification", "BETA")
            for e in _registry_entries()}


@lru_cache(maxsize=1)
def _by_key() -> dict[str, ConnectorMetadata]:
    return {metadata.connector_key: metadata for metadata in bundled_connectors()}


def bundled_by_key(connector_key: str) -> ConnectorMetadata | None:
    return _by_key().get(connector_key)


def spec_hash(spec: dict) -> str:
    material = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()[:32]


# ── adapter factory ────────────────────────────────────────────────────────
# One adapter instance per process: the embedded engine keeps live job state in
# memory, so handing out fresh instances would lose track of running syncs.
_adapter: IntegrationEngineAdapter | None = None


def get_adapter() -> IntegrationEngineAdapter:
    global _adapter
    if _adapter is None:
        # A table, not a chain of ifs: adding an engine should be one line here
        # and a package, not an edit to control flow that already has two
        # branches and would grow a third.
        configured = settings.engine_type.upper()
        if configured == EngineType.AIRBYTE_API.value:
            from app.adapters.airbyte_api.adapter import AirbyteApiAdapter

            _adapter = AirbyteApiAdapter()
        elif configured == EngineType.SQL_DIRECT.value:
            from app.adapters.sql_direct.adapter import SqlDirectAdapter

            _adapter = SqlDirectAdapter()
        else:
            from app.adapters.airbyte_protocol.adapter import EmbeddedAirbyteAdapter

            _adapter = EmbeddedAirbyteAdapter()
    return _adapter


async def close_adapter() -> None:
    global _adapter
    if _adapter is not None:
        await _adapter.close()
        _adapter = None
