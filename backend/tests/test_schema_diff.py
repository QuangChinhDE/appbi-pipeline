"""Schema diff classification (spec section 15.2).

Severity depends on whether a stream is actually selected: a field nobody syncs
disappearing is information, the same field disappearing from a selected stream
is breaking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.enums import SchemaChangeSeverity
from app.services import schema_service


class FakeSnapshot:
    """Minimal stand-in — diff() only reads `normalized_catalog`."""

    def __init__(self, streams: list[dict[str, Any]]) -> None:
        self.normalized_catalog = {"streams": streams}
        self.discovered_at = datetime.now(timezone.utc)


def stream(
    name: str,
    fields: dict[str, str],
    *,
    namespace: str | None = None,
    primary_key: list[list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "namespace": namespace,
        "json_schema": {
            "type": "object",
            "properties": {key: {"type": value} for key, value in fields.items()},
        },
        "supported_sync_modes": ["full_refresh", "incremental"],
        "source_defined_primary_key": primary_key or [],
        "default_cursor_field": [],
    }


BASE = FakeSnapshot([
    stream("orders", {"id": "integer", "total": "number", "updated_at": "string"}),
    stream("customers", {"id": "integer", "email": "string"}),
])

SELECTED = {(None, "orders"), (None, "customers")}


def severities(result: dict[str, list[dict[str, Any]]], kind: str) -> list[str]:
    return [c["severity"] for bucket in result.values() for c in bucket if c["kind"] == kind]


def test_new_stream_is_informational_and_not_auto_selected() -> None:
    after = FakeSnapshot(BASE.normalized_catalog["streams"] + [
        stream("refunds", {"id": "integer"}),
    ])
    result = schema_service.diff(BASE, after, selected=SELECTED)
    assert severities(result, "STREAM_ADDED") == [SchemaChangeSeverity.INFO.value]
    assert not schema_service.has_breaking(result)


def test_new_field_is_informational() -> None:
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "total": "number",
                          "updated_at": "string", "currency": "string"}),
        stream("customers", {"id": "integer", "email": "string"}),
    ])
    result = schema_service.diff(BASE, after, selected=SELECTED)
    assert severities(result, "FIELD_ADDED") == [SchemaChangeSeverity.INFO.value]
    assert not schema_service.has_breaking(result)


def test_removing_a_selected_stream_is_breaking() -> None:
    after = FakeSnapshot([stream("customers", {"id": "integer", "email": "string"})])
    result = schema_service.diff(BASE, after, selected=SELECTED)
    assert severities(result, "STREAM_REMOVED") == [SchemaChangeSeverity.BREAKING.value]
    assert schema_service.has_breaking(result)


def test_removing_an_unselected_stream_is_only_informational() -> None:
    after = FakeSnapshot([stream("customers", {"id": "integer", "email": "string"})])
    result = schema_service.diff(BASE, after, selected={(None, "customers")})
    assert severities(result, "STREAM_REMOVED") == [SchemaChangeSeverity.INFO.value]
    assert not schema_service.has_breaking(result)


def test_removing_a_selected_field_is_breaking() -> None:
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "updated_at": "string"}),
        stream("customers", {"id": "integer", "email": "string"}),
    ])
    result = schema_service.diff(BASE, after, selected=SELECTED)
    assert severities(result, "FIELD_REMOVED") == [SchemaChangeSeverity.BREAKING.value]


def test_type_change_on_a_selected_stream_is_breaking() -> None:
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "total": "string", "updated_at": "string"}),
        stream("customers", {"id": "integer", "email": "string"}),
    ])
    result = schema_service.diff(BASE, after, selected=SELECTED)
    assert severities(result, "FIELD_TYPE_CHANGED") == [SchemaChangeSeverity.BREAKING.value]


def test_type_change_on_an_unselected_stream_is_only_a_warning() -> None:
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "total": "string", "updated_at": "string"}),
        stream("customers", {"id": "integer", "email": "string"}),
    ])
    result = schema_service.diff(BASE, after, selected={(None, "customers")})
    assert severities(result, "FIELD_TYPE_CHANGED") == [SchemaChangeSeverity.WARNING.value]


def test_losing_the_cursor_is_breaking() -> None:
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "total": "number"}),
        stream("customers", {"id": "integer", "email": "string"}),
    ])
    result = schema_service.diff(
        BASE, after, selected=SELECTED,
        selected_cursors={(None, "orders"): ["updated_at"]},
    )
    assert severities(result, "CURSOR_REMOVED") == [SchemaChangeSeverity.BREAKING.value]
    assert schema_service.has_breaking(result)


def test_primary_key_change_on_a_selected_stream_is_breaking() -> None:
    before = FakeSnapshot([stream("orders", {"id": "integer"}, primary_key=[["id"]])])
    after = FakeSnapshot([
        stream("orders", {"id": "integer", "uid": "string"}, primary_key=[["uid"]]),
    ])
    result = schema_service.diff(before, after, selected={(None, "orders")})
    assert severities(result, "PRIMARY_KEY_CHANGED") == [SchemaChangeSeverity.BREAKING.value]


def test_identical_snapshots_produce_no_changes() -> None:
    result = schema_service.diff(BASE, BASE, selected=SELECTED)
    assert result == {"added": [], "removed": [], "changed": []}


def test_field_list_flattens_nullable_unions() -> None:
    fields = schema_service.field_list({
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "note": {"type": ["null", "string"]},
            "when": {"type": "string", "format": "date-time"},
        },
    })
    by_name = {field["name"]: field for field in fields}
    assert by_name["note"]["nullable"] is True
    assert by_name["note"]["type"] == "string"
    assert "date-time" in by_name["when"]["type"]
