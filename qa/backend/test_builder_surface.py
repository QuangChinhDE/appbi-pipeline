"""Connector Builder: the full low-code surface the editor exposes.

`test_builder.py` covers the basics. This file covers everything added for
parity with Airbyte's own builder — every component here is one the editor can
produce, so a compile failure means a screen that promises something the engine
cannot run.
"""

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
        "user_inputs": [],
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


# ── pagination ─────────────────────────────────────────────────────────────

def test_cursor_pagination_reads_the_next_page_from_the_body() -> None:
    definition = _definition()
    definition["streams"][0]["pagination"] = {
        "mode": "cursor", "page_size": 100, "cursor_path": "meta.next", "page_param": "cursor",
    }
    strategy = (builder.compile_manifest(definition)["streams"][0]
                ["retriever"]["paginator"]["pagination_strategy"])
    assert strategy["type"] == "CursorPagination"
    assert "meta.next" in strategy["cursor_value"]
    # Without a stop condition the connector would page forever.
    assert strategy["stop_condition"]


def test_cursor_pagination_needs_to_know_where_the_cursor_is() -> None:
    definition = _definition()
    definition["streams"][0]["pagination"] = {"mode": "cursor"}
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_CURSOR_PATH_REQUIRED"


def test_link_header_pagination_replaces_the_path() -> None:
    definition = _definition()
    definition["streams"][0]["pagination"] = {"mode": "link_header"}
    paginator = builder.compile_manifest(definition)["streams"][0]["retriever"]["paginator"]
    # The next URL is absolute, so it replaces the path rather than becoming a
    # query parameter.
    assert paginator["page_token_option"]["type"] == "RequestPath"


def test_an_unknown_pagination_mode_is_rejected() -> None:
    definition = _definition()
    definition["streams"][0]["pagination"] = {"mode": "telepathy"}
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_PAGINATION_UNSUPPORTED"


# ── authentication ─────────────────────────────────────────────────────────

def test_oauth_references_credentials_and_declares_them() -> None:
    definition = _definition(auth={
        "method": "oauth2",
        "oauth": {"token_url": "https://api.example.com/oauth/token", "scopes": "read, write"},
    })
    manifest = builder.compile_manifest(definition)
    auth = manifest["streams"][0]["retriever"]["requester"]["authenticator"]
    assert auth["type"] == "OAuthAuthenticator"
    assert auth["scopes"] == ["read", "write"]
    assert auth["client_secret"] == "{{ config['client_secret'] }}"

    spec = manifest["spec"]["connection_specification"]
    for key in ("client_id", "client_secret", "refresh_token"):
        assert spec["properties"][key]["airbyte_secret"] is True
        assert key in spec["required"]


def test_oauth_without_a_token_endpoint_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(auth={"method": "oauth2", "oauth": {}}))
    assert caught.value.code == "BUILDER_OAUTH_TOKEN_URL_REQUIRED"


def test_session_token_logs_in_then_carries_the_token() -> None:
    definition = _definition(auth={
        "method": "session_token",
        "session": {"login_path": "/auth/login", "token_path": "data.token",
                    "header": "X-Session"},
    })
    auth = (builder.compile_manifest(definition)["streams"][0]
            ["retriever"]["requester"]["authenticator"])
    assert auth["type"] == "SessionTokenAuthenticator"
    assert auth["session_token_path"] == ["data", "token"]
    assert auth["request_authentication"]["inject_into"]["field_name"] == "X-Session"


def test_jwt_auth_declares_its_secret() -> None:
    definition = _definition(auth={"method": "jwt", "jwt": {"algorithm": "RS256"}})
    manifest = builder.compile_manifest(definition)
    auth = manifest["streams"][0]["retriever"]["requester"]["authenticator"]
    assert auth["type"] == "JwtAuthenticator"
    assert auth["algorithm"] == "RS256"
    assert manifest["spec"]["connection_specification"]["properties"]["jwt_secret"][
        "airbyte_secret"] is True


# ── partitioning ───────────────────────────────────────────────────────────

def test_list_partitioning_repeats_the_stream_per_value() -> None:
    definition = _definition()
    definition["streams"][0]["partition"] = {
        "mode": "list", "values": "us, eu, apac", "param": "region",
    }
    router = builder.compile_manifest(definition)["streams"][0]["retriever"]["partition_router"]
    assert router["type"] == "ListPartitionRouter"
    assert router["values"] == ["us", "eu", "apac"]
    assert router["request_option"]["field_name"] == "region"


