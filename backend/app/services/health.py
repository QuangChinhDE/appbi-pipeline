"""Is the data OK, what is wrong, what does it hurt, and what should I do.

Those four questions in that order are the whole design. The screen this feeds
used to show seven counts and three lists and leave the reader to join them up:
"failed 24h: 7" and "success rate 91%" are facts about runs, not answers about
data, and working out whether the seven failures share one cause meant opening
seven of them.

So the joining happens here. The inputs were all already in the database --
run history with error categories and fingerprints, notifications with dedup
keys and remediation actions, freshness deadlines, transform health -- spread
across four screens that each knew a part.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.models.enums import (
    AlertEventType, HealthLevel, NotificationStatus, PipelineHealth, PipelineStatus,
    ResourceStatus, RunStatus, Severity,
)
from app.models.integration import Destination, Pipeline, Source
from app.models.ops import Notification
from app.models.run import PipelineRun
from app.schemas.health import (
    AnomalyRow, CauseShare, DataHealth, FreshnessRow, HealthIssue, HealthMetric,
    HealthResource, PlatformSignal, ReliabilityDay, StageHealth, TransformRow,
)
from app.services import monitoring

FAILED_STATUSES = (RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT)

#: How many affected things a card names before it starts counting them. Four
#: is what fits without the card becoming the list it was meant to replace.
NAMED_AFFECTED = 4

#: A run carrying less than half its usual rows is worth saying out loud, and a
#: run carrying none at all when it usually carries some is worth shouting. Not
#: a model: the median of recent comparable runs, which is enough to catch the
#: failure that reports success and cheap enough to compute on every load.
VOLUME_WARN = 0.5
#: Twice its usual duration. Slower is not broken, but it is how broken starts.
DURATION_WARN = 2.0
#: Below this many rows a ratio says nothing -- a table with nine rows
#: yesterday and four today is not an incident.
VOLUME_FLOOR = 50
#: Fewer than this many comparable runs and there is no "usual" to compare to.
BASELINE_RUNS = 4

#: A run rate at or above this is good news rather than merely not-bad news.
#: Used for reliability and for on-time delivery, which are both "what share of
#: the time did this work" questions and should not answer to two bars.
HEALTHY_RATE = 95.0

#: Queue depths the product treats as a backlog rather than as being busy.
#: Pipeline runs are short and many; a Transform build is long and few, so one
#: waiting Transform is not yet news and twenty waiting syncs is.
PIPELINE_QUEUE_BACKLOG = 20
TRANSFORM_QUEUE_BACKLOG = 5

#: An engine operation open longer than this is no longer "in flight". It is
#: the same ledger the reconciler works from, and an operation it has not
#: resolved in an hour is holding a resource that carries credentials.
ENGINE_OPERATION_STALE_SECONDS = 3600


def _ref(kind: str, name: str, identifier: uuid.UUID | None = None,
         href: str | None = None) -> HealthResource:
    return HealthResource(type=kind, id=identifier, name=name, href=href)


# Which earlier pass already covers each kind of alert.
#
# The alert engine and the passes above are two routes to the same incident:
# the engine notices it when it happens, the passes notice it by reading the
# state it left behind. When both arrive, the pass wins -- it knows the root
# cause, which pipelines are affected and what to do, where the notification
# knows only that something happened. Without this, one expired credential is
# reported once as "Legacy ERP can no longer sign in, 3 pipelines affected"
# and again, underneath, as "1 source cannot sign in".
COVERED_BY: dict[AlertEventType, frozenset[str]] = {
    AlertEventType.RUN_FAILED: frozenset({"source", "destination", "pipeline"}),
    AlertEventType.CONSECUTIVE_FAILURES: frozenset({"source", "destination", "pipeline"}),
    AlertEventType.SOURCE_AUTH_ERROR: frozenset({"source"}),
    AlertEventType.DESTINATION_ERROR: frozenset({"destination"}),
    AlertEventType.SCHEMA_BREAKING_CHANGE: frozenset({"source", "pipeline"}),
    AlertEventType.FRESHNESS_BREACH: frozenset({"freshness"}),
    AlertEventType.ENGINE_DEGRADED: frozenset({"platform"}),
}


# What kind of problem a connection's own health code describes.
#
# These are the codes the connection check writes when it fails, and they map
# onto the same vocabulary the run failures use, so one broken warehouse reads
# the same whether a pipeline tripped over it or the check found it first.
_CATEGORY_OF_CODE = {
    "SOURCE_AUTHENTICATION_FAILED": "authentication",
    "DESTINATION_AUTHENTICATION_FAILED": "authentication",
    "SOURCE_PERMISSION_DENIED": "permission",
    "DESTINATION_PERMISSION_DENIED": "permission",
    "SOURCE_UNREACHABLE": "network",
    "DESTINATION_UNREACHABLE": "network",
    "DESTINATION_STAGING_CONFLICT": "configuration",
    "DESTINATION_WRITE_FAILED": "destination_write",
    "SOURCE_READ_FAILED": "source_read",
}


# Why a Transform project is not building, in a sentence rather than a state.
#
# The state -- "build failed" -- is what the Transform card shows beside the
# project's name, where the project name supplies the context. On an issue card
# the title has already said the project cannot build its tables, so repeating
# the state there spends a line and tells the reader nothing.
_TRANSFORM_CAUSE = {
    "health.transform.parseFailed": "health.cause.transform.parse",
    "health.transform.buildFailed": "health.cause.transform.build",
    "health.transform.warning": "health.cause.transform.warning",
}


def _live_failures(
    sources: dict[uuid.UUID, Source], destinations: dict[uuid.UUID, Destination],
    rows: list[dict[str, Any]],
) -> set[uuid.UUID]:
    """Connections whose recorded failure is still true.

    `health_status` is written when a check fails and is not cleared by the
    thing quietly starting to work again, so on its own it answers "has this
    ever failed", and the page is asking "is this failing". A success carried
    through the same connection afterwards settles it.
    """
    broken: set[uuid.UUID] = set()
    for actor in (*sources.values(), *destinations.values()):
        if actor.status is not ResourceStatus.ACTIVE:
            continue
        if actor.health_status is not HealthLevel.ERROR:
            continue
        if _recovered_since(actor.updated_at, actor.id, rows):
            continue
        broken.add(actor.id)
    return broken


def _recovered_since(
    marked_at: datetime | None, actor_id: uuid.UUID, rows: list[dict[str, Any]],
) -> bool:
    """Has anything succeeded through this connection since it was marked bad?

    Every stored failure -- an actor's health, an open notification, a run's
    error -- is a record of a moment, and the page is asked a question about
    now. A success through the same connection afterwards is the strongest
    evidence available that the moment has passed.
    """
    if marked_at is None:
        return False
    for row in rows:
        pipeline = row["pipeline"]
        if not _uses(pipeline, actor_id):
            continue
        if pipeline.last_success_at and pipeline.last_success_at > marked_at:
            return True
    return False


def _uses(pipeline: Pipeline, actor_id: uuid.UUID) -> bool:
    """Does this pipeline read from, or write to, that connection?"""
    return actor_id in (pipeline.source_id, pipeline.destination_id)


async def build(session: AsyncSession, ctx: RequestContext) -> DataHealth:
    now = utcnow()
    workspace_id = ctx.workspace_id

    pipelines = list((await session.scalars(
        select(Pipeline).where(
            Pipeline.workspace_id == workspace_id,
            Pipeline.deleted_at.is_(None),
            Pipeline.status != PipelineStatus.DELETED,
        )
    )).all())
    by_id = {p.id: p for p in pipelines}

    sources = {
        s.id: s for s in (await session.scalars(
            select(Source).where(Source.workspace_id == workspace_id,
                                 Source.deleted_at.is_(None))
        )).all()
    }
    destinations = {
        d.id: d for d in (await session.scalars(
            select(Destination).where(Destination.workspace_id == workspace_id,
                                      Destination.deleted_at.is_(None))
        )).all()
    }

    rows, health_counts = await monitoring.monitoring_rows(session, ctx)
    runs_7d = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.created_at >= now - timedelta(days=7),
        ).order_by(PipelineRun.created_at.desc())
    )).all())

    health = DataHealth(generated_at=now)
    # Which connections are failing *now*, as opposed to carrying a record of
    # having failed once. Everything below reads this rather than re-deriving
    # it, so the cards cannot disagree with each other.
    broken = _live_failures(sources, destinations, rows)
    health.freshness = _freshness(rows, now)
    health.reliability, health.failure_causes = _reliability(runs_7d, now)
    health.volume, health.duration = await _anomalies(session, workspace_id, by_id, now)
    health.transforms, transform_problems = await _transforms(session, workspace_id)
    health.stages = _stages(sources, destinations, rows, health_counts,
                            health.transforms, broken)
    health.platform = await _platform(session, ctx)

    health.issues = await _issues(
        session, ctx, now,
        rows=rows, by_id=by_id, sources=sources, destinations=destinations,
        runs_7d=runs_7d, freshness=health.freshness, broken=broken,
        volume=health.volume, duration=health.duration,
        transform_problems=transform_problems, platform=health.platform,
    )
    health.metrics = _metrics(health, runs_7d, now, await _previous_week_rate(
        session, workspace_id, now))
    health.status, health.headline_code, health.headline_vars = _headline(health)
    return health


# ── the four questions, one at a time ────────────────────────────────────────

def _freshness(rows: list[dict[str, Any]], now: datetime) -> list[FreshnessRow]:
    """How late each pipeline's data is against the deadline it promised.

    This existed already and was only reachable from the Monitoring page, which
    is the wrong place for it: whether a report can be trusted this morning is
    the question, and it was two clicks from where people ask it.
    """
    out: list[FreshnessRow] = []
    for row in rows:
        pipeline = row["pipeline"]
        deadline = row.get("freshness_deadline")
        late = (now - deadline).total_seconds() if deadline else None
        if deadline is None:
            state = "unscheduled"
        elif row.get("freshness_breached"):
            state = "late"
        else:
            state = "on_time"
        out.append(FreshnessRow(
            pipeline_id=pipeline.id, name=pipeline.name,
            deadline=deadline, late_seconds=late, state=state,
        ))
    out.sort(key=lambda r: -(r.late_seconds or float("-inf")))
    return out


def _reliability(
    runs: list[PipelineRun], now: datetime
) -> tuple[list[ReliabilityDay], list[CauseShare]]:
    """Seven days of outcomes, and what the failures had in common.

    One number -- "95% this week" -- cannot say whether that is better or worse
    than last week, and cannot say whether the failures were one bad afternoon
    or a slow decline. The shape is the information.
    """
    buckets: dict[str, ReliabilityDay] = {}
    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        buckets[day] = ReliabilityDay(date=day)

    causes: dict[str, int] = defaultdict(int)
    for run in runs:
        day = run.created_at.date().isoformat()
        bucket = buckets.get(day)
        if bucket is None:
            continue
        if run.status is RunStatus.SUCCEEDED:
            bucket.succeeded += 1
        elif run.status in FAILED_STATUSES:
            bucket.failed += 1
            causes[(run.error_category.value if run.error_category else "UNKNOWN")] += 1

    total = sum(causes.values())
    shares = [
        CauseShare(cause=cause, count=count, share=count / total if total else 0.0)
        for cause, count in sorted(causes.items(), key=lambda item: -item[1])
    ]
    return list(buckets.values()), shares


async def _anomalies(
    session: AsyncSession, workspace_id: uuid.UUID,
    by_id: dict[uuid.UUID, Pipeline], now: datetime,
) -> tuple[list[AnomalyRow], list[AnomalyRow]]:
    """A run can succeed and still be wrong.

    Volume and duration are the two ways that happens: a pipeline that returns
    a tenth of its usual rows, and one that takes three times as long. Neither
    shows up in a status, and the first is the one an analyst finds out about
    from a colleague looking at a report.

    The baseline is the median of the pipeline's own recent successful runs.
    Median rather than mean because one enormous backfill should not raise the
    bar for every day after it.
    """
    recent = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status == RunStatus.SUCCEEDED,
            PipelineRun.created_at >= now - timedelta(days=14),
        ).order_by(PipelineRun.created_at.desc())
    )).all())

    per_pipeline: dict[uuid.UUID, list[PipelineRun]] = defaultdict(list)
    for run in recent:
        per_pipeline[run.pipeline_id].append(run)

    volume: list[AnomalyRow] = []
    duration: list[AnomalyRow] = []
    for pipeline_id, runs in per_pipeline.items():
        pipeline = by_id.get(pipeline_id)
        if pipeline is None or len(runs) <= BASELINE_RUNS:
            continue
        latest, history = runs[0], runs[1:]

        counts = [r.records_synced for r in history if r.records_synced is not None]
        if latest.records_synced is not None and len(counts) >= BASELINE_RUNS:
            baseline = statistics.median(counts)
            if baseline >= VOLUME_FLOOR:
                ratio = latest.records_synced / baseline
                if ratio < VOLUME_WARN:
                    volume.append(AnomalyRow(
                        pipeline_id=pipeline_id, name=pipeline.name,
                        current=float(latest.records_synced), baseline=float(baseline),
                        change=ratio - 1.0, run_id=latest.id, at=latest.created_at,
                    ))

        def seconds(run: PipelineRun) -> float | None:
            if run.started_at and run.ended_at:
                return (run.ended_at - run.started_at).total_seconds()
            return None

        spans = [s for s in (seconds(r) for r in history) if s and s > 0]
        current = seconds(latest)
        if current and len(spans) >= BASELINE_RUNS:
            baseline = statistics.median(spans)
            # A job that normally takes four seconds and today takes twelve is
            # noise, not a regression.
            if baseline >= 30 and current / baseline >= DURATION_WARN:
                duration.append(AnomalyRow(
                    pipeline_id=pipeline_id, name=pipeline.name,
                    current=current, baseline=baseline,
                    change=current / baseline - 1.0, run_id=latest.id,
                    at=latest.created_at,
                ))

    volume.sort(key=lambda a: a.change)
    duration.sort(key=lambda a: -a.change)
    return volume, duration


def _stages(
    sources: dict[uuid.UUID, Source], destinations: dict[uuid.UUID, Destination],
    rows: list[dict[str, Any]], health_counts: dict[str, int],
    transforms: list[TransformRow], broken: set[uuid.UUID],
) -> list[StageHealth]:
    """Where in the journey the trouble is, in four numbers per stage.

    Not a lineage diagram. The question this answers is narrower and asked more
    often: is it the source, the pipeline, or the warehouse -- because that
    decides who picks the problem up.
    """
    def actor_counts(items) -> tuple[int, int]:
        """Healthy means nothing is wrong with it now.

        Not "is it switched on" -- that is configuration, and counting it as
        health showed "Sources 11/11" with a green tick while the banner above
        said an expired credential on one of those sources had stopped three
        pipelines. And not "has it ever failed" either: `broken` has already
        discounted the connections that have carried data since.
        """
        healthy = sum(
            1 for item in items
            if item.status == ResourceStatus.ACTIVE and item.id not in broken
        )
        return len(items), healthy

    source_total, source_ok = actor_counts(list(sources.values()))
    dest_total, dest_ok = actor_counts(list(destinations.values()))
    pipeline_problem = sum(
        1 for row in rows
        if row["health"] in (PipelineHealth.ACTION_REQUIRED, PipelineHealth.FAILED)
        or row.get("freshness_breached")
    )
    stages = [
        StageHealth(stage="sources", total=source_total, healthy=source_ok,
                    problem=source_total - source_ok),
        StageHealth(stage="pipelines", total=len(rows),
                    healthy=len(rows) - pipeline_problem, problem=pipeline_problem),
        StageHealth(stage="destinations", total=dest_total, healthy=dest_ok,
                    problem=dest_total - dest_ok),
    ]
    if transforms:
        broken = sum(1 for row in transforms if row.state != "healthy")
        stages.append(StageHealth(stage="transforms", total=len(transforms),
                                  healthy=len(transforms) - broken, problem=broken))
    return stages


async def _transforms(
    session: AsyncSession, workspace_id: uuid.UUID,
) -> tuple[list[TransformRow], list[TransformRow]]:
    """The other half of the product.

    The overview spoke only about getting data in. Everything a report actually
    reads is built by a Transform, so a screen that says the pipelines are fine
    while the model feeding the sales dashboard has been failing since midnight
    is telling half a truth.
    """
    try:
        from app.transforms.models import TransformProject
    except Exception:                                             # pragma: no cover
        # Transform is an optional overlay; without it there is nothing to say
        # rather than something to apologise for.
        return [], []

    projects = list((await session.scalars(
        select(TransformProject).where(
            TransformProject.workspace_id == workspace_id,
            TransformProject.deleted_at.is_(None),
        )
    )).all()) if hasattr(TransformProject, "deleted_at") else list((await session.scalars(
        select(TransformProject).where(TransformProject.workspace_id == workspace_id)
    )).all())

    out: list[TransformRow] = []
    problems: list[TransformRow] = []
    for project in projects:
        level = getattr(project, "health_status", None)
        parse = str(getattr(project, "parse_status", "") or "").upper()
        if parse == "ERROR":
            state, detail = "failing", "health.transform.parseFailed"
        elif level is HealthLevel.ERROR:
            state, detail = "failing", "health.transform.buildFailed"
        elif level is HealthLevel.WARNING:
            state, detail = "warning", "health.transform.warning"
        else:
            state, detail = "healthy", None
        row = TransformRow(
            project_id=project.id, name=project.name, state=state,
            detail_code=detail,
        )
        out.append(row)
        if state != "healthy":
            problems.append(row)
    return out, problems


async def _platform(session: AsyncSession, ctx: RequestContext) -> list[PlatformSignal]:
    """Whether the machinery itself is running.

    Still a few words rather than a dashboard: an analyst needs to know that a
    green report is trustworthy, and the depth of a queue is an engineer's
    question that lives on Monitoring. What changed is which machinery it
    covers. It used to describe the sync engine and nothing else, so the card
    could read "operational" while no Transform had executed for a day and the
    tables behind every report were stale.

    The facts come from `monitoring.platform_facts`, which `api/metrics.py`
    also reads; the judgement of what counts as bad is made here, once.
    """
    signals: list[PlatformSignal] = []
    try:
        engine = await monitoring.engine_status(session, ctx, detailed=False)
    except Exception:                                             # pragma: no cover
        engine = {}
    try:
        facts = await monitoring.platform_facts(session)
    except Exception:                                             # pragma: no cover
        facts = {}

    signals.append(PlatformSignal(
        key="sync_engine",
        state="operational" if bool(engine.get("operational", True)) else "down",
    ))
    signals.extend(_platform_signals(facts, engine))
    return signals


def _platform_signals(facts: dict, engine: dict) -> list[PlatformSignal]:
    """Facts to states. Pure, so the thresholds can be tested without a database."""
    signals: list[PlatformSignal] = []

    # The engine's own contract calls this `queued_runs`. Reading `queued` --
    # a key it has never returned -- meant the card reported an empty queue
    # however deep the real one was, which is the most reassuring way to be
    # wrong.
    queued = int(engine.get("queued_runs") or 0)
    signals.append(PlatformSignal(
        key="pipeline_queue",
        state="normal" if queued < PIPELINE_QUEUE_BACKLOG else "backlog",
        detail_code="health.platform.queued" if queued else None,
        detail_vars={"n": queued} if queued else {},
    ))

    if facts:
        # A worker that is down moves nothing, so nothing turns red on its own.
        # This is the signal that would have paged.
        alive = bool(facts.get("transform_worker_alive", True))
        signals.append(PlatformSignal(
            key="transform_worker", state="operational" if alive else "down",
        ))

        tq = int(facts.get("transform_queued") or 0)
        signals.append(PlatformSignal(
            key="transform_queue",
            state="normal" if tq < TRANSFORM_QUEUE_BACKLOG else "backlog",
            detail_code="health.platform.queued" if tq else None,
            detail_vars={"n": tq} if tq else {},
        ))

        # No scheduler heartbeat exists, so this claims only what it can see.
        # Both schedulers advance `next_run_at` before deciding whether to run,
        # so a pipeline still due past a whole minimum interval was never
        # claimed. "normal" here means no evidence of a problem -- not proof
        # that a scheduler process is alive, which nothing here can prove.
        overdue = int(facts.get("scheduler_overdue") or 0)
        signals.append(PlatformSignal(
            key="scheduler",
            state="normal" if not overdue else "backlog",
            detail_code="health.platform.overdue" if overdue else None,
            detail_vars={"n": overdue} if overdue else {},
        ))

        # An open operation means the product and the engine may disagree, and
        # the engine's side of that disagreement holds credentials. In flight
        # is fine; unresolved for an hour is not.
        open_ops = int(facts.get("engine_operations_open") or 0)
        stale = float(facts.get("engine_operation_oldest_open_seconds") or 0.0)
        signals.append(PlatformSignal(
            key="engine_operations",
            state="normal" if not (open_ops and stale > ENGINE_OPERATION_STALE_SECONDS)
            else "backlog",
            detail_code="health.platform.openOps" if open_ops else None,
            detail_vars={"n": open_ops} if open_ops else {},
        ))
    return signals


# ── the issues, grouped by what caused them ──────────────────────────────────

async def _issues(
    session: AsyncSession, ctx: RequestContext, now: datetime, *,
    rows: list[dict[str, Any]], by_id: dict[uuid.UUID, Pipeline],
    sources: dict[uuid.UUID, Source], destinations: dict[uuid.UUID, Destination],
    runs_7d: list[PipelineRun], freshness: list[FreshnessRow], broken: set[uuid.UUID],
    volume: list[AnomalyRow], duration: list[AnomalyRow],
    transform_problems: list[TransformRow], platform: list[PlatformSignal],
) -> list[HealthIssue]:
    issues: list[HealthIssue] = []

    # 1. Failing pipelines, gathered by the thing that broke them.
    #
    # Ten pipelines down on one expired credential is one problem. Listing it
    # ten times is how an incident looks bigger than it is and takes longer to
    # understand -- the reader has to notice the repetition themselves.
    failing: dict[tuple, list[tuple[Pipeline, PipelineRun]]] = defaultdict(list)
    seen_pipelines: set[uuid.UUID] = set()
    for run in runs_7d:
        if run.status not in FAILED_STATUSES or run.pipeline_id in seen_pipelines:
            continue
        pipeline = by_id.get(run.pipeline_id)
        if pipeline is None or pipeline.last_success_at and pipeline.consecutive_failures == 0:
            continue
        if pipeline.consecutive_failures == 0:
            continue
        seen_pipelines.add(run.pipeline_id)
        category = run.error_category.value if run.error_category else "UNKNOWN"
        if category in {"AUTHENTICATION", "SOURCE_READ", "PERMISSION"}:
            root = ("source", pipeline.source_id)
        elif category == "DESTINATION_WRITE":
            root = ("destination", pipeline.destination_id)
        else:
            root = ("fingerprint", run.error_fingerprint or run.error_code or category)
        failing[(category, root)].append((pipeline, run))

    for (category, root), members in failing.items():
        latest = max((run for _, run in members), key=lambda r: r.created_at)
        earliest = min((run for _, run in members), key=lambda r: r.created_at)
        kind, root_ref = _root_of(root, sources, destinations, members)
        affected = [
            _ref("pipeline", pipeline.name, pipeline.id,
                 f"/pipelines/{pipeline.id}?tab=status")
            for pipeline, _ in members[:NAMED_AFFECTED]
        ]
        issues.append(HealthIssue(
            key=f"fail:{category}:{root[1]}",
            severity="CRITICAL" if len(members) > 1 or category == "AUTHENTICATION"
            else "WARNING",
            kind=kind,
            title_code=f"health.issue.{category.lower()}",
            title_vars={"name": root_ref.name},
            cause_code=f"health.cause.{category.lower()}",
            cause_vars={"name": root_ref.name},
            impact_code="health.impact.pipelines",
            impact_vars={"n": len(members)},
            evidence_code="health.evidence.failures",
            evidence_vars={
                "n": sum(p.consecutive_failures for p, _ in members),
                "code": latest.error_code or category,
            },
            root=root_ref,
            affected=affected,
            affected_total=len(members),
            started_at=earliest.created_at,
            last_seen_at=latest.created_at,
            occurrence_count=sum(p.consecutive_failures for p, _ in members),
            action_code=latest.remediation_action,
            action_href=root_ref.href,
        ))

    # 1b. Connections that are already broken, before anything has run on them.
    #
    # A pipeline failing is how most faults are noticed, but it is not how they
    # start: a warehouse whose staging area has changed under it is broken from
    # that moment, and stays quiet until the next load lands on it. Reporting it
    # while everything is idle is the whole value -- by the time a run fails, the
    # report it feeds is already late.
    #
    # Anything already named as the root of a failure above is skipped, so this
    # adds a card only for the faults nothing has tripped over yet.
    already = {issue.root.id for issue in issues if issue.root is not None}
    for actor, kind, path in [
        *((s, "source", "sources") for s in sources.values()),
        *((d, "destination", "destinations") for d in destinations.values()),
    ]:
        if actor.id in already or actor.id not in broken:
            continue
        code = (actor.health_code or "UNKNOWN").lower()
        category = _CATEGORY_OF_CODE.get(actor.health_code or "", "configuration")
        users = [p for p in rows if _uses(p["pipeline"], actor.id)]
        ref = _ref(kind, actor.name, actor.id, f"/{path}/{actor.id}")
        issues.append(HealthIssue(
            key=f"actor:{actor.id}:{code}",
            # Nothing is failing yet, so this is a warning -- but everything
            # downstream of it will fail on its next run, which is why it is on
            # the page at all rather than only on the connection's own screen.
            severity="WARNING",
            kind=kind,
            title_code=f"health.issue.{category}",
            title_vars={"name": actor.name},
            cause_code=f"health.cause.{category}",
            cause_vars={"name": actor.name},
            impact_code="health.impact.willFail" if users else "health.impact.idle",
            impact_vars={"n": len(users)} if users else {},
            evidence_code="health.evidence.actorCheck",
            evidence_vars={"code": actor.health_code or "UNKNOWN"},
            root=ref,
            affected=[
                _ref("pipeline", p["pipeline"].name, p["pipeline"].id,
                     f"/pipelines/{p['pipeline'].id}?tab=status")
                for p in users[:NAMED_AFFECTED]
            ],
            affected_total=len(users),
            started_at=actor.updated_at,
            last_seen_at=actor.updated_at,
            action_code="OPEN_CONFIGURATION",
            action_href=ref.href,
        ))

    # 2. Data that missed the time it promised, whether or not anything failed.
    #
    # Only where nothing above already accounts for it. A pipeline that cannot
    # authenticate is late as a matter of course, and saying so in a second card
    # asks the reader to work out, from two lists of the same pipeline names,
    # that there is only one thing to fix. Where the lateness *is* a consequence
    # it is folded into the card that caused it instead -- worth more there,
    # because "3 pipelines affected" is abstract and "the data is 2h behind" is
    # what somebody downstream is about to notice.
    claimed: dict[uuid.UUID, HealthIssue] = {}
    for issue in issues:
        for resource in issue.affected:
            if resource.id is not None:
                claimed.setdefault(resource.id, issue)

    all_late = [row for row in rows if row.get("freshness_breached")]
    for row in all_late:
        owner = claimed.get(row["pipeline"].id)
        if owner is None:
            continue
        behind = (now - row["freshness_deadline"]).total_seconds()
        worst_so_far = owner.impact_vars.get("seconds", 0) if owner.impact_vars else 0
        if behind > worst_so_far and owner.impact_code == "health.impact.pipelines":
            owner.impact_code = "health.impact.pipelinesStale"
            owner.impact_vars = {**owner.impact_vars, "seconds": int(behind)}

    late = [row for row in all_late if row["pipeline"].id not in claimed]
    if late:
        worst = max(late, key=lambda row: (now - row["freshness_deadline"]).total_seconds())
        behind = (now - worst["freshness_deadline"]).total_seconds()
        issues.append(HealthIssue(
            key="freshness",
            severity="CRITICAL" if behind > 6 * 3600 else "WARNING",
            kind="freshness",
            title_code="health.issue.freshness",
            title_vars={"n": len(late)},
            cause_code="health.cause.freshness",
            cause_vars={"name": worst["pipeline"].name},
            impact_code="health.impact.stale",
            impact_vars={"seconds": int(behind)},
            evidence_code="health.evidence.deadline",
            evidence_vars={"n": len(late)},
            root=_ref("pipeline", worst["pipeline"].name, worst["pipeline"].id,
                      f"/pipelines/{worst['pipeline'].id}?tab=status"),
            affected=[
                _ref("pipeline", row["pipeline"].name, row["pipeline"].id,
                     f"/pipelines/{row['pipeline'].id}?tab=status")
                for row in late[:NAMED_AFFECTED]
            ],
            affected_total=len(late),
            last_seen_at=now,
            occurrence_count=len(late),
            action_href="/monitoring",
        ))

    # 3. Runs that succeeded and brought back almost nothing.
    for anomaly in volume[:3]:
        issues.append(HealthIssue(
            key=f"volume:{anomaly.pipeline_id}",
            severity="WARNING",
            kind="volume",
            title_code="health.issue.volume",
            title_vars={"name": anomaly.name},
            # Nothing failed, so there is no error to quote -- but the fact that
            # it succeeded is itself the finding, and it is the half of the
            # system the reader can stop looking at.
            cause_code="health.cause.volume",
            impact_code="health.impact.volume",
            impact_vars={"percent": round(abs(anomaly.change) * 100)},
            evidence_code="health.evidence.volume",
            evidence_vars={"current": int(anomaly.current), "baseline": int(anomaly.baseline)},
            root=_ref("pipeline", anomaly.name, anomaly.pipeline_id,
                      f"/pipelines/{anomaly.pipeline_id}?tab=jobs"),
            affected_total=1,
            last_seen_at=anomaly.at,
            action_href=f"/runs/{anomaly.run_id}" if anomaly.run_id else None,
        ))

    # 4. Runs that are getting slower. Not broken; on the way there.
    for anomaly in duration[:2]:
        issues.append(HealthIssue(
            key=f"duration:{anomaly.pipeline_id}",
            severity="INFO",
            kind="duration",
            title_code="health.issue.duration",
            title_vars={"name": anomaly.name},
            cause_code="health.cause.duration",
            impact_code="health.impact.duration",
            impact_vars={"percent": round(anomaly.change * 100)},
            evidence_code="health.evidence.duration",
            evidence_vars={
                "current": int(anomaly.current), "baseline": int(anomaly.baseline),
            },
            root=_ref("pipeline", anomaly.name, anomaly.pipeline_id,
                      f"/pipelines/{anomaly.pipeline_id}?tab=jobs"),
            affected_total=1,
            last_seen_at=anomaly.at,
        ))

    # 5. The reporting tables themselves.
    for row in transform_problems[:3]:
        issues.append(HealthIssue(
            key=f"transform:{row.project_id}",
            severity="CRITICAL" if row.state == "failing" else "WARNING",
            kind="transform",
            title_code="health.issue.transform",
            title_vars={"name": row.name},
            cause_code=_TRANSFORM_CAUSE.get(row.detail_code or ""),
            impact_code="health.impact.transform",
            evidence_code=row.detail_code,
            root=_ref("transform", row.name, row.project_id, f"/transforms/{row.project_id}"),
            affected_total=1,
            last_seen_at=now,
            action_href=f"/transforms/{row.project_id}",
        ))

    # 6. The machinery.
    for signal in platform:
        if signal.state in ("down", "backlog"):
            issues.append(HealthIssue(
                key=f"platform:{signal.key}",
                severity="CRITICAL" if signal.state == "down" else "WARNING",
                kind="platform",
                title_code=f"health.issue.platform.{signal.key}",
                cause_code=signal.detail_code,
                cause_vars=signal.detail_vars,
                last_seen_at=now,
                action_href="/monitoring",
            ))

    # 7. Anything the alert engine noticed that the passes above did not.
    #
    # Two rules, both learned from watching this run against real data.
    #
    # STILL TRUE. An unresolved notification is not the same as a live problem:
    # a pipeline that failed at 3am and has succeeded four times since has an
    # open `RUN_FAILED` notification and nothing wrong with it. "Is my data OK"
    # is a question about now, so a notification whose subject has recovered is
    # history and belongs on the Alerts page, not here.
    #
    # STILL GROUPED. Four notifications about four pipelines failing is one
    # sentence -- "4 pipelines have failing runs" -- with the pipelines named.
    # Dropping them in one per card would rebuild, at the bottom of the page,
    # exactly the list of events the passes above exist to replace.
    known = {issue.kind for issue in issues}
    notifications = list((await session.scalars(
        select(Notification).where(
            Notification.workspace_id == ctx.workspace_id,
            # Open means not yet resolved. There is no OPEN member: a
            # notification is NEW until somebody looks at it and ACKNOWLEDGED
            # after, and both are still problems.
            Notification.status != NotificationStatus.RESOLVED,
        ).order_by(Notification.updated_at.desc()).limit(60)
    )).all())

    late_now = {row.pipeline_id for row in freshness if row.state == "late"}
    # Everything any card above has already put in front of the reader. An
    # alert about one of these is the same incident arriving by its other
    # route, whatever the alert happens to be called.
    spoken_for = {
        resource.id for issue in issues for resource in issue.affected
        if resource.id is not None
    } | {issue.root.id for issue in issues if issue.root is not None}

    grouped: dict[str, list[Notification]] = defaultdict(list)
    for note in notifications:
        kind = note.event_type.value.lower()
        if note.resource_id is not None and note.resource_id in spoken_for:
            continue
        if COVERED_BY.get(note.event_type, frozenset()) & known:
            continue
        if _has_recovered(note, by_id, late_now):
            continue
        grouped[kind].append(note)

    for kind, notes in grouped.items():
        latest = max(notes, key=lambda n: n.updated_at)
        earliest = min(notes, key=lambda n: n.created_at)
        affected = []
        for note in notes[:NAMED_AFFECTED]:
            pipeline = by_id.get(note.resource_id) if note.resource_id else None
            if pipeline is not None:
                affected.append(_ref("pipeline", pipeline.name, pipeline.id,
                                     f"/pipelines/{pipeline.id}?tab=status"))
        issues.append(HealthIssue(
            key=f"alert:{kind}",
            severity="CRITICAL" if any(n.severity is Severity.CRITICAL for n in notes)
            else "WARNING",
            kind=kind,
            title_code=f"health.alert.{kind}",
            title_vars={"n": len(notes)},
            impact_code="health.impact.pipelines" if affected else None,
            impact_vars={"n": len(notes)} if affected else {},
            affected=affected,
            affected_total=len(notes),
            started_at=earliest.created_at,
            last_seen_at=latest.updated_at,
            occurrence_count=sum(n.occurrence_count for n in notes),
            action_code=latest.remediation_action,
            action_href="/alerts",
        ))

    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    issues.sort(key=lambda issue: (order.get(issue.severity, 3), -issue.affected_total))
    return issues[:12]


def _has_recovered(
    note: Notification, by_id: dict[uuid.UUID, Pipeline], late: set[uuid.UUID],
) -> bool:
    """Is the thing this notification is about still true right now?

    The alert engine records a failure when it happens and leaves the
    notification open until somebody clears it. That is right for an inbox and
    wrong for a health page: an unread note about Tuesday says nothing about
    whether today's data can be trusted.

    "Still true" is a different question for the two kinds of claim, and the
    page answers both live elsewhere -- so it must answer them the same way
    here, or two adjacent cards will disagree:

      a failure alert  -- has the pipeline succeeded since?
      a freshness alert -- is the pipeline late against its deadline now?
    """
    pipeline = by_id.get(note.resource_id) if note.resource_id else None
    if pipeline is None:
        # Not about a pipeline -- an engine or destination notice has no
        # "recovered since" to check, so it stands until somebody resolves it.
        return False
    if note.event_type is AlertEventType.FRESHNESS_BREACH:
        return pipeline.id not in late
    if pipeline.consecutive_failures > 0:
        return False
    last_success = pipeline.last_success_at
    return bool(last_success and note.updated_at and last_success >= note.updated_at)

def _root_of(
    root: tuple, sources: dict, destinations: dict, members: list,
) -> tuple[str, HealthResource]:
    kind, identifier = root
    if kind == "source" and identifier in sources:
        actor = sources[identifier]
        return "source", _ref("source", actor.name, actor.id, f"/sources/{actor.id}")
    if kind == "destination" and identifier in destinations:
        actor = destinations[identifier]
        return "destination", _ref("destination", actor.name, actor.id,
                                   f"/destinations/{actor.id}")
    pipeline = members[0][0]
    return "pipeline", _ref("pipeline", pipeline.name, pipeline.id,
                            f"/pipelines/{pipeline.id}?tab=status")


# ── the four numbers, and the sentence above them ────────────────────────────

async def _previous_week_rate(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime,
) -> float | None:
    """Success rate over [now-14d, now-7d), counted rather than loaded.

    Deliberately its own query. The reliability arrow needs fourteen days, and
    every other thing on this page -- which pipelines are failing, what they
    have in common, what is late -- is a question about the last seven. Widening
    the shared sample to satisfy the arrow would have quietly started reporting
    last week's resolved incidents as current ones.

    An aggregate rather than rows: nothing needs the runs themselves, and a
    fortnight of a busy workspace is a lot of objects to build in order to
    divide two numbers.
    """
    start = now - timedelta(days=14)
    end = now - timedelta(days=7)
    finished = (RunStatus.SUCCEEDED, *FAILED_STATUSES)
    total, succeeded = (await session.execute(
        select(
            func.count(),
            func.count().filter(PipelineRun.status == RunStatus.SUCCEEDED),
        ).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status.in_(finished),
            PipelineRun.created_at >= start,
            PipelineRun.created_at < end,
        )
    )).one()
    if not total:
        return None
    return (succeeded or 0) / total * 100


def _rate_tone(rate: float | None) -> str:
    """Good, not good, or no claim -- and zero is a rate, not an absence.

    `(on_time or 100) >= 95` read a perfect score out of nothing at all:
    0% on-time is falsy, so the fallback fired and the metric was painted green
    at the exact moment every scheduled pipeline had missed its deadline. The
    same shape sat on reliability. Nothing numeric here may be judged by
    truthiness again -- `None` means there was no sample to judge, and that is
    a different statement from "none of them worked".
    """
    if rate is None:
        return "neutral"
    return "good" if rate >= HEALTHY_RATE else "warn"


def _metrics(
    health: DataHealth, runs: list[PipelineRun], now: datetime,
    previous_rate: float | None = None,
) -> list[HealthMetric]:
    """Four numbers, each with the direction it moved.

    A percentage on its own cannot be judged. 96% is excellent for a fleet that
    was at 91% and alarming for one that was at 99.8%, and the reader has no
    way to know which without the arrow.
    """
    week_ago = now - timedelta(days=7)

    def rate(sample: list[PipelineRun]) -> float | None:
        # Only finished runs can have succeeded or failed. A run still going
        # is not a failure, and counting it as one makes every busy morning
        # look like an outage.
        finished = [r for r in sample
                    if r.status is RunStatus.SUCCEEDED or r.status in FAILED_STATUSES]
        if not finished:
            return None
        ok = sum(1 for r in finished if r.status is RunStatus.SUCCEEDED)
        return ok / len(finished) * 100

    # A week against the week before it. This used to cut the same seven days
    # in half and compare the two halves, which answered a different question
    # -- "was Thursday better than Monday" -- and answered it noisily, because
    # three and a half days of a small fleet is a handful of runs.
    current_rate = rate([r for r in runs if r.created_at >= week_ago])
    delta = (current_rate - previous_rate
             if current_rate is not None and previous_rate is not None else None)

    late = [row for row in health.freshness if row.state == "late"]
    scheduled = [row for row in health.freshness if row.state in ("late", "on_time")]
    on_time = (len(scheduled) - len(late)) / len(scheduled) * 100 if scheduled else None

    day_ago = now - timedelta(hours=24)
    records = sum(r.records_synced or 0 for r in runs if r.created_at >= day_ago)
    prior = sum(r.records_synced or 0 for r in runs
                if day_ago - timedelta(hours=24) <= r.created_at < day_ago)

    critical = sum(1 for i in health.issues if i.severity == "CRITICAL")
    return [
        HealthMetric(
            key="reliability", value=round(current_rate, 1) if current_rate is not None else None,
            unit="percent", delta=round(delta, 1) if delta is not None else None,
            tone=_rate_tone(current_rate),
        ),
        HealthMetric(
            key="issues", value=len(health.issues), unit="count",
            tone="bad" if critical else "warn" if health.issues else "good",
        ),
        HealthMetric(
            key="freshness", value=round(on_time, 1) if on_time is not None else None,
            unit="percent", tone=_rate_tone(on_time),
        ),
        HealthMetric(
            key="records", value=float(records), unit="records",
            delta=round((records - prior) / prior * 100, 1) if prior else None,
            tone="neutral",
        ),
    ]


def _headline(health: DataHealth) -> tuple[str, str | None, dict]:
    """The sentence before the numbers.

    Status first, because a number invites arithmetic and a status invites a
    decision. Somebody opening this at nine in the morning should not have to
    read six figures to learn whether today is a normal day.
    """
    critical = [i for i in health.issues if i.severity == "CRITICAL"]
    if critical:
        worst = critical[0]
        return "CRITICAL", "health.headline.critical", {
            "n": len(health.issues),
            "title_code": worst.title_code,
            "root": worst.root.name if worst.root else "",
        }
    if health.issues:
        return "ATTENTION", "health.headline.attention", {"n": len(health.issues)}
    return "HEALTHY", "health.headline.healthy", {}
