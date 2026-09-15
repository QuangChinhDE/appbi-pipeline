"""What the Overview promises, asserted instead of demonstrated.

These invariants were established by staging real incidents against a running
stack and reading the page -- an expired credential stopping three pipelines, a
dbt model whose column went away, an upstream delete taking a table from 4,579
rows to 503. That found six defects nothing else would have. It also left the
guarantees depending on somebody running the demo and looking, which is not a
guarantee.

So the demo keeps its job -- exploring, and finding what nobody thought to
assert -- and the behaviour it proved is pinned here.

The judgement is tested, not the plumbing. `_issues` and `_platform_signals`
decide what a reader is told; the queries that feed them are exercised by
running the product. Model objects are built in memory, which SQLAlchemy allows
without a session, and the one query `_issues` makes is answered by a stub.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ErrorCategory
from app.models.enums import (
    AlertEventType, HealthLevel, NotificationStatus, PipelineHealth,
    PipelineStatus, ResourceStatus, RunStatus, Severity,
)
from app.models.integration import Destination, Pipeline, Source
from app.models.ops import Notification
from app.models.run import PipelineRun
from app.schemas.health import DataHealth, FreshnessRow
from app.services import health as health_service

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
WS = uuid.uuid4()


# ── the little the judgement needs around it ────────────────────────────────

class _Scalars:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _StubSession:
    """Answers the one query `_issues` makes: open notifications."""

    def __init__(self, notifications=()): self._notifications = list(notifications)

    async def scalars(self, _statement): return _Scalars(self._notifications)


class _Ctx:
    workspace_id = WS


def _source(name="Legacy ERP", health=HealthLevel.ERROR, code="SOURCE_AUTHENTICATION_FAILED"):
    return Source(
        id=uuid.uuid4(), workspace_id=WS, name=name, connector_key="source-postgres",
        status=ResourceStatus.ACTIVE, health_status=health, health_code=code,
        updated_at=NOW - timedelta(hours=2),
    )


def _destination(name="Warehouse"):
    return Destination(
        id=uuid.uuid4(), workspace_id=WS, name=name, connector_key="destination-postgres",
        status=ResourceStatus.ACTIVE, health_status=HealthLevel.HEALTHY,
        updated_at=NOW - timedelta(days=1),
    )


def _pipeline(name, source, destination, *, failures=1, last_success=None):
    return Pipeline(
        id=uuid.uuid4(), workspace_id=WS, name=name,
        source_id=source.id, destination_id=destination.id,
        status=PipelineStatus.ACTIVE, consecutive_failures=failures,
        last_success_at=last_success,
    )


def _run(pipeline, *, status=RunStatus.FAILED, category=ErrorCategory.AUTHENTICATION,
         code="SOURCE_AUTHENTICATION_FAILED", minutes_ago=10):
    return PipelineRun(
        id=uuid.uuid4(), workspace_id=WS, pipeline_id=pipeline.id, status=status,
        created_at=NOW - timedelta(minutes=minutes_ago),
        error_category=category, error_code=code, error_fingerprint=code,
        remediation_action="UPDATE_CREDENTIALS",
    )


def _row(pipeline, *, breached=False, deadline_minutes=30,
         pipeline_health=PipelineHealth.ACTION_REQUIRED):
    return {
        "pipeline": pipeline,
        "health": pipeline_health,
        "freshness_breached": breached,
        "freshness_deadline": NOW - timedelta(minutes=deadline_minutes),
    }


def _issues(rows, runs, sources, destinations, *, freshness=(), notifications=()):
    by_id = {row["pipeline"].id: row["pipeline"] for row in rows}
    return asyncio.run(health_service._issues(
        _StubSession(notifications), _Ctx(), NOW,
        rows=rows, by_id=by_id,
        sources={s.id: s for s in sources},
        destinations={d.id: d for d in destinations},
        runs_7d=runs, freshness=list(freshness), broken=set(),
        volume=[], duration=[], transform_problems=[], platform=[],
    ))


# ── one cause, one card ─────────────────────────────────────────────────────

def test_one_dead_credential_is_one_issue_naming_every_pipeline_it_stopped():
    """Three pipelines, one expired credential. Three cards would be three
    investigations of the same thing."""
    source, destination = _source(), _destination()
    pipelines = [_pipeline(f"ERP {n} to warehouse", source, destination) for n in "abc"]
    runs = [_run(p) for p in pipelines]
    rows = [_row(p) for p in pipelines]

    issues = _issues(rows, runs, [source], [destination])

    assert len(issues) == 1, [i.title_code for i in issues]
    issue = issues[0]
    assert issue.title_code == "health.issue.authentication"
    assert issue.root is not None and issue.root.name == "Legacy ERP"
    assert issue.affected_total == 3
    assert issue.action_code == "UPDATE_CREDENTIALS"
    assert issue.severity == "CRITICAL"


def test_two_unrelated_causes_stay_two_issues():
    """Grouping must not collapse different problems into one."""
    auth_source, read_source = _source(), _source(name="Web analytics")
    destination = _destination()
    failing_auth = _pipeline("Auth one", auth_source, destination)
    failing_read = _pipeline("Read one", read_source, destination)
    runs = [
        _run(failing_auth),
        _run(failing_read, category=ErrorCategory.SOURCE_READ, code="SOURCE_READ_FAILED"),
    ]
    rows = [_row(failing_auth), _row(failing_read)]

    issues = _issues(rows, runs, [auth_source, read_source], [destination])

    assert {i.title_code for i in issues} == {
        "health.issue.authentication", "health.issue.source_read",
    }


# ── history is not an incident ──────────────────────────────────────────────

def test_a_pipeline_that_has_since_succeeded_is_not_a_current_incident():
    """An unresolved notification about Tuesday says nothing about today."""
    source, destination = _source(health=HealthLevel.HEALTHY, code=None), _destination()
    recovered = _pipeline("Recovered", source, destination,
                          failures=0, last_success=NOW - timedelta(minutes=5))
    note = Notification(
        id=uuid.uuid4(), workspace_id=WS, event_type=AlertEventType.RUN_FAILED,
        severity=Severity.WARNING, status=NotificationStatus.NEW,
        title="Pipeline failed", body="",
        dedup_key="x", resource_id=recovered.id, occurrence_count=1,
        created_at=NOW - timedelta(hours=3), updated_at=NOW - timedelta(hours=3),
    )

    issues = _issues([_row(recovered, pipeline_health=PipelineHealth.HEALTHY)],
                     [], [source], [destination], notifications=[note])

    assert issues == [], [i.title_code for i in issues]


# ── a consequence is not a second problem ───────────────────────────────────

def test_lateness_caused_by_a_known_incident_folds_into_it():
    """The pipelines are late *because* the credential died. Saying so twice
    asks the reader to work out there is only one thing to fix."""
    source, destination = _source(), _destination()
    pipelines = [_pipeline(f"ERP {n}", source, destination) for n in "ab"]
    runs = [_run(p) for p in pipelines]
    rows = [_row(p, breached=True, deadline_minutes=120) for p in pipelines]

    issues = _issues(rows, runs, [source], [destination])

    assert len(issues) == 1
    issue = issues[0]
    assert issue.title_code == "health.issue.authentication"
    # The lateness is not lost -- it strengthens the card that caused it.
    assert issue.impact_code == "health.impact.pipelinesStale"
    assert issue.impact_vars.get("seconds", 0) > 0


def test_a_freshness_breach_nothing_else_explains_still_appears():
    """The fold must not swallow lateness that has no other cause."""
    source, destination = _source(health=HealthLevel.HEALTHY, code=None), _destination()
    late = _pipeline("Nightly", source, destination, failures=0)

    issues = _issues([_row(late, breached=True, deadline_minutes=90,
                           pipeline_health=PipelineHealth.HEALTHY)],
                     [], [source], [destination])

    assert [i.title_code for i in issues] == ["health.issue.freshness"]


# ── the platform card ───────────────────────────────────────────────────────

def test_pipeline_queue_reads_the_engine_contracts_own_field():
    """`queued_runs` is what `engine_status` returns. Reading `queued` reported
    an empty queue however deep the real one was."""
    signals = {s.key: s for s in health_service._platform_signals({}, {"queued_runs": 7})}
    queue = signals["pipeline_queue"]
    assert queue.detail_vars == {"n": 7}
    assert queue.state == "normal"

    deep = {s.key: s for s in health_service._platform_signals(
        {}, {"queued_runs": health_service.PIPELINE_QUEUE_BACKLOG})}
    assert deep["pipeline_queue"].state == "backlog"


def test_a_queue_of_zero_is_reported_without_a_count():
    signals = {s.key: s for s in health_service._platform_signals({}, {"queued_runs": 0})}
    assert signals["pipeline_queue"].state == "normal"
    assert signals["pipeline_queue"].detail_code is None


@pytest.mark.parametrize("alive,expected", [(True, "operational"), (False, "down")])
def test_transform_worker_liveness_reaches_the_platform_card(alive, expected):
    facts = {"transform_worker_alive": alive, "transform_queued": 0,
             "scheduler_overdue": 0, "engine_operations_open": 0,
             "engine_operation_oldest_open_seconds": 0.0}
    signals = {s.key: s for s in health_service._platform_signals(facts, {})}
    assert signals["transform_worker"].state == expected


def test_a_transform_queue_that_is_not_draining_is_a_backlog():
    facts = {"transform_worker_alive": True,
             "transform_queued": health_service.TRANSFORM_QUEUE_BACKLOG,
             "scheduler_overdue": 0, "engine_operations_open": 0,
             "engine_operation_oldest_open_seconds": 0.0}
    signals = {s.key: s for s in health_service._platform_signals(facts, {})}
    assert signals["transform_queue"].state == "backlog"
    assert signals["transform_queue"].detail_vars["n"] == health_service.TRANSFORM_QUEUE_BACKLOG


def test_scheduler_claims_no_problem_rather_than_liveness():
    """Nothing here can prove a scheduler process is alive. With nothing
    overdue the honest statement is only that there is no evidence of trouble."""
    quiet = {"transform_worker_alive": True, "transform_queued": 0,
             "scheduler_overdue": 0, "engine_operations_open": 0,
             "engine_operation_oldest_open_seconds": 0.0}
    signals = {s.key: s for s in health_service._platform_signals(quiet, {})}
    assert signals["scheduler"].state == "normal"
    assert signals["scheduler"].detail_code is None

    backed_up = {**quiet, "scheduler_overdue": 4}
    signals = {s.key: s for s in health_service._platform_signals(backed_up, {})}
    assert signals["scheduler"].state == "backlog"
    assert signals["scheduler"].detail_vars == {"n": 4}


def test_engine_operations_are_judged_by_age_not_by_count():
    """Three sagas in flight is healthy. Three stuck since Tuesday is an
    orphaned credential."""
    in_flight = {"transform_worker_alive": True, "transform_queued": 0,
                 "scheduler_overdue": 0, "engine_operations_open": 3,
                 "engine_operation_oldest_open_seconds": 30.0}
    signals = {s.key: s for s in health_service._platform_signals(in_flight, {})}
    assert signals["engine_operations"].state == "normal"

    stuck = {**in_flight,
             "engine_operation_oldest_open_seconds":
                 health_service.ENGINE_OPERATION_STALE_SECONDS + 1}
    signals = {s.key: s for s in health_service._platform_signals(stuck, {})}
    assert signals["engine_operations"].state == "backlog"


# ── numbers that mean what they say ─────────────────────────────────────────

def _metrics(freshness_rows, runs, previous_rate=None):
    health = DataHealth(generated_at=NOW)
    health.freshness = list(freshness_rows)
    health.issues = []
    return {m.key: m for m in health_service._metrics(health, runs, NOW, previous_rate)}


def _fresh(state):
    return FreshnessRow(pipeline_id=uuid.uuid4(), name="p", state=state)


def test_nothing_on_time_is_not_good_news():
    """`(on_time or 100) >= 95` painted 0% green: zero is falsy, so the
    fallback fired at the exact moment every deadline had been missed."""
    metrics = _metrics([_fresh("late"), _fresh("late")], [])
    assert metrics["freshness"].value == 0.0
    assert metrics["freshness"].tone != "good"


def test_no_scheduled_pipeline_makes_no_claim_about_punctuality():
    metrics = _metrics([_fresh("unscheduled")], [])
    assert metrics["freshness"].value is None
    assert metrics["freshness"].tone == "neutral"


def test_everything_on_time_is_good():
    metrics = _metrics([_fresh("on_time"), _fresh("on_time")], [])
    assert metrics["freshness"].value == 100.0
    assert metrics["freshness"].tone == "good"


def test_reliability_counts_the_last_seven_days_and_only_finished_runs():
    pipeline = _pipeline("p", _source(), _destination())
    runs = [
        _run(pipeline, status=RunStatus.SUCCEEDED, minutes_ago=60),
        _run(pipeline, status=RunStatus.FAILED, minutes_ago=120),
        # Still going: not a failure, and must not enter the denominator.
        _run(pipeline, status=RunStatus.RUNNING, minutes_ago=5),
        # Eight days old: outside the current window entirely.
        _run(pipeline, status=RunStatus.FAILED, minutes_ago=8 * 24 * 60),
    ]
    metrics = _metrics([], runs)
    assert metrics["reliability"].value == 50.0


def test_the_delta_compares_with_the_previous_seven_days():
    pipeline = _pipeline("p", _source(), _destination())
    runs = [_run(pipeline, status=RunStatus.SUCCEEDED, minutes_ago=60)]
    metrics = _metrics([], runs, previous_rate=80.0)
    assert metrics["reliability"].value == 100.0
    assert metrics["reliability"].delta == 20.0


def test_no_earlier_sample_means_no_delta_rather_than_no_change():
    pipeline = _pipeline("p", _source(), _destination())
    runs = [_run(pipeline, status=RunStatus.SUCCEEDED, minutes_ago=60)]
    metrics = _metrics([], runs, previous_rate=None)
    assert metrics["reliability"].delta is None


def test_reliability_with_nothing_finished_makes_no_claim():
    metrics = _metrics([], [])
    assert metrics["reliability"].value is None
    assert metrics["reliability"].tone == "neutral"
