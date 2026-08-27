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


# ── choosing a parent has to actually connect the two streams ───────────────
#
# The complaint this answers: "connect stream cha sang stream con đang gặp lỗi".
# Picking a parent produced partitions and nothing else -- the child's request
# was unchanged, so it read the same unfiltered collection once per parent and
# looked like it worked. The only escape was to know that
# `{{ stream_partition.<field> }}` exists, and the hint mentioned it for the URL
# path alone, while every Base API takes the parent id in the form body.

def _parent_child(**partition):
    return _definition(streams=[
        {
            "name": "lead_service", "path": "/lead/services", "http_method": "POST",
            "record_selector": "services", "primary_key": "id",
            "pagination": {"mode": "none"}, "incremental": False,
            "query_params": [], "headers": [],
        },
        {
            "name": "lead", "path": "/lead/list", "http_method": "POST",
            "record_selector": "leads", "primary_key": "id",
            "pagination": {"mode": "none"}, "incremental": False,
            "query_params": [], "headers": [],
            "partition": {"mode": "parent", "parent_stream": "lead_service",
                          "parent_key": "id", "partition_field": "service_id",
                          **partition},
        },
    ])


def _stream_of(definition, stream_name):
    """One compiled stream, wherever this manifest happens to keep it.

    `definitions` is added only when a substream needs a resolvable pointer, so
    reaching for it unconditionally works on parent/child connectors and raises
    KeyError on every other one.
    """
    compiled = builder.compile_manifest(builder.validate(definition))
    return next(s for s in compiled["streams"] if s["name"] == stream_name)


def _router(definition, stream_name):
    return _stream_of(definition, stream_name)["retriever"].get("partition_router") or {}


def test_naming_the_parent_parameter_sends_the_parent_id() -> None:
    router = _router(_parent_child(param="service_id", inject_into="body_data"), "lead")
    assert router["type"] == "SubstreamPartitionRouter"
    config = router["parent_stream_configs"][0]
    assert config["stream"] == "#/definitions/streams/lead_service"
    assert config["parent_key"] == "id"
    assert config["partition_field"] == "service_id"
    option = config["request_option"]
    assert option["field_name"] == "service_id"
    # Into the form body, because that is where a POST-form API reads it. The
    # query string is the default and is wrong for every Base endpoint.
    assert option["inject_into"] == "body_data"


def test_the_parent_id_in_the_url_path_is_a_valid_alternative() -> None:
    """`/repos/{{ stream_partition.x }}` is a real shape and must keep working.

    Naming a parameter is the convenient way; interpolating the partition
    yourself is the other. Both are accepted, and the manifest then sends
    nothing extra because the path already carries it.
    """
    definition = _parent_child()
    child = next(s for s in definition["streams"] if s["name"] == "lead")
    child["path"] = "/service/{{ stream_partition.service_id }}/leads"
    config = _router(definition, "lead")["parent_stream_configs"][0]
    assert "request_option" not in config


def test_a_parent_whose_id_is_never_used_is_refused() -> None:
    """N identical requests that report success is worse than an error.

    `SubstreamPartitionRouter` repeats the stream once per parent record; it does
    not change the request. So a child that neither names a parameter nor
    interpolates the partition reads the same first page once per parent and
    calls it a sync. This is the shape behind "chọn stream cha xong vẫn không
    chạy đúng": the editor accepted it and nothing said why.
    """
    with pytest.raises(ValidationError) as caught:
        builder.validate(_parent_child())
    assert caught.value.code == "BUILDER_PARENT_KEY_UNUSED"
    # The message has to name both ways out, since either one fixes it.
    assert "stream_partition.service_id" in str(caught.value)
    assert caught.value.details["field"] == "streams[1].partition.param"


def test_the_parent_cursor_is_off_unless_asked_for() -> None:
    """`incremental_dependency` drops parents whose children changed later.

    Measured on Base: 234 of 234 CRM pipelines hold a deal newer than the
    pipeline itself, so with this on those pipelines leave the partition list
    after the first sync and their deals stop arriving -- while every run still
    reports success. It must never be the default.
    """
    off = _router(_parent_child(param="service_id"), "lead")["parent_stream_configs"][0]
    assert "incremental_dependency" not in off

    on = _router(_parent_child(param="service_id", incremental_parent=True),
                 "lead")["parent_stream_configs"][0]
    assert on["incremental_dependency"] is True


# ── a declared cursor has to filter something ──────────────────────────────

