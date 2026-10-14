#!/usr/bin/env python3
"""Check that the shared agent configuration is internally consistent.

    python scripts/check-agent-config.py            # this repository
    python scripts/check-agent-config.py --root DIR # another checkout (tests)

Why a script: agent instructions rot quietly. A rule that names a file which
was renamed still reads as authoritative; a `paths:` glob that matches nothing
means the rule never loads; a malformed settings.json silently disables every
hook. None of that fails a product test, so it has to fail here.

Checks, each reported with the file it came from:

  imports   .claude/CLAUDE.md imports AGENTS.md, and AGENTS.md exists
  refs      every repository path named in backticks exists (globs must match)
  rules     every rule has `paths:` frontmatter and every glob matches a file
  skills    every skill folder has SKILL.md with a matching `name` and a description
  settings  settings.json parses, uses known hook events, valid matcher regexes,
            and each hook command points at a script that exists
  tracked   nothing outside the allow-list is tracked under .claude/, and no
            tracked file is an env file, a secrets dir, local settings, evidence
            or a service-account key
  secrets   no secret-shaped string in the tracked governance files

Exit 0 when every check passes, 1 otherwise. Read-only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path

#: Codex and similar agents truncate AGENTS.md beyond this size.
AGENTS_MAX_BYTES = 32 * 1024

#: The only files under .claude/ that may be tracked. Mirrors .gitignore.
CLAUDE_ALLOWED = (
    ".claude/CLAUDE.md",
    ".claude/settings.json",
    ".claude/rules/*.md",
    ".claude/skills/*/SKILL.md",
)

#: Tracked files that must never exist, wherever they are.
FORBIDDEN_TRACKED = (
    (re.compile(r"(^|/)\.env(\.[^/]+)?(?<!\.example)$"), "environment file"),
    (re.compile(r"(^|/)secrets/"), "secrets directory"),
    (re.compile(r"(^|/)settings\.local\.json$"), "personal agent settings"),
    (re.compile(r"(^|/)evidence-[^/]*\.json$"), "run evidence"),
    (re.compile(r"service[-_]?account[^/]*\.json$", re.I), "service-account key"),
    (re.compile(r"(^|/)\.claude/(projects|memory)/"), "agent memory"),
)

SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\"private_key\"\s*:"),
)

HOOK_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification",
    "UserPromptSubmit", "Stop", "SubagentStop", "SubagentStart", "PreCompact",
    "SessionStart", "SessionEnd", "PermissionRequest",
}

#: Top-level entries a backticked token must start with to count as a path.
#: Anything else in backticks (`ctx.require(...)`, `t()`) is code, not a path.
PATH_ROOTS = (
    "AGENTS.md", "README.md", ".claude/", ".github/", "backend/", "frontend/",
    "scripts/", "deploy/", "docs/", ".sdlc/", "qa/", "engine-lock.json", "connector-lock.json",
    "compatibility.yaml", "docker-compose", "run.sh", "run.ps1", "pytest.ini",
)

#: Paths documents name on purpose although they never exist in a clone.
INTENTIONALLY_ABSENT = {
    ".claude/settings.local.json",
    "qa/",  # named in security.md as the deleted tree an old test lived in
}

BACKTICK = re.compile(r"`([^`\n]+)`")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, check: str, where: str, message: str) -> None:
        self.errors.append(f"[{check}] {where}: {message}")


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.split("\0") if p]


def glob_matches(pattern: str, files: list[str]) -> bool:
    """`**`-aware match of a repository glob against tracked paths."""
    regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    regex = regex.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    compiled = re.compile(regex + r"(?:/.*)?$")
    return any(compiled.match(f) for f in files)


def governance_docs(root: Path) -> list[Path]:
    docs = [root / "AGENTS.md", root / ".claude" / "CLAUDE.md"]
    docs += sorted((root / ".claude" / "rules").glob("*.md"))
    docs += sorted((root / ".claude" / "skills").glob("*/SKILL.md"))
    docs += sorted((root / "docs" / "features").glob("**/*.md"))
    return [d for d in docs if d.is_file()]


def frontmatter(text: str) -> dict[str, object] | None:
    """Minimal YAML frontmatter reader: `key: value` and `key:` + `- item` lists."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    data: dict[str, object] = {}
    key = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        item = re.match(r"\s+-\s+(.*)$", line)
        if item and key:
            value = data.setdefault(key, [])
            if isinstance(value, list):
                value.append(item.group(1).strip().strip("\"'"))
            continue
        pair = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if pair:
            key = pair.group(1)
            data[key] = pair.group(2).strip().strip("\"'") or []
    return data


def check_imports(root: Path, report: Report) -> None:
    agents = root / "AGENTS.md"
    claude = root / ".claude" / "CLAUDE.md"
    if not agents.is_file():
        report.fail("imports", "AGENTS.md", "missing")
        return
    if agents.stat().st_size > AGENTS_MAX_BYTES:
        report.fail("imports", "AGENTS.md", f"larger than {AGENTS_MAX_BYTES} bytes")
    if not claude.is_file():
        report.fail("imports", ".claude/CLAUDE.md", "missing")
        return
    imports = re.findall(r"^@(\S+)", claude.read_text(encoding="utf-8"), re.M)
    if "../AGENTS.md" not in imports:
        report.fail("imports", ".claude/CLAUDE.md", "does not import @../AGENTS.md")
    for target in imports:
        if not (claude.parent / target).resolve().is_file():
            report.fail("imports", ".claude/CLAUDE.md", f"@{target} does not resolve")


