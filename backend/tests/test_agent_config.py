"""The agent-config validator accepts this repository and rejects each way of breaking it.

`scripts/check-agent-config.py` is the only thing that notices a rule pointing
at a renamed file, a settings.json that silently disables every hook, or a
personal settings file committed by a broad `git add`. A validator that has
never been seen to fail proves nothing, so every check here is exercised
against a deliberately broken copy as well as the real tree.
"""

from __future__ import annotations

import json

import pytest

from .governance_support import ROOT, load_script, make_repo

config = load_script("check-agent-config.py")


@pytest.fixture()
def repo(tmp_path):
    return make_repo(tmp_path)


def errors(path) -> list[str]:
    return config.run(path)


def test_the_real_repository_passes():
    assert errors(ROOT) == []


def test_the_fixture_baseline_passes(repo):
    assert errors(repo) == []


def test_missing_agents_md_is_rejected(repo):
    (repo / "AGENTS.md").unlink()
    assert any("[imports] AGENTS.md: missing" in e for e in errors(repo))


def test_claude_md_that_stops_importing_agents_md_is_rejected(repo):
    path = repo / ".claude" / "CLAUDE.md"
    path.write_text(path.read_text(encoding="utf-8").replace("@../AGENTS.md", ""), encoding="utf-8")
    assert any("does not import @../AGENTS.md" in e for e in errors(repo))


def test_a_rule_naming_a_missing_file_is_rejected(repo):
    path = repo / ".claude" / "rules" / "backend.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nSee `backend/app/gone.py`.\n", encoding="utf-8")
    assert any("`backend/app/gone.py` does not exist" in e for e in errors(repo))


def test_a_skill_naming_a_missing_file_is_rejected(repo):
    path = repo / ".claude" / "skills" / "fix-bug" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nUse `.sdlc/templates/plan.md`.\n", encoding="utf-8")
    assert any("[refs] .claude/skills/fix-bug/SKILL.md" in e for e in errors(repo))


def test_a_rule_glob_that_matches_nothing_is_rejected(repo):
    path = repo / ".claude" / "rules" / "frontend.md"
    text = path.read_text(encoding="utf-8").replace('"frontend/**"', '"frontnd/**"')
    path.write_text(text, encoding="utf-8")
    assert any("`frontnd/**` matches no tracked file" in e for e in errors(repo))


def test_a_rule_without_paths_is_rejected(repo):
    path = repo / ".claude" / "rules" / "testing.md"
    text = path.read_text(encoding="utf-8").replace("paths:", "globs:")
    path.write_text(text, encoding="utf-8")
    found = errors(repo)
    assert any("uses `globs:`" in e for e in found)
    assert any("no `paths:` list" in e for e in found)


def test_a_skill_with_the_wrong_name_is_rejected(repo):
    path = repo / ".claude" / "skills" / "review-change" / "SKILL.md"
    text = path.read_text(encoding="utf-8").replace("name: review-change", "name: review")
    path.write_text(text, encoding="utf-8")
    assert any("`name` must be 'review-change'" in e for e in errors(repo))


def test_malformed_settings_json_is_rejected(repo):
    (repo / ".claude" / "settings.json").write_text('{"hooks": {', encoding="utf-8")
    assert any("invalid JSON" in e for e in errors(repo))


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["hooks"].__setitem__("PreToolUze", d["hooks"].pop("PreToolUse")), "unknown hook event"),
    (lambda d: d["hooks"]["PreToolUse"][0].__setitem__("matcher", "Bash("), "matcher"),
    (lambda d: d["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__(
        "command", 'python "$CLAUDE_PROJECT_DIR/scripts/missing_guard.py" bash'), "does not exist"),
    (lambda d: d["hooks"]["PreToolUse"][0].__setitem__("hooks", []), "has no hooks"),
    (lambda d: d.pop("hooks"), "no `hooks` object"),
])
def test_invalid_hook_configuration_is_rejected(repo, mutate, expected):
    path = repo / ".claude" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any(expected in e for e in errors(repo))


@pytest.mark.parametrize("unsafe", [
    ".claude/settings.local.json",
    ".claude/projects/x/memory/MEMORY.md",
    ".env.local",
    "secrets/gcp.json",
    "evidence-run.json",
    "deploy/service-account-prod.json",
])
def test_unsafe_tracked_files_are_rejected(repo, unsafe):
    path = repo / unsafe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    from .governance_support import git
    git(repo, "add", "-f", unsafe)
    assert any(unsafe in e for e in errors(repo))


def test_committed_examples_are_allowed():
    assert not any(".env.example" in e or ".env.production.example" in e for e in errors(ROOT))


def test_a_secret_in_a_rule_is_rejected(repo):
    path = repo / ".claude" / "rules" / "security.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nghp_" + "a" * 36 + "\n", encoding="utf-8")
    assert any("[secrets] .claude/rules/security.md" in e for e in errors(repo))


def test_the_gitignore_keeps_personal_files_out_and_governance_in():
    import subprocess

    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT).returncode == 0

    for personal in (".claude/settings.local.json", ".claude/worktrees/x", ".claude/rules/notes.txt",
                     "docs/features/some-feature/intent.md", "docs/spec/section-34.md", ".env", "secrets/k"):
        assert ignored(personal), personal
    for shared in ("AGENTS.md", ".claude/CLAUDE.md", ".claude/settings.json", ".claude/rules/backend.md",
                   ".claude/skills/fix-bug/SKILL.md", "docs/features/_TEMPLATE/plan.md"):
        assert not ignored(shared), shared
