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


def build_configured_catalog(
    streams: list[ConfiguredStream], *, generation_id: int = 1, sync_id: int = 1
) -> dict[str, Any]:
    """ConfiguredAirbyteCatalog -- handed to both `read` and `write`.

    `generation_id` / `minimum_generation_id` / `sync_id` implement the Airbyte
    refresh protocol. A destination that overwrites is told to drop everything
    below the current generation; an appending one keeps every generation
    (minimum 0). Recent destination connectors refuse to start without these.
    """
    configured: list[dict[str, Any]] = []
    for stream in streams:
        truncating = stream.destination_sync_mode == "overwrite"
        entry: dict[str, Any] = {
            "stream": {
                "name": stream.name,
                "json_schema": stream.json_schema or {"type": "object"},
                "supported_sync_modes": sorted({stream.sync_mode, "full_refresh"}),
            },
            "sync_mode": stream.sync_mode,
            "destination_sync_mode": stream.destination_sync_mode,
            "generation_id": generation_id,
            "minimum_generation_id": generation_id if truncating else 0,
            "sync_id": sync_id,
        }
        if stream.namespace:
            entry["stream"]["namespace"] = stream.namespace
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