def check_refs(root: Path, files: list[str], report: Report) -> None:
    # Only tracked files count: a path that exists on one machine but not in a
    # fresh clone is exactly the reference this check exists to catch.
    present = set(files)
    dirs = {f.rsplit("/", i)[0] for f in files for i in range(1, f.count("/") + 1)}
    for doc in governance_docs(root):
        rel = doc.relative_to(root).as_posix()
        for token in BACKTICK.findall(doc.read_text(encoding="utf-8")):
            path = token.strip().split()[0].rstrip(".,:;")
            path = path.split("::")[0]
            if not path.startswith(PATH_ROOTS) or "<" in path or "…" in path:
                continue
            if path in INTENTIONALLY_ABSENT:
                continue
            if "*" in path or "?" in path:
                if not glob_matches(path, files):
                    report.fail("refs", rel, f"glob `{path}` matches no tracked file")
                continue
            target = path.rstrip("/")
            if target in present or target in dirs:
                continue
            report.fail("refs", rel, f"`{path}` does not exist")


def check_rules(root: Path, files: list[str], report: Report) -> None:
    rules = sorted((root / ".claude" / "rules").glob("*.md"))
    if not rules:
        report.fail("rules", ".claude/rules", "no rule files")
    for rule in rules:
        rel = rule.relative_to(root).as_posix()
        meta = frontmatter(rule.read_text(encoding="utf-8"))
        if meta is None:
            report.fail("rules", rel, "no frontmatter")
            continue
        if "globs" in meta:
            report.fail("rules", rel, "uses `globs:`, which Claude Code does not read; use `paths:`")
        paths = meta.get("paths")
        if not isinstance(paths, list) or not paths:
            report.fail("rules", rel, "no `paths:` list -- the rule would load everywhere")
            continue
        for pattern in paths:
            if not glob_matches(pattern, files):
                report.fail("rules", rel, f"paths glob `{pattern}` matches no tracked file")


def check_skills(root: Path, report: Report) -> None:
    folders = sorted(p for p in (root / ".claude" / "skills").glob("*") if p.is_dir())
    if not folders:
        report.fail("skills", ".claude/skills", "no skills")
    for folder in folders:
        skill = folder / "SKILL.md"
        rel = skill.relative_to(root).as_posix()
        if not skill.is_file():
            report.fail("skills", rel, "missing")
            continue
        meta = frontmatter(skill.read_text(encoding="utf-8")) or {}
        if meta.get("name") != folder.name:
            report.fail("skills", rel, f"`name` must be {folder.name!r}")
        if not meta.get("description"):
            report.fail("skills", rel, "no `description` -- it would never be selected")


def check_settings(root: Path, report: Report) -> None:
    path = root / ".claude" / "settings.json"
    rel = ".claude/settings.json"
    if not path.is_file():
        report.fail("settings", rel, "missing -- no hook is wired")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.fail("settings", rel, f"invalid JSON: {exc}")
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        report.fail("settings", rel, "no `hooks` object")
        return
    for event, entries in hooks.items():
        if event not in HOOK_EVENTS:
            report.fail("settings", rel, f"unknown hook event {event!r}")
        if not isinstance(entries, list):
            report.fail("settings", rel, f"{event} must be a list")
            continue
        for entry in entries:
            matcher = entry.get("matcher", "")
            try:
                re.compile(matcher)
            except re.error as exc:
                report.fail("settings", rel, f"{event} matcher {matcher!r}: {exc}")
            commands = entry.get("hooks")
            if not isinstance(commands, list) or not commands:
                report.fail("settings", rel, f"{event} {matcher!r} has no hooks")
                continue
            for hook in commands:
                if hook.get("type") != "command" or not hook.get("command"):
                    report.fail("settings", rel, f"{event} {matcher!r}: hook needs type=command and a command")
                    continue
                for script in re.findall(r"\$CLAUDE_PROJECT_DIR/([^\"'\s]+)", hook["command"]):
                    if not (root / script).is_file():
                        report.fail("settings", rel, f"hook script {script} does not exist")


def check_tracked(files: list[str], report: Report) -> None:
    for f in files:
        if f.startswith(".claude/") and not any(fnmatch.fnmatchcase(f, p) for p in CLAUDE_ALLOWED):
            report.fail("tracked", f, "tracked under .claude/ but not on the allow-list")
        for pattern, kind in FORBIDDEN_TRACKED:
            if pattern.search(f):
                report.fail("tracked", f, f"{kind} must not be tracked")


def check_secrets(root: Path, report: Report) -> None:
    for doc in governance_docs(root) + [root / ".claude" / "settings.json"]:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                report.fail("secrets", doc.relative_to(root).as_posix(),
                            "contains a secret-shaped string")


def run(root: Path) -> list[str]:
    report = Report()
    files = tracked_files(root)
    check_imports(root, report)
    check_refs(root, files, report)
    check_rules(root, files, report)
    check_skills(root, report)
    check_settings(root, report)
    check_tracked(files, report)
    check_secrets(root, report)
    return report.errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    errors = run(args.root.resolve())
    for line in errors:
        print(line)
    print("agent config: " + ("FAILED" if errors else "OK"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
