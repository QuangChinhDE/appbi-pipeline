"""The shape of "is my data OK", answered once on the server.

WHY THESE ARE CODES AND NOT SENTENCES.

Every string here is a key plus a bag of values. The product's rule is that a
failure's words are chosen when somebody *reads* them, not when the failure
happens -- a worker has no request and cannot know who is asking -- and this
page is the place that rule matters most: it is the first screen of the
morning, and it must be in the reader's language whether the run failed an hour
ago or last week.

WHY THE SERVER DOES THE THINKING.

The interface used to receive counts and lists and was expected to work out
what they meant. Seven failures and a 91% success rate is data; "CRM stopped
updating four hours ago because its credentials expired, and three reporting
tables are stale as a result" is the answer, and it needs run history,
notification dedup keys, freshness deadlines and the pipeline graph to reach.
None of that belongs in a browser.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class HealthResource(BaseModel):
    """A thing an issue is about, or a thing it hurts."""

    type: str
    id: uuid.UUID | None = None
    name: str
    #: Where to go to act on it, relative to the workspace. The browser adds
    #: the workspace segment, because only it knows which one is on screen.
    href: str | None = None


class HealthIssue(BaseModel):
    """One problem, however many runs it broke.

    GROUPED BY CAUSE, NOT BY RUN. Ten pipelines failing on one expired
    credential is one issue with ten affected pipelines -- not ten rows that a
    person has to read before noticing they say the same thing. That is the
    difference between a list of events and something somebody can act on.
    """

    #: Stable across refreshes so the interface can keep a card expanded, and
    #: so two loads of the same problem are the same problem.
    key: str
    severity: str
    kind: str

    title_code: str
    title_vars: dict = Field(default_factory=dict)
    #: Why it is happening, when the evidence supports saying so.
    cause_code: str | None = None
    cause_vars: dict = Field(default_factory=dict)
    #: What it costs -- the half a data analyst reads first.
    impact_code: str | None = None
    impact_vars: dict = Field(default_factory=dict)
    #: What the claim rests on: counts, codes, times.
    evidence_code: str | None = None
    evidence_vars: dict = Field(default_factory=dict)

    root: HealthResource | None = None
    affected: list[HealthResource] = Field(default_factory=list)
    #: Beyond the ones listed, so a card can say "and 14 more" truthfully.
    affected_total: int = 0

    started_at: datetime | None = None
    last_seen_at: datetime | None = None
    occurrence_count: int = 1

    #: The remediation vocabulary the product already speaks, so the button is
    #: the same one the error envelope would have offered.
    action_code: str | None = None
    action_href: str | None = None


class HealthMetric(BaseModel):
    """One number at the top, and whether it is moving the right way."""

    key: str
    value: float | None = None
    unit: str = "count"
    #: Against the period before. None where there is no earlier period to
    #: compare with, which is not the same as no change.
    delta: float | None = None
    tone: str = "neutral"


class FreshnessRow(BaseModel):
    pipeline_id: uuid.UUID
    name: str
    deadline: datetime | None = None
    #: Seconds past the deadline. Negative means still inside it.
    late_seconds: float | None = None
    state: str = "unknown"


class ReliabilityDay(BaseModel):
    date: str
    succeeded: int = 0
    failed: int = 0


class CauseShare(BaseModel):
    cause: str
    count: int
    share: float


class AnomalyRow(BaseModel):
    """A run that succeeded and still went wrong.

    A pipeline reporting SUCCEEDED with a tenth of its usual rows is the
    failure a status-based dashboard cannot see, and the one an analyst
    notices last -- in a report, from a colleague.
    """

    pipeline_id: uuid.UUID
    name: str
    current: float
    baseline: float
    #: Signed: -0.9 is ninety percent down.
    change: float
    run_id: uuid.UUID | None = None
    at: datetime | None = None


class StageHealth(BaseModel):
    stage: str
    total: int = 0
    healthy: int = 0
    problem: int = 0


class TransformRow(BaseModel):
    project_id: uuid.UUID
    name: str
    state: str
    detail_code: str | None = None
    detail_vars: dict = Field(default_factory=dict)


class PlatformSignal(BaseModel):
    key: str
    state: str
    detail_code: str | None = None
    detail_vars: dict = Field(default_factory=dict)


class DataHealth(BaseModel):
    """Everything the first screen of the morning needs, in one answer."""

    #: HEALTHY | ATTENTION | CRITICAL -- the status a reader sees before any
    #: number, because a number invites arithmetic and a status invites action.
    status: str = "HEALTHY"
    headline_code: str | None = None
    headline_vars: dict = Field(default_factory=dict)
    generated_at: datetime | None = None

    metrics: list[HealthMetric] = Field(default_factory=list)
    issues: list[HealthIssue] = Field(default_factory=list)
    freshness: list[FreshnessRow] = Field(default_factory=list)
    reliability: list[ReliabilityDay] = Field(default_factory=list)
    failure_causes: list[CauseShare] = Field(default_factory=list)
    volume: list[AnomalyRow] = Field(default_factory=list)
    duration: list[AnomalyRow] = Field(default_factory=list)
    stages: list[StageHealth] = Field(default_factory=list)
    transforms: list[TransformRow] = Field(default_factory=list)
    platform: list[PlatformSignal] = Field(default_factory=list)
