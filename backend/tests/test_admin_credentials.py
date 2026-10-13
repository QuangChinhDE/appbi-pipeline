"""Where the local/demo administrator credentials come from.

The launchers create a simple first-login account in a new `.env`, as requested
for local installations. The browser bundle and sign-in page still receive no
credential. `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` must reach the
containers, and bootstrap must reconcile an existing account so editing `.env`
and rerunning the launcher actually changes the login.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest


def _repo_root() -> pathlib.Path | None:
    """The checkout, from wherever the tests happen to be running.

    Inside the test image only `app/` and `tests/` are mounted, so walking up
    from `__file__` lands nowhere useful; the runner mounts the checkout and
    names it. From a plain checkout the walk works and nothing is needed.
    """
    named = os.environ.get("APPBI_REPO_ROOT")
    if named and (pathlib.Path(named) / "docker-compose.yml").is_file():
        return pathlib.Path(named)
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    return None


ROOT = _repo_root()

if ROOT is None:
    # Skipped rather than failed: the suite is normally run inside the backend
    # image with only app/ and tests/ mounted, and a collection error there
    # would look like a broken test file rather than a missing mount.
    pytest.skip(
        "these read the checkout itself; mount it and set APPBI_REPO_ROOT",
        allow_module_level=True,
    )


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── the browser bundle and sign-in page publish no credential ────────────

#: How a comment begins, in every file this checks.
_COMMENT_STARTS = ("#", "//", "*", '"""', "'''")


def _code_lines(text: str) -> list[str]:
    """Everything but comments.

    A comment naming the old password is how the next reader learns why any of
    this is here; forbidding the mention would forbid the explanation. What
    must not survive is the value being used.
    """
    return [line for line in text.splitlines()
            if not line.strip().startswith(_COMMENT_STARTS)]


def test_the_published_password_is_no_longer_used_anywhere() -> None:
    """It was in .env.example, seeded into five role accounts, and printed on
    the sign-in page. A password written in a public repository is a password
    every install has."""
    offenders = []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        if any("Admin@123456" in line
               for line in _code_lines(path.read_text(encoding="utf-8"))):
            offenders.append(str(path.relative_to(ROOT)))
    for name in (".env.example", ".env.production.example", "docker-compose.yml"):
        if any("Admin@123456" in line for line in _code_lines(_read(name))):
            offenders.append(name)
    assert offenders == []


def test_the_sign_in_page_reads_no_credential() -> None:
    page = _read("frontend/src/app/login/page.tsx")
    assert "process.env.NEXT_PUBLIC_DEMO" not in page
    assert "demoHint" not in page
    assert "otherRoles" not in page


def test_the_build_no_longer_carries_the_variables() -> None:
    """Next inlines NEXT_PUBLIC_* into the bundle, so a value reaching the
    build is a value any visitor can read out of it."""
    for name in ("docker-compose.yml", "frontend/Dockerfile", ".env.example"):
        assert not any("NEXT_PUBLIC_DEMO" in line
                       for line in _code_lines(_read(name))), name


def test_the_default_password_setting_is_empty() -> None:
    """Empty means "leave the stored password alone". A default that is also a
    valid password is a default nobody changes, because nothing asks them to."""
    from app.core.config import Settings
    assert Settings.model_fields["seed_admin_password"].default == ""


# ── .env can reach the setting at last ───────────────────────────────────

@pytest.mark.parametrize("key", ["SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD"])
def test_the_admin_settings_are_passed_into_the_containers(key: str) -> None:
    """The fault this shares with WORKER_MAX_PARALLEL_SYNCS: a setting absent
    from the compose environment cannot be set from .env at all, and the
    deployment silently keeps the built-in value."""
    assert re.search(rf"^\s+{key}: \$\{{{key}", _read("docker-compose.yml"), re.M)


@pytest.mark.parametrize("key", ["SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD"])
def test_the_admin_settings_are_documented_in_env_example(key: str) -> None:
    """`sync_env_keys` copies new keys from .env.example into an existing .env,
    so a key that is not there never reaches an upgraded install."""
    assert re.search(rf"^{key}=", _read(".env.example"), re.M)


# ── run.sh fills the password in, once ───────────────────────────────────

def test_run_sh_sets_the_documented_local_defaults_when_env_has_none() -> None:
    script = _read("run.sh")
    assert "DEFAULT_ADMIN_EMAIL='admin@appbi.local'" in script
    assert "DEFAULT_ADMIN_PASSWORD='AppBI@123456'" in script
    assert "ensure_admin_email" in script
    assert "ensure_admin_password" in script
    assert "generate_password" not in script


def test_run_sh_preserves_credentials_that_the_operator_changed() -> None:
    """A non-placeholder value returns before Set-EnvValue, so rerunning the
    launcher cannot put the local default back over an operator's value."""
    script = _read("run.sh")
    for function_name in ("ensure_admin_email", "ensure_admin_password"):
        match = re.search(
            rf"{function_name}\(\) \{{(?P<body>.*?)\n\}}", script, re.S
        )
        assert match is not None
        body = match.group("body")
        assert 'is_placeholder "$current" || return 0' in body
        assert "set_env_value" in body


def test_the_windows_script_does_the_same() -> None:
    """PowerShell and Bash must create the same first-login account."""
    script = _read("run.ps1")
    assert "$script:DefaultAdminEmail = 'admin@appbi.local'" in script
    assert "$script:DefaultAdminPassword = 'AppBI@123456'" in script
    assert "Set-AdminEmailIfUnset" in script
    assert "Set-AdminPasswordIfUnset" in script
    assert "NEXT_PUBLIC_DEMO" not in script


# ── reconciling the database with .env ───────────────────────────────────

def test_reconciliation_runs_on_every_start() -> None:
    """Creating the account once and never looking again is what made .env
    unable to change it: editing the password changed nothing, so an install
    that thought it had replaced the published default was still on it."""
    from app.bootstrap import _reconcile_admin, _seed_demo
    import inspect
    assert inspect.iscoroutinefunction(_reconcile_admin)
    assert "_reconcile_admin" in inspect.getsource(_seed_demo)


def test_an_empty_password_leaves_the_stored_one_alone() -> None:
    """Otherwise an operator who cleared the line would find the account's
    password replaced by the empty string, or by a generated one they never
    saw."""
    from app.bootstrap import _reconcile_admin
    import inspect
    source = inspect.getsource(_reconcile_admin)
    assert "if desired and not verify_password" in source


def test_a_changed_email_renames_rather_than_duplicates() -> None:
    """A second administrator appearing, while the first keeps its old
    password and stays signed-in-able, is the dangerous half of getting this
    wrong."""
    from app.bootstrap import _reconcile_admin
    import inspect
    source = inspect.getsource(_reconcile_admin)
    assert "is_platform_admin" in source
    assert "admin.email = email" in source


def test_a_reset_is_said_out_loud() -> None:
    """It overrides a password somebody may have set in the web UI, so it is
    logged at WARNING -- and the password itself never is."""
    from app.bootstrap import _reconcile_admin
    import inspect
    source = inspect.getsource(_reconcile_admin)
    assert "bootstrap.admin_password_reset" in source
    assert "logging.WARNING" in source
