"""Reading a few parents instead of all of them.

Base's largest tables are read one parent at a time: every ticket belongs to a
service desk, every task to a project. Reading all of them is one request per
parent, every sync, whether or not anybody looks at the result. A workspace
that cares about two desks out of forty should be able to say so.

The obvious design does not work, and the API was asked before this was built
rather than after:

    POST service/extapi/v1/ticket/get.all   (no service_id)  -> INVALID_SERVICE_ID
    POST wework/extapi/v3/task/project      (no project id)  -> "Invalid data"
    POST workflow/extapi/v1/jobs/get        (no workflow_id) -> 20 jobs, 6 workflows
    POST workflow/extapi/v1/jobs/get        workflow_id=11299 -> 5 jobs, only 11299

So "all" cannot be one unfiltered call for tickets and tasks; it stays one
call per parent, exactly as today. What is new is naming the parents, and for
those two that turns forty requests into two. `jobs/get` is the one that reads
everything in one call already, and it accepts the filter.

The ids go into the request. Nothing is fetched and then discarded.
"""

from __future__ import annotations

import copy

import pytest

from app.adapters.airbyte_protocol.protocol import (
    scoped_config_keys,
    with_scoped_partitions,
)

#: The shape `compile_manifest` produces for these three streams.
MANIFEST = {
    "type": "DeclarativeSource",
    "metadata": {
        "appbi_scopes": {
            "ticket": {"config_key": "service_ids", "field": "service_id"},
            "job": {"config_key": "workflow_ids", "field": "workflow_id"},
        }
    },
    "definitions": {
        "streams": {
            # Has a parent: the substream router walks every service desk.
            "ticket": {
                "retriever": {
                    "requester": {
                        "request_body_data": {
                            "access_token_v2": "{{ config['access_token_v2'] }}",
                            "service_id": "{{ stream_partition.parent_id }}",
                        }
                    },
                    "partition_router": {
                        "type": "SubstreamPartitionRouter",
                        "parent_stream_configs": [{"partition_field": "parent_id"}],
                    },
                }
            },
            # No parent: one call reads every workflow's jobs.
            "job": {
                "retriever": {
                    "requester": {
                        "request_body_data": {
                            "access_token_v2": "{{ config['access_token_v2'] }}",
                        }
                    },
                }
            },
            # Not scopeable at all.
            "service": {"retriever": {"requester": {"request_body_data": {}}}},
        }
    },
}


def _stream(manifest, name):
    return manifest["definitions"]["streams"][name]


def _router(manifest, name):
    return _stream(manifest, name)["retriever"].get("partition_router")


def _body(manifest, name):
    return _stream(manifest, name)["retriever"]["requester"]["request_body_data"]


# ── naming nothing keeps today's behaviour ───────────────────────────────

@pytest.mark.parametrize("nothing", [None, [], "", {}, ["", "  "]])
def test_an_empty_choice_reads_everything(nothing) -> None:
    """The default, and what every source that exists today keeps: the
    substream router walking every parent, untouched."""
    out = with_scoped_partitions(MANIFEST, {"service_ids": nothing})
    assert _router(out, "ticket")["type"] == "SubstreamPartitionRouter"


def test_a_stream_with_no_parent_stays_unfiltered() -> None:
    """`jobs/get` reads every workflow in one call. Naming nothing must not
    add a router, or an empty `workflow_id` would be sent on every request."""
    out = with_scoped_partitions(MANIFEST, {})
    assert _router(out, "job") is None
    assert "workflow_id" not in _body(out, "job")


def test_a_manifest_with_no_scopes_is_returned_as_is() -> None:
    plain = {"definitions": {"streams": {"a": {}}}}
    assert with_scoped_partitions(plain, {"service_ids": ["1"]}) is plain


# ── naming ids replaces the walk ─────────────────────────────────────────

def test_named_desks_replace_the_parent_walk() -> None:
    """The point of the feature: two desks out of forty means two requests,
    not forty followed by a filter."""
    out = with_scoped_partitions(MANIFEST, {"service_ids": ["11", "12"]})
    router = _router(out, "ticket")
    assert router["type"] == "ListPartitionRouter"
    assert router["values"] == ["11", "12"]


def test_the_request_template_still_resolves() -> None:
    """The list router reuses `parent_id`, which is the name the substream
    router used, so the body template that reads it needs no change."""
    out = with_scoped_partitions(MANIFEST, {"service_ids": ["11"]})
    assert _router(out, "ticket")["cursor_field"] == "parent_id"
    assert _body(out, "ticket")["service_id"] == "{{ stream_partition.parent_id }}"


def test_a_parentless_stream_gains_the_request_field() -> None:
    """`job` has no template for the filter, because compiling one in would
    send an empty value on every unscoped sync. It is added only when there is
    something to send."""
    out = with_scoped_partitions(MANIFEST, {"workflow_ids": ["11299"]})
    assert _router(out, "job")["values"] == ["11299"]
    assert _body(out, "job")["workflow_id"] == "{{ stream_partition.parent_id }}"


def test_the_token_is_not_disturbed() -> None:
    out = with_scoped_partitions(MANIFEST, {"service_ids": ["11"]})
    assert _body(out, "ticket")["access_token_v2"] == "{{ config['access_token_v2'] }}"


def test_streams_nobody_scoped_are_untouched() -> None:
    out = with_scoped_partitions(MANIFEST, {"service_ids": ["11"]})
    assert _router(out, "service") is None
    assert _body(out, "service") == {}


def test_two_streams_can_be_scoped_at_once() -> None:
    out = with_scoped_partitions(
        MANIFEST, {"service_ids": ["11"], "workflow_ids": ["22", "33"]})
    assert _router(out, "ticket")["values"] == ["11"]
    assert _router(out, "job")["values"] == ["22", "33"]


# ── reading what was typed ───────────────────────────────────────────────

def test_ids_are_trimmed_and_blanks_dropped() -> None:
    """A chip editor produces clean strings, but a config written by hand or
    restored from a backup may not."""
    out = with_scoped_partitions(MANIFEST, {"service_ids": [" 11 ", "", "12", "  "]})
    assert _router(out, "ticket")["values"] == ["11", "12"]


def test_numbers_are_accepted() -> None:
    out = with_scoped_partitions(MANIFEST, {"service_ids": [11, 12]})
    assert _router(out, "ticket")["values"] == ["11", "12"]


def test_a_comma_separated_string_still_works() -> None:
    """Not what the form sends, but what somebody editing JSON by hand will
    write. Refusing it would look like the feature is broken."""
    out = with_scoped_partitions(MANIFEST, {"service_ids": "11, 12"})
    assert _router(out, "ticket")["values"] == ["11", "12"]


# ── the shared manifest must not be edited ───────────────────────────────

def test_the_connector_definition_is_left_alone() -> None:
    """One source's choice reaching another source's sync would be the worst
    possible failure here -- silent, and wrong in both directions."""
    before = copy.deepcopy(MANIFEST)
    with_scoped_partitions(MANIFEST, {"service_ids": ["11"], "workflow_ids": ["22"]})
    assert MANIFEST == before


# ── the keys never reach the connector ───────────────────────────────────

def test_the_scope_keys_are_named_for_removal() -> None:
    """They are consumed while the manifest is built. Forwarding them would
    have the CDK reject a property the spec it validates against never
    declared."""
    assert scoped_config_keys(MANIFEST) == {"service_ids", "workflow_ids"}


def test_a_manifest_without_scopes_names_none() -> None:
    assert scoped_config_keys({"definitions": {}}) == set()
    assert scoped_config_keys(None) == set()
