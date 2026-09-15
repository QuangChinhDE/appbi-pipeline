#!/usr/bin/env python3
"""The one verification entry point, at three levels of cost.

    python scripts/verify.py fast    # coding loop: only what the diff touches
    python scripts/verify.py task    # before claiming a task is done
    python scripts/verify.py full    # everything, the CI equivalent

Why a script instead of a checklist: a checklist is a thing a human remembers.
The commands below are the same ones `.github/workflows/ci.yml` runs, so a green
local `full` means the same thing a green CI run means, and a check added in one
place is not silently missing from the other.

`fast` and `task` select steps from the files changed against the merge base
(or the working tree, if nothing is committed yet). `full` runs everything.

A step whose tooling is not installed is reported as SKIPPED. At `full` a skip
is a failure -- an unrunnable check is not a passed check. Exit code is 0 only
if every selected step passed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Step:
    name: str
    command: list[str]
    cwd: Path
    #: Path prefixes that make this step relevant. Empty means always relevant.
    triggers: tuple[str, ...] = ()
    #: Lowest level at which the step runs even when the diff does not touch it.
    always_from: str = "full"
    #: Executable that must exist for the step to be runnable at all.
    requires: str | None = None
    #: Directory that must exist (node_modules, a venv) for the step to run.
    requires_dir: Path | None = None
    notes: str = ""


LEVELS = ("fast", "task", "full")


def steps() -> list[Step]:
    py = sys.executable
    return [
        Step(
            name="backend: undefined names (pyflakes)",
            command=[py, "-m", "pyflakes", "app"],
            cwd=BACKEND,
            triggers=("backend/",),
            always_from="task",
        ),
        Step(
            name="backend: migration chain has one head",
            command=[py, str(ROOT / "scripts" / "check-migration-heads.py")],
            cwd=ROOT,
            triggers=("backend/migrations/", "backend/app/models/"),
            always_from="task",
        ),
        Step(
            name="backend: pytest",
            command=[py, "-m", "pytest", "tests", "-q"],
            cwd=BACKEND,
            # The suite includes structural tests that read frontend source, so
            # a .tsx change is a reason to run it.
            triggers=("backend/", "frontend/src/"),
            always_from="task",
        ),
        Step(
            name="deploy: alert rules parse",
            command=[py, str(ROOT / "scripts" / "check-alert-rules.py")],
            cwd=ROOT,
            triggers=("deploy/monitoring/",),
            always_from="task",
        ),
        Step(
            name="frontend: typecheck",
            command=["npx", "tsc", "--noEmit"],
            cwd=FRONTEND,
            triggers=("frontend/",),
            always_from="task",
            requires="npx",
            requires_dir=FRONTEND / "node_modules",
            notes="run `npm ci` in frontend/",
        ),
        Step(
            name="frontend: lint",
            # Deliberately CI's command, not package.json's `lint` script. The
            # script adds `--max-warnings 0`; the CI lane does not, and CI is the
            # gate that actually blocks a merge. Running the stricter form here
            # would make a local check fail on warnings that ship anyway, which
            # teaches people to ignore the runner. If the project wants warnings
            # to block, change the CI lane and this line together.
            command=["npx", "next", "lint"],
            cwd=FRONTEND,
            triggers=("frontend/",),
            always_from="task",
            requires="npx",
            requires_dir=FRONTEND / "node_modules",
            notes="run `npm ci` in frontend/",
        ),
        Step(
            name="frontend: build",
            command=["npm", "run", "build"],
            cwd=FRONTEND,
            triggers=("frontend/",),
            # Too slow for the coding loop; required before done and in CI.
            always_from="task",
            requires="npm",
            requires_dir=FRONTEND / "node_modules",
            notes="run `npm ci` in frontend/",
        ),
    ]


#: Steps too slow to be worth running on every edit, even when the diff touches
#: their area. `fast` skips them; `task` and `full` do not.
SLOW_IN_FAST = {"frontend: build", "frontend: lint"}


def changed_paths() -> list[str]:
    """Files changed against origin/master, plus anything uncommitted."""
    out: set[str] = set()
    for args in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        if result.returncode == 0:
            out.update(line.strip() for line in result.stdout.splitlines() if line.strip())

    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/master"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        result = subprocess.run(
            ["git", "diff", "--name-only", merge_base.stdout.strip(), "HEAD"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.returncode == 0:
            out.update(line.strip() for line in result.stdout.splitlines() if line.strip())

    return sorted(out)


def selected(level: str, changed: list[str]) -> list[Step]:
    chosen = []
    for step in steps():
        if level == "full":
            chosen.append(step)
            continue
        if level == "fast" and step.name in SLOW_IN_FAST:
            continue
        relevant = not step.triggers or any(
            path.startswith(trigger) for path in changed for trigger in step.triggers
        )
        if relevant:
            chosen.append(step)
    return chosen


def run(step: Step, level: str) -> tuple[str, str]:
    if step.requires and not shutil.which(step.requires):
        return SKIP, f"{step.requires} not on PATH"
    if step.requires_dir and not step.requires_dir.is_dir():
        return SKIP, f"missing {step.requires_dir.name}; {step.notes}"

    print(f"\n--- {step.name}")
    print(f"    $ {' '.join(step.command)}   (in {step.cwd.relative_to(ROOT) or '.'})")
    result = subprocess.run(step.command, cwd=step.cwd, shell=(sys.platform == "win32"))
    return (PASS, "") if result.returncode == 0 else (FAIL, f"exit {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=LEVELS, nargs="?", default="fast")
    args = parser.parse_args()

    changed = changed_paths() if args.level != "full" else []
    chosen = selected(args.level, changed)

    print(f"verify [{args.level}] -- {len(chosen)} step(s)")
    if args.level != "full":
        print(f"  {len(changed)} changed path(s) considered")
    if not chosen:
        print("nothing to check for these changes")
        return 0

    results: list[tuple[Step, str, str]] = []
    for step in chosen:
        status, detail = run(step, args.level)
        results.append((step, status, detail))

    print("\n" + "=" * 60)
    failed = False
    for step, status, detail in results:
        suffix = f"  ({detail})" if detail else ""
        print(f"  {status}  {step.name}{suffix}")
        if status == FAIL:
            failed = True
        elif status == SKIP and args.level == "full":
            # An unrunnable check is not a passed check.
            failed = True
    print("=" * 60)
    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
