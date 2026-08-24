"""IntegrationEngineAdapter -- the single boundary that understands the engine.

Domain services depend on this Protocol and nothing else. That is what makes
"upgrade Airbyte" a change to one package plus a contract-test run, instead of a
change spread across the product (guardrail 5, section 24).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.adapters.dto import (
    ConnectionCheckResult, ConnectorDescriptor, ConnectorMetadata, DiscoveredCatalog,
    EngineActorRequest, EngineConnectionRequest, EngineHealth, EngineJobRef, EngineJobStatus,
    EngineLogResult, EngineResourceRef, EngineSyncRequest,
)
from app.models.enums import EngineResourceType, EngineType


@runtime_checkable
class IntegrationEngineAdapter(Protocol):
    engine_type: EngineType
    contract_version: str

    async def health(self) -> EngineHealth: ...

    async def list_connector_metadata(self) -> list[ConnectorMetadata]: ...

    async def get_connector_spec(self, connector: ConnectorDescriptor) -> ConnectorMetadata: ...

    async def test_declarative_read(
        self,
        connector: ConnectorDescriptor,
        *,
        manifest: dict,
        config: dict,
        stream_name: str,
        record_limit: int = 25,
        page_limit: int = 2,
    ) -> dict:
        """Run a connector that is defined by a document rather than an image.

        The product hands over a declarative definition and gets back records,
        logs and a verdict. How that document reaches the engine is the
        adapter's business (guardrail 5).
        """
        ...

    def declarative_runner(self) -> tuple[str, str] | None:
        """The image that executes a manifest-defined connector, if any.

        `(repository, version)`, or None when this engine cannot run connectors
        built in the product. Asked of the adapter rather than hard-coded above
        it: which image runs a declarative connector is an engine fact, and an
        engine that has no such runner needs to be able to say so instead of
        having an Airbyte image assumed on its behalf.
        """
        ...

    async def resource_exists(self, resource_type: EngineResourceType, ref: str) -> bool:
        """Does the engine still have the resource this ref names?

        Added for the case a restore creates: a product database pointed at a
        different engine deployment than the one it was written against. Every
        `engine_mappings` row then names something that does not exist, and
        without this the first symptom is a sync failing hours later.

        Must distinguish *absent* from *unreachable*. Returning False when the
        engine is merely down would tell an operator to recreate resources that
        are perfectly fine -- so an unreachable engine raises
        EngineUnavailableError rather than answering.
        """
        ...

    # --- sources ---------------------------------------------------------
    async def create_source(self, request: EngineActorRequest) -> EngineResourceRef: ...
    async def update_source(self, ref: str, request: EngineActorRequest) -> EngineResourceRef: ...
    async def delete_source(self, ref: str) -> None: ...
    async def check_source(
        self, connector: ConnectorDescriptor, configuration: dict
    ) -> ConnectionCheckResult: ...
    async def discover_source(
        self, connector: ConnectorDescriptor, configuration: dict, *, source_ref: str | None = None
    ) -> DiscoveredCatalog: ...

    # --- destinations ----------------------------------------------------
    async def create_destination(self, request: EngineActorRequest) -> EngineResourceRef: ...
    async def update_destination(self, ref: str, request: EngineActorRequest) -> EngineResourceRef: ...
    async def delete_destination(self, ref: str) -> None: ...
    async def check_destination(
        self, connector: ConnectorDescriptor, configuration: dict
    ) -> ConnectionCheckResult: ...

    # --- connections (product "pipelines") -------------------------------
    async def create_connection(self, request: EngineConnectionRequest) -> EngineResourceRef: ...
    async def update_connection(self, ref: str, request: EngineConnectionRequest) -> EngineResourceRef: ...
    async def delete_connection(self, ref: str) -> None: ...

    # --- jobs ------------------------------------------------------------
    async def trigger_sync(self, request: EngineSyncRequest) -> EngineJobRef: ...
    async def get_job(self, ref: str) -> EngineJobStatus: ...
    async def cancel_job(self, ref: str) -> EngineJobStatus: ...
    async def get_job_logs(
        self, ref: str, *, cursor: int = 0, limit: int = 500
    ) -> EngineLogResult: ...

    async def close(self) -> None: ...
