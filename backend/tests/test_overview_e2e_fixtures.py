"""The payloads the browser tests render, built from the real contract.

The Overview golden flows serve these instead of standing a database up, which
is what lets them run anywhere. The risk in that trade is obvious: a mocked
response drifts from the real one and the tests keep passing while the product
breaks.

So the fixtures are not hand-written JSON. They are built here from
`OverviewResponse` itself and written to disk, and this module asserts that
what is on disk still validates. Change the contract and this fails before the
browser tests get a chance to pass against a shape the API no longer returns.

    python backend/tests/test_overview_e2e_fixtures.py     # regenerate

Regenerating is deliberate and explicit; pytest only ever checks.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

from app.schemas.domain import OverviewKpis, OverviewResponse
from app.schemas.health import (
    CauseShare, DataHealth, FreshnessRow, HealthIssue, HealthMetric,
    HealthResource, PlatformSignal, ReliabilityDay, StageHealth, TransformRow,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures"
NOW = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
WS = "c25eb1b9-3b5c-4173-a877-ff639880a535"
SOURCE = "203a2d53-58d0-4189-8cca-9545de39f3cd"


def _week(succeeded: int, failed: int) -> list[ReliabilityDay]:
    return [
        ReliabilityDay(
            date=(NOW - timedelta(days=offset)).date().isoformat(),
            succeeded=succeeded, failed=failed,
        )
        for offset in range(6, -1, -1)
    ]


def _platform(**overrides: str) -> list[PlatformSignal]:
    states = {
        "sync_engine": "operational", "pipeline_queue": "normal",
        "transform_worker": "operational", "transform_queue": "normal",
        "scheduler": "normal", "engine_operations": "normal",
    }
    states.update(overrides)
    return [PlatformSignal(key=key, state=state) for key, state in states.items()]


def _envelope(health: DataHealth, onboarding: dict[str, bool]) -> OverviewResponse:
    # The legacy KPI block is still part of the contract and still populated;
    # this task does not remove it, so the fixtures carry it too.
    return OverviewResponse(
        health=health,
        kpis=OverviewKpis(active_pipelines=8, total_sources=5, total_destinations=1),
        onboarding=onboarding,
    )


DONE = {"has_source": True, "has_destination": True,
        "has_pipeline": True, "has_successful_run": True}


def empty_workspace() -> OverviewResponse:
    """Nothing built yet. A green tick over nothing is the most misleading
    thing this screen could say, so it must show the four steps instead."""
    return _envelope(
        DataHealth(generated_at=NOW, status="HEALTHY"),
        {"has_source": False, "has_destination": False,
         "has_pipeline": False, "has_successful_run": False},
    )


def healthy_morning() -> OverviewResponse:
    health = DataHealth(
        generated_at=NOW, status="HEALTHY", headline_code="health.headline.healthy",
        metrics=[
            HealthMetric(key="reliability", value=99.2, unit="percent", delta=1.4, tone="good"),
            HealthMetric(key="issues", value=0, unit="count", tone="good"),
            HealthMetric(key="freshness", value=100.0, unit="percent", tone="good"),
            HealthMetric(key="records", value=29716, unit="records", tone="neutral"),
        ],
        issues=[],
        freshness=[FreshnessRow(pipeline_id=WS, name="Shop orders to warehouse",
                                state="on_time")],
        reliability=_week(12, 0),
        stages=[StageHealth(stage="sources", total=5, healthy=5, problem=0),
                StageHealth(stage="pipelines", total=8, healthy=8, problem=0)],
        transforms=[TransformRow(project_id=WS, name="Warehouse marts", state="healthy")],
        platform=_platform(),
    )
    return _envelope(health, DONE)


def root_cause_incident() -> OverviewResponse:
    """One expired credential, three stopped pipelines, one card."""
    health = DataHealth(
        generated_at=NOW, status="CRITICAL", headline_code="health.headline.critical",
        headline_vars={"n": 1, "root": "Legacy ERP"},
        metrics=[
            HealthMetric(key="reliability", value=71.0, unit="percent", delta=-24.0, tone="warn"),
            HealthMetric(key="issues", value=1, unit="count", tone="bad"),
            HealthMetric(key="freshness", value=62.5, unit="percent", tone="warn"),
            HealthMetric(key="records", value=1204, unit="records", tone="neutral"),
        ],
        issues=[HealthIssue(
            key="fail:AUTHENTICATION:legacy-erp", severity="CRITICAL", kind="source",
            title_code="health.issue.authentication", title_vars={"name": "Legacy ERP"},
            cause_code="health.cause.authentication", cause_vars={"name": "Legacy ERP"},
            impact_code="health.impact.pipelinesStale",
            impact_vars={"n": 3, "seconds": 7380},
            evidence_code="health.evidence.failures",
            evidence_vars={"n": 17, "code": "SOURCE_AUTHENTICATION_FAILED"},
            root=HealthResource(type="source", id=SOURCE, name="Legacy ERP",
                                href=f"/sources/{SOURCE}"),
            affected=[
                HealthResource(type="pipeline", name="ERP purchase orders to warehouse"),
                HealthResource(type="pipeline", name="ERP stock movements to warehouse"),
                HealthResource(type="pipeline", name="ERP suppliers to warehouse"),
            ],
            affected_total=3, occurrence_count=17,
            action_code="UPDATE_CREDENTIALS", action_href=f"/sources/{SOURCE}",
        )],
        freshness=[
            FreshnessRow(pipeline_id=WS, name="ERP purchase orders to warehouse",
                         state="late", late_seconds=7380),
            FreshnessRow(pipeline_id=SOURCE, name="Shop orders to warehouse", state="on_time"),
        ],
        reliability=_week(8, 4),
        failure_causes=[CauseShare(cause="AUTHENTICATION", count=17, share=0.85)],
        stages=[StageHealth(stage="sources", total=5, healthy=4, problem=1),
                StageHealth(stage="pipelines", total=8, healthy=5, problem=3)],
        transforms=[TransformRow(project_id=WS, name="Warehouse marts", state="healthy")],
        platform=_platform(),
    )
    return _envelope(health, DONE)


def degraded_platform() -> OverviewResponse:
    """Nothing on time, and the machinery underneath is the reason. 0% is a
    reading, not an absence -- it must never be painted as good."""
    health = DataHealth(
        generated_at=NOW, status="ATTENTION", headline_code="health.headline.attention",
        headline_vars={"n": 1},
        metrics=[
            HealthMetric(key="reliability", value=88.0, unit="percent", tone="warn"),
            HealthMetric(key="issues", value=1, unit="count", tone="warn"),
            # The case that used to render green.
            HealthMetric(key="freshness", value=0.0, unit="percent", tone="warn"),
            HealthMetric(key="records", value=0, unit="records", tone="neutral"),
        ],
        issues=[HealthIssue(
            key="freshness", severity="WARNING", kind="freshness",
            title_code="health.issue.freshness", title_vars={"n": 2},
            impact_code="health.impact.stale", impact_vars={"seconds": 9000},
            evidence_code="health.evidence.deadline", evidence_vars={"n": 2},
            affected_total=2, action_href="/monitoring",
        )],
        freshness=[FreshnessRow(pipeline_id=WS, name="Finance invoices to warehouse",
                                state="late", late_seconds=9000)],
        reliability=_week(6, 2),
        stages=[StageHealth(stage="pipelines", total=8, healthy=6, problem=2)],
        transforms=[TransformRow(project_id=WS, name="Warehouse marts", state="failing",
                                 detail_code="health.transform.buildFailed")],
        platform=_platform(transform_worker="down", transform_queue="backlog",
                           scheduler="backlog", engine_operations="backlog"),
    )
    return _envelope(health, DONE)


SCENARIOS = {
    "empty-workspace": empty_workspace,
    "healthy-morning": healthy_morning,
    "root-cause-incident": root_cause_incident,
    "degraded-platform": degraded_platform,
}


def _payload(build) -> dict:
    return json.loads(build().model_dump_json())


def test_every_scenario_still_matches_the_overview_contract() -> None:
    """What the browser tests serve is what the API would return."""
    for name, build in SCENARIOS.items():
        path = FIXTURES / f"{name}.json"
        assert path.exists(), (
            f"missing {path.name}; regenerate with "
            f"`python backend/tests/{pathlib.Path(__file__).name}`"
        )
        stored = json.loads(path.read_text(encoding="utf-8"))
        # Validates, and still says what the scenario means to say.
        OverviewResponse.model_validate(stored)
        assert stored == _payload(build), (
            f"{name}.json has drifted from the scenario it is built from; "
            f"regenerate it deliberately rather than editing the JSON"
        )


def test_the_degraded_scenario_really_carries_the_cases_it_guards() -> None:
    """A fixture that stopped containing the interesting value would let the
    browser test pass without testing anything."""
    degraded = degraded_platform().health
    freshness = next(m for m in degraded.metrics if m.key == "freshness")
    assert freshness.value == 0.0 and freshness.tone != "good"
    assert {s.key: s.state for s in degraded.platform}["transform_worker"] == "down"

    incident = root_cause_incident().health
    assert len(incident.issues) == 1
    assert incident.issues[0].affected_total == 3


def _regenerate() -> None:
    """Rewrite the fixtures from the schema. Deliberate, and never automatic."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, build in SCENARIOS.items():
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(_payload(build), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote {name}.json")


if __name__ == "__main__":  # pragma: no cover
    _regenerate()
