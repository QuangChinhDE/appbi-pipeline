"""Adapter DTOs.

These are the *only* shapes that cross the engine boundary. Domain services
speak this vocabulary; nothing below the adapter (Airbyte JSON, docker output,
HTTP payloads) is ever handed upwards (section 24.2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.errors import ErrorCategory
from app.models.enums import EngineStatus, EngineType, RunStatus


@dataclass(slots=True)
class EngineHealth:
    reachable: bool
    engine_type: EngineType
    status: EngineStatus
    version: str | None = None
    detail: str | None = None
    checked_at: datetime | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConnectorMetadata:
    connector_key: str
    display_name: str
    connector_type: str            # SOURCE | DESTINATION
    docker_repository: str
    version: str
    spec_schema: dict[str, Any]
    category: str = "Database"
    description: str | None = None
    icon: str | None = None
    icon_url: str | None = None
    documentation_url: str | None = None
    release_stage: str = "generally_available"
    support_level: str = "community"
    latest_version: str | None = None
    # The tag the engine will actually run, when the engine decides that rather
    # than the product. In AIRBYTE_API mode Airbyte pins its own connector
    # versions, so `version` (what this product bundled) and this can differ,
    # and reporting only the former tells operators the wrong thing about what
    # is about to execute.
    engine_version: str | None = None
    engine_definition_id: str | None = None
    supports_oauth: bool = False
    supports_incremental: bool = True
    supports_cdc: bool = False
    supports_namespaces: bool = True
    supported_destination_sync_modes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConnectorDescriptor:
    """What the engine needs in order to run one connector."""

    connector_key: str
    docker_repository: str
    version: str
    engine_definition_id: str | None = None
    # Set for connectors defined by a document rather than a purpose-built
    # image. The product never inspects it; only the adapter knows what to do
    # with it, which is what keeps the engine's vocabulary on one side.
    declarative_manifest: dict[str, Any] | None = None

    @property
    def image(self) -> str:
        return f"{self.docker_repository}:{self.version}"


@dataclass(slots=True)
class EngineResourceRef:
    ref: str
    engine_type: EngineType
    version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EngineActorRequest:
    """Create/update a source or destination on the engine."""

    workspace_id: uuid.UUID
    product_resource_id: uuid.UUID
    name: str
    connector: ConnectorDescriptor
    configuration: dict[str, Any]     # merged non-secret config + resolved secrets


@dataclass(slots=True)
class ConnectionCheckResult:
    succeeded: bool
    message: str | None = None
    error_code: str | None = None
    category: ErrorCategory | None = None
    technical_message: str | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class DiscoveredStream:
    name: str
    json_schema: dict[str, Any]
    namespace: str | None = None
    supported_sync_modes: list[str] = field(default_factory=lambda: ["full_refresh"])
    source_defined_cursor: bool = False
    default_cursor_field: list[str] = field(default_factory=list)
    source_defined_primary_key: list[list[str]] = field(default_factory=list)
    is_resumable: bool | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name

    @property
    def schema_hash(self) -> str:
        """A stable fingerprint of this stream's shape, for change detection.

        Lives on the DTO because it is a property of the DTO: hashing a JSON
        schema involves nothing any particular engine knows about. It used to
        sit in the Airbyte protocol module, which made `schema_service` — a
        layer that must work with any engine — import an Airbyte one.
        """
        import hashlib
        import json as _json

        material = _json.dumps(self.json_schema, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass(slots=True)
class DiscoveredCatalog:
    streams: list[DiscoveredStream]
    catalog_hash: str
    discovered_at: datetime
    connector_version: str | None = None


@dataclass(slots=True)
class ConfiguredStream:
    """One selected stream, resolved down to what the engine must be told."""

    name: str
    json_schema: dict[str, Any]
    sync_mode: str
    destination_sync_mode: str
    namespace: str | None = None
    cursor_field: list[str] = field(default_factory=list)
    primary_key: list[list[str]] = field(default_factory=list)


@dataclass(slots=True)
class EngineConnectionRequest:
    workspace_id: uuid.UUID
    product_resource_id: uuid.UUID
    name: str
    source_ref: str
    destination_ref: str
    streams: list[ConfiguredStream]
    namespace_format: str | None = None
    stream_prefix: str | None = None
    # Product owns scheduling (ADR-004 option B); the engine connection is
    # created manual-like and triggered by the product scheduler.
    schedule: dict[str, Any] | None = None


@dataclass(slots=True)
class EngineSyncRequest:
    workspace_id: uuid.UUID
    pipeline_id: uuid.UUID
    run_id: uuid.UUID
    connection_ref: str
    source: ConnectorDescriptor
    destination: ConnectorDescriptor
    source_config: dict[str, Any]
    destination_config: dict[str, Any]
    streams: list[ConfiguredStream]
    state: dict[str, Any] | list[Any] | None = None
    # Airbyte refresh protocol: destinations use these to decide what prior data
    # to keep versus truncate.
    generation_id: int = 1
    sync_id: int = 1
    timeout_seconds: int = 7200
    log_path: str | None = None


@dataclass(slots=True)
class EngineJobRef:
    ref: str
    engine_type: EngineType
    attempt: int = 1


@dataclass(slots=True)
class StreamStat:
    stream_name: str
    namespace: str | None
    records_emitted: int
    bytes_emitted: int
    status: str = "COMPLETED"


@dataclass(slots=True)
class EngineFailure:
    code: str
    category: ErrorCategory
    summary: str
    technical_message: str | None = None
    remediation_action: str | None = None
    fingerprint: str | None = None


@dataclass(slots=True)
class EngineJobStatus:
    ref: str
    status: RunStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    records_synced: int | None = None
    bytes_synced: int | None = None
    attempt: int = 1
    failure: EngineFailure | None = None
    stream_stats: list[StreamStat] = field(default_factory=list)
    # Airbyte Protocol state committed by the destination, persisted so the
    # next incremental run resumes from the right place.
    state: Any | None = None
    raw_status: str | None = None
    log_path: str | None = None


@dataclass(slots=True)
class EngineLogResult:
    lines: list[str]
    next_cursor: int | None
    has_more: bool
    total_lines: int | None = None
