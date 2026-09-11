"""Naming a destination table something other than what the source called it.

Airbyte supports this and always has -- `prefix` on a connection, and a custom
namespace format. It is not a connector feature: `NamespacingMapper` lives in
the platform's replication worker, rewrites the catalog the destination is
given, rewrites the stream descriptor on every record passing through, and
reverts state on the way back so what is persisted stays in the source's
terms.

This product runs its own replication loop in embedded mode, and that loop had
none of it: one catalog for both sides, records forwarded as raw bytes. The
field was accepted, stored, and silently ignored -- which is how Base Service
and Base Workflow, whose sources both expose a stream called `stage`, were
given prefixes so they would not collide and then collided anyway, corrupting
each other's `stage_airbyte_tmp` staging table in four of thirty-six runs.

These tests pin the mapper. No Docker and no database: it is pure message
rewriting, which is the part worth testing directly.
"""

from __future__ import annotations

import json

import pytest

from app.adapters.airbyte_protocol import protocol as ap
from app.adapters.dto import ConfiguredStream


def _record(stream: str, namespace: str | None = None, **data) -> ap.AirbyteMessage:
    payload = {"type": "RECORD",
               "record": {"stream": stream, "data": data or {"id": 1}, "emitted_at": 1}}
    if namespace is not None:
        payload["record"]["namespace"] = namespace
    return ap.parse_line(json.dumps(payload).encode())


def _stream_state(name: str, namespace: str | None = "public", cursor: str = "2026-01-01"):
    return {
        "type": "STREAM",
        "stream": {
            "stream_state": {"updated_at": cursor},
            "stream_descriptor": {"name": name, "namespace": namespace},
        },
    }


def _state_message(state: dict) -> ap.AirbyteMessage:
    return ap.parse_line(json.dumps({"type": "STATE", "state": state}).encode())


def _configured(name: str, namespace: str | None = "public") -> ConfiguredStream:
    return ConfiguredStream(
        name=name, namespace=namespace, json_schema={"type": "object"},
        sync_mode="incremental", destination_sync_mode="append_dedup",
        cursor_field=["updated_at"], primary_key=[["id"]],
    )


# ── the names themselves ───────────────────────────────────────────────────

def test_a_prefix_is_added_to_the_destination_name() -> None:
    naming = ap.StreamNaming(prefix="base_")
    assert naming.name_at_destination("stage") == "base_stage"


def test_no_prefix_leaves_the_name_alone() -> None:
    assert ap.StreamNaming().name_at_destination("stage") == "stage"
    assert ap.StreamNaming(prefix="").name_at_destination("stage") == "stage"


def test_the_source_namespace_token_is_substituted() -> None:
    """Airbyte's own placeholder, so a format copied from their documentation
    behaves the same here."""
    naming = ap.StreamNaming(namespace_format="${SOURCE_NAMESPACE}_raw")
    assert naming.namespace_at_destination("public") == "public_raw"


def test_a_literal_namespace_format_wins_over_the_source() -> None:
    naming = ap.StreamNaming(namespace_format="base_service")
    assert naming.namespace_at_destination("public") == "base_service"


def test_a_format_that_resolves_to_nothing_falls_back() -> None:
    """A source that emits no namespace against a format of only the token
    would otherwise ask the destination for a schema named `""`."""
    naming = ap.StreamNaming(namespace_format="${SOURCE_NAMESPACE}")
    assert naming.namespace_at_destination(None) is None


def test_doing_nothing_is_recognised_as_doing_nothing() -> None:
    """`active` decides whether the hot path re-serialises every record."""
    assert ap.StreamNaming().active is False
    assert ap.StreamNaming(prefix="", namespace_format="").active is False
    assert ap.StreamNaming(prefix="base_").active is True
    assert ap.StreamNaming(namespace_format="raw").active is True


# ── the two catalogs ───────────────────────────────────────────────────────

