"""A Postgres-to-Postgres engine that has never heard of Airbyte.

This exists to test the interface, not to compete with Airbyte. An abstraction
with only one family of implementations behind it has not been proven to
abstract anything, and `IntegrationEngineAdapter` had two implementations that
were both Airbyte — same protocol, same catalog shape, same job model, same
vocabulary. Whether the boundary was real or merely tidy was an open question.

So: no connector images, no Airbyte Protocol, no spec/check/discover/read, no
server-side connection object, no job service. Rows move over a database
connection, in this process.

**Scope, stated plainly.** Postgres sources and Postgres destinations, full
refresh and incremental on a cursor column. That is narrow on purpose — the
point is coverage of the *interface*, not of the connector ecosystem. Anything
outside it is refused with a reason rather than half-supported.

**Where the abstraction did not fit**, which is the useful output of the
exercise, is recorded in `docs/ENGINE-PORTABILITY.md`. Four things had to be
synthesised here because the interface assumes an engine provides them:
connector specs, a `check` operation, server-side connection objects, and job
identity. None required changing the interface, which is the finding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.adapters.dto import (
    ConfiguredStream, ConnectionCheckResult, ConnectorDescriptor, ConnectorMetadata,
    DiscoveredCatalog, DiscoveredStream, EngineActorRequest, EngineConnectionRequest,
    EngineFailure, EngineHealth, EngineJobRef, EngineJobStatus, EngineLogResult,
    EngineResourceRef, EngineSyncRequest, StreamStat,
)
from app.adapters.error_mapper import fingerprint
from app.core.errors import EngineOperationError, ErrorCategory
from app.core.logging import log_event
from app.models.enums import EngineResourceType, EngineStatus, EngineType, RunStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── connector catalogue ──────────────────────────────────────────────────────
# First place the interface did not fit. `get_connector_spec` assumes the engine
# can be asked what configuration a connector takes — Airbyte answers that from
# the connector image. There is no image here and nothing to ask, so the specs
# are declared. That is not a workaround: an engine that supports two connectors
# knows both of them statically, and pretending otherwise would add indirection
# for no reader's benefit.

_POSTGRES_SPEC: dict[str, Any] = {
    "type": "object",
    "required": ["host", "port", "database", "username"],
    "properties": {
        "host": {"type": "string", "title": "Host", "order": 0},
        "port": {"type": "integer", "title": "Port", "default": 5432, "order": 1},
        "database": {"type": "string", "title": "Database", "order": 2},
        "schema": {"type": "string", "title": "Schema", "default": "public", "order": 3},
        "username": {"type": "string", "title": "Username", "order": 4},
        # `writeOnly` rather than Airbyte's `airbyte_secret`: this engine has no
        # reason to speak Airbyte's JSON Schema dialect, and the product's
        # secret detection was taught to read the standard marker instead of
        # assuming the vendor one. That change is one of the findings.
        "password": {"type": "string", "title": "Password", "writeOnly": True, "order": 5},
    },
}

CONNECTORS: dict[str, ConnectorMetadata] = {
    "sql-postgres-source": ConnectorMetadata(
        connector_key="sql-postgres-source",
        display_name="Postgres (direct SQL)",
        connector_type="SOURCE",
        # No image exists. The field is required by the DTO because Airbyte
        # needs it; a scheme is used so nothing mistakes it for something
        # pullable and `docker pull` on it fails loudly rather than oddly.
        docker_repository="sql-direct://postgres",
        version="1",
        spec_schema=_POSTGRES_SPEC,
        category="Database",
        description="Reads tables over a database connection. No connector image.",
        supports_incremental=True,
        supports_namespaces=True,
    ),
    "sql-postgres-destination": ConnectorMetadata(
        connector_key="sql-postgres-destination",
        display_name="Postgres warehouse (direct SQL)",
        connector_type="DESTINATION",
        docker_repository="sql-direct://postgres",
        version="1",
        spec_schema=_POSTGRES_SPEC,
        category="Database",
        description="Writes tables over a database connection. No connector image.",
        supported_destination_sync_modes=["overwrite", "append", "append_dedup"],
    ),
}


@dataclass
class _Actor:
    """A source or destination. Held here because the engine has no store."""

    ref: str
    connector_key: str
    configuration: dict[str, Any]
    name: str


@dataclass
class _Connection:
    """Second place the interface did not fit.

    `create_connection` assumes the engine owns a connection object with a
    lifecycle — Airbyte does. Here a connection is only ever a set of arguments
    to the next sync, so one is fabricated and kept in memory. The ref it
    returns is what the product stores in `engine_mappings`, and it has to
    survive a restart to be honest: it does not, which is recorded as a real
    limitation rather than papered over.
    """

    ref: str
    source_ref: str
    destination_ref: str
    streams: list[ConfiguredStream]
    namespace_format: str | None = None
    stream_prefix: str | None = None


@dataclass
class _Job:
    ref: str
    status: RunStatus = RunStatus.STARTING
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    records: int = 0
    bytes_: int = 0
    stream_stats: list[StreamStat] = field(default_factory=list)
    failure: EngineFailure | None = None
    state: Any | None = None
    logs: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    task: asyncio.Task | None = None

    def log(self, line: str) -> None:
        self.logs.append(f"{_utcnow().isoformat(timespec='seconds')} {line}")


class SqlDirectAdapter:
    """Moves rows between Postgres databases with SQL."""

    engine_type = EngineType.SQL_DIRECT
    contract_version = "1"

    def __init__(self) -> None:
        self._sources: dict[str, _Actor] = {}
        self._destinations: dict[str, _Actor] = {}
        self._connections: dict[str, _Connection] = {}
        self._jobs: dict[str, _Job] = {}

    # ── connection helper ────────────────────────────────────────────────
    @staticmethod
    def _dsn(configuration: dict[str, Any]) -> str:
        user = configuration.get("username", "")
        password = configuration.get("password", "")
        host = configuration.get("host", "localhost")
        port = configuration.get("port", 5432)
        database = configuration.get("database", "")
        credentials = f"{user}:{password}@" if user else ""
        return f"postgresql://{credentials}{host}:{port}/{database}"

    async def _connect(self, configuration: dict[str, Any]):
        import asyncpg

        return await asyncpg.connect(self._dsn(configuration), timeout=20)

    # ── engine ───────────────────────────────────────────────────────────
    async def health(self) -> EngineHealth:
        """Third place the interface did not fit, mildly.

        There is no engine process to ask. The honest answer is that this
        engine is as available as the interpreter running it, so it reports
        healthy and says what it is in `version`.
        """
        return EngineHealth(
            reachable=True, engine_type=self.engine_type, status=EngineStatus.HEALTHY,
            version="sql-direct/1", checked_at=_utcnow(),
            metrics={"in_flight_jobs": sum(
                1 for job in self._jobs.values() if not job.status.is_terminal)},
        )

    async def list_connector_metadata(self) -> list[ConnectorMetadata]:
        return list(CONNECTORS.values())

    async def get_connector_spec(self, connector: ConnectorDescriptor) -> ConnectorMetadata:
        metadata = CONNECTORS.get(connector.connector_key)
        if metadata is None:
            raise EngineOperationError(
                message=f"Engine này không hỗ trợ connector '{connector.connector_key}'.",
                technical_message=(
                    f"sql_direct supports {sorted(CONNECTORS)}; it moves rows "
                    "between Postgres databases and has no connector ecosystem."),
            )
        return metadata

    def declarative_runner(self) -> tuple[str, str] | None:
        """No. This engine cannot run a manifest-defined connector.

        The Connector Builder compiles to the Airbyte low-code CDK, and there
        is nothing here that executes one. Saying so lets `publish` fail with a
        reason instead of recording an image this engine would never run.
        """
        return None

    async def test_declarative_read(self, connector, *, manifest, config,
                                    stream_name, record_limit=25, page_limit=2) -> dict:
        return {
            "ok": False, "records": [], "logs": [], "requests": [],
            "record_preview_supported": False,
            "error": {
                "summary": "Engine hiện tại không chạy được connector tự build.",
                "code": "BUILDER_UNSUPPORTED_BY_ENGINE",
                "category": ErrorCategory.CONFIGURATION.value,
                "technical_message": (
                    "sql_direct has no declarative runtime. Connector Builder "
                    "requires an engine that executes low-code manifests."),
            },
        }

    # ── actors ───────────────────────────────────────────────────────────
    def _register(self, store: dict[str, _Actor], kind: str,
                  request: EngineActorRequest) -> EngineResourceRef:
        ref = f"sql-direct://{kind}/{uuid.uuid4()}"
        store[ref] = _Actor(ref=ref, connector_key=request.connector.connector_key,
                            configuration=dict(request.configuration), name=request.name)
        return EngineResourceRef(ref=ref, engine_type=self.engine_type,
                                 version=request.connector.version)

    async def create_source(self, request: EngineActorRequest) -> EngineResourceRef:
        return self._register(self._sources, "source", request)

    async def update_source(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        self._sources[ref] = _Actor(ref=ref, connector_key=request.connector.connector_key,
                                    configuration=dict(request.configuration),
                                    name=request.name)
        return EngineResourceRef(ref=ref, engine_type=self.engine_type,
                                 version=request.connector.version)

    async def delete_source(self, ref: str) -> None:
        self._sources.pop(ref, None)

    async def resource_exists(self, resource_type: EngineResourceType, ref: str) -> bool:
        registry = {
            EngineResourceType.SOURCE: self._sources,
            EngineResourceType.DESTINATION: self._destinations,
            EngineResourceType.CONNECTION: self._connections,
        }.get(resource_type)
        # Jobs are not retained: this engine runs a sync in-process and keeps
        # no history of its own, so a job ref resolves to nothing by design.
        return ref in registry if registry is not None else False

    async def create_destination(self, request: EngineActorRequest) -> EngineResourceRef:
        return self._register(self._destinations, "destination", request)

    async def update_destination(self, ref: str, request: EngineActorRequest) -> EngineResourceRef:
        self._destinations[ref] = _Actor(
            ref=ref, connector_key=request.connector.connector_key,
            configuration=dict(request.configuration), name=request.name)
        return EngineResourceRef(ref=ref, engine_type=self.engine_type,
                                 version=request.connector.version)

    async def delete_destination(self, ref: str) -> None:
        self._destinations.pop(ref, None)

    async def _check(self, configuration: dict[str, Any], side: str) -> ConnectionCheckResult:
        """Fourth place the interface did not fit.

        There is no `check` operation to call: Airbyte connectors implement one,
        a database does not. Opening a connection and running `SELECT 1` is the
        equivalent, and it answers the same question the caller is asking —
        can this configuration reach the thing it names.
        """
        started = _utcnow()
        try:
            connection = await self._connect(configuration)
            try:
                await connection.fetchval("SELECT 1")
            finally:
                await connection.close()
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheckResult(
                succeeded=False,
                message="Không kết nối được tới cơ sở dữ liệu.",
                error_code="SQL_CONNECT_FAILED",
                category=ErrorCategory.NETWORK,
                technical_message=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
        elapsed = int((_utcnow() - started).total_seconds() * 1000)
        return ConnectionCheckResult(succeeded=True, duration_ms=elapsed)

    async def check_source(self, connector: ConnectorDescriptor,
                           configuration: dict) -> ConnectionCheckResult:
        return await self._check(configuration, "SOURCE")

    async def check_destination(self, connector: ConnectorDescriptor,
                                configuration: dict) -> ConnectionCheckResult:
        return await self._check(configuration, "DESTINATION")

    # ── discovery ────────────────────────────────────────────────────────
    async def discover_source(self, connector: ConnectorDescriptor, configuration: dict,
                              *, source_ref: str | None = None) -> DiscoveredCatalog:
        """The catalogue, read from information_schema instead of a connector."""
        schema = configuration.get("schema") or "public"
        connection = await self._connect(configuration)
        try:
            rows = await connection.fetch(
                """
                SELECT table_name, column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = $1
                 ORDER BY table_name, ordinal_position
                """,
                schema,
            )
            # pg_catalog, not information_schema. `table_constraints` only
            # shows constraints to the table's *owner*, so a least-privilege
            # reader — which is exactly the account a source should use — gets
            # zero rows and no error. Discovery then reports no primary keys,
            # the product offers no deduplication, and nothing anywhere says
            # why. Verified: as `demo_reader` the information_schema query
            # returns 0 rows while this one returns all three keys.
            #
            # array_position keeps composite keys in index order; a key
            # reported in the wrong order is a key that does not match.
            keys = await connection.fetch(
                """
                SELECT c.relname AS table_name, a.attname AS column_name
                  FROM pg_index i
                  JOIN pg_class c ON c.oid = i.indrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  JOIN pg_attribute a
                    ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
                 WHERE i.indisprimary AND n.nspname = $1
                 ORDER BY c.relname, array_position(i.indkey, a.attnum)
                """,
                schema,
            )
        finally:
            await connection.close()

        primary_keys: dict[str, list[list[str]]] = {}
        for row in keys:
            primary_keys.setdefault(row["table_name"], []).append([row["column_name"]])

        tables: dict[str, dict[str, Any]] = {}
        for row in rows:
            properties = tables.setdefault(row["table_name"], {})
            properties[row["column_name"]] = {
                "type": (["null", _json_type(row["data_type"])]
                         if row["is_nullable"] == "YES" else _json_type(row["data_type"]))
            }

        streams = [
            DiscoveredStream(
                name=table,
                namespace=schema,
                json_schema={"type": "object", "properties": properties},
                # Incremental needs a cursor the caller picks; every table here
                # supports both modes and the product decides which to use.
                supported_sync_modes=["full_refresh", "incremental"],
                source_defined_cursor=False,
                default_cursor_field=[],
                source_defined_primary_key=primary_keys.get(table, []),
            )
            for table, properties in sorted(tables.items())
        ]

        material = json.dumps(
            [[s.namespace, s.name, sorted(s.json_schema.get("properties", {}))]
             for s in streams], sort_keys=True)
        import hashlib

        return DiscoveredCatalog(
            streams=streams,
            catalog_hash=hashlib.sha256(material.encode()).hexdigest(),
            discovered_at=_utcnow(),
            connector_version="1",
        )

    # ── connections ──────────────────────────────────────────────────────
    async def create_connection(self, request: EngineConnectionRequest) -> EngineResourceRef:
        ref = f"sql-direct://connection/{uuid.uuid4()}"
        self._connections[ref] = _Connection(
            ref=ref, source_ref=request.source_ref, destination_ref=request.destination_ref,
            streams=list(request.streams), namespace_format=request.namespace_format,
            stream_prefix=request.stream_prefix,
        )
        return EngineResourceRef(ref=ref, engine_type=self.engine_type)

    async def update_connection(self, ref: str,
                                request: EngineConnectionRequest) -> EngineResourceRef:
        self._connections[ref] = _Connection(
            ref=ref, source_ref=request.source_ref, destination_ref=request.destination_ref,
            streams=list(request.streams), namespace_format=request.namespace_format,
            stream_prefix=request.stream_prefix,
        )
        return EngineResourceRef(ref=ref, engine_type=self.engine_type)

    async def delete_connection(self, ref: str) -> None:
        self._connections.pop(ref, None)

    # ── jobs ─────────────────────────────────────────────────────────────
    async def trigger_sync(self, request: EngineSyncRequest) -> EngineJobRef:
        ref = f"sql-direct://job/{request.run_id}"
        job = _Job(ref=ref)
        self._jobs[ref] = job
        job.task = asyncio.create_task(self._run(request, job), name=f"sql-sync-{request.run_id}")
        return EngineJobRef(ref=ref, engine_type=self.engine_type)

    async def get_job(self, ref: str) -> EngineJobStatus:
        job = self._jobs.get(ref)
        if job is None:
            # Same failure mode the embedded engine has, for the same reason:
            # jobs live in this process, so a restart loses them. The product's
            # reconciler resolves these from its own database.
            return EngineJobStatus(
                ref=ref, status=RunStatus.FAILED_TO_START, raw_status="UNTRACKED",
                failure=EngineFailure(
                    code="ENGINE_JOB_LOST", category=ErrorCategory.ENGINE,
                    summary="Không tìm thấy tiến trình đồng bộ trên engine.",
                    remediation_action="RETRY_RUN",
                    fingerprint=fingerprint("sql-direct job lost"),
                ))
        return self._snapshot(job)

    async def cancel_job(self, ref: str) -> EngineJobStatus:
        job = self._jobs.get(ref)
        if job is None:
            return await self.get_job(ref)
        if job.status.is_terminal:
            return self._snapshot(job)      # cancel is idempotent
        job.cancel_requested = True
        job.log("cancel requested")
        return self._snapshot(job)

    async def get_job_logs(self, ref: str, *, cursor: int = 0,
                           limit: int = 500) -> EngineLogResult:
        job = self._jobs.get(ref)
        lines = job.logs if job else []
        window = lines[cursor: cursor + limit]
        next_cursor = cursor + len(window)
        return EngineLogResult(
            lines=window,
            next_cursor=next_cursor if next_cursor < len(lines) else None,
            has_more=next_cursor < len(lines),
            total_lines=len(lines),
        )

    def _snapshot(self, job: _Job) -> EngineJobStatus:
        return EngineJobStatus(
            ref=job.ref, status=job.status, started_at=job.started_at,
            ended_at=job.ended_at,
            records_synced=job.records, bytes_synced=job.bytes_,
            stream_stats=list(job.stream_stats), failure=job.failure,
            state=job.state, raw_status=job.status.value,
        )

    # ── the sync itself ──────────────────────────────────────────────────
    async def _run(self, request: EngineSyncRequest, job: _Job) -> None:
        job.status = RunStatus.RUNNING
        job.log(f"sql-direct sync starting, {len(request.streams)} stream(s)")

        state: dict[str, Any] = dict(request.state or {}) if isinstance(request.state, dict) else {}

        try:
            source = await self._connect(request.source_config)
            destination = await self._connect(request.destination_config)
        except Exception as exc:  # noqa: BLE001
            job.status = RunStatus.FAILED
            job.ended_at = _utcnow()
            job.failure = EngineFailure(
                code="SQL_CONNECT_FAILED", category=ErrorCategory.NETWORK,
                summary="Không kết nối được tới cơ sở dữ liệu.",
                technical_message=f"{type(exc).__name__}: {str(exc)[:300]}",
                fingerprint=fingerprint("sql-direct connect"),
            )
            job.log(f"FAILED: {exc}")
            return

        try:
            target_schema = request.destination_config.get("schema") or "public"
            await destination.execute(
                f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"')

            for stream in request.streams:
                if job.cancel_requested:
                    job.status = RunStatus.CANCELLED
                    job.ended_at = _utcnow()
                    job.log("cancelled before finishing")
                    return
                moved, size = await self._sync_stream(
                    source, destination, stream, target_schema, state, job)
                job.records += moved
                job.bytes_ += size
                job.stream_stats.append(StreamStat(
                    stream_name=stream.name, namespace=stream.namespace,
                    records_emitted=moved, bytes_emitted=size))

            job.state = state or None
            job.status = RunStatus.SUCCEEDED
            job.ended_at = _utcnow()
            job.log(f"done: {job.records} record(s), {job.bytes_} byte(s)")
        except Exception as exc:  # noqa: BLE001
            job.status = RunStatus.FAILED
            job.ended_at = _utcnow()
            job.failure = EngineFailure(
                code="SQL_SYNC_FAILED", category=ErrorCategory.SOURCE_READ,
                summary="Đồng bộ thất bại.",
                technical_message=f"{type(exc).__name__}: {str(exc)[:300]}",
                fingerprint=fingerprint(f"sql-direct {type(exc).__name__}"),
            )
            job.log(f"FAILED: {exc}")
            log_event(logger, logging.ERROR, "sql_direct.sync_failed",
                      ref=job.ref, error=str(exc)[:300])
        finally:
            await source.close()
            await destination.close()

    async def _sync_stream(self, source, destination, stream: ConfiguredStream,
                           target_schema: str, state: dict[str, Any],
                           job: _Job) -> tuple[int, int]:
        qualified = f'"{stream.namespace or "public"}"."{stream.name}"'
        target = f'"{target_schema}"."{stream.name}"'
        columns = sorted((stream.json_schema.get("properties") or {}).keys())
        if not columns:
            job.log(f"{stream.name}: no columns selected, skipping")
            return 0, 0

        column_list = ", ".join(f'"{c}"' for c in columns)
        cursor = stream.cursor_field[0] if stream.cursor_field else None
        incremental = stream.sync_mode == "incremental" and cursor
        state_key = f"{stream.namespace or ''}.{stream.name}"

        query = f"SELECT {column_list} FROM {qualified}"
        arguments: list[Any] = []
        if incremental and state.get(state_key) is not None:
            query += f' WHERE "{cursor}" > $1'
            arguments.append(state[state_key])
        if incremental:
            query += f' ORDER BY "{cursor}"'

        rows = await source.fetch(query, *arguments)
        job.log(f"{stream.name}: read {len(rows)} row(s)"
                + (f" since {state.get(state_key)}" if incremental else ""))

        if stream.destination_sync_mode == "overwrite":
            await destination.execute(f"DROP TABLE IF EXISTS {target}")

        await destination.execute(
            f"CREATE TABLE IF NOT EXISTS {target} ("
            + ", ".join(f'"{c}" text' for c in columns)
            + ', "_appbi_extracted_at" timestamptz DEFAULT now())'
        )

        if not rows:
            return 0, 0

        # append_dedup without a primary key would silently duplicate, so the
        # keys drive a delete-then-insert rather than being ignored.
        if stream.destination_sync_mode == "append_dedup" and stream.primary_key:
            key_columns = [k[0] for k in stream.primary_key if k]
            for row in rows:
                where = " AND ".join(
                    f'"{name}" = ${i + 1}' for i, name in enumerate(key_columns))
                await destination.execute(
                    f"DELETE FROM {target} WHERE {where}",
                    *[str(row[name]) for name in key_columns])

        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        payload = [tuple(_as_text(row[c]) for c in columns) for row in rows]
        await destination.executemany(
            f"INSERT INTO {target} ({column_list}) VALUES ({placeholders})", payload)

        if incremental:
            state[state_key] = max(row[cursor] for row in rows)

        size = sum(len(str(value) or "") for row in payload for value in row)
        job.log(f"{stream.name}: wrote {len(rows)} row(s) to {target}")
        return len(rows), size

    async def close(self) -> None:
        for job in list(self._jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()


def _as_text(value: Any) -> str | None:
    """Everything lands as text.

    A real warehouse loader maps types. This one does not, and says so rather
    than implying a fidelity it does not have: the purpose is to exercise the
    interface, and type mapping is the connector ecosystem's problem, which is
    exactly what this engine deliberately does not have.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _json_type(sql_type: str) -> str:
    if sql_type in ("integer", "bigint", "smallint"):
        return "integer"
    if sql_type in ("numeric", "real", "double precision"):
        return "number"
    if sql_type == "boolean":
        return "boolean"
    return "string"
