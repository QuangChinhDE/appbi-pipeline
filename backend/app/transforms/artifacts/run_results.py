"""Read ``run_results.json``.

What happened in one invocation, per resource: status, timing, the adapter's
own response, and the message when something failed.  The Results panel renders
this directly -- it is not re-derived from log text, which is why a failure keeps
its row count and its adapter response instead of becoming a string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.transforms.artifacts.schema_version import ArtifactVersion, artifact_version

#: dbt statuses that mean "this resource did not do its job".
FAILING = frozenset({"error", "fail", "runtime error"})
#: Statuses that are not failures but are not clean either.
WARNING = frozenset({"warn"})
PASSING = frozenset({"success", "pass"})

_LINE = re.compile(r"\bline (?P<line>\d+)", re.IGNORECASE)


@dataclass(slots=True)
class NodeResult:
    unique_id: str
    status: str
    name: str = ""
    resource_type: str = ""
    execution_time: float | None = None
    message: str | None = None
    relation_name: str | None = None
    rows_affected: int | None = None
    bytes_processed: int | None = None
    failures: int | None = None
    adapter_response: dict[str, Any] = field(default_factory=dict)
    #: {path?, line?} when the message said where.  Drives click-to-line.
    location: dict[str, Any] = field(default_factory=dict)
    compiled_code: str | None = None

    @property
    def failed(self) -> bool:
        return self.status.lower() in FAILING

    @property
    def warned(self) -> bool:
        return self.status.lower() in WARNING


@dataclass(slots=True)
class ParsedRunResults:
    version: ArtifactVersion
    results: list[NodeResult]
    elapsed_time: float | None = None
    #: The selector dbt actually ran with, echoed back in newer artifacts.
    args: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Totals the invocation row carries, so a list need not open this."""
        totals = {
            "total": len(self.results), "succeeded": 0, "failed": 0, "skipped": 0,
            "tests_passed": 0, "tests_failed": 0, "tests_warned": 0,
        }
        for item in self.results:
            status = item.status.lower()
            is_test = item.resource_type in ("test", "unit_test")
            if status == "skipped":
                totals["skipped"] += 1
            elif status in FAILING:
                totals["failed"] += 1
            elif status in WARNING:
                # A warn is a completed node; it is only "not clean" for tests,
                # which is the distinction the Results panel draws too.
                totals["succeeded"] += 1
            else:
                totals["succeeded"] += 1
            if is_test:
                if status in FAILING:
                    totals["tests_failed"] += 1
                elif status in WARNING:
                    totals["tests_warned"] += 1
                elif status in PASSING:
                    totals["tests_passed"] += 1
        return totals

    def rows_affected(self) -> int | None:
        values = [item.rows_affected for item in self.results if item.rows_affected is not None]
        return sum(values) if values else None

    def first_failure(self) -> NodeResult | None:
        return next((item for item in self.results if item.failed), None)


def parse_run_results(
    document: dict[str, Any], *, names: dict[str, tuple[str, str]] | None = None,
) -> ParsedRunResults:
    """Parse the artifact.

    ``names`` maps unique_id -> (name, resource_type), normally taken from the
    manifest of the same invocation.  run_results identifies nodes only by
    unique_id, so without it the Results table would show
    `model.my_project.fct_orders` where a person expects `fct_orders`.
    """
    version = artifact_version(document, "run-results")
    names = names or {}
    results: list[NodeResult] = []

    for entry in document.get("results") or []:
        if not isinstance(entry, dict):
            continue
        unique_id = str(entry.get("unique_id") or "")
        if not unique_id:
            continue
        fallback_name, fallback_type = names.get(
            unique_id, (unique_id.rsplit(".", 1)[-1], unique_id.split(".", 1)[0]),
        )
        adapter = entry.get("adapter_response")
        adapter = adapter if isinstance(adapter, dict) else {}
        message = _string(entry.get("message"))
        results.append(NodeResult(
            unique_id=unique_id,
            status=str(entry.get("status") or "unknown"),
            name=fallback_name,
            resource_type=fallback_type,
            execution_time=_number(entry.get("execution_time")),
            message=message,
            relation_name=_string(entry.get("relation_name")),
            rows_affected=_integer(
                adapter.get("rows_affected") or adapter.get("rows_affected_count"),
            ),
            bytes_processed=_integer(
                adapter.get("bytes_processed") or adapter.get("bytes_billed"),
            ),
            failures=_integer(entry.get("failures")),
            adapter_response=adapter,
            location=_location(message),
            compiled_code=_string(entry.get("compiled_code")),
        ))

    args = document.get("args")
    return ParsedRunResults(
        version=version,
        results=results,
        elapsed_time=_number(document.get("elapsed_time")),
        args=args if isinstance(args, dict) else {},
    )


def _location(message: str | None) -> dict[str, Any]:
    if not message:
        return {}
    match = _LINE.search(message)
    return {"line": int(match.group("line"))} if match else {}


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