def test_the_destination_catalog_carries_the_new_names() -> None:
    catalog = ap.build_configured_catalog(
        [_configured("stage")], naming=ap.StreamNaming(prefix="service_"),
    )
    assert catalog["streams"][0]["stream"]["name"] == "service_stage"


def test_the_source_catalog_is_untouched() -> None:
    """The bug this prevents: asking a source for `service_stage`, a stream it
    has never heard of, and getting an empty sync that reports success."""
    catalog = ap.build_configured_catalog([_configured("stage")])
    assert catalog["streams"][0]["stream"]["name"] == "stage"


def test_the_namespace_format_reaches_the_destination_catalog() -> None:
    catalog = ap.build_configured_catalog(
        [_configured("stage", "public")],
        naming=ap.StreamNaming(namespace_format="${SOURCE_NAMESPACE}_raw"),
    )
    assert catalog["streams"][0]["stream"]["namespace"] == "public_raw"


def test_everything_else_about_the_stream_survives_the_rename() -> None:
    """Cursor, primary key and sync modes decide how the destination writes.
    Rebuilding the catalog must not quietly drop them."""
    plain = ap.build_configured_catalog([_configured("stage")])["streams"][0]
    renamed = ap.build_configured_catalog(
        [_configured("stage")], naming=ap.StreamNaming(prefix="x_"),
    )["streams"][0]

    assert renamed["cursor_field"] == plain["cursor_field"] == ["updated_at"]
    assert renamed["primary_key"] == plain["primary_key"] == [["id"]]
    assert renamed["sync_mode"] == plain["sync_mode"]
    assert renamed["destination_sync_mode"] == plain["destination_sync_mode"]
    assert renamed["generation_id"] == plain["generation_id"]


# ── records on the way through ─────────────────────────────────────────────

def test_a_record_is_renamed_for_the_destination() -> None:
    mapped = json.loads(ap.StreamNaming(prefix="service_").map_message(_record("stage")))
    assert mapped["record"]["stream"] == "service_stage"


def test_the_record_payload_is_not_disturbed() -> None:
    """Every row of every sync goes through here. Losing a field, or the
    emitted_at a destination orders by, would corrupt the data itself."""
    message = _record("stage", "public", id=7, name="Hà Nội", nested={"a": [1, 2]})
    mapped = json.loads(ap.StreamNaming(prefix="p_").map_message(message))
    assert mapped["record"]["data"] == {"id": 7, "name": "Hà Nội", "nested": {"a": [1, 2]}}
    assert mapped["record"]["emitted_at"] == 1
    assert mapped["type"] == "RECORD"


def test_an_unrenamed_sync_forwards_the_original_bytes() -> None:
    """The hot path must not pay for a feature nobody turned on: identity,
    not a JSON round-trip."""
    message = _record("stage")
    assert ap.StreamNaming().map_message(message) is message.raw


def test_the_namespace_on_a_record_is_mapped_too() -> None:
    message = _record("stage", "public")
    mapped = json.loads(
        ap.StreamNaming(namespace_format="${SOURCE_NAMESPACE}_raw").map_message(message)
    )
    assert mapped["record"]["namespace"] == "public_raw"


def test_a_record_without_a_namespace_gains_one_only_from_a_format() -> None:
    """Inventing a namespace would move every table into a schema nobody
    asked for."""
    mapped = json.loads(ap.StreamNaming(prefix="p_").map_message(_record("stage")))
    assert "namespace" not in mapped["record"]


# ── state, both directions ─────────────────────────────────────────────────

def test_state_going_to_the_destination_is_renamed() -> None:
    """The destination tracks state against the catalog it was given. Sending
    it a descriptor naming a stream not in that catalog is a mismatch it has
    no way to resolve."""
    mapped = json.loads(
        ap.StreamNaming(prefix="service_").map_message(_state_message(_stream_state("stage")))
    )
    assert mapped["state"]["stream"]["stream_descriptor"]["name"] == "service_stage"


