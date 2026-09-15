"""An alert body is prose somebody reads, not a serialisation.

Notification titles and bodies are written by a worker and stored, so they are
plain sentences rather than the code-plus-variables an API error carries. That
is settled. What is not acceptable inside those sentences is a machine
rendering of a value:

    The refresh deadline passed at 2026-09-14T22:44:17.953898+00:00.

Found on the Alerts page during the UI audit. `isoformat()` is the right way to
put a datetime on the wire and the wrong way to put one in front of a person --
microseconds and a UTC offset answer a question nobody reading an alert asked.

This reads the source rather than the database because the defect is in what
the code chooses to interpolate, and because the alert services have no test
fixtures to build a notification from.
"""

from __future__ import annotations

import ast
import pathlib

ALERTS = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "alerts.py"

#: The keywords whose values a person reads verbatim on the Alerts page.
PROSE_KEYWORDS = {"title", "body"}


def _prose_expressions() -> list[tuple[int, ast.expr]]:
    """Every expression passed as a notification title or body."""
    tree = ast.parse(ALERTS.read_text(encoding="utf-8"))
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in PROSE_KEYWORDS:
                found.append((keyword.value.lineno, keyword.value))
    return found


def test_alert_prose_is_written_for_a_reader() -> None:
    """No notification title or body interpolates a machine timestamp."""
    offenders = []
    for lineno, expr in _prose_expressions():
        for inner in ast.walk(expr):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "isoformat"
            ):
                offenders.append(lineno)
    assert not offenders, (
        "alerts.py interpolates isoformat() into a notification a person reads, "
        f"at line(s) {sorted(set(offenders))}. Format the value for a reader instead."
    )


def test_the_freshness_body_still_names_a_time() -> None:
    """Removing the ISO stamp must not remove the fact it carried.

    The deadline is the one thing that body exists to report, so a fix that
    quietly dropped it would pass the test above and lose the information.
    """
    prose = [expr for _, expr in _prose_expressions()]
    freshness = [
        expr for expr in prose
        if isinstance(expr, ast.JoinedStr)
        and any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and "refresh deadline" in part.value
            for part in expr.values
        )
    ]
    assert freshness, "the freshness notification no longer states a refresh deadline"
    assert any(
        isinstance(part, ast.FormattedValue) for part in freshness[0].values
    ), "the freshness body names a deadline but interpolates no value for it"
