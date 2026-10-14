"""The PreToolUse hook blocks what it claims to, allows ordinary work, and is wired.

`scripts/claude_guard.py` is run exactly as Claude Code runs it: a JSON payload
on stdin, exit 2 to block. Each blocked case is a command that destroys work or
changes a protected contract; each allowed case is something an agent does many
times a session, because a guard that blocks ordinary reads gets disabled.

The last test pins what the guard does *not* do. It matches command text, so an
indirect write gets through. That is stated rather than hidden: the
server-side guarantees live in branch rules and the CI guardrail.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .governance_support import ROOT

GUARD = ROOT / "scripts" / "claude_guard.py"


def guard(mode: str, tool_input: dict) -> int:
    payload = json.dumps({"tool_name": "x", "tool_input": tool_input})
    return subprocess.run([sys.executable, str(GUARD), mode], input=payload,
                          capture_output=True, text=True).returncode


@pytest.mark.parametrize("command", [
    "git reset --hard HEAD~1",
    "git reset --soft HEAD && git reset --hard",
    "git checkout -- backend/app/main.py",
    "git restore backend/app/main.py",
    "git clean -fd",
    "git push --force origin master",
    "git push -f origin master",
    "git branch -D feature",
    "docker compose down -v",
    "docker compose -f docker-compose.yml down --volumes",
    "docker volume rm appbi_pgdata",
    "docker volume prune -f",
    "docker system prune -a --volumes",
    "./run.sh --clean",
    "python scripts/rotate-kek.py generate",
    "python scripts/rotate-kek.py rotate --new-key abc",
    "python scripts/build-connector-lock.py",
    "python scripts/vendor-engine.py lock",
])
def test_destructive_commands_are_blocked(command):
    assert guard("bash", {"command": command}) == 2, command


@pytest.mark.parametrize("command", [
    # The bypasses a Write/Edit-only hook used to leave open.
    "sed -i 's/0.59.1/0.63.0/' engine-lock.json",
    "echo '{}' > connector-lock.json",
    "cat new.yaml >> compatibility.yaml",
    "python -c \"open('engine-lock.json','w').write('{}')\"",
    "cp /tmp/lock.json connector-lock.json",
    "mv engine-lock.json engine-lock.old",
    "rm compatibility.yaml",
    "git checkout origin/other -- engine-lock.json",
    "jq '.airbyte.version=\"1.0\"' engine-lock.json | tee engine-lock.json",
    "Set-Content -Path engine-lock.json -Value '{}'",
    "Remove-Item compatibility.yaml",
    # Secrets.
    "echo SECRET_ENCRYPTION_KEY=$(openssl rand -base64 32) >> .env",
    "sed -i 's/^SECRET_ENCRYPTION_KEY=.*/SECRET_ENCRYPTION_KEY=x/' .env",
    "cp .env.example .env",
    "rm -rf secrets/",
    "Set-Content .env.production 'x'",
])
def test_shell_writes_to_protected_files_are_blocked(command):
    assert guard("bash", {"command": command}) == 2, command


@pytest.mark.parametrize("command", [
    "git status",
    "git diff engine-lock.json",
    "cat connector-lock.json",
    "python scripts/build-connector-lock.py --verify",
    "git log --oneline -- compatibility.yaml",
    "grep -n version engine-lock.json",
    "python -m pytest backend/tests -q > /tmp/out.txt",
    "git push --force-with-lease origin feature",
    "git restore --staged backend/app/main.py",
    "docker compose down",
    "docker compose up -d",
    "npm run build 2>&1 | tail -5",
    "sed -i 's/a/b/' frontend/src/lib/format.ts",
    "cp .env.example /tmp/review.txt",
    "ls secrets",
    # Reads that redirect their *output* somewhere harmless.
    "git diff --stat HEAD -- engine-lock.json compatibility.yaml > /tmp/diff.txt",
    "docker compose -f docker-compose.yml config -q >/dev/null 2>/tmp/err; cat connector-lock.json",
    "grep version engine-lock.json 2>&1 | head",
])
def test_ordinary_commands_are_allowed(command):
    assert guard("bash", {"command": command}) == 0, command


@pytest.mark.parametrize("tool_input", [
    {"file_path": "engine-lock.json"},
    {"file_path": "D:\\Appv2\\appbi-pipeline\\connector-lock.json"},
    {"file_path": "/repo/compatibility.yaml"},
    {"file_path": "/repo/.env"},
    {"file_path": "/repo/.env.production"},
    {"file_path": "/repo/secrets/gcp-sa.json"},
    {"notebook_path": "/repo/secrets/explore.ipynb"},
])
def test_file_edits_to_protected_files_are_blocked(tool_input):
    assert guard("contracts", tool_input) == 2, tool_input


@pytest.mark.parametrize("tool_input", [
    {"file_path": "/repo/backend/app/main.py"},
    {"file_path": "/repo/.env.example"},
    {"file_path": "/repo/.env.production.example"},
    {"file_path": "/repo/docs/engine-lock.json.md"},
    {"notebook_path": "/repo/notebooks/a.ipynb"},
])
def test_ordinary_file_edits_are_allowed(tool_input):
    assert guard("contracts", tool_input) == 0, tool_input


def test_malformed_payload_and_unknown_mode_do_not_block():
    """A hook that crashes on unexpected input blocks every tool call."""
    assert subprocess.run([sys.executable, str(GUARD), "bash"], input="not json",
                          capture_output=True, text=True).returncode == 0
    assert guard("unknown", {"command": "git reset --hard"}) == 0


def test_settings_wire_both_guards_to_every_tool_that_can_write():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    wiring = {
        entry["matcher"]: [h["command"] for h in entry["hooks"]]
        for entry in settings["hooks"]["PreToolUse"]
    }
    import re

    def wired(tool: str, mode: str) -> bool:
        return any(re.fullmatch(m, tool) and any(f"claude_guard.py\" {mode}" in c for c in cmds)
                   for m, cmds in wiring.items())

    for tool in ("Bash", "PowerShell"):
        assert wired(tool, "bash"), tool
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        assert wired(tool, "contracts"), tool


def test_known_limit_indirect_writes_are_not_caught():
    """Documented, not a bug: the hook matches text. A script that writes the
    lock without naming it on the command line gets through, which is why the
    CI guardrail -- not this hook -- is what refuses a lock change."""
    assert guard("bash", {"command": "python /tmp/innocent.py"}) == 0
