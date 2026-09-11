"""Changing how large a page is, without editing a connector.

From a customer deployment: a Base tenant whose tickets are larger than
average had the source container killed for exceeding its memory, and the
engineer on site fixed it by hard-coding `page_size=100` into
`connectors/base_vn/work.py` -- on top of the comment in that file saying not
to change it for one tenant.

Both halves of that were right. The tenant did need smaller pages, and one
tenant's record size is not a reason to change the request pattern for every
deployment. What was missing was a way to say it per source.

One page is held whole in the source container before records are emitted, so
page size is the lever that decides peak memory. It follows record size, which
is a property of the tenant, not of the connector.
"""

from __future__ import annotations

import copy

import pytest

from app.adapters.airbyte_protocol.protocol import (
    page_size_from_config,
    with_page_size,
)

#: The shape `compile_manifest` produces: a paginator per stream, and a page
#: size declared only where the connector controls it.
MANIFEST = {
    "type": "DeclarativeSource",
    "definitions": {
        "streams": {
            "ticket": {
                "retriever": {
                    "paginator": {
                        "type": "DefaultPaginator",
                        "page_size_option": {"field_name": "limit"},
                        "pagination_strategy": {"type": "PageIncrement", "page_size": 500,
                                                "start_from_page": 1},
                    }
                }
            },
            "service": {
                "retriever": {
                    "paginator": {
                        "type": "DefaultPaginator",
                        "pagination_strategy": {"type": "PageIncrement",
                                                "start_from_page": 1},
                    }
                }
            },
            "stage": {"retriever": {"paginator": {"type": "NoPagination"}}},
        }
    },
}


# ── rewriting the manifest ────────────────────────────────────────────────

def test_a_declared_page_size_is_replaced() -> None:
    out = with_page_size(MANIFEST, 100)
    strategy = out["definitions"]["streams"]["ticket"]["retriever"]["paginator"][
        "pagination_strategy"]
    assert strategy["page_size"] == 100


def test_a_stream_that_declares_none_is_left_alone() -> None:
    """Deliberate, and load-bearing. The CDK compares a short page against
    `page_size` to decide a stream has ended, so asserting a size the server
    was never told about either stops on the server's own first page or pages
    past the end forever."""
    out = with_page_size(MANIFEST, 100)
    strategy = out["definitions"]["streams"]["service"]["retriever"]["paginator"][
        "pagination_strategy"]
    assert "page_size" not in strategy


def test_a_stream_that_cannot_page_is_untouched() -> None:
    out = with_page_size(MANIFEST, 100)
    assert out["definitions"]["streams"]["stage"]["retriever"]["paginator"] == {
        "type": "NoPagination"}


def test_everything_else_survives() -> None:
    out = with_page_size(MANIFEST, 100)
    strategy = out["definitions"]["streams"]["ticket"]["retriever"]["paginator"][
        "pagination_strategy"]
    assert strategy["start_from_page"] == 1
    assert strategy["type"] == "PageIncrement"
    assert out["type"] == "DeclarativeSource"


def test_the_shared_manifest_is_not_mutated() -> None:
    """The connector definition is shared by every source in the deployment.
    Editing it in place would let one tenant's tuning reach another's sync --
    and persist until the process restarted."""
    before = copy.deepcopy(MANIFEST)
    with_page_size(MANIFEST, 50)
    assert MANIFEST == before


@pytest.mark.parametrize("nothing", [0, None, -1])
def test_asking_for_nothing_changes_nothing(nothing) -> None:
    assert with_page_size(MANIFEST, nothing) is MANIFEST


def test_an_empty_manifest_is_handled() -> None:
    assert with_page_size({}, 100) == {}
    assert with_page_size(None, 100) is None


# ── reading what the source asked for ────────────────────────────────────

def test_a_number_is_read() -> None:
    assert page_size_from_config({"page_size": 100}) == 100


def test_a_string_is_read() -> None:
    """The form field is a JSON-schema string, so this is the normal path."""
    assert page_size_from_config({"page_size": "100"}) == 100
    assert page_size_from_config({"page_size": " 250 "}) == 250


@pytest.mark.parametrize("blank", ["", None])
def test_blank_falls_back(blank) -> None:
    assert page_size_from_config({"page_size": blank}, fallback=200) == 200


def test_absent_falls_back() -> None:
    assert page_size_from_config({"domain": "base.vn"}, fallback=200) == 200


@pytest.mark.parametrize("junk", ["abc", "10.5", "-5", "0", [], {}])
def test_junk_falls_back_rather_than_raising(junk) -> None:
    """Read in the middle of preparing a sync. A value somebody mistyped
    months ago must not be the thing that stops it running."""
    assert page_size_from_config({"page_size": junk}, fallback=0) == 0


def test_a_source_overrides_the_deployment_default() -> None:
    """The precedence that matters: the deployment can lower every source on a
    small host, and one tenant with larger records can go lower still."""
    assert page_size_from_config({"page_size": "50"}, fallback=200) == 50


# ── what the customer actually hit ───────────────────────────────────────

def test_the_ticket_stream_can_be_brought_within_a_1gb_container() -> None:
    """Measured on site: ~326 KB per ticket, a page of 500 is ~163 MB before
    the CDK's own overhead, and the container was killed. At 100 the same page
    is ~33 MB."""
    out = with_page_size(MANIFEST, page_size_from_config({"page_size": "100"}))
    strategy = out["definitions"]["streams"]["ticket"]["retriever"]["paginator"][
        "pagination_strategy"]
    assert strategy["page_size"] == 100
    assert 100 * 326_000 < 40 * 1024 * 1024
