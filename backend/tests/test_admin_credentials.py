"""Where the administrator's password comes from, and where it must not appear.

Two faults, reported together from a running install.

The sign-in page printed a working credential. `.env.example` shipped
NEXT_PUBLIC_DEMO_EMAIL=admin@appbi.local and NEXT_PUBLIC_DEMO_PASSWORD=
Admin@123456, Next inlined them at build time, and the page prefilled both
boxes and displayed the pair underneath -- plus a line naming four more
accounts that shared the password. Every install that followed the documented
path served a platform-administrator credential to anyone who could reach it.
The render was already guarded by a flag; the shipped default defeated the
guard. So the mechanism is gone, not re-defaulted.

And .env could not change it. SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD existed
in the settings and were absent from the compose environment, so a deployment
that set them got the built-in values anyway -- the same "the setting exists
and cannot be set" fault that hid WORKER_MAX_PARALLEL_SYNCS. Worse, the seed
created the account once and never looked again, so even a reachable setting
would not have moved an account that already existed.
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


# ── nothing publishes a password any more ────────────────────────────────

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

def test_run_sh_generates_a_password_when_env_has_none() -> None:
    script = _read("run.sh")
    assert "ensure_admin_password" in script
    assert "generate_password" in script


def test_run_sh_prints_it_once_and_says_where_it_lives() -> None:
    """Printed on the run that created it and never again -- and never in the
    web UI. Somebody has to be able to sign in afterwards."""
    script = _read("run.sh")
    assert "ADMIN_PASSWORD_GENERATED" in script
    assert "SEED_ADMIN_PASSWORD" in script


def test_the_windows_script_does_the_same() -> None:
    """A Windows install that still shipped the published password would be
    the same hole with a different entry point."""
    script = _read("run.ps1")
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
