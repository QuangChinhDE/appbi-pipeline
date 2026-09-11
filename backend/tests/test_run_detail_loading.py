"""What a run detail is allowed to read off the ORM object.

From a customer deployment, in the API log:

    ERROR api.unhandled path=/api/v1/runs/{id}/retry
    sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called

`run_detail` read `run.attempts` itself. That relationship is `selectin`, so it
arrives loaded on a run fetched by a query -- which is every GET -- and
unloaded on one that was just created. A retry returns exactly that: a brand
new row, added to the session, never selected. Reading the collection from
synchronous code inside an async request emitted a lazy SELECT, and SQLAlchemy
refuses that rather than blocking the event loop.

The run was queued and committed before the serialiser ran, so the retry
started anyway and the person got a 500. Three times, in the log, before one
attempt happened to succeed -- each 500 hiding a run that was already going.

The fix is structural: a synchronous presenter is handed loaded data, never an
ORM object with lazy edges left on it. These tests hold that line without a
database, by handing in an object that raises on the attribute the presenter
must not touch.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from app.api.v1.presenters import run_detail
from app.schemas.domain import RunView


class _ExplodingAttempts:
    """Stands in for the unloaded relationship.

    SQLAlchemy raises `MissingGreenlet` here; the type does not matter, only
    that reading it is an error the presenter must never provoke.
    """

    def __iter__(self):
        raise AssertionError(
            "run_detail read run.attempts -- that is the lazy load that "
            "returned 500 on every retry"
        )


class _Run:
    """Only what the presenter is allowed to read."""

    def __init__(self) -> None:
        self.attempts = _ExplodingAttempts()
        self.technical_metadata = {"trace_id": "trc_1", "engine_status": "SUCCEEDED",
                                   "technical_message": "khong duoc lo ra"}


class _Attempt:
    def __init__(self, number: int, status: str = "FAILED") -> None:
        self.attempt_number = number
        self.status = type("S", (), {"value": status})()
        self.started_at = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.ended_at = datetime(2026, 9, 11, 12, 1, 30, tzinfo=timezone.utc)
        self.records_synced = 1319
        self.bytes_synced = 4096
        self.failure_summary = "Nguồn ngắt kết nối giữa chừng."


def _base() -> RunView:
    return RunView(
        id="6f1c7d0e-0000-4000-8000-000000000001",
        short_id="6f1c7d0e",
        run_type="PIPELINE",
        status="FAILED",
        trigger_type="RETRY",
        triggered_by=None,
        created_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        records_synced=1319,
        is_stale=False,
    )


def test_the_presenter_never_touches_the_relationship() -> None:
    """The regression, stated directly: given a run whose `attempts` raises,
    the detail must still be produced from what was passed in."""
    detail = run_detail(
        _base(), _Run(),
        attempts=[_Attempt(1), _Attempt(2)],
        stream_stats=[], source_ref=None, destination_ref=None,
    )
    assert [a.attempt_number for a in detail.attempts] == [1, 2]


def test_attempts_must_be_supplied_by_the_caller() -> None:
    """Keyword-only and required. A default of `None` would let a future
    caller reintroduce the lazy read by simply forgetting the argument."""
    parameter = inspect.signature(run_detail).parameters["attempts"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_the_loader_is_awaited_where_the_route_calls_it() -> None:
    """The other half of the fix: the collection is fetched by a query the
    route awaits, not by the ORM behind the presenter's back."""
    from app.services import runs as run_service
    assert inspect.iscoroutinefunction(run_service.attempts_of)


def test_attempt_timing_is_reported() -> None:
    detail = run_detail(
        _base(), _Run(), attempts=[_Attempt(1)],
        stream_stats=[], source_ref=None, destination_ref=None,
    )
    assert detail.attempts[0].duration_seconds == 90.0
    assert detail.attempts[0].records_synced == 1319


def test_an_attempt_still_running_has_no_duration() -> None:
    attempt = _Attempt(1, status="RUNNING")
    attempt.ended_at = None
    detail = run_detail(
        _base(), _Run(), attempts=[attempt],
        stream_stats=[], source_ref=None, destination_ref=None,
    )
    assert detail.attempts[0].duration_seconds is None


def test_no_attempts_is_not_an_error() -> None:
    """A run that never started has none, and that is a normal answer rather
    than an empty-collection bug."""
    detail = run_detail(
        _base(), _Run(), attempts=[],
        stream_stats=[], source_ref=None, destination_ref=None,
    )
    assert detail.attempts == []


def test_the_technical_message_is_not_leaked_into_metadata() -> None:
    """`technical_metadata` carries a connector's raw output, which is gated
    behind a separate permission elsewhere. Widening the filter here would
    hand it to every caller of the detail endpoint."""
    detail = run_detail(
        _base(), _Run(), attempts=[],
        stream_stats=[], source_ref=None, destination_ref=None,
    )
    assert "technical_message" not in (detail.technical_metadata or {})
    assert detail.technical_metadata.get("trace_id") == "trc_1"


@pytest.mark.parametrize("field", ["stream_stats", "source_ref", "destination_ref"])
def test_the_other_collections_are_passed_in_too(field: str) -> None:
    """They already were. Pinning it stops the pattern eroding back toward
    lazy access one convenience at a time."""
    parameter = inspect.signature(run_detail).parameters[field]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
