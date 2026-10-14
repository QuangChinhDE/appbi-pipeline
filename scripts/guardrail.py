#!/usr/bin/env python3
"""Diff-aware change guardrail: what a change touches, how risky it is, and
whether it weakens anything that protects the product.

    python scripts/guardrail.py                       # working tree vs merge base
    python scripts/guardrail.py --base SHA --head SHA # an exact commit range (CI)
    python scripts/guardrail.py --json

Verdicts, and exit codes:

    PASS        0  nothing protected was weakened; required tests exist
    BLOCK       1  a protected contract changed, or a gate was removed or
                   weakened, without owner approval for this exact commit
    UNVERIFIED  2  the guardrail could not establish the facts it needs
                   (unresolvable base, a required test that does not exist)

Why it exists: `verify.py` answers "do the checks pass?". It cannot answer "is
this diff the kind of change that is allowed to make them pass?" -- a deleted
test, a CI job that stopped running, a lock file bumped to quiet an error all
leave a green run behind. Those are only visible by comparing base and head.

Trust: in CI this script is run *from the merge base* (`git show BASE:...`)
and reads head only as data, so a pull request that edits it cannot change
the rules it is judged by. Locally it runs from the working tree and is
advisory.

Owner approval: a BLOCK finding becomes APPROVED only when
APPBI_PROTECTION_APPROVAL_SHA equals the full head commit SHA. In CI that value
comes from a repository variable, which only a repository admin can set, and it
stops matching as soon as another commit is pushed. The working tree has no
SHA, so local runs can never be approved -- a deliberate protection change is
judged in CI, on the exact commit that will merge.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKTREE = "WORKTREE"

PASS, BLOCK, UNVERIFIED = "PASS", "BLOCK", "UNVERIFIED"
EXIT = {PASS: 0, BLOCK: 1, UNVERIFIED: 2}
RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}

#: What AGENTS.md requires written down at each level.
DOCS_REQUIRED = {
    "Low": "none",
    "Medium": "a plan in the task or PR description: files, tests, regression surface",
    "High": "docs/features/<slug>/{intent,spec,plan}.md from docs/features/_TEMPLATE/ before code, "
            "and an independent review",
}

# --------------------------------------------------------------------------
# Policy. Read from the base revision in CI, so a PR cannot loosen it.
# --------------------------------------------------------------------------

#: (area, risk, path globs, tests that must exist and are run for this area)
AREAS = [
    ("engine contracts", "High",
     ("engine-lock.json", "connector-lock.json", "compatibility.yaml"),
     ("backend/tests/test_base_connectors.py",)),
    ("engine boundary", "High",
     ("backend/app/adapters/**",),
     ("backend/tests/test_architecture_boundaries.py", "backend/tests/test_sync_state.py",
      "backend/tests/test_client_side_cursor.py")),
    ("connectors", "High",
     ("backend/app/connectors/**",),
     ("backend/tests/test_base_connectors.py", "backend/tests/test_connector_resources.py")),
    ("migrations and models", "High",
     ("backend/migrations/**", "backend/app/models/**", "backend/alembic.ini"),
     ()),
    ("auth, permissions, secrets", "High",
     ("backend/app/core/security.py", "backend/app/core/secrets.py",
      "backend/app/core/permissions.py", "backend/app/core/config.py",
      "backend/app/bootstrap.py", "backend/app/api/v1/auth*.py"),
     ("backend/tests/test_permissions.py", "backend/tests/test_admin_credentials.py",
      "backend/tests/test_nested_secrets.py")),
    ("error envelope", "High",
     ("backend/app/core/errors.py", "backend/app/main.py"),
     ("backend/tests/test_architecture_boundaries.py", "backend/tests/test_connector_error_text.py")),
    ("transform execution and release", "High",
     ("backend/app/transforms/**", "backend/app/transform_worker.py"),
     ("backend/tests/test_transform_safety.py", "backend/tests/test_release_rollback.py",
      "backend/tests/test_transform_commands.py")),
    ("run execution", "High",
     ("backend/app/worker.py",),
     ("backend/tests/test_auto_retry.py", "backend/tests/test_sync_state.py")),
    ("deployment topology and installers", "High",
     ("docker-compose*.yml", "deploy/**", "run.sh", "run.ps1", "backend/Dockerfile",
      "frontend/Dockerfile", ".env.example", ".env.production.example"),
     ("backend/tests/test_settings_reachable.py",)),
    ("protection system", "High",
     ("scripts/verify.py", "scripts/guardrail.py", "scripts/claude_guard.py",
      "scripts/check-agent-config.py", ".github/**", ".claude/settings.json",
      "AGENTS.md", ".claude/CLAUDE.md", ".gitignore",
      "backend/tests/test_agent_config.py", "backend/tests/test_guardrail.py",
      "backend/tests/test_claude_guard.py", "backend/tests/test_verify.py",
      "backend/tests/test_architecture_boundaries.py", "backend/tests/test_layering_debt.py",
      "backend/tests/governance_support.py", "backend/tests/test_sdlc_pilot.py"),
     ("backend/tests/test_agent_config.py", "backend/tests/test_guardrail.py",
      "backend/tests/test_claude_guard.py", "backend/tests/test_verify.py")),
    ("shared API contract", "High",
     ("backend/app/api/**", "backend/app/schemas/**", "frontend/src/lib/api.ts"),
     ("backend/tests/test_route_uniqueness.py", "backend/tests/test_architecture_boundaries.py")),
    ("domain services", "Medium",
     ("backend/app/services/**", "backend/app/*.py"),
     ()),
    # Before "shared frontend": translated text is Low (AGENTS.md) even though
    # the catalogue lives in src/lib/.
    ("i18n text", "Low",
     ("frontend/src/lib/i18n.ts",),
     ("backend/tests/test_i18n_coverage.py",)),
    ("shared frontend", "Medium",
     ("frontend/src/components/ui/**", "frontend/src/lib/**", "frontend/src/hooks/**"),
     ("backend/tests/test_architecture_boundaries.py",)),
    ("frontend", "Low",
     ("frontend/**",),
     ("backend/tests/test_i18n_coverage.py",)),
    ("agent guidance", "Low",
     (".claude/**", "docs/features/**"),
     ("backend/tests/test_agent_config.py",)),
    ("documentation", "Low",
     ("README.md", "docs/**"),
     ()),
]

#: Hand-edits here change what the release claims it was certified against.
PROTECTED_CONTRACTS = ("engine-lock.json", "connector-lock.json", "compatibility.yaml")

#: Deleting one of these removes a gate rather than changing it.
PROTECTION_CRITICAL = (
    "scripts/verify.py", "scripts/guardrail.py", "scripts/claude_guard.py",
    "scripts/check-agent-config.py", "scripts/check-migration-heads.py",
    "scripts/check-alert-rules.py", ".github/workflows/ci.yml",
    ".claude/settings.json", "AGENTS.md", ".claude/CLAUDE.md",
    "backend/tests/test_architecture_boundaries.py", "backend/tests/test_agent_config.py",
    "backend/tests/test_guardrail.py", "backend/tests/test_claude_guard.py",
    "backend/tests/test_verify.py", "backend/tests/test_layering_debt.py",
    "backend/tests/test_permissions.py", "backend/tests/test_sdlc_pilot.py",
)

#: CI jobs that must keep existing and keep their commands.
CI_WORKFLOW = ".github/workflows/ci.yml"

#: Applied migrations are history on every installed database.
MIGRATIONS = "backend/migrations/versions/"

ADAPTER_PROTOCOL = ("backend/app/adapters/base.py", "IntegrationEngineAdapter")
ENGINE_ENUM = ("backend/app/models/enums.py", "EngineType")
DEBT_REGISTER = ("backend/tests/test_layering_debt.py", "DEBT")

#: Anchored to where a marker takes effect -- a decorator, a statement, a YAML
#: key -- so a test that merely *mentions* one in a string is not flagged.
SKIP_MARKERS = {
    "tests": re.compile(
        r"^\s*@pytest\.mark\.(skip|skipif|xfail)\b|^\s*pytest\.(skip|xfail)\("
        r"|^\s*(test|it|describe)(\.describe)?\.(skip|fixme|only)\("
        r"|^\s*pytestmark\s*=.*\b(skip|xfail)"
    ),
    "workflows": re.compile(
        r"^\s*continue-on-error:\s*true|^\s*if:\s*(false|\$\{\{\s*false\s*\}\})\s*$"
    ),
}

# --------------------------------------------------------------------------


@dataclass
class Finding:
    verdict: str          # BLOCK or UNVERIFIED
    rule: str
    path: str
    reason: str
    remediation: str
    approved: bool = False


@dataclass
class Result:
    base: str
    head: str
    risk: str = "Low"
    areas: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    approval: str = "none"

    @property
    def verdict(self) -> str:
        live = [f for f in self.findings if not f.approved]
        if any(f.verdict == BLOCK for f in live):
            return BLOCK
        if any(f.verdict == UNVERIFIED for f in live):
            return UNVERIFIED
        return PASS


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout if result.returncode == 0 else ""

    def read(self, rev: str, path: str) -> str | None:
        if rev == WORKTREE:
            p = self.root / path
            return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        result = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=self.root,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.stdout if result.returncode == 0 else None

    def changes(self, base: str, head: str) -> list[tuple[str, str, str | None]]:
        """(status, path, old_path) for every changed file. Renames keep the old path."""
        if head == WORKTREE:
            raw = self.git("diff", "--name-status", "-M", base)
            raw += "".join(f"A\t{p}\n" for p in
                           self.git("ls-files", "--others", "--exclude-standard").splitlines())
        else:
            raw = self.git("diff", "--name-status", "-M", base, head)
        out = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if not parts or not parts[0]:
                continue
            status = parts[0][0]
            if status in "RC" and len(parts) == 3:
                out.append((status, parts[2], parts[1]))
            elif len(parts) >= 2:
                out.append((status, parts[1], None))
        return out

    def exists(self, rev: str, path: str) -> bool:
        return self.read(rev, path) is not None


def matches(path: str, patterns) -> bool:
    for pattern in patterns:
        if "**" in pattern:
            prefix = pattern.split("**")[0]
            if path.startswith(prefix) and fnmatch.fnmatchcase(path, pattern.replace("**", "*")):
                return True
        elif fnmatch.fnmatchcase(path, pattern) and path.count("/") == pattern.count("/"):
            return True
    return False


# ------------------------------------------------------------------ parsers

def test_names(source: str | None) -> set[str]:
    if not source:
        return set()
    return set(re.findall(r"^\s*(?:async\s+)?def\s+(test_\w+)", source, re.M)) | \
        set(re.findall(r"""^\s*test\(\s*['"]([^'"]+)['"]""", source, re.M))


def protocol_methods(source: str | None, cls: str) -> set[str]:
    if not source:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


def enum_members(source: str | None, cls: str) -> set[str]:
    if not source:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {t.id for n in node.body if isinstance(n, ast.Assign) for t in n.targets
                    if isinstance(t, ast.Name)}
    return set()


def debt_register(source: str | None, name: str) -> dict[str, int] | None:
    if not source:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            try:
                return {str(k): int(v) for k, v in ast.literal_eval(node.value).items()}
            except (ValueError, TypeError):
                return None
    return None


def ci_jobs(source: str | None) -> dict[str, set[str]]:
    """job id -> the set of `run:` commands it executes. Line-based on purpose:
    no YAML dependency, and the workflow's layout is regular."""
    jobs: dict[str, set[str]] = {}
    if not source:
        return jobs
    in_jobs, current, block = False, None, None
    for line in source.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if re.match(r"^\S", line):
            in_jobs = False
            continue
        job = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if job:
            current, block = job.group(1), None
            jobs[current] = set()
            continue
        if current is None:
            continue
        run = re.match(r"^\s+(?:-\s+)?run:\s*(.*)$", line)
        if run:
            value = run.group(1).strip()
            if value in ("|", ">", "|-", ">-"):
                block = len(line) - len(line.lstrip())
            else:
                jobs[current].add(value)
                block = None
            continue
        if block is not None:
            if line.strip() and len(line) - len(line.lstrip()) > block:
                jobs[current].add(line.strip())
            elif line.strip():
                block = None
    return jobs


def verify_steps(source: str | None) -> set[str]:
    return set(re.findall(r'^\s*name="([^"]+)"', source or "", re.M))


def hook_commands(source: str | None) -> set[str]:
    try:
        data = json.loads(source or "{}")
    except json.JSONDecodeError:
        return set()
    out = set()
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries or []:
            for hook in entry.get("hooks") or []:
                out.add(f"{event}|{entry.get('matcher', '')}|{hook.get('command', '')}")
    return out


def added_lines(repo: Repo, base: str, head: str, path: str) -> list[str]:
    args = ["diff", "-U0", base] + ([] if head == WORKTREE else [head]) + ["--", path]
    diff = repo.git(*args, check=False)
    if not diff and head == WORKTREE and not repo.exists(base, path):
        return (repo.read(WORKTREE, path) or "").splitlines()
    return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]


