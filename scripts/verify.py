#!/usr/bin/env python3
"""The one verification entry point, at three levels of cost.

    python scripts/verify.py fast    # coding loop: only what the diff touches
    python scripts/verify.py task    # before claiming a task is done
    python scripts/verify.py full    # everything CI runs

Why a script instead of a checklist: a checklist is a thing a human remembers.
The commands below are the same ones `.github/workflows/ci.yml` runs -- every
CI job has a step here, including the Overview golden flows -- so a green local
`full` means the same thing a green CI run means, and a check added in one
place is not silently missing from the other. `backend/tests/test_verify.py`
fails if the two drift apart.

`fast` and `task` select steps from the files changed against the merge base
(plus anything uncommitted). `full` runs everything.

Every step ends in one of five states:

    PASS        it ran and succeeded
    FAIL        it ran and failed
    SKIPPED     not run, and this level does not require it (`fast` only)
    UNVERIFIED  required, but could not run -- tooling missing, or nothing
                covers a changed path
    BLOCKED     the change guardrail refused the diff (scripts/guardrail.py)

What a non-PASS means depends on the level, because the levels answer
different questions:

    fast   "is what I am editing still sound?" -- a missing toolchain is
           SKIPPED and tolerated, so it never blocks the coding loop.
    task   "may I tell the user this is done?" -- only if every selected step
           is PASS. UNVERIFIED is a check nobody performed, and `done` would
           then be a claim about evidence that does not exist.
    full   "is the repository releasable?"     -- the same, across everything.

Two ways a task used to pass with nothing checked are closed:

  * a changed path that no step covers (a script, a workflow, a lock file)
    is UNVERIFIED at `task`, not "nothing to check";
  * `task` with no change at all is UNVERIFIED: there is nothing to have
    verified, so there is nothing to call done.

Exit code is 0 only when every selected step is PASS -- except at `fast`,
where SKIPPED is allowed.
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

PASS, FAIL, SKIPPED, UNVERIFIED, BLOCKED = "PASS", "FAIL", "SKIPPED", "UNVERIFIED", "BLOCKED"


@dataclass
class Step:
    name: str
    command: list[str]
    cwd: Path
    #: Path prefixes that make this step relevant. Empty means always relevant.
    triggers: tuple[str, ...] = ()
    #: Executable that must exist for the step to be runnable at all.
    requires: str | None = None
    #: Directory that must exist (node_modules, a venv) for the step to run.
    requires_dir: Path | None = None
    notes: str = ""
    #: Exit codes with a meaning other than FAIL.
    exit_states: dict[int, str] = field(default_factory=dict)


LEVELS = ("fast", "task", "full")

#: Paths whose change needs no check beyond the guardrail: prose a reader
#: reviews, not code a test can exercise. Everything else must be covered by
#: at least one step, or `task` reports it UNVERIFIED.
REVIEW_ONLY = ("README.md", "docs/huong-dan-pipeline/")

#: Everything the backend suite reads. It holds structural tests over frontend
#: source, deployment files and the governance system, not only backend code.
PYTEST_TRIGGERS = (
    "backend/", "frontend/src/", "frontend/e2e/", "scripts/", ".claude/", ".github/",
    "AGENTS.md", "docs/features/", ".gitignore", "pytest.ini", "docker-compose",
    "deploy/", "run.sh", "run.ps1", ".env.example", ".env.production.example",
    "engine-lock.json", "connector-lock.json", "compatibility.yaml",
)


def steps() -> list[Step]:
    py = sys.executable
    return [
        Step(
            name="governance: change guardrail",
            command=[py, str(ROOT / "scripts" / "guardrail.py")],
            cwd=ROOT,
            # Always relevant: it is what decides whether the diff is allowed.
            exit_states={1: BLOCKED, 2: UNVERIFIED},
        ),
        Step(
            name="backend: undefined names (pyflakes)",
            command=[py, "-m", "pyflakes", "app"],
            cwd=BACKEND,
            triggers=("backend/",),
        ),
        Step(
            name="backend: migration chain has one head",
            command=[py, str(ROOT / "scripts" / "check-migration-heads.py")],
            cwd=ROOT,
            triggers=("backend/migrations/", "backend/app/models/"),
        ),
        Step(
            name="backend: pytest",
            command=[py, "-m", "pytest", "tests", "-q"],
            cwd=BACKEND,
            triggers=PYTEST_TRIGGERS,
        ),
        Step(
            name="deploy: alert rules parse",
            command=[py, str(ROOT / "scripts" / "check-alert-rules.py")],
            cwd=ROOT,
            triggers=("deploy/monitoring/",),
        ),
        Step(
            name="governance: agent config",
            command=[py, str(ROOT / "scripts" / "check-agent-config.py")],
            cwd=ROOT,
            triggers=("AGENTS.md", ".claude/", "docs/features/", "scripts/",
                      ".github/", ".gitignore"),
        ),
        Step(
            name="frontend: typecheck",
            command=["npx", "tsc", "--noEmit"],
            cwd=FRONTEND,
            triggers=("frontend/",),
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
            requires="npm",
            requires_dir=FRONTEND / "node_modules",
            notes="run `npm ci` in frontend/",
        ),
        Step(
            name="frontend: overview golden flows",
            # CI's `e2e` job. Runs after the build above, which it serves.
            command=["npm", "run", "test:e2e:overview"],
            cwd=FRONTEND,
            triggers=("frontend/", "backend/tests/fixtures/", "backend/app/schemas/"),
            requires="npm",
            requires_dir=FRONTEND / "node_modules" / "@playwright",
            notes="run `npm ci` and `npx playwright install chromium` in frontend/",
        ),
    ]


#: Steps too slow to be worth running on every edit, even when the diff touches
#: their area. `fast` skips them; `task` and `full` do not.
SLOW_IN_FAST = {"frontend: build", "frontend: lint", "frontend: overview golden flows"}


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_paths() -> list[str]:
    """Files changed against origin/master, plus anything uncommitted."""
    out: set[str] = set()
    out.update(git_lines("diff", "--name-only", "HEAD"))
    out.update(git_lines("diff", "--name-only", "--cached"))
    out.update(git_lines("ls-files", "--others", "--exclude-standard"))
    merge_base = git_lines("merge-base", "HEAD", "origin/master")
    if merge_base:
        out.update(git_lines("diff", "--name-only", merge_base[0], "HEAD"))
    return sorted(out)


def relevant(step: Step, changed: list[str]) -> bool:
    return not step.triggers or any(
        path.startswith(trigger) for path in changed for trigger in step.triggers
    )


def selected(level: str, changed: list[str]) -> list[Step]:
    chosen = []
    for step in steps():
        if level == "full":
            chosen.append(step)
            continue
        if level == "fast" and step.name in SLOW_IN_FAST:
            continue
        if relevant(step, changed):
            chosen.append(step)
    return chosen


def uncovered(changed: list[str]) -> list[str]:
    """Changed paths no triggered step exercises (the always-on guardrail
    does not count: it judges the diff, it does not test the code)."""
    covering = [s for s in steps() if s.triggers]
    return [
        path for path in changed
        if not path.startswith(REVIEW_ONLY)
        and not any(path.startswith(t) for s in covering for t in s.triggers)
    ]


def run(step: Step, level: str) -> tuple[str, str]:
    missing = SKIPPED if level == "fast" else UNVERIFIED
    if step.requires and not shutil.which(step.requires):
        return missing, f"{step.requires} not on PATH"
    if step.requires_dir and not step.requires_dir.is_dir():
        return missing, f"missing {step.requires_dir.relative_to(ROOT)}; {step.notes}"

    print(f"\n--- {step.name}")
    print(f"    $ {' '.join(step.command)}   (in {step.cwd.relative_to(ROOT) or '.'})")
    result = subprocess.run(step.command, cwd=step.cwd, shell=(sys.platform == "win32"))
    if result.returncode == 0:
        return PASS, ""
    state = step.exit_states.get(result.returncode, FAIL)
    return state, f"exit {result.returncode}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=LEVELS, nargs="?", default="fast")
    args = parser.parse_args(argv)

    changed = changed_paths() if args.level != "full" else []
    chosen = selected(args.level, changed)
    gaps = uncovered(changed) if args.level != "full" else []

    print(f"verify [{args.level}] -- {len(chosen)} step(s)")
    if args.level != "full":
        print(f"  {len(changed)} changed path(s) considered")

    results: list[tuple[str, str, str]] = []
    if args.level == "task" and not changed:
        results.append(("change under verification", UNVERIFIED,
                        "no change against origin/master; nothing to call done"))
    for step in chosen:
        status, detail = run(step, args.level)
        results.append((step.name, status, detail))
    for path in gaps:
        results.append((f"coverage: {path}", SKIPPED if args.level == "fast" else UNVERIFIED,
                        "no verification step covers this path"))

    print()
    print("=" * 60)
    tolerated = {PASS, SKIPPED} if args.level == "fast" else {PASS}
    failed = False
    for name, status, detail in results:
        suffix = f"  ({detail})" if detail else ""
        print(f"  {status:<10} {name}{suffix}")
        if status not in tolerated:
            failed = True
    print("=" * 60)

    not_run = [r for r in results if r[1] in (SKIPPED, UNVERIFIED)]
    if not_run:
        print(f"{len(not_run)} item(s) NOT verified here:")
        for name, status, detail in not_run:
            print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
        if args.level == "fast":
            print("  `task` treats these as failures; fix the toolchain or add "
                  "coverage before reporting done.")

    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
