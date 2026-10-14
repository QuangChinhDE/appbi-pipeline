"""The SDLC, end to end, on one task at each risk level.

Each scenario is a realistic change committed on a throwaway repository, then
taken through the two gates a task passes before it can be called done:

  scripts/guardrail.py   risk, required documents, required tests, verdict
  scripts/verify.py      which checks `task` selects for the diff

The point is not any single assertion -- those are covered in test_guardrail.py
and test_verify.py -- but that the pieces agree with each other and with the
risk table in AGENTS.md for a task of each kind. The last scenario is the
adversarial one: an agent that tries to call a task done over a failed or
unverified gate.
"""

from __future__ import annotations

import sys

from .governance_support import git, load_script, make_repo

guardrail = load_script("guardrail.py")
verify = load_script("verify.py")


def change(repo, files: dict[str, str]):
    base = git(repo, "rev-parse", "HEAD").strip()
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(p.read_text(encoding="utf-8") + text if p.exists() else text, encoding="utf-8")
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "scenario")
    result = guardrail.evaluate(guardrail.Repo(repo), base, "HEAD", None)
    selected = [s.name for s in verify.selected("task", result.changed)]
    return result, selected


def test_low_risk_ui_text(tmp_path):
    """Copy change in the translation catalogue: no documents, i18n coverage
    and the frontend checks, guardrail PASS."""
    result, selected = change(make_repo(tmp_path), {"frontend/src/lib/i18n.ts": "\n// copy tweak\n"})
    assert (result.risk, result.verdict) == ("Low", "PASS")
    assert guardrail.DOCS_REQUIRED["Low"] == "none"
    assert result.required_tests == ["backend/tests/test_i18n_coverage.py"]
    assert {"frontend: typecheck", "frontend: build", "backend: pytest",
            "governance: change guardrail"} <= set(selected)
    assert verify.uncovered(result.changed) == []


def test_medium_risk_service_behaviour(tmp_path):
    """Run-list behaviour change in a domain service: a plan in the PR,
    backend checks, guardrail PASS, still not High."""
    result, selected = change(make_repo(tmp_path), {"backend/app/services/runs.py": "\n# behaviour change\n"})
    assert (result.risk, result.verdict) == ("Medium", "PASS")
    assert "plan" in guardrail.DOCS_REQUIRED["Medium"]
    assert {"backend: undefined names (pyflakes)", "backend: pytest"} <= set(selected)
    assert "frontend: build" not in selected


def test_high_risk_protected_contract(tmp_path):
    """An engine pin bump: High, feature documents and review required, and
    BLOCKed until the owner approves this exact commit."""
    repo = make_repo(tmp_path)
    result, selected = change(repo, {"engine-lock.json": "\n"})
    assert (result.risk, result.verdict) == ("High", "BLOCK")
    assert "docs/features/" in guardrail.DOCS_REQUIRED["High"]
    assert any(f.rule == "protected-contract" for f in result.findings)
    assert "backend/tests/test_base_connectors.py" in result.required_tests
    assert "backend: pytest" in selected

    head = git(repo, "rev-parse", "HEAD").strip()
    approved = guardrail.evaluate(guardrail.Repo(repo), result.base, "HEAD", head)
    assert approved.verdict == "PASS" and all(f.approved for f in approved.findings)


def test_high_risk_engine_boundary_change_is_high_without_blocking(tmp_path):
    """A new adapter helper is High (review, docs) but not a weakening."""
    result, _ = change(make_repo(tmp_path), {"backend/app/adapters/sql_direct/helpers.py": "X = 1\n"})
    assert (result.risk, result.verdict) == ("High", "PASS")
    assert "engine boundary" in result.areas


def test_adversarial_false_done_is_refused(tmp_path, monkeypatch, capsys):
    """An agent's work passes everything except one required gate, and it
    reports the task done anyway. The Definition of Done is `verify.py task`
    exiting 0; it does not, for a failed gate, an unrunnable one, or a guardrail
    BLOCK -- and the guardrail BLOCK is reproduced from a real diff."""
    repo = make_repo(tmp_path)
    blocked, _ = change(repo, {"compatibility.yaml": "\n# recorded without a run\n"})
    assert blocked.verdict == "BLOCK"

    monkeypatch.setattr(verify, "changed_paths", lambda: blocked.changed)
    py = [sys.executable, "-c"]
    scenarios = {
        "failed test": verify.Step("backend: pytest", py + ["raise SystemExit(1)"], verify.ROOT,
                                   triggers=("compatibility.yaml",)),
        "unrunnable gate": verify.Step("backend: pytest", py + ["pass"], verify.ROOT,
                                       triggers=("compatibility.yaml",), requires="no-such-tool-xyz"),
        "guardrail block": verify.Step("governance: change guardrail", py + ["raise SystemExit(1)"],
                                       verify.ROOT, exit_states={1: verify.BLOCKED}),
    }
    for name, bad in scenarios.items():
        ok = verify.Step("frontend: typecheck", py + ["pass"], verify.ROOT)
        monkeypatch.setattr(verify, "steps", lambda bad=bad, ok=ok: [ok, bad])
        assert verify.main(["task"]) == 1, name
        out = capsys.readouterr().out
        assert out.rstrip().endswith("FAILED"), name