# ------------------------------------------------------------------ checks

def evaluate(repo: Repo, base: str, head: str, approval_sha: str | None) -> Result:
    head_sha = head if head == WORKTREE else repo.git("rev-parse", head).strip()
    result = Result(base=base, head=head_sha)
    changes = repo.changes(base, head)
    result.changed = sorted({p for _, p, _ in changes})

    def block(rule, path, reason, remediation):
        result.findings.append(Finding(BLOCK, rule, path, reason, remediation))

    def unverified(rule, path, reason, remediation):
        result.findings.append(Finding(UNVERIFIED, rule, path, reason, remediation))

    # Risk and the tests each touched area requires.
    required: set[str] = set()
    for status, path, old in changes:
        for candidate in filter(None, (path, old)):
            for area, risk, patterns, tests in AREAS:
                if matches(candidate, patterns):
                    if area not in result.areas:
                        result.areas.append(area)
                    if RISK_ORDER[risk] > RISK_ORDER[result.risk]:
                        result.risk = risk
                    required.update(tests)
                    break
    result.required_tests = sorted(required)
    for test in result.required_tests:
        if not repo.exists(head, test):
            unverified("required-test-missing", test,
                       "an area this diff touches names this test, and it does not exist at head",
                       "restore the test, or change the mapping in a separately approved guardrail change")

    for status, path, old in changes:
        # 1. Protected contracts: any change.
        if path in PROTECTED_CONTRACTS or old in PROTECTED_CONTRACTS:
            block("protected-contract", path,
                  "engine/connector lock or compatibility record changed",
                  "only an explicit engine or connector change may touch this; get owner approval "
                  "for the exact commit (APPBI_PROTECTION_APPROVAL_SHA)")

        # 2. Migration history: modifying or removing an existing revision.
        for p in filter(None, (path, old)):
            if p.startswith(MIGRATIONS) and p.endswith(".py") and status in "MDR" and repo.exists(base, p):
                block("migration-history", p,
                      "an existing migration was modified, renamed or deleted; installed databases "
                      "have already run it",
                      "add a new revision instead")
                break

        # 3. Gate files deleted.
        if status == "D" and path in PROTECTION_CRITICAL:
            block("gate-removed", path, "a protection-critical file was deleted",
                  "restore it; removing a gate needs owner approval for the exact commit")
        if status == "R" and old in PROTECTION_CRITICAL:
            block("gate-removed", old, f"a protection-critical file was renamed to {path}",
                  "keep the path; workflows and the guardrail refer to it by name")

        # 4. Tests deleted or removed.
        source = old or path
        if source.startswith(("backend/tests/", "frontend/e2e/")) and re.search(r"(test_[^/]*\.py|\.spec\.ts)$", source):
            before = test_names(repo.read(base, source))
            after = test_names(repo.read(head, path)) if status != "D" else set()
            lost = sorted(before - after)
            if lost:
                block("test-removed", path,
                      f"{len(lost)} test(s) removed: {', '.join(lost[:5])}{' ...' if len(lost) > 5 else ''}",
                      "a test is removed only with the behaviour it asserts; get owner approval")

        # 5. New skip/xfail/only/continue-on-error/if:false in tests and workflows.
        kind = "workflows" if path.startswith(".github/") else \
            "tests" if path.startswith(("backend/tests/", "frontend/e2e/")) else None
        if status in "AMR" and kind:
            hits = [l.strip() for l in added_lines(repo, base, head, path) if SKIP_MARKERS[kind].search(l)]
            if hits:
                block("gate-weakened", path, f"adds a skip or soft-fail: {hits[0][:100]}",
                      "fix the failure instead of skipping it")

    changed = set(result.changed)

    # 6. CI jobs and their commands.
    if CI_WORKFLOW in changed:
        before, after = ci_jobs(repo.read(base, CI_WORKFLOW)), ci_jobs(repo.read(head, CI_WORKFLOW))
        for job, commands in before.items():
            if job not in after:
                block("ci-job-removed", CI_WORKFLOW, f"job `{job}` no longer exists",
                      "restore the job; required status checks depend on it")
                continue
            for command in sorted(commands - after[job]):
                block("ci-command-removed", CI_WORKFLOW, f"job `{job}` stopped running `{command[:80]}`",
                      "restore the command or get owner approval")

    # 7. verify.py steps.
    if "scripts/verify.py" in changed:
        lost = verify_steps(repo.read(base, "scripts/verify.py")) - verify_steps(repo.read(head, "scripts/verify.py"))
        for name in sorted(lost):
            block("verify-step-removed", "scripts/verify.py", f"step `{name}` was removed",
                  "restore the step or get owner approval")

    # 8. Hook wiring.
    if ".claude/settings.json" in changed:
        lost = hook_commands(repo.read(base, ".claude/settings.json")) - \
            hook_commands(repo.read(head, ".claude/settings.json"))
        for hook in sorted(lost):
            block("hook-removed", ".claude/settings.json", f"hook removed or changed: {hook}",
                  "restore the hook or get owner approval")

    # 9. Engine abstraction.
    path, cls = ADAPTER_PROTOCOL
    if path in changed:
        lost = protocol_methods(repo.read(base, path), cls) - protocol_methods(repo.read(head, path), cls)
        if lost:
            block("adapter-contract", path, f"{cls} lost: {', '.join(sorted(lost))}",
                  "every engine implements this Protocol; removing a method is an engine-boundary change")
    path, cls = ENGINE_ENUM
    if path in changed:
        lost = enum_members(repo.read(base, path), cls) - enum_members(repo.read(head, path), cls)
        if lost:
            block("engine-mode-removed", path, f"{cls} lost: {', '.join(sorted(lost))}",
                  "installed workspaces store this value; removing a mode strands them")

    # 10. Baseline debt can only shrink.
    path, name = DEBT_REGISTER
    if path in changed:
        before, after = debt_register(repo.read(base, path), name), debt_register(repo.read(head, path), name)
        if before is not None:
            if after is None:
                block("debt-register", path, f"`{name}` is gone or unreadable",
                      "keep the register; it is what stops new violations")
            else:
                for key, value in sorted(after.items()):
                    if value > before.get(key, 0):
                        block("debt-grew", path, f"{key}: {before.get(key, 0)} -> {value}",
                              "fix the new violation instead of raising its allowance")

    # Owner approval for this exact commit.
    if approval_sha and head != WORKTREE:
        if approval_sha.strip() == head_sha:
            result.approval = f"approved for {head_sha}"
            for f in result.findings:
                if f.verdict == BLOCK:
                    f.approved = True
        else:
            result.approval = "approval is for a different commit; ignored"
    return result


