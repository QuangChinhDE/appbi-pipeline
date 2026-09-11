"""Airbyte Protocol message handling.

The wire format between a connector and its orchestrator is newline-delimited
JSON on stdout/stdin. This module knows that format and nothing else -- parsing,
catalog construction and state extraction. It is the reason the platform can
drive genuine `airbyte/source-*` and `airbyte/destination-*` images.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.adapters.dto import ConfiguredStream, DiscoveredStream

# Message types we act on. Anything else (ANALYTICS, ESTIMATE, CONTROL...) is
# logged and ignored -- forward compatibility with newer connector versions.
TYPE_RECORD = "RECORD"
TYPE_STATE = "STATE"
TYPE_LOG = "LOG"
TYPE_TRACE = "TRACE"
TYPE_SPEC = "SPEC"
TYPE_CATALOG = "CATALOG"
TYPE_CONNECTION_STATUS = "CONNECTION_STATUS"


@dataclass(slots=True)
class AirbyteMessage:
    type: str
    payload: dict[str, Any]
    raw: bytes


def parse_line(line: bytes) -> AirbyteMessage | None:
    """Return a message, or None for noise the connector printed on stdout."""
    stripped = line.strip()
    if not stripped or not stripped.startswith(b"{"):
        return None
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    msg_type = payload.get("type")
    if not isinstance(msg_type, str):
        return None
    return AirbyteMessage(type=msg_type, payload=payload, raw=stripped)


def log_text(message: AirbyteMessage) -> str | None:
    """Flatten LOG / TRACE messages into one human-readable line."""
    if message.type == TYPE_LOG:
        log = message.payload.get("log") or {}
        return f"[{log.get('level', 'INFO')}] {log.get('message', '')}"
    if message.type == TYPE_TRACE:
        trace = message.payload.get("trace") or {}
        kind = trace.get("type")
        if kind == "ERROR":
            err = trace.get("error") or {}
            parts = [err.get("message") or "", err.get("internal_message") or ""]
            return "[ERROR] " + " | ".join(p for p in parts if p)
        if kind == "STREAM_STATUS":
            status = trace.get("stream_status") or {}
            descriptor = (status.get("stream_descriptor") or {}).get("name", "?")
            return f"[STATUS] {descriptor}: {status.get('status')}"
        return f"[TRACE] {json.dumps(trace)[:500]}"
    return None


def trace_error(message: AirbyteMessage) -> dict[str, Any] | None:
    if message.type != TYPE_TRACE:
        return None
    trace = message.payload.get("trace") or {}
    if trace.get("type") != "ERROR":
        return None
    return trace.get("error") or {}


def record_stream_key(message: AirbyteMessage) -> tuple[str | None, str]:
    record = message.payload.get("record") or {}
    return record.get("namespace"), str(record.get("stream", ""))


def state_payload(message: AirbyteMessage) -> Any:
    """The bit a source expects back via `--state` on the next run."""
    return message.payload.get("state")


# ── catalog ────────────────────────────────────────────────────────────────

def parse_catalog(payload: dict[str, Any]) -> list[DiscoveredStream]:
    catalog = payload.get("catalog") or payload
    streams: list[DiscoveredStream] = []
    for raw in catalog.get("streams") or []:
        streams.append(
            DiscoveredStream(
                name=raw.get("name", ""),
                namespace=raw.get("namespace"),
                json_schema=raw.get("json_schema") or {},
                supported_sync_modes=list(raw.get("supported_sync_modes") or ["full_refresh"]),
                source_defined_cursor=bool(raw.get("source_defined_cursor")),
                default_cursor_field=list(raw.get("default_cursor_field") or []),
                source_defined_primary_key=[list(pk) for pk in (raw.get("source_defined_primary_key") or [])],
                is_resumable=raw.get("is_resumable"),
            )
        )
    streams.sort(key=lambda s: (s.namespace or "", s.name))
    return streams


def catalog_hash(streams: list[DiscoveredStream]) -> str:
    """Stable over ordering so a re-discover of identical schema hashes equal."""
    material = json.dumps(
        [
            {
                "namespace": s.namespace,
                "name": s.name,
                "schema": s.json_schema,
                "modes": sorted(s.supported_sync_modes),
                "pk": s.source_defined_primary_key,
                "cursor": s.default_cursor_field,
            }
            for s in streams
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def stream_schema_hash(stream: DiscoveredStream) -> str:
    """Kept for callers inside this module; the implementation is on the DTO."""
    return stream.schema_hash


#: What Airbyte substitutes in a custom namespace format. Kept identical so a
#: format written against Airbyte's documentation behaves the same here.
SOURCE_NAMESPACE_TOKEN = "${SOURCE_NAMESPACE}"


@dataclass(frozen=True, slots=True)
class StreamNaming:
    """How a stream is named at the destination, versus at the source.

    Airbyte does this in the platform, not in a connector: `NamespacingMapper`
    sits in the replication worker, rewrites the catalog handed to the
    destination, rewrites the stream descriptor on every record passing
    through, and reverts state messages on the way back so what is persisted
    is still in the source's terms. This is the same job in the same place --
    the source never learns the destination's names, and the cursor saved at
    the end stays resumable against a source that has never heard of a prefix.
    """

    prefix: str | None = None
    namespace_format: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.prefix) or bool(self.namespace_format)

    def name_at_destination(self, name: str) -> str:
        return f"{self.prefix}{name}" if self.prefix else name

    def namespace_at_destination(self, namespace: str | None) -> str | None:
        """A format of `${SOURCE_NAMESPACE}` reproduces the source namespace,
        which is Airbyte's default behaviour spelled out."""
        if not self.namespace_format:
            return namespace
        resolved = self.namespace_format.replace(SOURCE_NAMESPACE_TOKEN, namespace or "")
        # An empty result means the format asked for a namespace the source did
        # not supply. Falling back to the source's own is what Airbyte does;
        # sending "" would have the destination create a nameless schema.
        return resolved or namespace

    def map_message(self, message: "AirbyteMessage") -> bytes:
        """Rewrite a RECORD or per-stream STATE for the destination.

        Returns the original bytes when there is nothing to change, so a sync
        without a prefix still forwards without re-serialising every record.
        """
        if not self.active:
            return message.raw
        if message.type == TYPE_RECORD:
            record = message.payload.get("record")
            if not isinstance(record, dict):
                return message.raw
            mapped = dict(message.payload)
            record = dict(record)
            record["stream"] = self.name_at_destination(str(record.get("stream", "")))
            namespace = self.namespace_at_destination(record.get("namespace"))
            if namespace is not None:
                record["namespace"] = namespace
            mapped["record"] = record
            return _dump(mapped)
        if message.type == TYPE_STATE:
            state = message.payload.get("state")
            if not isinstance(state, dict):
                return message.raw
            mapped = _rename_descriptors(state, self._to_destination)
            if mapped is None:
                return message.raw
            return _dump({**message.payload, "state": mapped})
        return message.raw

    def revert_state(self, state: Any) -> Any:
        """Undo `map_message` on state coming back from the destination.

        Takes the state object itself -- what is handed back to the source via
        `--state` on the next run, and what is stored on the pipeline.

        Persisting the destination's names would hand the next run a cursor for
        a stream its source never emitted: the source would ignore it and
        silently re-read its whole history, and `_incremental_only` would no
        longer recognise the stream it belongs to. That class of mistake has
        already emptied a destination once here, so it is undone at the edge
        rather than tolerated downstream.
        """
        if not self.active or not isinstance(state, dict):
            return state
        mapped = _rename_descriptors(state, self._to_source)
        return mapped if mapped is not None else state

    # -- descriptor rewriting, both directions ----------------------------
    def _to_destination(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        out = dict(descriptor)
        out["name"] = self.name_at_destination(str(descriptor.get("name", "")))
        namespace = self.namespace_at_destination(descriptor.get("namespace"))
        if namespace is not None:
            out["namespace"] = namespace
        return out

    def _to_source(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        out = dict(descriptor)
        name = str(descriptor.get("name", ""))
        if self.prefix and name.startswith(self.prefix):
            out["name"] = name[len(self.prefix):]
        return out


def _dump(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _rename_descriptors(state: dict[str, Any], rename) -> dict[str, Any] | None:
    """Apply `rename` to every stream descriptor one state object carries.

    Both shapes are covered: a STREAM state names one stream, a GLOBAL state
    carries a list of them under `stream_states`. A legacy blob names nothing,
    and returning None leaves the caller forwarding the original bytes.
    """
    changed = False
    new_state = dict(state)

    stream = state.get("stream")
    if isinstance(stream, dict) and isinstance(stream.get("stream_descriptor"), dict):
        new_stream = dict(stream)
        new_stream["stream_descriptor"] = rename(stream["stream_descriptor"])
        new_state["stream"] = new_stream
        changed = True

    glob = state.get("global")
    if isinstance(glob, dict) and isinstance(glob.get("stream_states"), list):
        new_global = dict(glob)
        new_global["stream_states"] = [
            {**entry, "stream_descriptor": rename(entry["stream_descriptor"])}
            if isinstance(entry, dict) and isinstance(entry.get("stream_descriptor"), dict)
            else entry
            for entry in glob["stream_states"]
        ]
        new_state["global"] = new_global
        changed = True

    if not changed:
        return None
    return new_state


def build_configured_catalog(
    streams: list[ConfiguredStream], *, generation_id: int = 1, sync_id: int = 1,
    naming: StreamNaming | None = None,
) -> dict[str, Any]:
    """ConfiguredAirbyteCatalog -- handed to both `read` and `write`.

    Built twice per sync when `naming` renames anything: the source is given
    the names it discovered, the destination the names its tables should have.
    Sharing one catalog between the two is why a prefix could not be honoured.

    `generation_id` / `minimum_generation_id` / `sync_id` implement the Airbyte
    refresh protocol. A destination that overwrites is told to drop everything
    below the current generation; an appending one keeps every generation
    (minimum 0). Recent destination connectors refuse to start without these.
    """
    naming = naming or StreamNaming()
    configured: list[dict[str, Any]] = []
    for stream in streams:
        truncating = stream.destination_sync_mode == "overwrite"
        entry: dict[str, Any] = {
            "stream": {
                "name": naming.name_at_destination(stream.name),
                "json_schema": stream.json_schema or {"type": "object"},
                "supported_sync_modes": sorted({stream.sync_mode, "full_refresh"}),
            },
            "sync_mode": stream.sync_mode,
            "destination_sync_mode": stream.destination_sync_mode,
            "generation_id": generation_id,
            "minimum_generation_id": generation_id if truncating else 0,
            "sync_id": sync_id,
        }
        namespace = naming.namespace_at_destination(stream.namespace)
        if namespace:
            entry["stream"]["namespace"] = namespace
        if stream.cursor_field:
            entry["cursor_field"] = stream.cursor_field
            entry["stream"]["default_cursor_field"] = stream.cursor_field
        if stream.primary_key:
            entry["primary_key"] = stream.primary_key
            entry["stream"]["source_defined_primary_key"] = stream.primary_key
        configured.append(entry)
    return {"streams": configured}


def normalize_state_for_source(state: Any) -> Any:
    """`--state` accepts a list of AirbyteStateMessage payloads, or legacy blob."""
    if state is None:
        return None
    if isinstance(state, list):
        return state
    if isinstance(state, dict):
        # Legacy single-blob state, or already a state message.
        if "type" in state or "data" in state or "stream" in state or "global" in state:
            return [state]
        return state
    return None


#: Config key a source may carry to change how many records one request asks
#: for. Blank or absent leaves the connector's own choice alone.
PAGE_SIZE_CONFIG_KEY = "page_size"


def with_page_size(manifest: dict[str, Any], page_size: int | None) -> dict[str, Any]:
    """Return the manifest with every declared page size replaced.

    How large a page is decides peak memory in the source container: one page
    is held whole before records are emitted. A Base ticket averages ~326 KB,
    so a page of 500 is ~163 MB before the CDK's own overhead, and on a tenant
    with larger records that was enough to have the container killed. The
    deployment engineer's only way out was to edit the connector's source and
    carry the patch across every update -- which is what this replaces.

    Only a page size the connector already declares is changed. Where none is
    declared the connector deliberately says nothing, because the CDK compares
    a short page against `page_size` to decide a stream has ended: asserting a
    size the server was never told about either stops early on the server's
    own default page, or pages past the end forever.

    The manifest is copied rather than edited: it is the connector definition
    shared by every source in the deployment, and one source's tuning must not
    reach another's sync.
    """
    if not page_size or page_size <= 0 or not manifest:
        return manifest

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            out = {key: rewrite(value) for key, value in node.items()}
            strategy = out.get("pagination_strategy")
            if isinstance(strategy, dict) and "page_size" in strategy:
                strategy["page_size"] = page_size
            return out
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        return node

    return rewrite(manifest)


def page_size_from_config(configuration: dict[str, Any], fallback: int = 0) -> int:
    """What this source asked for, or the deployment default, or nothing.

    Read leniently: the value arrives from a JSON-schema string field, so "100"
    and 100 both mean the same thing, and anything unreadable means "leave the
    connector alone" rather than raising in the middle of a sync.
    """
    raw = (configuration or {}).get(PAGE_SIZE_CONFIG_KEY)
    if raw is None or raw == "":
        return fallback
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def parse_spec(payload: dict[str, Any]) -> dict[str, Any]:
    spec = payload.get("spec") or {}
    return {
        "connection_specification": spec.get("connectionSpecification") or {},
        "documentation_url": spec.get("documentationUrl"),
        "supports_incremental": bool(spec.get("supportsIncremental", True)),
        "supports_normalization": bool(spec.get("supportsNormalization", False)),
        "supports_dbt": bool(spec.get("supportsDBT", False)),
        "supported_destination_sync_modes": list(spec.get("supported_destination_sync_modes") or []),
        "advanced_auth": spec.get("advanced_auth"),
    }


def connection_status(payload: dict[str, Any]) -> tuple[bool, str | None]:
    status = payload.get("connectionStatus") or {}
    return status.get("status") == "SUCCEEDED", status.get("message")
