"""Connector Builder: compilation, validation, and the boundaries it must keep."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
# The compiler, not the persistence layer: these tests exercise pure
# functions and must not need a database driver to run.
from app.services import builder_manifest as builder


def _definition(**overrides):
    base = {
        "name": "Orders API",
        "base_url": "https://api.example.com",
        "auth": {"method": "none"},
        "streams": [{
            "name": "orders",
            "path": "/v1/orders",
            "http_method": "GET",
            "record_selector": "data.items",
            "primary_key": "id",
            "pagination": {"mode": "page", "page_size": 100},
            "incremental": False,
            "query_params": [{"key": "status", "value": "paid"}],
            "headers": [],
        }],
    }
    base.update(overrides)
    return base


# ── validation refuses what cannot possibly run ────────────────────────────

def test_base_url_must_be_absolute() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(base_url="api.example.com"))
    assert caught.value.code == "BUILDER_BASE_URL_INVALID"
    # The UI puts the message on the offending input, so the field must be named.
    assert caught.value.details["field"] == "base_url"


def test_a_connector_needs_at_least_one_stream() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(streams=[]))
    assert caught.value.code == "BUILDER_NO_STREAM"


def test_duplicate_stream_names_are_rejected() -> None:
    definition = _definition()
    definition["streams"] = definition["streams"] * 2
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_STREAM_DUPLICATE"


def test_incremental_without_a_cursor_is_rejected() -> None:
    definition = _definition()
    definition["streams"][0]["incremental"] = True
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_CURSOR_REQUIRED"


def test_api_key_auth_needs_a_header_name() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(auth={"method": "api_key"}))
    assert caught.value.code == "BUILDER_AUTH_HEADER_REQUIRED"


# ── compilation produces what the engine expects ───────────────────────────

def test_manifest_has_the_shape_the_runner_requires() -> None:
    manifest = builder.compile_manifest(_definition())
    assert manifest["type"] == "DeclarativeSource"
    assert manifest["check"]["stream_names"] == ["orders"]
    stream = manifest["streams"][0]
    assert stream["type"] == "DeclarativeStream"
    retriever = stream["retriever"]
    assert retriever["requester"]["path"] == "/v1/orders"
    assert retriever["requester"]["request_parameters"] == {"status": "paid"}
    # The dotted selector becomes the extractor path.
    assert retriever["record_selector"]["extractor"]["field_path"] == ["data", "items"]
    assert retriever["paginator"]["type"] == "DefaultPaginator"


def test_empty_record_selector_means_the_response_is_the_array() -> None:
    definition = _definition()
    definition["streams"][0]["record_selector"] = ""
    manifest = builder.compile_manifest(definition)
    extractor = manifest["streams"][0]["retriever"]["record_selector"]["extractor"]
    assert extractor["field_path"] == []


def test_credentials_are_referenced_never_inlined() -> None:
    """The manifest is stored in our database and sent to the browser, so a
    secret must never end up inside it (§21)."""
    definition = _definition(auth={"method": "api_key", "header": "X-Token"})
    manifest = builder.compile_manifest(definition)
    authenticator = manifest["streams"][0]["retriever"]["requester"]["authenticator"]
    assert authenticator["type"] == "ApiKeyAuthenticator"
    assert authenticator["api_token"] == "{{ config['api_key'] }}"
    assert authenticator["inject_into"]["field_name"] == "X-Token"

    spec = manifest["spec"]["connection_specification"]
    assert spec["properties"]["api_key"]["airbyte_secret"] is True
    assert "api_key" in spec["required"]


def test_incremental_adds_a_cursor_and_a_start_date_field() -> None:
    definition = _definition()
    definition["streams"][0].update(
        {"incremental": True, "cursor_field": "updated_at", "cursor_param": "since"}
    )
    manifest = builder.compile_manifest(definition)
    cursor = manifest["streams"][0]["incremental_sync"]
    assert cursor["cursor_field"] == "updated_at"
    assert cursor["start_time_option"]["field_name"] == "since"
    # A cursor is meaningless without somewhere to start from.
    assert "start_date" in manifest["spec"]["connection_specification"]["properties"]


def test_the_runner_is_pinned() -> None:
    """A floating runner tag would silently change every built connector at once."""
    descriptor = builder.descriptor()
    assert descriptor.docker_repository == "airbyte/source-declarative-manifest"
    assert descriptor.version and descriptor.version != "latest"
    assert descriptor.image == f"airbyte/source-declarative-manifest:{descriptor.version}"


def test_starter_project_is_valid_out_of_the_box() -> None:
    """A new project must be testable immediately; an empty editor teaches nothing."""
    builder.compile_manifest(builder.starter_definition("Demo"))


# ── schema inference ───────────────────────────────────────────────────────

def test_schema_inference_widens_rather_than_guessing() -> None:
    schema = builder.infer_schema([
        {"id": 1, "name": "a", "score": 1},
        {"id": 2, "name": None, "score": 1.5},
    ])
    props = schema["properties"]
    assert props["id"]["type"] == "integer"
    # Seen as null once, so it stays nullable.
    assert props["name"]["type"] == ["string", "null"]
    # int + float is numeric, not a coin toss.
    assert props["score"]["type"] == "number"


def test_schema_inference_of_nothing_is_an_empty_object_not_a_crash() -> None:
    schema = builder.infer_schema([])
    assert schema["type"] == "object"
    assert schema["properties"] == {}


# ── the engine boundary holds ──────────────────────────────────────────────

def test_only_the_adapter_knows_the_injection_key() -> None:
    """`__injected_declarative_manifest` is Airbyte's contract, so it must not
    appear anywhere above the adapter (guardrail 5)."""
    import pathlib

    root = pathlib.Path(builder.__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        if "adapters" in path.parts:
            continue
        if "__injected_declarative_manifest" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"engine vocabulary leaked into {offenders}"
