"""Engine instances, product<->engine mappings and the connector catalog.

`engine_mappings` is the only place a product UUID is tied to an engine handle.
Nothing engine-shaped is stored on the domain entities themselves, which is what
makes swapping or sharding the engine a mapping-table change (sections 22.7, 64).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import (
    Certification, ConnectorStatus, ConnectorType, EngineResourceType, EngineStatus, EngineType,
    ProductResourceType,
)


class EngineInstance(Base, TimestampMixin):
    __tablename__ = "engine_instances"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    engine_type: Mapped[EngineType] = mapped_column(SAEnum(EngineType, name="engine_type"), nullable=False)
    base_url_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter_contract_version: Mapped[str] = mapped_column(String(16), default="1", nullable=False)
    status: Mapped[EngineStatus] = mapped_column(
        SAEnum(EngineStatus, name="engine_status"), default=EngineStatus.UNKNOWN, nullable=False
    )
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capacity_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EngineMapping(Base, TimestampMixin):
    __tablename__ = "engine_mappings"
    __table_args__ = (
        UniqueConstraint("product_resource_type", "product_resource_id", "engine_resource_type",
                         name="uq_engine_mapping_resource"),
        Index("ix_engine_mappings_ws", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    product_resource_type: Mapped[ProductResourceType] = mapped_column(
        SAEnum(ProductResourceType, name="product_resource_type"), nullable=False
    )
    product_resource_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    engine_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("engine_instances.id"), nullable=True
    )
    engine_type: Mapped[EngineType] = mapped_column(SAEnum(EngineType, name="engine_type"), nullable=False)
    engine_resource_type: Mapped[EngineResourceType] = mapped_column(
        SAEnum(EngineResourceType, name="engine_resource_type"), nullable=False
    )
    engine_resource_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class ConnectorDefinition(Base, TimestampMixin):
    """Normalized, product-owned connector metadata cache (section 11.3).

    A registry outage must never stop the Sources page from rendering, so the
    catalog lives here and is refreshed out of band.
    """

    __tablename__ = "connector_definitions"
    __table_args__ = (UniqueConstraint("connector_key", name="uq_connector_key"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    # Indexed: the catalogue is browsed by name on every wizard step.
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    connector_type: Mapped[ConnectorType] = mapped_column(
        SAEnum(ConnectorType, name="connector_type"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(64), default="Database", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Upstream logo and docs. Both are display-only; a missing value degrades to
    # the built-in icon and no link rather than blocking the connector.
    icon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    documentation_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    docker_repository: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The tag the engine will actually run, when the engine picks it. Distinct
    # from `version`, which is what this product bundled and locked: in
    # AIRBYTE_API mode Airbyte pins its own connector versions, and reporting
    # only the product's tells operators the wrong thing about what will
    # execute. Null until a refresh has asked the engine.
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_stage: Mapped[str] = mapped_column(String(32), default="generally_available", nullable=False)
    # Airbyte's own rating for the connector, kept distinct from `certification`,
    # which is this product's stance (section 53).
    support_level: Mapped[str] = mapped_column(
        String(32), default="community", server_default="community", nullable=False,
    )
    # Backend-only handle used by the API adapter; never serialised to the FE.
    engine_definition_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    supports_oauth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_incremental: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_cdc: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_namespaces: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supported_destination_sync_modes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    spec_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Set for connectors built in the product: the behaviour lives in this
    # document and is executed by a generic runner image. Only the adapter reads
    # it — nothing above the boundary knows what shape it has.
    declarative_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # What to show instead of the image tag. A connector built here runs on a
    # shared runner, so its image tag says nothing about the user's revision.
    display_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A built connector belongs to the workspace that built it; a catalogue
    # entry with no owner is available to every workspace.
    owner_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec_source: Mapped[str] = mapped_column(String(32), default="BUNDLED", nullable=False)

    status: Mapped[ConnectorStatus] = mapped_column(
        SAEnum(ConnectorStatus, name="connector_status"), default=ConnectorStatus.ACTIVE, nullable=False
    )
    certification: Mapped[Certification] = mapped_column(
        SAEnum(Certification, name="connector_certification"),
        default=Certification.BETA, nullable=False,
    )
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_pulled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def image(self) -> str:
        return f"{self.docker_repository}:{self.version}"
