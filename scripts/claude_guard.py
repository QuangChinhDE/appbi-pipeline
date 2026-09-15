#!/usr/bin/env python3
"""PreToolUse guards for Claude Code. Two of them, both deliberately narrow.

    python scripts/claude_guard.py bash        # matcher: Bash
    python scripts/claude_guard.py contracts   # matcher: Write|Edit

Reads the hook payload on stdin. Exit 0 allows the call; exit 2 blocks it and
returns the message on stderr to the model.

Why these exist as hooks rather than prose or tests:

`bash`   -- the commands it blocks destroy uncommitted work or rewrite published
            history. There is no diff to review afterwards and no CI lane that
            can fail, because the damage happens before anything is committed.
            A rule in a file is advice; this is the only form that arrives in
            time.

`contracts` -- a hand-edited `engine-lock.json`, `connector-lock.json` or
            `compatibility.yaml` is internally consistent, so no test and no CI
            job can tell a deliberate engine migration from an incidental bump
            made to quiet an error. The only moment the distinction is legible
            is the moment of the edit, when the task's intent is still known.

Cost: one short-lived Python process per Bash/Write/Edit call, no I/O beyond
stdin. Nothing else is hooked; checks that a test or CI can run belong there.
"""

from __future__ import annotations

import json
import re
import sys

ALLOW, BLOCK = 0, 2

#: Commands that lose work irreversibly. Each pattern is anchored at a command
#: boundary so an unrelated string containing the words does not trip it.
DANGEROUS = [
    (r"\bgit\s+reset\s+(--\S+\s+)*--hard\b",
     "`git reset --hard` discards uncommitted work permanently."),
    (r"\bgit\s+checkout\s+(--\S+\s+)*--\s",
     "`git checkout -- <path>` discards uncommitted changes to those files."),
    (r"\bgit\s+restore\b(?!.*--staged\b)",
     "`git restore` without --staged discards uncommitted changes."),
    (r"\bgit\s+clean\b.*-\w*[fd]",
     "`git clean -f/-d` deletes untracked files, including local .env and notes."),
    (r"\bgit\s+push\b.*(--force\b(?!-with-lease)|(?<![\w-])-f\b)",
     "`git push --force` rewrites published history. Use --force-with-lease, "
     "and only when the user asked for it."),
    (r"\bgit\s+branch\s+(-D|--delete\s+--force)\b",
     "`git branch -D` deletes an unmerged branch."),
    (r"\bdocker\s+(compose\s+)?down\b.*(-v\b|--volumes\b)",
     "`down -v` deletes the Postgres and engine volumes -- every workspace, "
     "credential and run in this installation."),
    (r"\bdocker\s+volume\s+(rm|prune)\b",
     "This removes installation data volumes."),
    (r"\brun\.(sh|ps1)\b.*--clean\b",
     "`--clean` deletes the databases. Confirm with the user first."),
]

#: Contracts that describe what a release was built and certified against.
CONTRACTS = ("engine-lock.json", "connector-lock.json", "compatibility.yaml")

CONTRACT_MESSAGE = """Blocked: {name} is a protected contract, not a config file.

See .claude/rules/engine-boundary.md. These files record what this product
version was built and certified against; a bump made in passing makes the
product claim a certification nobody ran.

Proceed only if the user's task is explicitly an engine or connector version
change. If it is, say so to the user, state the compatibility and deployment
impact, and ask them to confirm -- they can then allow the edit. If it is not,
the change you want almost certainly belongs somewhere else.

Lock files are generated, not hand-written: connector-lock.json comes from
scripts/build-connector-lock.py."""


def payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def guard_bash(data: dict) -> int:
    command = (data.get("tool_input") or {}).get("command", "")
    if not command:
        return ALLOW
    for pattern, why in DANGEROUS:
        if re.search(pattern, command):
            print(
                f"Blocked: {why}\n\n"
                "Preserving local uncommitted work is a project rule. If the user "
                "explicitly asked for this, tell them it is blocked by a project "
                "hook and let them run it themselves.",
                file=sys.stderr,
            )
            return BLOCK
    return ALLOW


def guard_contracts(data: dict) -> int:
    path = (data.get("tool_input") or {}).get("file_path", "")
    normalized = path.replace("\\", "/")
    for name in CONTRACTS:
        if normalized.endswith("/" + name) or normalized == name:
            print(CONTRACT_MESSAGE.format(name=name), file=sys.stderr)
            return BLOCK
    return ALLOW


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = payload()
    if mode == "bash":
        return guard_bash(data)
    if mode == "contracts":
        return guard_contracts(data)
    return ALLOW


if __name__ == "__main__":
    raise SystemExit(main())
