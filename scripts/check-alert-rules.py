#!/usr/bin/env python3
"""Assert every Prometheus alert rule has an expression and a `for`.

A rule without `expr` is dead. A rule without `for` fires on the first scrape
that crosses the threshold, so a routine restart pages someone at 3am -- which
is how an on-call rotation learns to ignore the alert that mattered.

Extracted from the CI job so a local verification run and the CI lane execute
the same code. Exit 0 on success, 1 on failure.

    python scripts/check-alert-rules.py
"""

from __future__ import annotations

import pathlib
import sys

SPEC = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "monitoring" / "alerts.yaml"


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("pyyaml is not installed (pip install pyyaml)", file=sys.stderr)
        return 1

    if not SPEC.is_file():
        print(f"no alert spec at {SPEC}", file=sys.stderr)
        return 1

    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    problems: list[str] = []
    count = 0

    for group in spec["groups"]:
        for rule in group["rules"]:
            count += 1
            name = rule.get("alert") or rule.get("record") or "<unnamed>"
            if not rule.get("expr"):
                problems.append(f"{name} has no expr:")
            if not rule.get("for"):
                problems.append(f"{name} has no for:")

    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{count} rules checked")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
