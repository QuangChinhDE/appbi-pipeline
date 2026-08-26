"""Embedded Airbyte engine.

Runs official Airbyte connector images directly over the Airbyte Protocol:

    docker run <source-image>      read  --config c --catalog cat [--state s]
      |  (RECORD / STATE messages, inspected in flight)
      v
    docker run -i <destination-image> write --config c --catalog cat

That is the same contract the Airbyte worker implements, so real connectors move
real rows and incremental state round-trips exactly as upstream defines it. This
adapter owns every Airbyte-shaped detail; the domain layer above sees only DTOs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adapters.airbyte_protocol import protocol as ap
from app.adapters.airbyte_protocol.docker_runner import (
    DockerRunner, DockerUnavailable, log_root, new_job_dir, write_json, workspace_root,
)
from app.adapters.dto import (
    ConnectionCheckResult, ConnectorDescriptor, ConnectorMetadata, DiscoveredCatalog,
    EngineActorRequest, EngineConnectionRequest, EngineFailure, EngineHealth, EngineJobRef,
    EngineJobStatus, EngineLogResult, EngineResourceRef, EngineSyncRequest, StreamStat,
)
from app.adapters.log_text import clean_line
from app.adapters.error_mapper import classify, fingerprint
from app.core.config import settings
from app.core.errors import EngineUnavailableError, ErrorCategory
from app.core.logging import log_event
from app.models.enums import EngineResourceType, EngineStatus, EngineType, RunStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _LiveJob:
    """In-process bookkeeping for one running sync."""

    ref: str
    run_id: str
    status: RunStatus = RunStatus.STARTING
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    records: int = 0
    bytes_: int = 0
    stream_stats: dict[tuple[str | None, str], StreamStat] = field(default_factory=dict)
    failure: EngineFailure | None = None
    state: Any | None = None
    log_path: str | None = None
    job_dir: str | None = None
    cancel_requested: bool = False
    task: asyncio.Task | None = None
    source_container: str = ""
    destination_container: str = ""


class EmbeddedAirbyteAdapter:
    """IntegrationEngineAdapter backed by local connector containers."""

    engine_type = EngineType.AIRBYTE_EMBEDDED
    contract_version = "1"

    def __init__(self, runner: DockerRunner | None = None) -> None:
        self.runner = runner or DockerRunner()
        self._jobs: dict[str, _LiveJob] = {}

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> EngineHealth:
        try:
            ok, detail = await self.runner.ping()
        except DockerUnavailable as exc:
            return EngineHealth(
                reachable=False, engine_type=self.engine_type, status=EngineStatus.OFFLINE,
                detail=str(exc), checked_at=_utcnow(),
            )
        active = [job for job in self._jobs.values() if job.status.is_active]
        return EngineHealth(
            reachable=ok,
            engine_type=self.engine_type,
            status=EngineStatus.HEALTHY if ok else EngineStatus.OFFLINE,
            version=f"docker {detail}" if ok else None,
            detail=None if ok else detail,
            checked_at=_utcnow(),
            metrics={"active_jobs": len(active), "tracked_jobs": len(self._jobs)},
        )

    # ── catalog ──────────────────────────────────────────────────────────
    async def list_connector_metadata(self) -> list[ConnectorMetadata]:
        """The bundled registry is the catalog source; specs are refreshed
        per-connector through `get_connector_spec` so one slow image cannot
        stall the whole refresh."""
        from app.adapters.registry import bundled_connectors

        return bundled_connectors()

    async def get_connector_spec(self, connector: ConnectorDescriptor) -> ConnectorMetadata:
        result = await self.runner.run_connector(
            connector.image, ["spec"], timeout=settings.spec_timeout_seconds,
            container_name=f"abpl-spec-{connector.connector_key}-{int(time.time())}",
        )
        if result.exit_code != 0:
            raise EngineUnavailableError(
                technical_message=(result.stderr or b"").decode(errors="replace")[-1500:],
            )
        spec: dict[str, Any] | None = None
        for line in result.stdout.splitlines():
            message = ap.parse_line(line)
            if message and message.type == ap.TYPE_SPEC:
                spec = ap.parse_spec(message.payload)
        if spec is None:
            raise EngineUnavailableError(technical_message="connector emitted no SPEC message")

        from app.adapters.registry import bundled_by_key

        base = bundled_by_key(connector.connector_key)
        return ConnectorMetadata(
            connector_key=connector.connector_key,
            display_name=base.display_name if base else connector.connector_key,
            connector_type=base.connector_type if base else "SOURCE",
            docker_repository=connector.docker_repository,
            version=connector.version,
            spec_schema=spec["connection_specification"],
            category=base.category if base else "Database",
            description=base.description if base else None,
            icon=base.icon if base else None,
            supports_oauth=bool(spec.get("advanced_auth")),
            supports_incremental=spec["supports_incremental"],
            supports_cdc=base.supports_cdc if base else False,
            supported_destination_sync_modes=spec["supported_destination_sync_modes"]
            or (base.supported_destination_sync_modes if base else []),
        )

    # ── actors ───────────────────────────────────────────────────────────
    # Nothing is registered remotely for the embedded engine: the product row is
    # the actor. We still return a ref so the mapping table, the saga and the
    # API adapter all follow one code path.
    def declarative_runner(self) -> tuple[str, str] | None:
        """The same runner image, executed directly on the Docker daemon."""
        from app.services.builder_manifest import RUNNER_REPOSITORY, RUNNER_VERSION

        return RUNNER_REPOSITORY, RUNNER_VERSION

    async def find_by_product_id(self, resource_type: str,
                                 product_resource_id: str) -> str | None:
        """Derivable here, and genuinely so.

        This adapter has no server-side registry: a "resource" is just a
        configuration the product holds, and the ref is a deterministic string
        built from the product id. So the crash window the Airbyte adapter has
        to recover from by listing does not exist here -- there is nothing on
        a remote system to lose track of.

        Worth stating explicitly, because a test double that behaved this way
        is what made the Airbyte bug invisible. It is true for this adapter and
        false for that one.
        """
        prefix = {"SOURCE": "source", "DESTINATION": "destination",
                  "PIPELINE": "connection"}.get(resource_type)
        return f"embedded://{prefix}/{product_resource_id}" if prefix else None

    async def create_source(self, request: EngineActorRequest) -> EngineResourceRef:
        return EngineResourceRef(
            ref=f"embedded://source/{request.product_resource_id}",
            engine_type=self.engine_type,
            version=request.connector.version,
            extra={"connector_key": request.connector.connector_key},
        )

    async def update_source(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        return EngineResourceRef(ref=ref, engine_type=self.engine_type, version=request.connector.version)

    async def delete_source(self, ref: str) -> None:
        return None

    async def resource_exists(self, resource_type: EngineResourceType, ref: str) -> bool:
        """Always true, and that is the honest answer for this engine.

        The embedded runner holds no server-side resources: a "source" here is
        a row in the product's own database plus a container started on demand.
        There is nothing that can go missing independently of the product, so
        after a restore nothing needs recreating -- which is exactly what a
        reconcile should report.
        """
        return True

    async def check_source(
        self, connector: ConnectorDescriptor, configuration: dict
    ) -> ConnectionCheckResult:
        return await self._check(connector, configuration, side="SOURCE")

    async def create_destination(self, request: EngineActorRequest) -> EngineResourceRef:
        return EngineResourceRef(
            ref=f"embedded://destination/{request.product_resource_id}",
            engine_type=self.engine_type,
            version=request.connector.version,
            extra={"connector_key": request.connector.connector_key},
        )

    async def update_destination(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        return EngineResourceRef(ref=ref, engine_type=self.engine_type, version=request.connector.version)

    async def delete_destination(self, ref: str) -> None:
        return None

    async def check_destination(
        self, connector: ConnectorDescriptor, configuration: dict
    ) -> ConnectionCheckResult:
        return await self._check(connector, configuration, side="DESTINATION")

    async def _check(
        self, connector: ConnectorDescriptor, configuration: dict, *, side: str
    ) -> ConnectionCheckResult:
        started = time.monotonic()
        job_dir = new_job_dir("check")
        try:
            config_path = write_json(job_dir / "config.json",
                                     self._config_for(connector, configuration))
            result = await self.runner.run_connector(
                connector.image, ["check", "--config", str(config_path)],
                timeout=settings.check_timeout_seconds,
                container_name=f"abpl-check-{job_dir.name}",
            )
            elapsed = int((time.monotonic() - started) * 1000)

            succeeded: bool | None = None
            message: str | None = None
            trace_message: str | None = None
            for line in result.stdout.splitlines():
                parsed = ap.parse_line(line)
                if parsed is None:
                    continue
                if parsed.type == ap.TYPE_CONNECTION_STATUS:
                    succeeded, message = ap.connection_status(parsed.payload)
                error = ap.trace_error(parsed)
                if error:
                    trace_message = error.get("message") or error.get("internal_message")

            if succeeded is True:
                return ConnectionCheckResult(succeeded=True, message=message, duration_ms=elapsed)

            raw = message or trace_message
            if raw is None:
                stderr = (result.stderr or b"").decode(errors="replace").strip()
                if result.timed_out:
                    raw = "Connector check timed out"
                else:
                    raw = stderr[-1500:] or f"connector exited with code {result.exit_code}"
            failure = classify(raw, side=side)
            return ConnectionCheckResult(
                succeeded=False,
                message=failure.summary,
                error_code=failure.code,
                category=failure.category,
                technical_message=failure.technical_message,
                duration_ms=elapsed,
            )
        except DockerUnavailable as exc:
            return ConnectionCheckResult(
                succeeded=False, message="Không tải được connector.",
                error_code="CONNECTOR_IMAGE_UNAVAILABLE", category=ErrorCategory.ENGINE,
                technical_message=str(exc),
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    # ── declarative connectors (Connector Builder) ───────────────────────
    # A built connector has no image of its own: a generic runner executes the
    # manifest handed to it through the config. This key is the engine's
    # contract for that, and this class is the only place that knows it.

    MANIFEST_CONFIG_KEY = "__injected_declarative_manifest"

    def _config_for(self, connector: ConnectorDescriptor, configuration: dict) -> dict:
        """Config as the runner needs to receive it.

        A connector built in the product runs on a generic image, so its
        behaviour has to travel with the config. Everything above this line
        passes an ordinary configuration and never learns that.
        """
        if not connector.declarative_manifest:
            return configuration
        return {**configuration, self.MANIFEST_CONFIG_KEY: connector.declarative_manifest}

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
        job_dir = new_job_dir("builder")
        try:
            config_path = write_json(job_dir / "config.json",
                                     {**config, self.MANIFEST_CONFIG_KEY: manifest})

            stream = next(
                (s for s in manifest.get("streams", []) if s.get("name") == stream_name),
                None,
            )
            schema = ((stream or {}).get("schema_loader") or {}).get("schema") or {}
            catalog_path = write_json(job_dir / "catalog.json", {
                "streams": [{
                    "stream": {
                        "name": stream_name,
                        "json_schema": schema,
                        "supported_sync_modes": ["full_refresh"],
                    },
                    "sync_mode": "full_refresh",
                    "destination_sync_mode": "overwrite",
                }],
            })

            result = await self.runner.run_connector(
                connector.image,
                # --debug makes the runner report the request it sent and the
                # response it got. Without that the editor can only say "1
                # record"; with it the user can see that the record was the
                # API's own error envelope.
                ["read", "--config", str(config_path),
                 "--catalog", str(catalog_path), "--debug"],
                timeout=settings.check_timeout_seconds,
                container_name=f"abpl-builder-{job_dir.name}",
            )

            records: list[dict] = []
            logs: list[str] = []
            requests: list[dict] = []
            failure: str | None = None

            for line in result.stdout.splitlines():
                exchange = _debug_exchange(line)
                if exchange is not None:
                    kind, detail = exchange
                    if kind == "request":
                        requests.append({"url": detail.get("url"),
                                         "body": None, "status": None})
                    elif requests:
                        # Attach to the request it answers; a response without a
                        # preceding request is noise we cannot place.
                        requests[-1]["status"] = detail.get("status")
                        requests[-1]["body"] = str(detail.get("body") or "")[:2000]
                    continue

                parsed = ap.parse_line(line)
                if parsed is None:
                    continue
                if parsed.type == ap.TYPE_RECORD:
                    # Stop collecting, but keep reading: the connector may still
                    # emit the TRACE that explains a later failure.
                    if len(records) < record_limit:
                        record = (parsed.payload or {}).get("record") or {}
                        records.append(record.get("data") or {})
                elif parsed.type in (ap.TYPE_LOG, ap.TYPE_TRACE):
                    text = ap.log_text(parsed)
                    if text and len(logs) < 200:
                        logs.append(text[:500])
                error = ap.trace_error(parsed)
                if error:
                    failure = error.get("message") or error.get("internal_message")

            if failure is None and result.exit_code not in (0, None):
                stderr = (result.stderr or b"").decode(errors="replace").strip()
                if result.timed_out:
                    failure = "Đọc thử quá thời gian cho phép."
                elif not records:
                    failure = stderr[-1500:] or f"connector exited with code {result.exit_code}"

            if failure:
                classified = classify(failure, side="SOURCE")
                return {
                    "ok": False,
                    "records": records,
                    "logs": logs,
                    "requests": requests[:5],
                    "error": {
                        "summary": classified.summary,
                        "code": classified.code,
                        "category": classified.category.value,
                        "technical_message": classified.technical_message,
                    },
                }

            return {"ok": True, "records": records, "logs": logs,
                    "requests": requests[:5], "error": None}

        except DockerUnavailable as exc:
            return {
                "ok": False, "records": [], "logs": [], "requests": [],
                "error": {
                    "summary": "Không tải được runner cho connector tùy biến.",
                    "code": "CONNECTOR_IMAGE_UNAVAILABLE",
                    "category": ErrorCategory.ENGINE.value,
                    "technical_message": str(exc),
                },
            }
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    # ── discovery ────────────────────────────────────────────────────────
    async def discover_source(
        self, connector: ConnectorDescriptor, configuration: dict, *, source_ref: str | None = None
    ) -> DiscoveredCatalog:
        job_dir = new_job_dir("discover")
        try:
            config_path = write_json(job_dir / "config.json",
                                     self._config_for(connector, configuration))
            result = await self.runner.run_connector(
                connector.image, ["discover", "--config", str(config_path)],
                timeout=settings.discover_timeout_seconds,
                container_name=f"abpl-discover-{job_dir.name}",
            )
            if result.timed_out:
                from app.core.errors import error_from_matrix

                raise error_from_matrix("SCHEMA_DISCOVERY_TIMEOUT")

            streams = None
            trace_message = None
            for line in result.stdout.splitlines():
                parsed = ap.parse_line(line)
                if parsed is None:
                    continue
                if parsed.type == ap.TYPE_CATALOG:
                    streams = ap.parse_catalog(parsed.payload)
                error = ap.trace_error(parsed)
                if error:
                    trace_message = error.get("message") or error.get("internal_message")

            if streams is None:
                raw = trace_message or (result.stderr or b"").decode(errors="replace")[-1500:]
                failure = classify(raw or "discover produced no catalog", side="SOURCE")
                raise EngineUnavailableError(
                    failure.summary, code=failure.code, category=failure.category,
                    status_code=502, technical_message=failure.technical_message,
                )
            return DiscoveredCatalog(
                streams=streams,
                catalog_hash=ap.catalog_hash(streams),
                discovered_at=_utcnow(),
                connector_version=connector.version,
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    # ── connections ──────────────────────────────────────────────────────
    async def create_connection(self, request: EngineConnectionRequest) -> EngineResourceRef:
        return EngineResourceRef(
            ref=f"embedded://connection/{request.product_resource_id}",
            engine_type=self.engine_type,
            extra={"streams": len(request.streams)},
        )

    async def update_connection(self, ref: str, request: EngineConnectionRequest) -> EngineResourceRef:
        return EngineResourceRef(ref=ref, engine_type=self.engine_type,
                                 extra={"streams": len(request.streams)})

    async def delete_connection(self, ref: str) -> None:
        return None

    # ── jobs ─────────────────────────────────────────────────────────────
    async def trigger_sync(self, request: EngineSyncRequest) -> EngineJobRef:
        ref = f"embedded://job/{request.run_id}"
        job = _LiveJob(ref=ref, run_id=str(request.run_id))
        job.source_container = f"abpl-{str(request.run_id)[:8]}-src"
        job.destination_container = f"abpl-{str(request.run_id)[:8]}-dst"
        self._jobs[ref] = job
        job.task = asyncio.create_task(self._execute(request, job), name=f"sync-{request.run_id}")
        return EngineJobRef(ref=ref, engine_type=self.engine_type)

    async def get_job(self, ref: str) -> EngineJobStatus:
        job = self._jobs.get(ref)
        if job is None:
            # The worker restarted; the reconciler resolves these from the DB.
            return EngineJobStatus(ref=ref, status=RunStatus.FAILED_TO_START, raw_status="UNTRACKED",
                                   failure=EngineFailure(
                                       code="ENGINE_JOB_LOST",
                                       category=ErrorCategory.ENGINE,
                                       summary="Không tìm thấy tiến trình đồng bộ trên engine.",
                                       remediation_action="RETRY_RUN",
                                       fingerprint=fingerprint("engine job lost"),
                                   ))
        return self._snapshot(job)

    async def cancel_job(self, ref: str) -> EngineJobStatus:
        job = self._jobs.get(ref)
        if job is None:
            return await self.get_job(ref)
        if job.status.is_terminal:
            return self._snapshot(job)  # cancel is idempotent (section 16.5)
        job.cancel_requested = True
        job.status = RunStatus.CANCEL_REQUESTED
        for container in (job.source_container, job.destination_container):
            if container:
                await self.runner.kill(container)
        return self._snapshot(job)

    async def get_job_logs(self, ref: str, *, cursor: int = 0, limit: int = 500) -> EngineLogResult:
        job = self._jobs.get(ref)
        path = Path(job.log_path) if job and job.log_path else self._log_path_for_ref(ref)
        if path is None or not path.exists():
            return EngineLogResult(lines=[], next_cursor=None, has_more=False, total_lines=0)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            all_lines = handle.readlines()
        window = all_lines[cursor: cursor + limit]
        next_cursor = cursor + len(window)
        return EngineLogResult(
            lines=[clean_line(line) for line in window],
            next_cursor=next_cursor if next_cursor < len(all_lines) else None,
            has_more=next_cursor < len(all_lines),
            total_lines=len(all_lines),
        )

    async def connection_state(self, ref: str) -> list[dict] | None:
        """None: this engine keeps no connection-scoped cursor.

        Explicit rather than inherited from the protocol default, because
        `IntegrationEngineAdapter` is `runtime_checkable` and `isinstance`
        looks for the attribute on the *instance* -- a default on the protocol
        does not satisfy it, and the adapter silently stops being an adapter.
        """
        return None

    async def set_connection_state(self, ref: str, state: list[dict]) -> bool:
        """False: nothing here to write a cursor into. See `connection_state`."""
        return False

    async def close(self) -> None:
        for job in list(self._jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()

    # ── execution ────────────────────────────────────────────────────────
    async def _execute(self, request: EngineSyncRequest, job: _LiveJob) -> None:
        job_dir = workspace_root() / f"run-{request.run_id}"
        job_dir.mkdir(parents=True, exist_ok=True)
        job.job_dir = str(job_dir)
        log_path = Path(request.log_path) if request.log_path else log_root() / f"run-{request.run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        job.log_path = str(log_path)

        log_file = log_path.open("a", encoding="utf-8")

        def emit(line: str) -> None:
            log_file.write(f"{_utcnow().isoformat()} {line}\n")
            log_file.flush()

        try:
            configured_catalog = ap.build_configured_catalog(
                request.streams, generation_id=request.generation_id, sync_id=request.sync_id
            )
            # A built connector's behaviour travels with its config, so the sync
            # path injects it exactly like check and discover do.
            source_config = write_json(
                job_dir / "source_config.json",
                self._config_for(request.source, request.source_config),
            )
            destination_config = write_json(
                job_dir / "destination_config.json",
                self._config_for(request.destination, request.destination_config),
            )
            catalog_path = write_json(job_dir / "catalog.json", configured_catalog)

            read_command = ["read", "--config", str(source_config), "--catalog", str(catalog_path)]
            state = ap.normalize_state_for_source(request.state)
            if state:
                state_path = write_json(job_dir / "state.json", state)
                read_command += ["--state", str(state_path)]

            emit(f"=== sync run {request.run_id} ===")
            emit(f"source      : {request.source.image}")
            emit(f"destination : {request.destination.image}")
            emit(f"streams     : {len(request.streams)} "
                 f"({', '.join(s.name for s in request.streams[:10])})")
            emit(f"state       : {'resuming from committed state' if state else 'none (initial sync)'}")
            emit(f"generation  : {request.generation_id} (sync #{request.sync_id})")

            emit("pulling connector images if needed...")
            await self.runner.ensure_image(request.source.image)
            await self.runner.ensure_image(request.destination.image)

            job.status = RunStatus.RUNNING

            source_proc = await self.runner.spawn(
                request.source.image, read_command, container_name=job.source_container
            )
            dest_proc = await self.runner.spawn(
                request.destination.image,
                ["write", "--config", str(destination_config), "--catalog", str(catalog_path)],
                container_name=job.destination_container,
                interactive=True,
            )

            source_errors: list[str] = []
            dest_errors: list[str] = []

            async def pump_source() -> None:
                assert source_proc.stdout is not None and dest_proc.stdin is not None
                async for raw_line in source_proc.stdout:
                    message = ap.parse_line(raw_line)
                    if message is None:
                        text = raw_line.decode(errors="replace").rstrip()
                        if text:
                            emit(f"[source:raw] {text[:2000]}")
                        continue
                    if message.type == ap.TYPE_RECORD:
                        job.records += 1
                        size = len(message.raw)
                        job.bytes_ += size
                        namespace, stream_name = ap.record_stream_key(message)
                        key = (namespace, stream_name)
                        stat = job.stream_stats.get(key)
                        if stat is None:
                            stat = StreamStat(stream_name=stream_name, namespace=namespace,
                                              records_emitted=0, bytes_emitted=0, status="RUNNING")
                            job.stream_stats[key] = stat
                        stat.records_emitted += 1
                        stat.bytes_emitted += size
                        dest_proc.stdin.write(message.raw + b"\n")
                        if job.records % 2000 == 0:
                            await dest_proc.stdin.drain()
                            emit(f"[source] {job.records} records forwarded")
                    elif message.type == ap.TYPE_STATE:
                        dest_proc.stdin.write(message.raw + b"\n")
                        await dest_proc.stdin.drain()
                        emit("[source] checkpoint state emitted")
                    else:
                        error = ap.trace_error(message)
                        if error:
                            source_errors.append(
                                error.get("message") or error.get("internal_message") or ""
                            )
                        text = ap.log_text(message)
                        if text:
                            emit(f"[source] {text}")
                try:
                    await dest_proc.stdin.drain()
                    dest_proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            async def pump_destination() -> None:
                assert dest_proc.stdout is not None
                async for raw_line in dest_proc.stdout:
                    message = ap.parse_line(raw_line)
                    if message is None:
                        text = raw_line.decode(errors="replace").rstrip()
                        if text:
                            emit(f"[destination:raw] {text[:2000]}")
                        continue
                    if message.type == ap.TYPE_STATE:
                        # The destination confirming a checkpoint is the only
                        # state we are allowed to persist.
                        payload = ap.state_payload(message)
                        if payload is not None:
                            existing = job.state if isinstance(job.state, list) else []
                            job.state = _merge_state(existing, payload)
                        emit("[destination] state committed")
                    else:
                        error = ap.trace_error(message)
                        if error:
                            dest_errors.append(
                                error.get("message") or error.get("internal_message") or ""
                            )
                        text = ap.log_text(message)
                        if text:
                            emit(f"[destination] {text}")

            async def drain_stderr(proc: asyncio.subprocess.Process, label: str,
                                   sink: list[str]) -> None:
                assert proc.stderr is not None
                async for raw_line in proc.stderr:
                    text = raw_line.decode(errors="replace").rstrip()
                    if not text:
                        continue
                    emit(f"[{label}:stderr] {text[:2000]}")
                    sink.append(text)

            source_stderr: list[str] = []
            dest_stderr: list[str] = []

            await asyncio.wait_for(
                asyncio.gather(
                    pump_source(),
                    pump_destination(),
                    drain_stderr(source_proc, "source", source_stderr),
                    drain_stderr(dest_proc, "destination", dest_stderr),
                ),
                timeout=request.timeout_seconds,
            )

            source_rc = await source_proc.wait()
            dest_rc = await dest_proc.wait()
            emit(f"source exit={source_rc} destination exit={dest_rc} records={job.records}")

            for stat in job.stream_stats.values():
                stat.status = "COMPLETED"

            if job.cancel_requested:
                job.status = RunStatus.CANCELLED
                job.failure = EngineFailure(
                    code="RUN_CANCELLED", category=ErrorCategory.CANCELLED,
                    summary="Lần chạy đã bị hủy theo yêu cầu.",
                    fingerprint=fingerprint("cancelled"),
                )
            elif source_rc == 0 and dest_rc == 0:
                job.status = RunStatus.SUCCEEDED
            else:
                side = "SOURCE" if source_rc != 0 else "DESTINATION"
                candidates = (
                    source_errors + source_stderr[-6:] if side == "SOURCE"
                    else dest_errors + dest_stderr[-6:]
                )
                raw = next((c for c in candidates if c), "")
                if not raw:
                    raw = f"{side.lower()} connector exited with code " \
                          f"{source_rc if side == 'SOURCE' else dest_rc}"
                job.status = RunStatus.FAILED
                job.failure = classify(raw, side=side,
                                       default_category=ErrorCategory.SOURCE_READ
                                       if side == "SOURCE" else ErrorCategory.DESTINATION_WRITE)

        except asyncio.TimeoutError:
            job.status = RunStatus.TIMED_OUT
            job.failure = EngineFailure(
                code="ENGINE_TIMEOUT", category=ErrorCategory.TIMEOUT,
                summary="Lần chạy vượt quá thời gian tối đa và đã bị dừng.",
                remediation_action="RETRY_LATER", fingerprint=fingerprint("run timeout"),
            )
            emit("[engine] run exceeded timeout, killing containers")
            await self._kill_job(job)
        except asyncio.CancelledError:
            job.status = RunStatus.CANCELLED
            await self._kill_job(job)
            raise
        except DockerUnavailable as exc:
            job.status = RunStatus.FAILED_TO_START
            job.failure = EngineFailure(
                code="CONNECTOR_IMAGE_UNAVAILABLE", category=ErrorCategory.ENGINE,
                summary="Không tải được image của connector.",
                technical_message=str(exc)[:2000], remediation_action="CONTACT_ADMIN",
                fingerprint=fingerprint(str(exc)),
            )
            emit(f"[engine] {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a run hang on a bug
            job.status = RunStatus.FAILED
            job.failure = classify(str(exc), default_category=ErrorCategory.ENGINE)
            emit(f"[engine] unexpected error: {exc}")
            log_event(logger, logging.ERROR, "sync.unexpected_error", run_id=job.run_id, error=str(exc))
        finally:
            job.ended_at = _utcnow()
            emit(f"=== finished status={job.status.value} records={job.records} "
                 f"bytes={job.bytes_} ===")
            log_file.close()
            # Config files hold resolved credentials -- delete them the moment
            # the run ends. Logs are already redacted by the connectors.
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _kill_job(self, job: _LiveJob) -> None:
        for container in (job.source_container, job.destination_container):
            if container:
                await self.runner.kill(container)

    def _snapshot(self, job: _LiveJob) -> EngineJobStatus:
        return EngineJobStatus(
            ref=job.ref,
            status=job.status,
            started_at=job.started_at,
            ended_at=job.ended_at,
            records_synced=job.records,
            bytes_synced=job.bytes_,
            failure=job.failure,
            stream_stats=list(job.stream_stats.values()),
            state=job.state,
            raw_status=job.status.value,
            log_path=job.log_path,
        )

    def _log_path_for_ref(self, ref: str) -> Path | None:
        run_id = ref.rsplit("/", 1)[-1]
        candidate = log_root() / f"run-{run_id}.log"
        return candidate if candidate.exists() else None

    def forget(self, ref: str) -> None:
        self._jobs.pop(ref, None)


def _merge_state(existing: list[Any], incoming: Any) -> list[Any]:
    """Keep one entry per stream descriptor; GLOBAL/LEGACY state replaces all."""
    if not isinstance(incoming, dict):
        return existing
    kind = incoming.get("type")
    if kind in (None, "LEGACY", "GLOBAL"):
        return [incoming]

    def descriptor(item: Any) -> str:
        stream = (item or {}).get("stream") or {}
        desc = stream.get("stream_descriptor") or {}
        return f"{desc.get('namespace')}.{desc.get('name')}"

    key = descriptor(incoming)
    merged = [item for item in existing if descriptor(item) != key]
    merged.append(incoming)
    return merged


def _debug_exchange(line: bytes) -> tuple[str, dict] | None:
    """Read one `--debug` frame describing an HTTP request or response.

    These are not Airbyte protocol messages, so the protocol parser ignores
    them. Recognising them here is what lets the builder show what actually went
    over the wire.
    """
    stripped = line.strip()
    if not stripped.startswith(b'{') or b'"DEBUG"' not in stripped:
        return None
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if payload.get("type") != "DEBUG":
        return None
    message = str(payload.get("message") or "")
    data = payload.get("data") or {}
    if "outbound API request" in message:
        return "request", data
    if "Receiving response" in message:
        return "response", data
    return None