def _cursor(**stream_overrides):
    definition = _definition()
    definition["streams"][0].update({"incremental": True, "cursor_field": "last_update",
                                     **stream_overrides})
    return _stream_of(definition, "orders")["incremental_sync"]


def test_an_api_that_does_not_filter_gets_a_client_side_cursor() -> None:
    """Otherwise `incremental` is a label with nothing behind it.

    The CDK only compares records against the high-water mark when
    `is_client_side_incremental` is set. Base CRM's `deal_activity` declared a
    cursor without it, saved 5,583 partitions of state, and re-emitted the
    identical 3,970 records on the next sync. The builder could reproduce that
    exactly, with no field to express what was wrong.
    """
    cursor = _cursor(cursor_filter_mode="client")
    assert cursor["is_client_side_incremental"] is True
    # And nothing invented on the request: an endpoint with no time filter must
    # not be sent a parameter name somebody guessed.
    assert "start_time_option" not in cursor
    assert "end_time_option" not in cursor
    # A closed window is needed for the comparison to have an upper bound.
    assert cursor["end_datetime"]["datetime"].startswith("{{ now_utc()")


def test_a_server_side_cursor_sends_its_bounds_where_it_is_told() -> None:
    cursor = _cursor(cursor_param="start_time", cursor_end_param="end_time",
                     cursor_inject_into="body_data")
    assert "is_client_side_incremental" not in cursor
    assert cursor["start_time_option"] == {
        "type": "RequestOption", "inject_into": "body_data",
        "field_name": "start_time"}
    assert cursor["end_time_option"]["inject_into"] == "body_data"


def test_the_default_cursor_still_goes_to_the_query_string() -> None:
    """The old behaviour, unchanged for every project already saved."""
    cursor = _cursor(cursor_param="updated_since")
    assert cursor["start_time_option"]["inject_into"] == "request_parameter"


# ── a page size nobody agreed to is worse than none ────────────────────────

def _paginator(**pagination):
    definition = _definition()
    definition["streams"][0]["pagination"] = pagination
    return _stream_of(definition, "orders")["retriever"]["paginator"]


def test_a_blank_page_size_sends_none_and_declares_none() -> None:
    """Measured on Base CRM Leads: `lead/list` pages and ignores `limit`.

    `page_size` is the number the CDK compares a short page against to decide it
    has reached the end. Asserting a size the server never agreed to stops on
    the server's own first default page -- or, if the guess is too small, pages
    past the end forever. With none declared it stops on an empty page, which is
    true whatever the server does.
    """
    paginator = _paginator(mode="page", page_size=None, page_param="page",
                           inject_into="body_data")
    assert "page_size_option" not in paginator
    assert "page_size" not in paginator["pagination_strategy"]
    assert paginator["page_token_option"]["inject_into"] == "body_data"


def test_a_page_size_goes_where_the_page_token_goes() -> None:
    """They are read by the same request, so they cannot live in different halves."""
    paginator = _paginator(mode="page", page_size=100, page_param="page",
                           size_param="limit", inject_into="body_data")
    assert paginator["pagination_strategy"]["page_size"] == 100
    assert paginator["page_size_option"]["field_name"] == "limit"
    assert paginator["page_size_option"]["inject_into"] == "body_data", (
        "the size went to the query string while the page went to the body, so a "
        "POST-form API would ignore it and page one record at a time")


# ── importing a manifest must not delete what it cannot render ─────────────

def test_a_parent_link_survives_a_yaml_round_trip() -> None:
    """It used to be dropped, which deleted the link on the next save.

    `definition_from_manifest` replaced every partition router with
    `{"mode": "none"}` -- and its own docstring promises the opposite, that
    anything unrenderable fails loudly rather than being silently discarded.
    """
    original = builder.validate(_parent_child(param="service_id",
                                              inject_into="body_data",
                                              incremental_parent=True))
    reimported = builder.definition_from_manifest(builder.manifest_yaml(original))
    child = next(s for s in reimported["streams"] if s["name"] == "lead")
    assert child["partition"] == {
        "mode": "parent", "parent_stream": "lead_service", "parent_key": "id",
        "partition_field": "service_id", "param": "service_id",
        "inject_into": "body_data", "incremental_parent": True,
    }
    # And it compiles back to the same router, which is the property that matters.
    assert (_router(reimported, "lead")["parent_stream_configs"][0]
            == _router(original, "lead")["parent_stream_configs"][0])


