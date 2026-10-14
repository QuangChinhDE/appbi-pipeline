"""`scripts/verify.py` means what it says: CI parity, five states, no empty PASS.

Three ways a verifier lies, each pinned here:

* it says "the CI equivalent" while CI runs something it does not -- the
  Overview golden flows were exactly that gap;
* it says OK because nothing was selected -- a change to a script, a workflow
  or a lock file used to select no step and report "nothing to check";
* it says OK although a required step never ran.
"""

from __future__ import annotations

import re
import sys

import pytest

from .governance_support import ROOT, load_script

verify = load_script("verify.py")

#: CI commands that prepare an environment rather than check anything.
SETUP = re.compile(r"^(python -m pip|pip |npm ci|npx playwright install)")


def ci_check_commands() -> set[str]:
    guardrail = load_script("guardrail.py")
    jobs = guardrail.ci_jobs((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    # `guardrail` is the CI form of verify's guardrail step: same script, but
    # loaded from the merge base, which only CI has a reason to do.
    return {c for job, commands in jobs.items() if job != "guardrail"
            for c in commands if not SETUP.match(c)}


def step_commands() -> list[str]:
    return [" ".join(s.command).replace("\\", "/") for s in verify.steps()]


def test_every_ci_check_has_a_verify_step():
    """`verify.py full` runs everything CI runs, so the two cannot disagree."""
    missing = []
    for command in ci_check_commands():
        # The CI form names scripts relative to the repo; verify uses absolute paths.
        tail = command.replace("python ", "").replace("-m ", "")
        tokens = [t for t in tail.split() if t not in ("tests", "-q", "app")]
        if not any(all(t in step for t in tokens) for step in step_commands()):
            missing.append(command)
    assert not missing, f"CI runs these and verify.py does not: {missing}"


def test_verify_runs_the_guardrail_on_every_selection():
    step = next(s for s in verify.steps() if "guardrail.py" in " ".join(s.command))
    assert step.triggers == () and step.exit_states == {1: "BLOCKED", 2: "UNVERIFIED"}


def test_the_ci_guardrail_job_runs_from_the_merge_base():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.search(r'git show "\$BASE:scripts/guardrail\.py"', ci), \
        "the guardrail must be loaded from the base revision, not the PR head"
    assert "APPBI_PROTECTION_APPROVAL_SHA: ${{ vars.APPBI_PROTECTION_APPROVAL_SHA }}" in ci


@pytest.mark.parametrize("path", [
    "scripts/verify.py", "scripts/claude_guard.py", ".github/workflows/ci.yml",
    ".claude/settings.json", "AGENTS.md", "engine-lock.json", "connector-lock.json",
    "compatibility.yaml", "docker-compose.embedded.yml", "deploy/egress/squid.conf",
    "run.sh", "run.ps1", ".gitignore", "backend/app/adapters/base.py",
])
def test_governance_and_contract_paths_select_a_real_check(path):
    chosen = [s.name for s in verify.selected("task", [path]) if s.triggers]
    assert chosen, f"{path} would be verified by nothing but the guardrail"
    assert verify.uncovered([path]) == []


def test_an_uncovered_path_is_unverified_at_task(monkeypatch, capsys):
    monkeypatch.setattr(verify, "changed_paths", lambda: ["docker-bake.hcl"])
    monkeypatch.setattr(verify, "steps", lambda: [])
    assert verify.main(["task"]) == 1
    assert "UNVERIFIED coverage: docker-bake.hcl" in capsys.readouterr().out


def test_an_uncovered_path_is_tolerated_in_the_coding_loop(monkeypatch):
    monkeypatch.setattr(verify, "changed_paths", lambda: ["docker-bake.hcl"])
    monkeypatch.setattr(verify, "steps", lambda: [])
    assert verify.main(["fast"]) == 0


def test_task_with_no_change_is_not_a_pass(monkeypatch, capsys):
    monkeypatch.setattr(verify, "changed_paths", lambda: [])
    monkeypatch.setattr(verify, "steps", lambda: [])
    assert verify.main(["task"]) == 1
    assert "UNVERIFIED change under verification" in capsys.readouterr().out


def test_documentation_only_change_passes_on_review(monkeypatch):
    monkeypatch.setattr(verify, "changed_paths", lambda: ["README.md"])
    monkeypatch.setattr(verify, "steps", lambda: [_step("guardrail", 0)])
    assert verify.main(["task"]) == 0


def _step(name, code, **kw):
    return verify.Step(name=name, command=[sys.executable, "-c", f"raise SystemExit({code})"],
                       cwd=ROOT, exit_states={1: verify.BLOCKED, 2: verify.UNVERIFIED}, **kw)


@pytest.mark.parametrize("code, state", [(0, "PASS"), (1, "BLOCKED"), (2, "UNVERIFIED"), (3, "FAIL")])
def test_exit_codes_map_to_states(code, state):
    assert verify.run(_step("x", code), "task")[0] == state


def test_a_blocked_guardrail_fails_the_task(monkeypatch, capsys):
    monkeypatch.setattr(verify, "changed_paths", lambda: ["README.md"])
    monkeypatch.setattr(verify, "steps", lambda: [_step("governance: change guardrail", 1)])
    assert verify.main(["task"]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_a_missing_toolchain_is_unverified_at_task_and_skipped_in_fast():
    step = _step("needs a tool", 0, requires="definitely-not-a-real-binary-xyz")
    assert verify.run(step, "task")[0] == "UNVERIFIED"
    assert verify.run(step, "full")[0] == "UNVERIFIED"
    assert verify.run(step, "fast")[0] == "SKIPPED"


def test_an_agent_cannot_declare_done_over_a_failed_gate(monkeypatch, capsys):
    """The adversarial case: everything else passes, one required gate fails or
    cannot run. `task` -- the command the Definition of Done names -- refuses."""
    monkeypatch.setattr(verify, "changed_paths", lambda: ["backend/app/services/runs.py"])
    for bad in (_step("backend: pytest", 3, triggers=("backend/",)),
                _step("backend: pytest", 0, triggers=("backend/",), requires="no-such-tool-xyz")):
        monkeypatch.setattr(verify, "steps", lambda bad=bad: [_step("guardrail", 0), bad])
        assert verify.main(["task"]) == 1
    assert "OK" not in capsys.readouterr().out.split()


def test_step_names_are_unique():
    names = [s.name for s in verify.steps()]
    assert len(names) == len(set(names))
