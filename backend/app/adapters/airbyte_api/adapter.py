"""Airbyte API engine.

Used when the platform is pointed at an existing self-managed Airbyte
deployment (ENGINE_TYPE=AIRBYTE_API). It speaks the Airbyte Config API and maps
every response into the same DTOs the embedded engine returns, so no service,
route or screen changes when the engine changes -- which is the whole point of
the adapter boundary (sections 24, 29).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.adapters.dto import (
    ConnectionCheckResult, ConnectorDescriptor, ConnectorMetadata, DiscoveredCatalog,
    DiscoveredStream, EngineActorRequest, EngineConnectionRequest, EngineFailure, EngineHealth,
    EngineJobRef, EngineJobStatus, EngineLogResult, EngineResourceRef, EngineSyncRequest, StreamStat,
)
from app.adapters.error_mapper import classify, fingerprint
from app.adapters.log_text import clean_line
from app.core.config import settings
from app.core.errors import (
    EngineResourceGoneError,
    AppError, EngineOperationError, EngineUnavailableError, ErrorCategory,
)
from app.core.logging import log_event
from app.models.enums import EngineResourceType, EngineStatus, EngineType, RunStatus

logger = logging.getLogger(__name__)

# What a connector's version is called when the engine did not tell us. It must
# never be "latest": that reads like a real pin and silently becomes whatever
# upstream pushed last.
UNPINNED = "unpinned"

# Airbyte job/attempt status -> product run status. Anything unmapped becomes
# RUNNING rather than a guess at a terminal state.
_JOB_STATUS_MAP: dict[str, RunStatus] = {
    "pending": RunStatus.QUEUED,
    "running": RunStatus.RUNNING,
    "incomplete": RunStatus.RUNNING,
    "succeeded": RunStatus.SUCCEEDED,
    "failed": RunStatus.FAILED,
    "cancelled": RunStatus.CANCELLED,
    "canceled": RunStatus.CANCELLED,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


# Routes that are alternatives to one another rather than separate
# requirements: the platform lines disagree about which exists, and the adapter
# tries them in turn. `scripts/verify-engine-api.py` reads this so that a 404
# on one member is not reported as a broken adapter -- the group is satisfied
# if any member answers, and only an empty group is a real gap.
ALTERNATIVE_ROUTE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("/api/v1/workspaces/list",
     "/api/v1/workspaces/list_paginated",
     "/api/v1/workspaces/list_by_organization_id"),
)


class _ClientCredentialsAuth(httpx.Auth):
    """Bearer tokens from Airbyte's application credentials.

    Found by standing the target topology up rather than by reading docs: an
    Airbyte 1.8.5 deployed from Helm chart V2 with `auth.enabled: true` answers
    the Config API with **401 for HTTP Basic**, including the instance admin's
    own email and password. The adapter only spoke Basic, so the product could
    not talk to a production Airbyte at all -- and every certification so far
    had run with auth disabled, which is exactly why nothing caught it.

    The scheme 1.x uses is client credentials: POST the application's
    `client_id`/`client_secret` to `/api/v1/applications/token`, then send the
    returned token as `Authorization: Bearer`. Applications are created in
    Airbyte (UI or API); the credentials are configuration, not something this
    product can mint.

    The token is fetched lazily, reused, and re-fetched once on a 401. Fetching
    per request would triple the traffic; never re-fetching would break every
    call after expiry, which on a long sync is the reconciler going blind
    mid-run.
    """

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None

    def _fetch(self) -> str:
        # A separate synchronous client on purpose: httpx.Auth's sync flow runs
        # inside the request pipeline, and reusing the adapter's async client
        # here would deadlock on its own connection pool.
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{self._base_url}/api/v1/applications/token",
                json={"client_id": self._client_id,
                      "client_secret": self._client_secret},
                headers={"Content-Type": "application/json"})
        if response.status_code >= 400:
            raise EngineOperationError(
                message="Không lấy được token xác thực từ engine.",
                code="ENGINE_AUTH_FAILED",
                technical_message=(
                    f"POST /api/v1/applications/token -> "
                    f"HTTP {response.status_code}: {response.text[:300]}. "
                    "The client_id/client_secret must belong to an Application "
                    "created in Airbyte; the instance admin's email and "
                    "password are not accepted here."))
        token = (response.json() or {}).get("access_token")
        if not token:
            raise EngineOperationError(
                code="ENGINE_AUTH_FAILED",
                technical_message="the token endpoint returned no access_token")
        return str(token)

    def auth_flow(self, request):
        if self._token is None:
            self._token = self._fetch()
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            # Expired or rotated. One retry, then let the 401 stand -- retrying
            # forever on a genuinely wrong credential is how a deployment melts
            # its own auth endpoint.
            self._token = self._fetch()
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request


def _build_auth(base_url: str):
    """Whichever scheme this deployment is configured for.

    Basic is kept because 0.59.x accepts it and the Compose certification lane
    runs on that. Production is 1.x with auth enabled, which does not.
    """
    if settings.airbyte_client_id and settings.airbyte_client_secret:
        return _ClientCredentialsAuth(base_url, settings.airbyte_client_id,
                                      settings.airbyte_client_secret)
    if settings.airbyte_api_username:
        return (settings.airbyte_api_username, settings.airbyte_api_password)
    return None


def _looks_absent(body: str) -> bool:
    """Airbyte's own wording for "no such object", on routes that do not 404.

    Matched on the platform's phrases rather than any status code, because
    `/workspaces/get_by_slug` answers a missing slug with a 404 carrying an
    "Internal Server Error" body and other routes vary. Narrow on purpose: a
    false positive here means a live job gets marked lost.
    """
    text = (body or "")[:500]
    return ("Could not find configuration for" in text
            or text.strip() == "Object not found.")


class AirbyteApiAdapter:
    """IntegrationEngineAdapter over the Airbyte Config API."""

    engine_type = EngineType.AIRBYTE_API
    contract_version = "1"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.airbyte_api_url).rstrip("/")
        auth = _build_auth(self.base_url)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(120.0, connect=15.0), auth=auth,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        self.workspace_ref = settings.airbyte_workspace_id
        # Filled on first use; see _definitions.
        self._definition_cache: dict[str, dict[str, dict]] = {}

    # ── workspace ────────────────────────────────────────────────────────
    # Which Airbyte workspace this deployment writes into. Wrong is worse than
    # missing: a plausible UUID from a different Airbyte passes every check and
    # then puts customer connections in the wrong tenant. So it is configured,
    # not inferred — except on a fresh local stack, where the alternative is a
    # manual step nobody remembers, and where the ambiguity that makes guessing
    # dangerous can be ruled out by refusing to guess when there is a choice.

    async def _resolve_workspace(self) -> str:
        if self.workspace_ref:
            return self.workspace_ref

        if not settings.airbyte_workspace_auto:
            raise EngineOperationError(
                message="Chưa cấu hình workspace của Airbyte.",
                technical_message=(
                    "AIRBYTE_WORKSPACE_ID is empty. Find it with "
                    "`python scripts/airbyte-workspace.py list`, or set "
                    "AIRBYTE_WORKSPACE_AUTO=true on a local stack that has "
                    "exactly one workspace."),
            )

        found = await self._list_workspaces()
        if len(found) != 1:
            raise EngineOperationError(
                message="Không xác định được workspace của Airbyte.",
                technical_message=(
                    f"AIRBYTE_WORKSPACE_AUTO is on but this Airbyte has "
                    f"{len(found)} workspaces. Auto-resolution only applies when "
                    "there is exactly one; anything else has to be chosen "
                    "deliberately via AIRBYTE_WORKSPACE_ID."),
            )

        self.workspace_ref = found[0]["workspaceId"]
        log_event(logger, logging.WARNING, "adapter.workspace_auto_resolved",
                  workspace=self.workspace_ref,
                  detail="resolved from the only workspace on this Airbyte; "
                         "set AIRBYTE_WORKSPACE_ID explicitly for anything "
                         "that is not a local stack")
        return self.workspace_ref

    # The Config API route for "what workspaces are there" is the one place
    # the 0.59 and 1.x lines actually disagree, and it is not a soft
    # deprecation: 1.8.5 answers /workspaces/list with 404. Certifying against
    # a real 1.8.5 is what found it -- endpoint-existence probing is cheap
    # precisely because it catches this class of change.
    #
    # Tried in order, and every variant is a POST that either answers or 404s,
    # so the fallback costs one round trip on the newer line and nothing on the
    # older one.
    _WORKSPACE_ROUTES: tuple[tuple[str, dict[str, Any]], ...] = (
        # 0.59.x, and still the cheapest question to ask.
        ("/api/v1/workspaces/list", {}),
        # 1.x. Needs an explicit pagination block -- omitting it is a 500, not
        # a default.
        ("/api/v1/workspaces/list_paginated",
         {"pagination": {"pageSize": 100, "rowOffset": 0}}),
        # 1.x, community edition, where every workspace hangs off the single
        # default organization.
        ("/api/v1/workspaces/list_by_organization_id",
         {"organizationId": "00000000-0000-0000-0000-000000000000"}),
    )

    async def _list_workspaces(self) -> list[dict[str, Any]]:
        errors: list[str] = []
        for path, payload in self._WORKSPACE_ROUTES:
            try:
                data = await self._post(path, payload, timeout=20)
            except EngineOperationError as exc:
                # 4xx/5xx from a route this platform version does not support.
                # Not fatal on its own -- the next variant is the point.
                errors.append(f"{path}: {str(exc)[:120]}")
                continue
            workspaces = data.get("workspaces")
            if workspaces is not None:
                return workspaces
            errors.append(f"{path}: answered without a `workspaces` key")
        raise EngineOperationError(
            message="Không đọc được danh sách workspace của Airbyte.",
            technical_message=("no workspace listing route answered on this "
                               "deployment: " + "; ".join(errors)),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Most of the Config API is POST-only, but a few endpoints are not.

        `/health` is one: it answers 405 to a POST. The adapter used to send
        one, so every health check failed and the engine looked offline while
        it was running.
        """
        try:
            response = await self._client.get(path, timeout=timeout)
        except httpx.HTTPError as exc:
            log_event(logger, logging.ERROR, "adapter.transport_error", path=path, error=str(exc))
            raise EngineUnavailableError(technical_message=f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise EngineUnavailableError(technical_message=response.text[:1500])
        if response.status_code >= 400:
            raise EngineOperationError(
                technical_message=f"HTTP {response.status_code}: {response.text[:1500]}")
        if not response.content:
            return {}
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any] | None = None,
                    *, timeout: float | None = None) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            log_event(logger, logging.ERROR, "adapter.transport_error", path=path, error=str(exc))
            raise EngineUnavailableError(technical_message=f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise EngineUnavailableError(technical_message=response.text[:1500])
        if response.status_code == 429:
            # Backpressure, not an answer. Callers that ask "does this exist"
            # must not read a rate limit as "no".
            raise EngineUnavailableError(
                technical_message=f"HTTP 429 rate limited: {response.text[:500]}")
        if response.status_code in (401, 403):
            # An auth or configuration incident. Also not an answer, and
            # previously indistinguishable from a 404 -- which is how a rotated
            # credential came to mark live syncs FAILED.
            raise EngineOperationError(
                message="Không xác thực được với engine đồng bộ.",
                code="ENGINE_AUTH_FAILED",
                technical_message=f"HTTP {response.status_code}: {response.text[:500]}")
        if response.status_code == 404 or _looks_absent(response.text):
            # The one case that positively means the resource is not there.
            # Airbyte answers a missing id with 404, and some routes answer 404
            # with a body saying "Could not find configuration for ...".
            raise EngineResourceGoneError(
                technical_message=f"HTTP {response.status_code}: {response.text[:500]}")
        if response.status_code >= 400:
            raise EngineOperationError(technical_message=f"HTTP {response.status_code}: {response.text[:1500]}")
        if not response.content:
            return {}
        return response.json()

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> EngineHealth:
        try:
            await self._get("/api/v1/health", timeout=10)
            # GET, and not `/get`: this is one of the few Config API routes
            # that is not POST-shaped, which is easy to get wrong and was.
            # Best-effort, but logged — an unknown engine version makes the
            # compatibility matrix useless, so it should not fail in silence.
            version = ""
            try:
                info = await self._get("/api/v1/instance_configuration", timeout=10)
                version = info.get("version") or ""
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.WARNING, "adapter.version_unknown",
                          error=str(exc)[:200])
            return EngineHealth(
                reachable=True, engine_type=self.engine_type, status=EngineStatus.HEALTHY,
                version=version or None, checked_at=_utcnow(),
            )
        except Exception as exc:  # noqa: BLE001
            return EngineHealth(
                reachable=False, engine_type=self.engine_type, status=EngineStatus.OFFLINE,
                detail=str(exc)[:400], checked_at=_utcnow(),
            )

    # ── catalog ──────────────────────────────────────────────────────────
    async def list_connector_metadata(self) -> list[ConnectorMetadata]:
        from app.adapters.registry import bundled_by_key, bundled_connectors

        out: list[ConnectorMetadata] = []
        for kind, path, id_field in (
            ("SOURCE", "/api/v1/source_definitions/list", "sourceDefinitionId"),
            ("DESTINATION", "/api/v1/destination_definitions/list", "destinationDefinitionId"),
        ):
            key = "sourceDefinitions" if kind == "SOURCE" else "destinationDefinitions"
            try:
                data = await self._post(path)
            except Exception as exc:  # noqa: BLE001 - registry outage must not break the page
                log_event(logger, logging.WARNING, "adapter.catalog_unavailable", error=str(exc))
                continue
            for definition in data.get(key) or []:
                repo = definition.get("dockerRepository", "")
                connector_key = repo.split("/")[-1] or definition.get("name", "")
                base = bundled_by_key(connector_key)
                out.append(
                    ConnectorMetadata(
                        connector_key=connector_key,
                        display_name=definition.get("name", connector_key),
                        connector_type=kind,
                        docker_repository=repo,
                        # No tag means we do not know what would run. "latest"
                        # is the one answer a release gate forbids, so the
                        # connector is surfaced unpinned and the catalogue
                        # refuses to certify it (see certification below).
                        version=definition.get("dockerImageTag") or UNPINNED,
                        spec_schema=base.spec_schema if base else {},
                        category=base.category if base else "Database",
                        icon=base.icon if base else None,
                        release_stage=definition.get("releaseStage", "generally_available"),
                        engine_definition_id=definition.get(id_field),
                        supports_incremental=base.supports_incremental if base else True,
                        supports_cdc=base.supports_cdc if base else False,
                        supported_destination_sync_modes=(
                            base.supported_destination_sync_modes if base else []
                        ),
                    )
                )
        return out or bundled_connectors()

    async def get_connector_spec(self, connector: ConnectorDescriptor) -> ConnectorMetadata:
        from app.adapters.registry import bundled_by_key

        base = bundled_by_key(connector.connector_key)
        is_source = (base.connector_type if base else "SOURCE") == "SOURCE"
        kind = "SOURCE" if is_source else "DESTINATION"

        try:
            definition_id = await self._definition_id(connector, kind)
        except EngineOperationError:
            # The deployment does not have this connector. The bundled spec is
            # still the right thing to render — it is what the wizard needs to
            # show a form — and creating a source from it will fail with a clear
            # message rather than a blank screen now.
            if base is None:
                raise
            return base

        path = ("/api/v1/source_definition_specifications/get" if is_source
                else "/api/v1/destination_definition_specifications/get")
        id_field = "sourceDefinitionId" if is_source else "destinationDefinitionId"
        payload = {id_field: definition_id, "workspaceId": await self._resolve_workspace()}
        data = await self._post(path, payload)
        spec = data.get("connectionSpecification") or {}

        # What this deployment will actually run. Airbyte pins its own tag, so
        # this is frequently not the version the product bundled.
        entry = (await self._definitions(kind)).get(connector.docker_repository) or {}
        engine_version = entry.get("dockerImageTag") or None

        return ConnectorMetadata(
            connector_key=connector.connector_key,
            display_name=base.display_name if base else connector.connector_key,
            connector_type="SOURCE" if is_source else "DESTINATION",
            docker_repository=connector.docker_repository,
            version=connector.version,
            engine_version=engine_version,
            spec_schema=spec,
            category=base.category if base else "Database",
            icon=base.icon if base else None,
            engine_definition_id=definition_id,
            supports_oauth=bool(data.get("advancedAuth")),
            supports_incremental=bool(data.get("supportsIncremental", True)),
            supported_destination_sync_modes=list(data.get("supportedDestinationSyncModes") or []),
        )

    # ── actors ───────────────────────────────────────────────────────────
    # ── connector definitions ────────────────────────────────────────────
    # The product identifies a connector by key ("source-postgres"); Airbyte
    # identifies it by a definition UUID that belongs to that deployment. The
    # translation lives here, because a UUID from someone else's installation is
    # exactly the kind of engine detail that must not leak upwards (guardrail 3).
    #
    # Resolved from the deployment rather than from our bundled registry: the
    # same connector can carry a different definition id, and certainly a
    # different pinned tag, on a different Airbyte.

    async def _definitions(self, kind: str) -> dict[str, dict]:
        """dockerRepository -> definition, for one side, cached per process."""
        cached = self._definition_cache.get(kind)
        if cached is not None:
            return cached

        path = ("/api/v1/source_definitions/list" if kind == "SOURCE"
                else "/api/v1/destination_definitions/list")
        field = "sourceDefinitions" if kind == "SOURCE" else "destinationDefinitions"
        data = await self._post(path, {})

        by_repository = {
            entry.get("dockerRepository"): entry
            for entry in data.get(field, [])
            if entry.get("dockerRepository")
        }
        self._definition_cache[kind] = by_repository
        log_event(logger, logging.INFO, "adapter.definitions_loaded",
                  kind=kind, count=len(by_repository))
        return by_repository

    async def _definition_id(self, connector: ConnectorDescriptor, kind: str) -> str:
        """The definition id this Airbyte uses for the connector we mean.

        A connector the deployment does not have is a configuration problem the
        operator can act on, so it is reported as one instead of surfacing later
        as a null-pointer from inside Airbyte.
        """
        if connector.engine_definition_id:
            return connector.engine_definition_id

        # A connector built in the product runs on the shared runner, and the
        # runner is registered on demand rather than shipped with the
        # deployment — so "not found" is not an error here, it is the first use.
        if connector.declarative_manifest is not None and kind == "SOURCE":
            return await self._runner_definition_id(connector)

        definitions = await self._definitions(kind)
        entry = definitions.get(connector.docker_repository)
        if entry is None:
            # Refresh once: a definition added after this process started is a
            # normal thing to happen on a long-lived deployment.
            self._definition_cache.pop(kind, None)
            entry = (await self._definitions(kind)).get(connector.docker_repository)

        if entry is None:
            raise EngineOperationError(
                message=(f"Airbyte deployment này chưa có connector "
                         f"'{connector.connector_key}'."),
                technical_message=(
                    f"{connector.docker_repository} is not among the "
                    f"{kind.lower()} definitions on this Airbyte. Add it to the "
                    "deployment before creating resources with it."),
            )

        id_field = "sourceDefinitionId" if kind == "SOURCE" else "destinationDefinitionId"
        return entry[id_field]

    # ── connectors built in the product ──────────────────────────────────
    # A built connector has no image of its own. Airbyte runs it the same way
    # this product's own engine does: a generic runner image reads the manifest
    # out of the config under a well-known key.
    #
    # Airbyte will not run an image it has no definition for, so the runner is
    # registered once per deployment as a custom source definition and then
    # reused by every built connector. One image, many configs — registering a
    # definition per connector would fill the deployment with duplicates of the
    # same thing.

    MANIFEST_CONFIG_KEY = "__injected_declarative_manifest"
    RUNNER_DEFINITION_NAME = "AppBI Declarative Runner"

    def _config_for(self, connector: ConnectorDescriptor, configuration: dict) -> dict:
        """Config as the runner needs to receive it.

        Everything above the adapter passes an ordinary configuration and never
        learns that some connectors carry their behaviour with them.
        """
        if not connector.declarative_manifest:
            return configuration
        return {**configuration, self.MANIFEST_CONFIG_KEY: connector.declarative_manifest}

    async def _runner_definition_id(self, connector: ConnectorDescriptor) -> str:
        """The definition id for the declarative runner, registering it if new.

        Racing with another process is harmless: both would create a definition,
        and the second one loses only in the sense that a duplicate row exists.
        The lookup below prefers whichever the deployment reports first, so both
        processes converge on the same answer afterwards.
        """
        existing = await self._definitions("SOURCE")
        entry = existing.get(connector.docker_repository)
        if entry is not None:
            return entry["sourceDefinitionId"]

        log_event(logger, logging.INFO, "adapter.runner_definition_create",
                  repository=connector.docker_repository, version=connector.version)
        data = await self._post("/api/v1/source_definitions/create_custom", {
            "workspaceId": await self._resolve_workspace(),
            "sourceDefinition": {
                "name": self.RUNNER_DEFINITION_NAME,
                "dockerRepository": connector.docker_repository,
                "dockerImageTag": connector.version,
                "documentationUrl": "https://docs.airbyte.com/connector-development/config-based/",
            },
        })
        # Anything cached is now stale by exactly this one entry.
        self._definition_cache.pop("SOURCE", None)
        return data["sourceDefinitionId"]

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
        """Exercise a manifest on the engine and report what it found.

        What this can and cannot do is decided by the engine, not by us. The
        Config API runs a whole connector — `check` and `discover` — but it has
        no endpoint that reads a bounded window of records; a read only happens
        as part of a sync, into a destination. So in this mode a Builder test
        proves the connector authenticates and reports the schema it will
        produce, and returns no sample rows.

        The alternative was to make the HTTP calls here, in the product. That
        would produce a record preview and a lie: the preview would come from
        the product's network, its Python, its CDK version, while the published
        connector runs somewhere else entirely. A test that does not exercise
        the thing being tested is worse than an honest gap, so the gap is
        reported instead — `record_preview_supported` tells the UI which of the
        two it is talking to.
        """
        payload = {**config, self.MANIFEST_CONFIG_KEY: manifest}
        definition_id = await self._runner_definition_id(connector)
        logs: list[str] = []

        checked = await self._post("/api/v1/scheduler/sources/check_connection", {
            "sourceDefinitionId": definition_id,
            "connectionConfiguration": payload,
            "workspaceId": await self._resolve_workspace(),
        }, timeout=settings.check_timeout_seconds)
        result = self._check_result(checked, side="SOURCE")
        logs.extend(self._job_log_lines(checked))

        if not result.succeeded:
            return {
                "ok": False,
                "records": [],
                "logs": logs,
                "error": {
                    "summary": result.message or "Kết nối thất bại.",
                    "code": result.error_code or "CONNECTOR_CHECK_FAILED",
                    "category": (result.category.value if result.category
                                 else ErrorCategory.CONFIGURATION.value),
                    "technical_message": result.technical_message,
                },
                "requests": [],
                "record_preview_supported": False,
            }

        # `check` passing means the credentials work. `discover` is what proves
        # the stream the user is editing actually resolves.
        #
        # Airbyte has no "discover from a definition and a config" endpoint —
        # `discover_schema` takes a sourceId. Passing a definition instead
        # returns a 500 with a NullPointerException, which reads like an
        # Airbyte outage rather than a wrong request. So a throwaway source is
        # created, discovered, and deleted: the same path a real source takes,
        # which is also what makes the result trustworthy.
        schema: dict | None = None
        error: dict | None = None
        temporary_ref: str | None = None
        try:
            created = await self._post("/api/v1/sources/create", {
                "workspaceId": await self._resolve_workspace(),
                "sourceDefinitionId": definition_id,
                "connectionConfiguration": payload,
                # Named so an operator finding one left behind knows what it is
                # and that deleting it is safe.
                "name": f"[builder test] {stream_name}",
            })
            temporary_ref = created["sourceId"]

            discovered = await self._post("/api/v1/sources/discover_schema", {
                "sourceId": temporary_ref, "disable_cache": True,
            }, timeout=settings.discover_timeout_seconds)
            logs.extend(self._job_log_lines(discovered))

            job = discovered.get("jobInfo") or {}
            if not job.get("succeeded", True):
                reason = (job.get("failureReason") or {})
                error = {
                    "summary": "Không đọc được cấu trúc dữ liệu từ connector.",
                    "code": "BUILDER_DISCOVER_FAILED",
                    "category": ErrorCategory.SCHEMA.value,
                    "technical_message": (reason.get("externalMessage")
                                          or reason.get("internalMessage")
                                          or "discover failed"),
                }
            else:
                streams = ((discovered.get("catalog") or {}).get("streams") or [])
                wanted = next(
                    (entry for entry in streams
                     if ((entry.get("stream") or {}).get("name")) == stream_name),
                    None,
                )
                if wanted is None:
                    names = sorted((entry.get("stream") or {}).get("name", "")
                                   for entry in streams)
                    error = {
                        "summary": f"Connector không trả về stream '{stream_name}'.",
                        "code": "BUILDER_STREAM_NOT_DISCOVERED",
                        "category": ErrorCategory.CONFIGURATION.value,
                        "technical_message": (
                            "discover returned: "
                            + (", ".join(n for n in names if n) or "no streams")),
                    }
                else:
                    schema = (wanted.get("stream") or {}).get("jsonSchema")
        except AppError as exc:
            error = {
                "summary": getattr(exc, "message", None) or "Không đọc được cấu trúc dữ liệu.",
                "code": getattr(exc, "code", None) or "BUILDER_DISCOVER_FAILED",
                "category": ErrorCategory.ENGINE.value,
                "technical_message": getattr(exc, "technical_message", None) or str(exc),
            }
        finally:
            if temporary_ref:
                # Leaving these behind would fill the deployment with sources
                # nobody created. Failing to clean up must not turn a passing
                # test into a failing one, so it is logged, not raised.
                try:
                    await self.delete_source(temporary_ref)
                except Exception as exc:  # noqa: BLE001
                    log_event(logger, logging.WARNING, "adapter.builder_temp_source_leaked",
                              source_ref=temporary_ref, error=str(exc)[:200])

        return {
            "ok": error is None,
            "records": [],
            "logs": logs,
            "error": error,
            "requests": [],
            "inferred_schema": schema,
            "record_preview_supported": False,
        }

    @staticmethod
    def _job_log_lines(data: dict[str, Any]) -> list[str]:
        """Whatever the connector said, out of whichever envelope carries it."""
        info = data.get("jobInfo") or {}
        logs = (info.get("logs") or {}).get("logLines") or []
        return [clean_line(str(line)) for line in logs]

    def declarative_runner(self) -> tuple[str, str] | None:
        """Airbyte runs manifests on the low-code CDK runner image.

        Registered on the deployment as a custom source definition the first
        time one is needed; see _runner_definition_id.
        """
        from app.services.builder_manifest import RUNNER_REPOSITORY, RUNNER_VERSION

        return RUNNER_REPOSITORY, RUNNER_VERSION

    async def create_source(self, request: EngineActorRequest) -> EngineResourceRef:
        data = await self._post("/api/v1/sources/create", {
            "workspaceId": await self._resolve_workspace(),
            "sourceDefinitionId": await self._definition_id(request.connector, "SOURCE"),
            "connectionConfiguration": self._config_for(
                request.connector, request.configuration),
            "name": request.name,
        })
        return EngineResourceRef(ref=data["sourceId"], engine_type=self.engine_type,
                                 version=request.connector.version)

    async def update_source(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        await self._post("/api/v1/sources/update", {
            "sourceId": ref,
            "connectionConfiguration": self._config_for(
                request.connector, request.configuration),
            "name": request.name,
        })
        return EngineResourceRef(ref=ref, engine_type=self.engine_type, version=request.connector.version)

    async def delete_source(self, ref: str) -> None:
        await self._post("/api/v1/sources/delete", {"sourceId": ref})

    _GET_ROUTE = {
        EngineResourceType.SOURCE: ("/api/v1/sources/get", "sourceId"),
        EngineResourceType.DESTINATION: ("/api/v1/destinations/get", "destinationId"),
        EngineResourceType.CONNECTION: ("/api/v1/connections/get", "connectionId"),
        EngineResourceType.JOB: ("/api/v1/jobs/get", "id"),
    }

    async def resource_exists(self, resource_type: EngineResourceType, ref: str) -> bool:
        route = self._GET_ROUTE.get(resource_type)
        if route is None:
            raise EngineOperationError(
                technical_message=f"no lookup route for {resource_type}")
        path, field = route
        try:
            await self._post(path, {field: ref}, timeout=20)
        except EngineResourceGoneError:
            # The only answer that means "not here". Every other failure --
            # 401 from a rotated credential, 403, 429, 5xx, a transport error --
            # propagates, because an engine that cannot answer must never be
            # reported as an engine that said no.
            return False
        return True

    async def check_source(self, connector: ConnectorDescriptor, configuration: dict) -> ConnectionCheckResult:
        data = await self._post("/api/v1/scheduler/sources/check_connection", {
            "sourceDefinitionId": await self._definition_id(connector, "SOURCE"),
            "connectionConfiguration": self._config_for(connector, configuration),
            "workspaceId": await self._resolve_workspace(),
        }, timeout=settings.check_timeout_seconds)
        return self._check_result(data, side="SOURCE")

    async def create_destination(self, request: EngineActorRequest) -> EngineResourceRef:
        data = await self._post("/api/v1/destinations/create", {
            "workspaceId": await self._resolve_workspace(),
            "destinationDefinitionId": await self._definition_id(
                request.connector, "DESTINATION"),
            "connectionConfiguration": request.configuration,
            "name": request.name,
        })
        return EngineResourceRef(ref=data["destinationId"], engine_type=self.engine_type,
                                 version=request.connector.version)

    async def update_destination(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        await self._post("/api/v1/destinations/update", {
            "destinationId": ref,
            "connectionConfiguration": request.configuration,
            "name": request.name,
        })
        return EngineResourceRef(ref=ref, engine_type=self.engine_type, version=request.connector.version)

    async def delete_destination(self, ref: str) -> None:
        await self._post("/api/v1/destinations/delete", {"destinationId": ref})

    async def check_destination(
        self, connector: ConnectorDescriptor, configuration: dict
    ) -> ConnectionCheckResult:
        data = await self._post("/api/v1/scheduler/destinations/check_connection", {
            "destinationDefinitionId": await self._definition_id(
                connector, "DESTINATION"),
            "connectionConfiguration": configuration,
            "workspaceId": await self._resolve_workspace(),
        }, timeout=settings.check_timeout_seconds)
        return self._check_result(data, side="DESTINATION")

    def _check_result(self, data: dict[str, Any], *, side: str) -> ConnectionCheckResult:
        status = (data.get("status") or "").lower()
        if status == "succeeded":
            return ConnectionCheckResult(succeeded=True, message=data.get("message"))
        raw = data.get("message") or (data.get("jobInfo") or {}).get("failureReason", {}).get(
            "externalMessage") or "connection check failed"
        failure = classify(raw, side=side)
        return ConnectionCheckResult(
            succeeded=False, message=failure.summary, error_code=failure.code,
            category=failure.category, technical_message=failure.technical_message,
        )

    # ── discovery ────────────────────────────────────────────────────────
    async def discover_source(
        self, connector: ConnectorDescriptor, configuration: dict, *, source_ref: str | None = None
    ) -> DiscoveredCatalog:
        if not source_ref:
            raise EngineOperationError(
                technical_message="Airbyte discover requires an existing engine source id"
            )
        data = await self._post("/api/v1/sources/discover_schema", {
            "sourceId": source_ref, "disable_cache": True,
        }, timeout=settings.discover_timeout_seconds)
        if not data.get("jobInfo", {}).get("succeeded", True):
            failure = (data.get("jobInfo") or {}).get("failureReason") or {}
            raise EngineOperationError(
                technical_message=failure.get("externalMessage") or "discover failed"
            )
        catalog = data.get("catalog") or {}
        streams: list[DiscoveredStream] = []
        for entry in catalog.get("streams") or []:
            stream = entry.get("stream") or entry
            streams.append(
                DiscoveredStream(
                    name=stream.get("name", ""),
                    namespace=stream.get("namespace"),
                    json_schema=stream.get("jsonSchema") or stream.get("json_schema") or {},
                    supported_sync_modes=list(
                        stream.get("supportedSyncModes") or stream.get("supported_sync_modes")
                        or ["full_refresh"]
                    ),
                    source_defined_cursor=bool(
                        stream.get("sourceDefinedCursor") or stream.get("source_defined_cursor")
                    ),
                    default_cursor_field=list(
                        stream.get("defaultCursorField") or stream.get("default_cursor_field") or []
                    ),
                    source_defined_primary_key=[
                        list(pk) for pk in (
                            stream.get("sourceDefinedPrimaryKey")
                            or stream.get("source_defined_primary_key") or []
                        )
                    ],
                )
            )
        streams.sort(key=lambda s: (s.namespace or "", s.name))
        material = "".join(f"{s.namespace}.{s.name}" for s in streams)
        return DiscoveredCatalog(
            streams=streams,
            catalog_hash=hashlib.sha256(material.encode()).hexdigest(),
            discovered_at=_utcnow(),
            connector_version=connector.version,
        )

    # ── connections ──────────────────────────────────────────────────────
    async def create_connection(self, request: EngineConnectionRequest) -> EngineResourceRef:
        payload = {
            "name": request.name,
            "sourceId": request.source_ref,
            "destinationId": request.destination_ref,
            "status": "active",
            # Product owns scheduling; the engine connection stays manual.
            "scheduleType": "manual",
            "namespaceDefinition": "customformat" if request.namespace_format else "destination",
            "syncCatalog": self._sync_catalog(request),
        }
        if request.namespace_format:
            payload["namespaceFormat"] = request.namespace_format
        if request.stream_prefix:
            payload["prefix"] = request.stream_prefix
        data = await self._post("/api/v1/connections/create", payload)
        return EngineResourceRef(ref=data["connectionId"], engine_type=self.engine_type)

    async def update_connection(self, ref: str, request: EngineConnectionRequest) -> EngineResourceRef:
        payload = {
            "connectionId": ref,
            "name": request.name,
            "status": "active",
            "scheduleType": "manual",
            "syncCatalog": self._sync_catalog(request),
        }
        if request.namespace_format:
            payload["namespaceFormat"] = request.namespace_format
        if request.stream_prefix:
            payload["prefix"] = request.stream_prefix
        await self._post("/api/v1/connections/update", payload)
        return EngineResourceRef(ref=ref, engine_type=self.engine_type)

    async def delete_connection(self, ref: str) -> None:
        await self._post("/api/v1/connections/delete", {"connectionId": ref})

    @staticmethod
    def _sync_catalog(request: EngineConnectionRequest) -> dict[str, Any]:
        streams = []
        for stream in request.streams:
            entry: dict[str, Any] = {
                "stream": {
                    "name": stream.name,
                    "jsonSchema": stream.json_schema or {"type": "object"},
                    "supportedSyncModes": sorted({stream.sync_mode, "full_refresh"}),
                },
                "config": {
                    "syncMode": stream.sync_mode,
                    "destinationSyncMode": stream.destination_sync_mode,
                    "selected": True,
                },
            }
            if stream.namespace:
                entry["stream"]["namespace"] = stream.namespace
            if stream.cursor_field:
                entry["config"]["cursorField"] = stream.cursor_field
            if stream.primary_key:
                entry["config"]["primaryKey"] = stream.primary_key
            streams.append(entry)
        return {"streams": streams}

    # ── jobs ─────────────────────────────────────────────────────────────
    async def trigger_sync(self, request: EngineSyncRequest) -> EngineJobRef:
        data = await self._post("/api/v1/connections/sync", {"connectionId": request.connection_ref})
        job = data.get("job") or {}
        return EngineJobRef(ref=str(job.get("id")), engine_type=self.engine_type)

    async def get_job(self, ref: str) -> EngineJobStatus:
        data = await self._post("/api/v1/jobs/get", {"id": int(ref)})
        return self._job_status(ref, data)

    async def cancel_job(self, ref: str) -> EngineJobStatus:
        data = await self._post("/api/v1/jobs/cancel", {"id": int(ref)})
        return self._job_status(ref, data)

    def _job_status(self, ref: str, data: dict[str, Any]) -> EngineJobStatus:
        job = data.get("job") or {}
        attempts = data.get("attempts") or []
        raw_status = str(job.get("status", "")).lower()
        status = _JOB_STATUS_MAP.get(raw_status, RunStatus.RUNNING)

        records = bytes_ = 0
        # "Nothing moved" and "the engine did not say" look identical once both
        # are zero, and they mean opposite things to whoever reads the run: a
        # successful incremental sync with no new rows is the normal case, and
        # showing it as an em dash makes it look like a reporting failure.
        reported = False
        stream_stats: list[StreamStat] = []
        failure: EngineFailure | None = None
        for wrapper in attempts:
            attempt = wrapper.get("attempt") or {}
            totals = attempt.get("totalStats") or {}
            if totals:
                reported = True
            records = max(records, int(totals.get("recordsCommitted") or totals.get("recordsEmitted") or 0))
            bytes_ = max(bytes_, int(totals.get("bytesEmitted") or 0))
            for entry in attempt.get("streamStats") or []:
                stats = entry.get("stats") or {}
                stream_stats.append(StreamStat(
                    stream_name=entry.get("streamName", ""),
                    namespace=entry.get("streamNamespace"),
                    records_emitted=int(stats.get("recordsEmitted") or 0),
                    bytes_emitted=int(stats.get("bytesEmitted") or 0),
                ))
            reasons = (attempt.get("failureSummary") or {}).get("failures") or []
            if reasons and status in (RunStatus.FAILED, RunStatus.CANCELLED):
                first = reasons[0]
                raw = first.get("externalMessage") or first.get("internalMessage") or "sync failed"
                origin = str(first.get("failureOrigin", "")).upper()
                failure = classify(raw, side="DESTINATION" if origin == "DESTINATION" else "SOURCE")

        if status is RunStatus.CANCELLED and failure is None:
            failure = EngineFailure(
                code="RUN_CANCELLED", category=ErrorCategory.CANCELLED,
                summary="Lần chạy đã bị hủy.", fingerprint=fingerprint("cancelled"),
            )
        return EngineJobStatus(
            ref=ref, status=status,
            started_at=_ts(job.get("createdAt")),
            ended_at=_ts(job.get("updatedAt")) if status.is_terminal else None,
            records_synced=records if reported else None,
            bytes_synced=bytes_ if reported else None,
            attempt=len(attempts) or 1, failure=failure, stream_stats=stream_stats,
            raw_status=raw_status,
        )

    @staticmethod
    def _log_lines(logs: dict[str, Any]) -> list[str]:
        """Flatten one attempt's logs, whichever shape this Airbyte uses.

        0.59.1 returns `logLines`, a list of pre-formatted strings. 1.8.5
        returns `events`, a list of objects, and leaves `logLines` present but
        empty. Reading only the first is not an error anywhere -- it is a job
        with no logs, which is exactly how the Kubernetes certification run
        reported it, and exactly how an operator debugging a failed sync would
        find the log view blank with nothing to explain why.

        Both are read rather than switching on the `version` field: an attempt
        may legitimately carry either, and a shape that is present should be
        used regardless of what a version number claims.
        """
        lines = [clean_line(str(line)) for line in (logs.get("logLines") or [])]
        for event in (logs.get("events") or []):
            if not isinstance(event, dict):
                lines.append(clean_line(str(event)))
                continue
            stamp = event.get("timestamp")
            when = ""
            if isinstance(stamp, (int, float)):
                # Milliseconds since the epoch. Rendered in UTC because the
                # reader is comparing against other logs, not against a wall
                # clock in one particular office.
                when = datetime.fromtimestamp(
                    stamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            level = str(event.get("level") or "").upper()
            source = str(event.get("logSource") or "")
            prefix = " ".join(part for part in (when, level, source) if part)
            message = clean_line(str(event.get("message") or ""))
            lines.append(f"{prefix} {message}".strip() if prefix else message)
        return lines

    async def get_job_logs(self, ref: str, *, cursor: int = 0, limit: int = 500) -> EngineLogResult:
        data = await self._post("/api/v1/jobs/get_debug_info", {"id": int(ref)})
        lines: list[str] = []
        for wrapper in (data.get("attempts") or []):
            lines.extend(self._log_lines(wrapper.get("logs") or {}))
        window = lines[cursor: cursor + limit]
        next_cursor = cursor + len(window)
        return EngineLogResult(
            lines=window,
            next_cursor=next_cursor if next_cursor < len(lines) else None,
            has_more=next_cursor < len(lines),
            total_lines=len(lines),
        )