def test_state_coming_back_is_reverted_to_source_names() -> None:
    """The one that matters. State is handed to the *source* on the next run.
    A cursor filed under `service_stage` would be ignored by a source that
    only knows `stage`, and the stream would re-read its whole history --
    or worse, be matched to nothing by `_incremental_only` and dropped."""
    committed = _stream_state("service_stage")
    reverted = ap.StreamNaming(prefix="service_").revert_state(committed)
    assert reverted["stream"]["stream_descriptor"]["name"] == "stage"


def test_the_cursor_value_survives_the_round_trip() -> None:
    naming = ap.StreamNaming(prefix="service_")
    out = json.loads(naming.map_message(_state_message(_stream_state("stage", cursor="2026-09-11"))))
    back = naming.revert_state(out["state"])
    assert back["stream"]["stream_state"] == {"updated_at": "2026-09-11"}
    assert back["stream"]["stream_descriptor"]["name"] == "stage"


def test_a_name_that_already_starts_with_the_prefix_is_not_over_stripped() -> None:
    """`stage` under prefix `stage_` becomes `stage_stage`; reverting must
    remove one prefix, not both halves."""
    naming = ap.StreamNaming(prefix="stage_")
    out = json.loads(naming.map_message(_state_message(_stream_state("stage"))))
    assert out["state"]["stream"]["stream_descriptor"]["name"] == "stage_stage"
    assert naming.revert_state(out["state"])["stream"]["stream_descriptor"]["name"] == "stage"


def test_global_state_maps_every_stream_it_carries() -> None:
    """CDC sources emit one shared cursor plus a descriptor per stream."""
    glob = {
        "type": "GLOBAL",
        "global": {
            "shared_state": {"lsn": 42},
            "stream_states": [
                {"stream_descriptor": {"name": "stage", "namespace": "public"},
                 "stream_state": {"a": 1}},
                {"stream_descriptor": {"name": "ticket", "namespace": "public"},
                 "stream_state": {"a": 2}},
            ],
        },
    }
    naming = ap.StreamNaming(prefix="svc_")
    out = json.loads(naming.map_message(_state_message(glob)))
    names = [e["stream_descriptor"]["name"] for e in out["state"]["global"]["stream_states"]]
    assert names == ["svc_stage", "svc_ticket"]

    back = naming.revert_state(out["state"])
    assert [e["stream_descriptor"]["name"] for e in back["global"]["stream_states"]] == [
        "stage", "ticket"]
    assert back["global"]["shared_state"] == {"lsn": 42}


def test_a_legacy_blob_state_passes_through() -> None:
    """Older sources emit `{"data": {...}}` with no descriptor. There is
    nothing to rename and nothing to break."""
    blob = {"type": "LEGACY", "data": {"cursor": "abc"}}
    message = _state_message(blob)
    naming = ap.StreamNaming(prefix="p_")
    assert naming.map_message(message) is message.raw
    assert naming.revert_state(blob) == blob


@pytest.mark.parametrize("state", [None, "", 42, []])
def test_reverting_a_non_state_is_harmless(state) -> None:
    assert ap.StreamNaming(prefix="p_").revert_state(state) == state


def test_reverting_without_a_prefix_changes_nothing() -> None:
    committed = _stream_state("stage")
    assert ap.StreamNaming().revert_state(committed) == committed


# ── what the two collide-prone Base pipelines actually get ─────────────────

def test_two_pipelines_with_the_same_stream_no_longer_collide() -> None:
    """The measured failure, stated as the fix. Base Service and Base Workflow
    both expose `stage`; given a prefix each, their destination tables and
    their staging tables are now distinct."""
    service = ap.build_configured_catalog(
        [_configured("stage")], naming=ap.StreamNaming(prefix="service_"))
    workflow = ap.build_configured_catalog(
        [_configured("stage")], naming=ap.StreamNaming(prefix="workflow_"))

    assert service["streams"][0]["stream"]["name"] == "service_stage"
    assert workflow["streams"][0]["stream"]["name"] == "workflow_stage"
    assert (service["streams"][0]["stream"]["name"]
            != workflow["streams"][0]["stream"]["name"])