def test_substream_partitioning_points_at_a_resolvable_parent() -> None:
    definition = _definition()
    definition["streams"] = [
        {**definition["streams"][0], "name": "customers", "path": "/customers"},
        {**definition["streams"][0], "name": "orders",
         "path": "/customers/{{ stream_partition.parent_id }}/orders",
         "partition": {"mode": "parent", "parent_stream": "customers",
                       "parent_key": "id", "partition_field": "parent_id"}},
    ]
    manifest = builder.compile_manifest(definition)
    router = manifest["streams"][1]["retriever"]["partition_router"]
    assert router["type"] == "SubstreamPartitionRouter"
    # The pointer has to resolve, so the parents are defined as well.
    assert "customers" in manifest["definitions"]["streams"]


def test_partitioning_on_a_missing_parent_is_rejected() -> None:
    definition = _definition()
    definition["streams"][0]["partition"] = {"mode": "parent", "parent_stream": "ghosts"}
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_PARENT_STREAM_UNKNOWN"


def test_a_stream_cannot_be_its_own_parent() -> None:
    definition = _definition()
    definition["streams"][0]["partition"] = {"mode": "parent", "parent_stream": "orders"}
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "BUILDER_PARENT_STREAM_SELF"


# ── filtering and transformation ───────────────────────────────────────────

def test_transformations_group_into_one_component_per_kind() -> None:
    definition = _definition()
    definition["streams"][0]["transformations"] = [
        {"type": "add", "path": "ingested_at", "value": "{{ now_utc() }}"},
        {"type": "add", "path": "source", "value": "orders"},
        {"type": "remove", "path": "internal.debug"},
    ]
    transformations = builder.compile_manifest(definition)["streams"][0]["transformations"]
    assert {t["type"] for t in transformations} == {"AddFields", "RemoveFields"}
    added = next(t for t in transformations if t["type"] == "AddFields")
    assert len(added["fields"]) == 2


def test_record_filter_is_emitted_only_when_asked_for() -> None:
    definition = _definition()
    selector = (builder.compile_manifest(definition)["streams"][0]
                ["retriever"]["record_selector"])
    assert "record_filter" not in selector

    definition["streams"][0]["record_filter"] = "{{ record.active }}"
    selector = (builder.compile_manifest(definition)["streams"][0]
                ["retriever"]["record_selector"])
    assert selector["record_filter"]["condition"] == "{{ record.active }}"


# ── retries ────────────────────────────────────────────────────────────────

def test_error_handler_can_honour_the_apis_own_retry_after() -> None:
    definition = _definition()
    definition["streams"][0]["error_handler"] = {
        "max_retries": 7,
        "backoff": {"mode": "header", "header": "Retry-After"},
        "filters": [{"http_codes": [429], "action": "RETRY", "message": "rate limited"}],
    }
    handler = (builder.compile_manifest(definition)["streams"][0]
               ["retriever"]["requester"]["error_handler"])
    assert handler["max_retries"] == 7
    assert handler["backoff_strategies"][0]["type"] == "WaitTimeFromHeader"
    assert handler["response_filters"][0]["http_codes"] == [429]


def test_no_error_handler_means_the_cdk_default() -> None:
    """An empty component is not the same as no component: emitting one would
    replace the CDK's own retry policy with nothing."""
    requester = (builder.compile_manifest(_definition())["streams"][0]
                 ["retriever"]["requester"])
    assert "error_handler" not in requester


# ── request shape ──────────────────────────────────────────────────────────

def test_composite_primary_key_becomes_a_list() -> None:
    definition = _definition()
    definition["streams"][0]["primary_key"] = "tenant_id, id"
    assert builder.compile_manifest(definition)["streams"][0]["primary_key"] == [
        "tenant_id", "id"]


def test_post_body_is_emitted_in_the_requested_encoding() -> None:
    definition = _definition()
    definition["streams"][0]["http_method"] = "POST"
    definition["streams"][0]["request_body"] = {
        "mode": "json", "entries": [{"key": "filter", "value": "paid"}],
    }
    requester = (builder.compile_manifest(definition)["streams"][0]
                 ["retriever"]["requester"])
    assert requester["request_body_json"] == {"filter": "paid"}
    assert "request_body_data" not in requester


# ── user inputs ────────────────────────────────────────────────────────────

def test_user_inputs_become_config_fields() -> None:
    definition = _definition(user_inputs=[
        {"key": "account_id", "title": "Account", "type": "string", "required": True},
        {"key": "api_secret", "type": "string", "secret": True},
    ])
    spec = builder.compile_manifest(definition)["spec"]["connection_specification"]
    assert spec["properties"]["account_id"]["title"] == "Account"
    assert "account_id" in spec["required"]
    assert spec["properties"]["api_secret"]["airbyte_secret"] is True


