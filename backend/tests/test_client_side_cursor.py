"""Endpoints whose cursor filter is not to the second.

A customer deployment reported the same ~136 tickets arriving every hour for
four hours while max(last_update) across the whole table had not moved for ten,
and ruled out the easy explanations: the records were spread across all seven
partitions with unrelated timestamps, so not one bulk update landing on a
boundary.

Asking the server settled it. `ticket/get.all` rounds `last_update_from` down
to the start of its day in +07. Measured against a live tenant, with
max(last_update) at 2026-09-09 14:41:52 +07:

    last_update_from = 14:41:53 that day   -> 8 records, all older than that
    last_update_from = 23:59:59 that day   -> the same 8
    last_update_from = 00:00:00 next day   -> none

So a cursor saved mid-afternoon is read as midnight, and every sync re-reads
everything updated today until the day rolls over.

It is not a Base-wide problem, which is why this is per-stream rather than a
blanket setting. Measured across the applications reachable with a token:

    Service / ticket     rounded to the day
    Workflow / job       rounded to the day
    Workflow / workflow  filters to the second
    Request  / request   filters to the second
    HRM      / employee  filters to the second
    WeWork   / task      filters to the second

`is_client_side_incremental` makes the CDK drop records at or below the window
start after they arrive. It costs nothing at the API -- the day still crosses
the wire -- and it stops those rows being written to the destination every
hour. Measured end to end on a pipeline with no state, then run again
immediately:

    without the flag   831 records, then 56
    with the flag      831 records, then 20

The twenty that remain sit exactly on their partition's cursor, which the
window includes at both ends. On the customer's busier tenant the same change
was 136 down to 9.
"""

from __future__ import annotations

import pytest

from app.connectors.base_vn import CONNECTORS
from app.connectors.base_vn._shared import Incremental, compile_manifest

#: What was measured, and why each one is set the way it is.
ROUNDED_TO_THE_DAY = {
    ("source-base-service", "ticket"),
    ("source-base-workflow", "job"),
}
FILTERS_PROPERLY = {
    ("source-base-workflow", "workflow"),
    ("source-base-request", "request"),
    ("source-base-wework", "task"),
}


def _stream(connector_key: str, stream_name: str):
    connector = next(c for c in CONNECTORS if c.connector_key == connector_key)
    return connector, next(s for s in connector.streams if s.name == stream_name)


@pytest.mark.parametrize("key,name", sorted(ROUNDED_TO_THE_DAY))
def test_the_measured_endpoints_filter_on_this_side(key: str, name: str) -> None:
    _, stream = _stream(key, name)
    assert stream.incremental is not None
    assert stream.incremental.client_side is True


@pytest.mark.parametrize("key,name", sorted(FILTERS_PROPERLY))
def test_the_endpoints_that_work_pay_nothing(key: str, name: str) -> None:
    """The filter is waste where the server already honours the cursor, and
    turning it on everywhere would hide the next endpoint that does not."""
    _, stream = _stream(key, name)
    assert stream.incremental is not None
    assert stream.incremental.client_side is False


def test_it_is_off_unless_measured() -> None:
    """A default of True would be a guess applied to forty-two streams."""
    assert Incremental().client_side is False


@pytest.mark.parametrize("key,name", sorted(ROUNDED_TO_THE_DAY))
def test_the_flag_reaches_the_manifest(key: str, name: str) -> None:
    connector, _ = _stream(key, name)
    cursor = compile_manifest(connector)["definitions"]["streams"][name]["incremental_sync"]
    assert cursor["is_client_side_incremental"] is True


@pytest.mark.parametrize("key,name", sorted(FILTERS_PROPERLY))
def test_the_others_do_not_carry_it(key: str, name: str) -> None:
    connector, _ = _stream(key, name)
    cursor = compile_manifest(connector)["definitions"]["streams"][name]["incremental_sync"]
    assert "is_client_side_incremental" not in cursor


@pytest.mark.parametrize("key,name", sorted(ROUNDED_TO_THE_DAY | FILTERS_PROPERLY))
def test_the_server_is_still_asked_to_filter(key: str, name: str) -> None:
    """Client-side filtering is on top of the request, not instead of it.
    Dropping the request parameter would pull every record in the table over
    the wire on every sync -- 831 instead of 56, on the tenant this was
    measured against."""
    connector, stream = _stream(key, name)
    cursor = compile_manifest(connector)["definitions"]["streams"][name]["incremental_sync"]
    assert cursor["start_time_option"]["field_name"] == stream.incremental.param


def test_a_stream_with_no_cursor_is_untouched() -> None:
    """Full-refresh streams have no window to compare against, so the flag
    would have nothing to mean."""
    connector, _ = _stream("source-base-service", "ticket")
    streams = compile_manifest(connector)["definitions"]["streams"]
    plain = [name for name, node in streams.items() if "incremental_sync" not in node]
    assert plain, "expected at least one full-refresh stream on this connector"
    for name in plain:
        assert "is_client_side_incremental" not in str(streams[name])
