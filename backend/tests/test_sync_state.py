"""What a run is allowed to resume from.

State is a resumption token. Only an incremental stream has anything to
resume -- and a modern source emits state during a *full* refresh too, because
it checkpoints so a failed run can pick up where it stopped. Postgres does it
by ctid. That token means "this table has been read to the end", which is true
inside one run and false at the start of the next.

Replaying it cost the destination its data. Measured, not imagined: a
full-refresh pipeline over the demo warehouse ran twice. The second run handed
the saved ctid state back to the source, the source correctly emitted nothing,
and the destination -- in `overwrite`, which truncates before it writes --
replaced both tables with the nothing it received. 500 customers and 2,000
orders became 0 and 0, and the run reported SUCCEEDED with 0 records.

No database: these are the two pure functions that decide it.
"""

from __future__ import annotations

import pytest

from app.models.enums import SyncMode
from app.services.runs import _incremental_only, _state_for_incremental_streams


class _Stream:
    def __init__(self, name: str, namespace: str | None, sync_mode: SyncMode,
                 selected: bool = True) -> None:
        self.stream_name = name
        self.namespace = namespace
        self.sync_mode = sync_mode
        self.selected = selected


class _Pipeline:
    def __init__(self, streams: list[_Stream], sync_state: list | None = None) -> None:
        self.streams = streams
        self.sync_state = sync_state or []


def _entry(name: str, namespace: str | None = "shop", records: int = 500) -> dict:
    """A per-stream state token in the shape the destination commits."""
    return {
        "type": "STREAM",
        "stream": {
            "stream_state": {"cursors": {}, "state_type": "ctid_based", "version": 3},
            "stream_descriptor": {"name": name, "namespace": namespace},
        },
        "sourceStats": {"recordCount": float(records)},
    }


def test_a_full_refresh_stream_never_resumes() -> None:
    """The bug, in one assertion. Second run of a full-refresh pipeline used to
    receive the first run's ctid state and read nothing."""
    pipeline = _Pipeline(
        [_Stream("customers", "shop", SyncMode.FULL_REFRESH)],
        [_entry("customers")],
    )
    assert _state_for_incremental_streams(pipeline) == []


def test_an_incremental_stream_still_resumes() -> None:
    """The fix must not throw away the thing state is for: an incremental
    pipeline that forgets its cursor re-reads its whole history every run."""
    pipeline = _Pipeline(
        [_Stream("orders", "shop", SyncMode.INCREMENTAL)],
        [_entry("orders")],
    )
    kept = _state_for_incremental_streams(pipeline)
    assert len(kept) == 1
    assert kept[0]["stream"]["stream_descriptor"]["name"] == "orders"


def test_a_mixed_pipeline_keeps_only_the_incremental_half() -> None:
    pipeline = _Pipeline(
        [
            _Stream("customers", "shop", SyncMode.FULL_REFRESH),
            _Stream("orders", "shop", SyncMode.INCREMENTAL),
        ],
        [_entry("customers"), _entry("orders")],
    )
    kept = _state_for_incremental_streams(pipeline)
    assert [e["stream"]["stream_descriptor"]["name"] for e in kept] == ["orders"]


def test_namespace_is_part_of_the_identity() -> None:
    """Two tables of the same name in different schemas are different streams,
    and matching on the name alone would hand one the other's cursor."""
    pipeline = _Pipeline(
        [_Stream("orders", "shop", SyncMode.INCREMENTAL)],
        [_entry("orders", namespace="analytics")],
    )
    assert _state_for_incremental_streams(pipeline) == []


def test_an_unselected_stream_keeps_nothing() -> None:
    """Deselecting a stream stops it syncing; its cursor stops being a claim
    about anything."""
    pipeline = _Pipeline(
        [_Stream("orders", "shop", SyncMode.INCREMENTAL, selected=False)],
        [_entry("orders")],
    )
    assert _state_for_incremental_streams(pipeline) == []


def test_a_global_token_is_kept() -> None:
    """A state entry with no stream descriptor is a global or legacy cursor.
    Dropping it would make an incremental pipeline re-read its whole history,
    which is the more expensive way to be wrong."""
    pipeline = _Pipeline([_Stream("orders", "shop", SyncMode.INCREMENTAL)])
    globalish = {"type": "GLOBAL", "global": {"shared_state": {"lsn": 42}}}
    assert _incremental_only(pipeline, [globalish]) == [globalish]


@pytest.mark.parametrize("state", [None, []])
def test_no_state_stays_no_state(state) -> None:
    pipeline = _Pipeline([_Stream("orders", "shop", SyncMode.INCREMENTAL)])
    assert _incremental_only(pipeline, state) == []


def test_write_back_is_filtered_too() -> None:
    """Filtering only on the way out would leave the row in the database, and
    the next person to switch a stream to incremental would inherit a cursor
    from a full refresh that happened weeks ago."""
    pipeline = _Pipeline([_Stream("customers", "shop", SyncMode.FULL_REFRESH)])
    committed = [_entry("customers")]
    assert _incremental_only(pipeline, committed) == []