def test_a_user_input_cannot_shadow_a_built_in_field() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(user_inputs=[{"key": "base_url"}]))
    assert caught.value.code == "BUILDER_INPUT_KEY_RESERVED"


def test_duplicate_user_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.validate(_definition(user_inputs=[{"key": "tenant"}, {"key": "tenant"}]))
    assert caught.value.code == "BUILDER_INPUT_DUPLICATE"


# ── incremental ────────────────────────────────────────────────────────────

def test_incremental_step_slices_the_window() -> None:
    definition = _definition()
    definition["streams"][0].update({
        "incremental": True, "cursor_field": "updated_at",
        "step": "P30D", "lookback": "P1D", "cursor_end_param": "until",
    })
    cursor = builder.compile_manifest(definition)["streams"][0]["incremental_sync"]
    assert cursor["step"] == "P30D"
    assert cursor["lookback_window"] == "P1D"
    assert cursor["end_time_option"]["field_name"] == "until"
    # A step without a granularity makes the CDK slice ambiguously.
    assert cursor["cursor_granularity"]


# ── YAML round trip ────────────────────────────────────────────────────────

def test_manifest_exports_as_yaml() -> None:
    document = builder.manifest_yaml(_definition())
    assert "type: DeclarativeSource" in document
    assert "orders" in document


def test_importing_our_own_export_preserves_the_essentials() -> None:
    original = _definition()
    reimported = builder.definition_from_manifest(builder.manifest_yaml(original))
    stream = reimported["streams"][0]
    assert stream["name"] == "orders"
    assert stream["path"] == "/v1/orders"
    assert stream["record_selector"] == "data.items"
    assert stream["pagination"]["mode"] == "page"
    # And it must still compile, or the round trip produced something unusable.
    builder.compile_manifest(reimported)


def test_importing_something_that_is_not_a_source_fails_loudly() -> None:
    """Silently accepting an unknown document would let the next save destroy
    whatever the user could see in the YAML."""
    with pytest.raises(ValidationError) as caught:
        builder.definition_from_manifest("type: NotASource\nversion: 1")
    assert caught.value.code == "BUILDER_MANIFEST_NOT_SOURCE"


def test_importing_broken_yaml_reports_why() -> None:
    with pytest.raises(ValidationError) as caught:
        builder.definition_from_manifest("streams: [unclosed")
    assert caught.value.code == "BUILDER_YAML_INVALID"
    assert caught.value.details["reason"]


# ── a declared key must exist in the declared schema ──────────────────────────
# Found by running a built connector on a real Airbyte, which refuses the
# catalog with "key: 'id' of path: '[id]' not found in schema". The product's
# own executor accepted it, so the connector looked fine right up to the point
# where it was used on the engine production runs on.

def test_a_primary_key_is_present_in_the_stream_schema() -> None:
    stream = {"name": "posts", "path": "/posts", "primary_key": "id"}
    schema = builder._stream_schema(stream)
    assert "id" in schema["properties"], (
        "Airbyte rejects a catalog whose primary key is not in the schema")


def test_a_composite_key_and_a_cursor_are_both_present() -> None:
    stream = {
        "name": "events", "path": "/events",
        "primary_key": "tenant_id, event_id",
        "incremental": True, "cursor_field": "occurred_at",
    }
    properties = builder._stream_schema(stream)["properties"]
    assert set(properties) == {"tenant_id", "event_id", "occurred_at"}


def test_a_user_declared_schema_is_extended_not_replaced() -> None:
    """The user's own field definitions must survive; only what is missing is added."""
    stream = {
        "name": "posts", "path": "/posts", "primary_key": "id",
        "schema": {"type": "object", "properties": {"title": {"type": "string"}}},
    }
    properties = builder._stream_schema(stream)["properties"]
    assert properties["title"] == {"type": "string"}
    assert "id" in properties


def test_the_compiled_stream_agrees_with_its_own_schema() -> None:
    """End to end through the compiler, not just the schema helper."""
    definition = builder.starter_definition("Keyed")
    definition["streams"][0].update({"name": "posts", "path": "/posts", "primary_key": "id"})
    manifest = builder.compile_manifest(definition)
    stream = manifest["streams"][0]
    declared = stream["schema_loader"]["schema"]["properties"]
    key = stream["primary_key"]
    for field in ([key] if isinstance(key, str) else key):
        assert field in declared, f"{field} is the primary key but is not in the schema"