def resolve_base(repo: Repo, head: str) -> str | None:
    ref = "HEAD" if head == WORKTREE else head
    for upstream in ("origin/master", "origin/main", "master"):
        out = repo.git("merge-base", ref, upstream, check=False).strip()
        if out:
            return out
    return None


def render(result: Result) -> str:
    lines = [
        f"guardrail: {result.verdict}",
        f"  base {result.base}",
        f"  head {result.head}",
        f"  risk {result.risk}  ({', '.join(result.areas) or 'no mapped area'})",
        f"  docs {DOCS_REQUIRED[result.risk]}",
        f"  {len(result.changed)} changed path(s)",
    ]
    if result.required_tests:
        lines.append("  required tests:")
        lines += [f"    - {t}" for t in result.required_tests]
    if result.findings:
        lines.append("  findings:")
        for f in result.findings:
            state = "APPROVED" if f.approved else f.verdict
            lines.append(f"    {state}  [{f.rule}] {f.path}: {f.reason}")
            lines.append(f"             -> {f.remediation}")
    lines.append(f"  owner approval: {result.approval}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--head", default=WORKTREE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = Repo(args.repo.resolve())
    base = args.base or resolve_base(repo, args.head)
    if not base or not repo.git("rev-parse", "--verify", f"{base}^{{commit}}", check=False):
        print("guardrail: UNVERIFIED -- no merge base to compare against; "
              "fetch origin/master or pass --base", file=sys.stderr)
        return EXIT[UNVERIFIED]

    base = repo.git("rev-parse", base).strip()
    result = evaluate(repo, base, args.head, os.environ.get("APPBI_PROTECTION_APPROVAL_SHA"))
    if args.json:
        payload = asdict(result)
        payload["verdict"] = result.verdict
        payload["docs_required"] = DOCS_REQUIRED[result.risk]
        print(json.dumps(payload, indent=2))
    else:
        print(render(result))
    return EXIT[result.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
