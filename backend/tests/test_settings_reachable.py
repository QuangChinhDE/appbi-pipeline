"""Every setting has to be reachable from .env.

This fault was found four times in one week, one setting at a time, and each
time after somebody had already edited .env and wondered why nothing changed:

    WORKER_MAX_PARALLEL_SYNCS   a VM lowered concurrency and got the default
    SEED_DEMO_DATA              demo connections kept appearing on a customer's
                                install that had turned them off
    SEED_ADMIN_EMAIL            .env named an administrator and was ignored
    SEED_ADMIN_PASSWORD         the published password stayed live

An audit of all hundred settings then found thirty-five more, including
BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD -- the two a production
install cannot start without. The bootstrap refuses to run without them and
names them in the refusal, and there was no way to supply them.

A setting absent from the compose environment cannot be set at all: the
container never receives it and pydantic falls back to the built-in default,
silently, with the edited .env sitting right there looking effective. Nothing
fails; it just does not work.

This test is the check nobody was doing.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

#: Set by the compose file itself, or read only by a host-side script, so not
#: reaching the container is the correct behaviour rather than an omission.
NOT_FROM_ENV = {
    # Compose points this at the postgres service; a value from .env would
    # send the containers somewhere else than the database they depend on.
    "database_url",
    # Paths inside the container, bound to volumes the compose file declares.
    "engine_workspace_dir", "engine_log_dir",
    "transform_workspace_dir", "transform_log_dir", "transform_storage_local_dir",
    # Identity of the build and the deployment, stamped at build time.
    "app_env", "service_name", "product_version",
    "build_sha", "build_digest", "build_time", "adapter_contract_version",
}


def _repo_root() -> pathlib.Path | None:
    named = os.environ.get("APPBI_REPO_ROOT")
    if named and (pathlib.Path(named) / "docker-compose.yml").is_file():
        return pathlib.Path(named)
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    return None


ROOT = _repo_root()

if ROOT is None:
    pytest.skip("this reads the compose files; mount the checkout and set "
                "APPBI_REPO_ROOT", allow_module_level=True)


def _keys_passed_into_containers() -> set[str]:
    keys: set[str] = set()
    for path in sorted(ROOT.glob("docker-compose*.yml")):
        text = path.read_text(encoding="utf-8")
        keys |= set(re.findall(r"^\s{2,}([A-Z][A-Z0-9_]*):\s", text, re.M))
    return keys


PASSED_IN = _keys_passed_into_containers()


def _settings_fields() -> list[str]:
    from app.core.config import Settings
    return [name for name in Settings.model_fields if name not in NOT_FROM_ENV]


def test_every_setting_reaches_the_containers() -> None:
    """The whole point. A name here and not in the compose environment is a
    knob that turns and does nothing."""
    unreachable = sorted(name.upper() for name in _settings_fields()
                         if name.upper() not in PASSED_IN)
    assert unreachable == [], (
        "these settings cannot be set from .env; add them to the shared "
        "environment block in docker-compose.yml with their current default"
    )


@pytest.mark.parametrize("key", [
    "BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_PASSWORD",
    "SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "SEED_DEMO_DATA",
    "WORKER_MAX_PARALLEL_SYNCS", "COOKIE_SECURE",
])
def test_the_ones_that_were_actually_broken(key: str) -> None:
    """Named individually as well as covered by the sweep above, because each
    of these cost somebody real time."""
    assert key in PASSED_IN


#: What pydantic reads as true and as false, so `1` and `true` do not count
#: as a disagreement.
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def test_no_compose_default_contradicts_the_code() -> None:
    """A compose default that disagrees with the code silently changes
    behaviour for every deployment that does not set the variable -- which is
    the quiet way a safe default becomes an unsafe one.

    Compose *filling in* a value the code leaves empty is not a disagreement:
    that is how the local setup gets a working OAuth redirect without anybody
    writing one. What must not happen is the two naming different values.
    """
    from app.core.config import Settings

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    declared = dict(re.findall(r"^\s{2,}([A-Z][A-Z0-9_]*): \$\{\1:-([^}]*)\}",
                               compose, re.M))
    contradictions = []
    for name, field in Settings.model_fields.items():
        key = name.upper()
        default = field.default
        if key not in declared or name in NOT_FROM_ENV:
            continue
        if not isinstance(default, (str, bool, int, float)) and default is not None:
            continue  # a list or a dict is built elsewhere; comparing text is noise
        written = declared[key]
        if isinstance(default, bool):
            same = (written.lower() in _TRUE) if default else (written.lower() in _FALSE)
        elif default is None or default == "":
            same = True  # compose is allowed to supply what the code leaves open
        else:
            same = written == str(default)
        if not same:
            contradictions.append(f"{key}: compose={written!r} code={default!r}")
    assert contradictions == []