def test_a_client_side_cursor_survives_a_yaml_round_trip() -> None:
    definition = _definition()
    definition["streams"][0].update({"incremental": True, "cursor_field": "last_update",
                                     "cursor_filter_mode": "client"})
    reimported = builder.definition_from_manifest(
        builder.manifest_yaml(builder.validate(definition)))
    assert reimported["streams"][0]["cursor_filter_mode"] == "client"
    cursor = _stream_of(reimported, "orders")["incremental_sync"]
    assert cursor["is_client_side_incremental"] is True


def test_an_unsized_paginator_survives_a_yaml_round_trip() -> None:
    """The reader defaulted a missing size to 50, which re-asserted it on save."""
    original = _definition()
    original["streams"][0]["pagination"] = {"mode": "page", "page_size": None,
                                            "page_param": "page"}
    reimported = builder.definition_from_manifest(
        builder.manifest_yaml(builder.validate(original)))
    assert reimported["streams"][0]["pagination"]["page_size"] is None
    paginator = _stream_of(reimported, "orders")["retriever"]["paginator"]
    assert "page_size" not in paginator["pagination_strategy"]


# ── the whole point: what the builder produces for a real two-level API ────

def test_the_builder_can_express_base_crm_leads() -> None:
    """Three streams, two levels of parent, one client-side cursor.

    `source-base-crm-leads` is hand-written Python because it ships with the
    product. A workspace facing the same API through the builder has to be able
    to reach the same manifest by filling in forms, or the builder is a demo.
    Compared against the shipped connector, component by component.
    """
    from app.connectors.base_vn import BY_KEY
    from app.connectors.base_vn._shared import compile_manifest as compile_bundled

    definition = {
        "name": "Leads via builder",
        "base_url": "https://apis.basecrm.vn/leads",
        "auth": {"method": "none"},
        "streams": [
            {"name": "lead_service", "path": "/lead/services", "http_method": "POST",
             "record_selector": "services", "primary_key": "id",
             "pagination": {"mode": "none"}, "incremental": False,
             "query_params": [], "headers": []},
            {"name": "lead", "path": "/lead/list", "http_method": "POST",
             "record_selector": "leads", "primary_key": "id",
             "pagination": {"mode": "page", "page_size": None, "page_param": "page",
                            "start_from": 1, "inject_on_first_request": True,
                            "inject_into": "body_data"},
             "incremental": True, "cursor_field": "last_update",
             "cursor_param": "start_time", "cursor_end_param": "end_time",
             "cursor_inject_into": "body_data", "cursor_format": "%s",
             "query_params": [], "headers": [],
             "partition": {"mode": "parent", "parent_stream": "lead_service",
                           "parent_key": "id", "partition_field": "service_id",
                           "param": "service_id", "inject_into": "body_data"}},
            {"name": "lead_feed", "path": "/lead/feed/list", "http_method": "POST",
             "record_selector": "feeds", "primary_key": "id",
             "pagination": {"mode": "none"}, "incremental": True,
             "cursor_field": "last_update", "cursor_filter_mode": "client",
             "cursor_format": "%s", "query_params": [], "headers": [],
             "partition": {"mode": "parent", "parent_stream": "lead", "parent_key": "id",
                           "partition_field": "lead_id", "param": "lead_id",
                           "inject_into": "body_data"}},
        ],
    }
    built = {s["name"]: s for s in
             builder.compile_manifest(builder.validate(definition))["streams"]}
    shipped = compile_bundled(BY_KEY["source-base-crm-leads"])["definitions"]["streams"]

    assert set(built) == set(shipped)
    for name in shipped:
        assert (built[name]["retriever"]["record_selector"]["extractor"]["field_path"]
                == shipped[name]["retriever"]["record_selector"]["extractor"]["field_path"])
        assert (built[name]["retriever"]["paginator"]["type"]
                == shipped[name]["retriever"]["paginator"]["type"])

    # The two-level chain, and the id actually travelling with the request.
    for child, parent, field in (("lead", "lead_service", "service_id"),
                                 ("lead_feed", "lead", "lead_id")):
        config = built[child]["retriever"]["partition_router"]["parent_stream_configs"][0]
        assert config["stream"].endswith("/" + parent)
        assert config["request_option"] == {
            "type": "RequestOption", "inject_into": "body_data", "field_name": field}
        assert "incremental_dependency" not in config

    # And the cursors: the server filters leads, nothing filters feeds.
    assert built["lead"]["incremental_sync"]["start_time_option"]["field_name"] == "start_time"
    assert "is_client_side_incremental" not in built["lead"]["incremental_sync"]
    assert built["lead_feed"]["incremental_sync"]["is_client_side_incremental"] is True
    assert "start_time_option" not in built["lead_feed"]["incremental_sync"]

