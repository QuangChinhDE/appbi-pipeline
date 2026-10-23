"""The change guardrail refuses each way of weakening the product's protection.

Every case builds a throwaway repository (governance_support.make_repo),
commits a baseline, commits one deliberately bad change on top, and asks
`scripts/guardrail.py` for its verdict on that exact range -- the same thing CI
does with a pull request.

The trust test at the bottom is the one that matters most: a pull request that
edits the guardrail to approve its own lock-file change passes when judged by
its own copy of the checker, and is blocked when judged by the base copy. That
is why CI runs it from the merge base.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from .governance_support import ROOT, git, load_script, make_repo

guardrail = load_script("guardrail.py")


@pytest.fixture()
def repo(tmp_path):
    return make_repo(tmp_path)


def head(repo) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def commit(repo, message="change") -> str:
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return head(repo)


def judge(repo, base, approval=None):
    return guardrail.evaluate(guardrail.Repo(repo), base, "HEAD", approval)


def edit(repo, path, transform):
    p = repo / path
    p.write_text(transform(p.read_text(encoding="utf-8")), encoding="utf-8")


def rules(result):
    return {f.rule for f in result.findings if not f.approved}


# ---------------------------------------------------------------- baseline

def test_a_harmless_change_passes_at_low_risk(repo):
    base = head(repo)
    (repo / "README.md").write_text("# AppBI Pipeline\n\nTypo fixed.\n", encoding="utf-8")
    commit(repo)
    result = judge(repo, base)
    assert result.verdict == "PASS" and result.risk == "Low", guardrail.render(result)


def test_a_frontend_component_change_is_low_and_maps_its_tests(repo):
    base = head(repo)
    (repo / "frontend/src/components/runs/RunBadge.tsx").parent.mkdir(parents=True, exist_ok=True)
    (repo / "frontend/src/components/runs/RunBadge.tsx").write_text("export {}\n", encoding="utf-8")
    commit(repo)
    result = judge(repo, base)
    assert result.verdict == "PASS" and result.risk == "Low"
    assert "backend/tests/test_i18n_coverage.py" in result.required_tests


def test_a_service_change_is_medium(repo):
    base = head(repo)
    edit(repo, "backend/app/services/runs.py", lambda s: s + "\n# note\n")
    commit(repo)
    assert judge(repo, base).risk == "Medium"


# ---------------------------------------------------------------- contracts

@pytest.mark.parametrize("lock", ["engine-lock.json", "connector-lock.json", "compatibility.yaml"])
def test_a_lock_change_is_blocked_without_approval(repo, lock):
    base = head(repo)
    edit(repo, lock, lambda s: s.replace("0.59.1", "0.63.0") + "\n")
    commit(repo)
    result = judge(repo, base)
    assert result.verdict == "BLOCK" and "protected-contract" in rules(result)
    assert result.risk == "High"


def test_owner_approval_is_bound_to_the_exact_commit(repo):
    base = head(repo)
    edit(repo, "engine-lock.json", lambda s: s + "\n")
    approved = commit(repo)

    assert judge(repo, base, approval=approved).verdict == "PASS"
    assert judge(repo, base, approval="0" * 40).verdict == "BLOCK"

    # Another push after the approval: the approval no longer matches anything.
    (repo / "README.md").write_text("later\n", encoding="utf-8")
    commit(repo)
    assert judge(repo, base, approval=approved).verdict == "BLOCK"


def test_approval_never_applies_to_the_working_tree(repo):
    base = head(repo)
    edit(repo, "engine-lock.json", lambda s: s + "\n")
    result = guardrail.evaluate(guardrail.Repo(repo), base, guardrail.WORKTREE, head(repo))
    assert result.verdict == "BLOCK"


def test_editing_an_applied_migration_is_blocked_and_adding_one_is_not(repo):
    versions = sorted((repo / "backend/migrations/versions").glob("*.py"))
    assert versions, "fixture has no migrations"
    base = head(repo)
    (repo / "backend/migrations/versions/zzzz_new_revision.py").write_text("# new\n", encoding="utf-8")
    commit(repo)
    result = judge(repo, base)
    assert "migration-history" not in rules(result) and result.risk == "High"

    base = head(repo)
    versions[0].write_text(versions[0].read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    commit(repo)
    assert "migration-history" in rules(judge(repo, base))


def test_removing_an_adapter_protocol_method_is_blocked(repo):
    base = head(repo)
    edit(repo, "backend/app/adapters/base.py",
         lambda s: re.sub(r"\n    async def cancel_job\(self, ref: str\) -> EngineJobStatus: \.\.\.", "", s))
    commit(repo)
    result = judge(repo, base)
    assert "adapter-contract" in rules(result), guardrail.render(result)


def test_removing_an_engine_mode_is_blocked(repo):
    enums = ROOT / "backend/app/models/enums.py"
    (repo / "backend/app/models/enums.py").write_text(enums.read_text(encoding="utf-8"), encoding="utf-8")
    base = commit(repo, "real enums")
    edit(repo, "backend/app/models/enums.py", lambda s: s.replace('    SQL_DIRECT = "SQL_DIRECT"\n', ""))
    commit(repo)
    assert "engine-mode-removed" in rules(judge(repo, base))


# ---------------------------------------------------------------- gates

def test_deleting_the_guardrail_is_blocked(repo):
    base = head(repo)
    (repo / "scripts/guardrail.py").unlink()
    commit(repo)
    assert "gate-removed" in rules(judge(repo, base))


def test_disabling_a_ci_job_is_blocked(repo):
    base = head(repo)
    edit(repo, ".github/workflows/ci.yml",
         lambda s: re.sub(r"\n  alerts:\n.*?(?=\n  \w|\Z)", "\n", s, flags=re.S))
    commit(repo)
    assert "ci-job-removed" in rules(judge(repo, base))


def test_dropping_a_ci_command_is_blocked(repo):
    base = head(repo)
    edit(repo, ".github/workflows/ci.yml", lambda s: s.replace("run: python -m pytest tests -q", "run: echo ok"))
    commit(repo)
    assert "ci-command-removed" in rules(judge(repo, base))


@pytest.mark.parametrize("soft", ["continue-on-error: true", "if: false"])
def test_soft_failing_a_ci_job_is_blocked(repo, soft):
    base = head(repo)
    edit(repo, ".github/workflows/ci.yml",
         lambda s: s.replace("    name: Backend\n", f"    name: Backend\n    {soft}\n", 1))
    commit(repo)
    assert "gate-weakened" in rules(judge(repo, base))


def test_removing_a_verify_step_is_blocked(repo):
    base = head(repo)
    edit(repo, "scripts/verify.py", lambda s: s.replace('name="deploy: alert rules parse"', 'name="x"'))
    commit(repo)
    assert "verify-step-removed" in rules(judge(repo, base))


def test_removing_a_hook_is_blocked(repo):
    base = head(repo)
    settings = json.loads((repo / ".claude/settings.json").read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"] = settings["hooks"]["PreToolUse"][:1]
    (repo / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")
    commit(repo)
    assert "hook-removed" in rules(judge(repo, base))


def test_deleting_a_test_is_blocked(repo):
    base = head(repo)
    edit(repo, "backend/tests/test_claude_guard.py",
         lambda s: s.replace("def test_known_limit_indirect_writes_are_not_caught", "def _not_a_test"))
    commit(repo)
    assert "test-removed" in rules(judge(repo, base))


@pytest.mark.parametrize("marker", ["@pytest.mark.skip(reason='flaky')", "@pytest.mark.xfail"])
def test_skipping_a_test_is_blocked(repo, marker):
    base = head(repo)
    edit(repo, "backend/tests/test_claude_guard.py",
         lambda s: s.replace("def test_known_limit", f"{marker}\ndef test_known_limit"))
    commit(repo)
    assert "gate-weakened" in rules(judge(repo, base))


def test_raising_a_debt_allowance_is_blocked(repo):
    base = head(repo)
    edit(repo, "backend/tests/test_layering_debt.py",
         lambda s: s.replace('"service-commit:services/runs.py": 9', '"service-commit:services/runs.py": 10'))
    commit(repo)
    assert "debt-grew" in rules(judge(repo, base))


def test_a_required_test_that_does_not_exist_is_unverified(repo):
    (repo / "backend/tests/test_nested_secrets.py").unlink()
    base = commit(repo, "base already lacks it")
    edit(repo, "backend/app/core/secrets.py", lambda s: s + "\n")
    commit(repo)
    result = judge(repo, base)
    assert result.verdict == "UNVERIFIED" and "required-test-missing" in rules(result)


def test_an_unresolvable_base_is_unverified(repo):
    code = guardrail.main(["--repo", str(repo), "--base", "f" * 40, "--head", "HEAD"])
    assert code == 2


# ---------------------------------------------------------------- review bypasses
# Each of these got past the first version of the guardrail; found by the
# independent review of the change that introduced it.

@pytest.mark.parametrize("mutate", [
    # an early exit: every old command is still there, so nothing is "removed"
    lambda s: s.replace("        run: python -m pytest tests -q",
                        "        run: |\n          exit 0\n          python -m pytest tests -q"),
    # an `if:` that is not literally `false`
    lambda s: s.replace("    name: Backend\n", "    name: Backend\n    if: github.event_name == 'push'\n", 1),
    # an env override nothing parsed
    lambda s: s.replace("    name: Backend\n", "    name: Backend\n    env:\n      PYTEST_ADDOPTS: -k nothing\n", 1),
])
def test_any_workflow_edit_needs_owner_approval(repo, mutate):
    base = head(repo)
    edit(repo, ".github/workflows/ci.yml", mutate)
    commit(repo)
    result = judge(repo, base)
    assert result.verdict == "BLOCK" and "gate-config-changed" in rules(result), guardrail.render(result)


def test_editing_the_trusted_guardrail_workflow_is_blocked(repo):
    base = head(repo)
    edit(repo, ".github/workflows/guardrail.yml", lambda s: s.replace(
        "${{ vars.APPBI_PROTECTION_APPROVAL_SHA }}", "${{ github.event.pull_request.head.sha }}"))
    commit(repo)
    assert "gate-config-changed" in rules(judge(repo, base))


def test_a_new_workflow_is_blocked(repo):
    base = head(repo)
    (repo / ".github/workflows/fake.yml").write_text("name: x\n", encoding="utf-8")
    commit(repo)
    assert "gate-config-changed" in rules(judge(repo, base))


@pytest.mark.parametrize("target", ["backend/tests/claude_guard_cases.py", "backend/scratch/test_claude_guard.py"])
def test_renaming_a_test_file_out_of_collection_is_blocked(repo, target):
    base = head(repo)
    (repo / target).parent.mkdir(parents=True, exist_ok=True)
    git(repo, "mv", "backend/tests/test_claude_guard.py", target)
    commit(repo)
    assert "test-uncollected" in rules(judge(repo, base))


def test_renaming_a_test_file_within_collection_is_allowed(repo):
    base = head(repo)
    git(repo, "mv", "backend/tests/test_claude_guard.py", "backend/tests/test_hook_guard.py")
    commit(repo)
    assert not {"test-uncollected", "test-removed"} & rules(judge(repo, base))


@pytest.mark.parametrize("path, text", [
    ("pytest.ini", "addopts = -q --deselect backend/tests/test_permissions.py\n"),
    ("backend/tests/conftest.py", "collect_ignore = ['test_permissions.py']\n"),
])
def test_test_runner_configuration_changes_are_blocked(repo, path, text):
    base = head(repo)
    p = repo / path
    p.write_text((p.read_text(encoding="utf-8") if p.exists() else "") + text, encoding="utf-8")
    commit(repo)
    assert "gate-config-changed" in rules(judge(repo, base))


@pytest.mark.parametrize("line", ["    p.skip('later')", "    skip('later')", "from pytest import skip"])
def test_aliased_skips_are_blocked(repo, line):
    base = head(repo)
    edit(repo, "backend/tests/test_claude_guard.py", lambda s: s + "\n" + line + "\n")
    commit(repo)
    assert "gate-weakened" in rules(judge(repo, base))


# ---------------------------------------------------------------- trust

def test_a_pr_cannot_approve_its_own_weakening(repo, tmp_path):
    """Head edits the guardrail to stop protecting the lock files, and bumps a
    lock in the same change. Its own checker says PASS; the base checker, which
    is what CI runs, says BLOCK."""
    base = head(repo)
    edit(repo, "scripts/guardrail.py", lambda s: s.replace(
        'PROTECTED_CONTRACTS = ("engine-lock.json", "connector-lock.json", "compatibility.yaml")',
        "PROTECTED_CONTRACTS = ()"))
    edit(repo, "engine-lock.json", lambda s: s.replace("0.59.1", "0.63.0") + "\n")
    commit(repo)

    def run_from(rev: str) -> int:
        script = tmp_path / f"guardrail-{rev[:7]}.py"
        script.write_text(git(repo, "show", f"{rev}:scripts/guardrail.py"), encoding="utf-8")
        return subprocess.run([sys.executable, str(script), "--repo", str(repo), "--base", base,
                               "--head", "HEAD"], capture_output=True, text=True).returncode

    assert run_from("HEAD") == 0      # the PR's own copy waves it through
    assert run_from(base) == 1        # the trusted copy does not
