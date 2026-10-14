#!/usr/bin/env python3
"""PreToolUse guards for Claude Code. Two of them, both deliberately narrow.

    python scripts/claude_guard.py bash        # matcher: Bash|PowerShell
    python scripts/claude_guard.py contracts   # matcher: Write|Edit|MultiEdit|NotebookEdit

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

`bash` also refuses a shell command that names a protected file *and* writes
to something -- `sed -i`, a redirect, `tee`, `cp`/`mv`, `Set-Content` and the
like -- because a Write/Edit hook is pointless if the same edit goes through
the shell. It also refuses the scripts that regenerate a lock or a key.

What this is not: a sandbox. It matches command text, so a sufficiently
indirect command (a script that writes the file, an encoded payload) gets
past it. It errs toward blocking: a commit message that merely quotes a
dangerous command is refused too. The guarantees that matter are enforced
server-side -- branch rules and `scripts/guardrail.py` in CI -- and this hook
exists to stop the honest mistake before it happens.

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
    # `docker compose -f x.yml down -v` and `docker-compose down -v` too: global
    # flags may sit between the binary and the subcommand.
    (r"\bdocker(\s+|-)(compose\b.*\s)?down\b.*(\s-v\b|--volumes\b)",
     "`down -v` deletes the Postgres and engine volumes -- every workspace, "
     "credential and run in this installation."),
    (r"\bdocker\s+volume\s+(rm|prune)\b",
     "This removes installation data volumes."),
    (r"\brun\.(sh|ps1)\b.*--clean\b",
     "`--clean` deletes the databases. Confirm with the user first."),
    (r"\bdocker\s+system\s+prune\b.*--volumes\b|\bdocker\s+compose\b.*\brm\b.*(-v\b|--volumes\b)",
     "This removes installation data volumes."),
    (r"\brotate-kek\.py\s+(generate|rotate)\b",
     "Rotating or generating the key-encryption key is an operator action; a "
     "wrong key makes every stored credential unreadable."),
    (r"\b(build-connector-lock|pin-engine-connectors)\.py\b(?!.*--verify\b)"
     r"|\bvendor-engine\.py\s+(lock|connectors)\b",
     "This regenerates an engine or connector lock. Only an explicit engine or "
     "connector change may run it."),
]

#: Files a shell command must not write. `.env.example` and
#: `.env.production.example` are templates and stay editable.
PROTECTED_FILES = re.compile(
    r"engine-lock\.json|connector-lock\.json|compatibility\.yaml"
    r"|(?<![\w.-])\.env(?:\.(?!example\b|production\.example\b)[\w-]+)*(?![\w.-])"
    r"|(?<![\w-])secrets[/\\]"
)

#: Ways a shell command writes, moves or deletes a file. Matched anywhere in the
#: command once a protected file is named in it -- deliberately coarse.
WRITES = re.compile(
    r"(?<![<>=!-])>{1,2}(?![=&])"
    r"|\bsed\b[^|;&]*\s-\w*i|\bperl\b[^|;&]*\s-\w*i|\byq\b[^|;&]*\s-i\b|\btee\b"
    r"|\b(cp|mv|rm|truncate|dd|install|ln|touch)\s"
    r"|\b(Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item|Clear-Content|Rename-Item)\b"
    r"|\[(System\.)?IO\.File\]::(Write|Append|Delete|Move|Copy)"
    r"|\bwrite_text\b|\bwrite_bytes\b|\bopen\([^)]*['\"][wax]|\bos\.(remove|rename|replace|unlink)\b|\bshutil\."
    r"|\bgit\s+(checkout|restore|apply|am|cherry-pick|mv|rm|stash)\b|\bpatch\b"
    r"|\bopenssl\s+rand\b",
    re.I,
)

#: Contracts that describe what a release was built and certified against.
CONTRACTS = ("engine-lock.json", "connector-lock.json", "compatibility.yaml")

SECRET_MESSAGE = """Blocked: {name} holds live secrets.

`SECRET_ENCRYPTION_KEY` and the auth secrets in .env encrypt every stored
credential; overwriting one makes them all unreadable, and nothing in git can
bring them back. Ask the user to make this change themselves."""

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
    target = PROTECTED_FILES.search(command)
    if target and WRITES.search(command):
        name = target.group(0).rstrip("/\\")
        message = CONTRACT_MESSAGE if name in CONTRACTS else SECRET_MESSAGE
        print(message.format(name=name) + "\n\n(The command names this file and "
              "writes, moves or deletes something; read-only commands are allowed.)",
              file=sys.stderr)
        return BLOCK
    return ALLOW


def guard_contracts(data: dict) -> int:
    tool_input = data.get("tool_input") or {}
    # Write/Edit/MultiEdit carry file_path; NotebookEdit carries notebook_path.
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    normalized = path.replace("\\", "/")
    base = normalized.rsplit("/", 1)[-1]
    for name in CONTRACTS:
        if base == name:
            print(CONTRACT_MESSAGE.format(name=name), file=sys.stderr)
            return BLOCK
    if (base == ".env" or base.startswith(".env.")) and not base.endswith(".example") \
            or re.search(r"(^|/)secrets/", normalized):
        print(SECRET_MESSAGE.format(name=base), file=sys.stderr)
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
