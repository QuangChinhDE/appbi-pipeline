"""Read ``sources.json`` -- the output of ``dbt source freshness``.

Freshness is a warehouse fact with a threshold attached: how long ago the newest
row landed, and whether that exceeds what the project declared acceptable.  Both
halves come from dbt; AppBI adds only the link to the Pipeline that loads the
table, and only when there is one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.transforms.artifacts.schema_version import ArtifactVersion, artifact_version


@dataclass(slots=True)
class SourceFreshness:
    unique_id: str
    status: str
    #: PASS | WARN | ERROR | RUNTIME ERROR, upper-cased for display.
    max_loaded_at: str | None = None
    snapshotted_at: str | None = None
    #: Seconds between the newest row and when dbt looked.
    age_seconds: float | None = None
    warn_after: dict[str, Any] = field(default_factory=dict)
    error_after: dict[str, Any] = field(default_factory=dict)
    execution_time: float | None = None
    message: str | None = None

    @property
    def failing(self) -> bool:
        return self.status.lower() in ("error", "runtime error")

    @property
    def warning(self) -> bool:
        return self.status.lower() == "warn"


@dataclass(slots=True)
class ParsedSources:
    version: ArtifactVersion
    results: dict[str, SourceFreshness]
    elapsed_time: float | None = None

    def worst_status(self) -> str | None:
        """The status a project-level badge should show.

        One erroring source makes the set erroring; that is the summary a person
        wants, rather than an average.
        """
        if not self.results:
            return None
        statuses = {item.status.lower() for item in self.results.values()}
        for candidate in ("runtime error", "error", "warn", "pass"):
            if candidate in statuses:
                return candidate.upper()
        return None


def parse_sources(document: dict[str, Any]) -> ParsedSources:
    version = artifact_version(document, "sources")
    results: dict[str, SourceFreshness] = {}

    for entry in document.get("results") or []:
        if not isinstance(entry, dict):
            continue
        unique_id = str(entry.get("unique_id") or "")
        if not unique_id:
            continue
        criteria = entry.get("criteria")
        criteria = criteria if isinstance(criteria, dict) else {}
        results[unique_id] = SourceFreshness(
            unique_id=unique_id,
            status=str(entry.get("status") or "unknown"),
            max_loaded_at=_string(entry.get("max_loaded_at")),
            snapshotted_at=_string(entry.get("snapshotted_at")),
            age_seconds=_number(entry.get("age")),
            warn_after=_threshold(criteria.get("warn_after")),
            error_after=_threshold(criteria.get("error_after")),
            execution_time=_number(entry.get("execution_time")),
            # A source that could not be reached at all reports the reason here
            # rather than in `age`, and that is the actionable half.
            message=_string(entry.get("message")),
        )

    return ParsedSources(
        version=version, results=results,
        elapsed_time=_number(document.get("elapsed_time")),
    )


def _threshold(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "count": value.get("count"),
        "period": _string(value.get("period")),
    }


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
