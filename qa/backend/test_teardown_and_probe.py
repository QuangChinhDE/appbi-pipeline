"""Teardown guardrails, and the engine contract probe.

Split out of `test_ci_lane.py` when the Airbyte-on-Kubernetes machinery was
removed. Everything else in that file tested a Helm values renderer, a CI
Kubernetes lane and an Application bootstrap script, none of which exist any
more: the engine runs in Compose beside the product.

These two subjects survived because their code did.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

repo_only = pytest.mark.skipif(
    not (ROOT / "scripts" / "production.py").exists(),
    reason="needs the repository layout")

pytestmark = [repo_only]





# ── teardown ─────────────────────────────────────────────────────────────────

def _production():
    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_docker(module, containers: dict, volumes: dict, networks: dict):
    """Stand in for the Docker CLI, returning the labels each object carries."""
    def fake_run(command, **kwargs):
        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        result = _Result()
        joined = " ".join(command)
        if "ps" in command and "-a" in command:
            result.stdout = "\n".join(
                json.dumps({"Names": name}) for name in containers)
        elif "volume" in command and "ls" in command:
            result.stdout = "\n".join(
                json.dumps({"Name": name,
                            "Labels": f"com.docker.compose.project={owner}"})
                for name, owner in volumes.items())
        elif "network" in command and "ls" in command:
            result.stdout = "\n".join(
                json.dumps({"Name": name}) for name in networks)
        elif "network" in command and "inspect" in command:
            result.stdout = networks.get(command[3], "")
        elif "inspect" in command:
            result.stdout = json.dumps(
                {"com.docker.compose.project": containers.get(command[2], "")})
        elif "rm" in command:
            deleted.append(joined)
        return result

    deleted: list[str] = []
    module.run = fake_run
    return deleted


def test_teardown_never_plans_to_delete_another_project(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point, and the thing name matching cannot get right.

    `appbi-net` carries no `appbi-ai` prefix and belongs to `appbi-ai`;
    `appbi-pipeline_appbi` looks like it belongs to nothing in particular and
    belongs to us. Any rule written over names gets at least one of these
    wrong. The Compose project label gets both right.
    """
    module = _production()
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "appbi-pipeline")
    _fake_docker(
        module,
        containers={"appbi-pipeline-api": "appbi-pipeline",
                    "appbi-pipeline-postgres": "appbi-pipeline",
                    "appbi-ai-backend-1": "appbi-ai",
                    "appbi-ai-db-1": "appbi-ai",
                    "some-unrelated-container": ""},
        volumes={"appbi-pipeline_pgdata": "appbi-pipeline",
                 "appbi-ai_db_data": "appbi-ai"},
        networks={"appbi-pipeline_appbi": "appbi-pipeline",
                  "appbi-net": "appbi-ai"})

    plan = module.teardown_plan({})

    assert plan["containers"] == ["appbi-pipeline-api", "appbi-pipeline-postgres"]
    assert plan["volumes"] == ["appbi-pipeline_pgdata"]
    assert plan["networks"] == ["appbi-pipeline_appbi"]
    # Not merely absent from the plan -- recorded as refused, so the manifest
    # shows the guard fired rather than that nothing matched.
    refused = " ".join(plan["refused"])
    assert "appbi-ai-backend-1" in refused
    assert "appbi-ai_db_data" in refused
    assert "appbi-net" in refused
    # And an unlabelled container belongs to nobody, so it is left alone.
    assert "some-unrelated-container" not in json.dumps(plan)


def test_teardown_is_a_dry_run_unless_told_otherwise(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A destructive default gets run destructively by accident exactly once."""
    import argparse

    module = _production()
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "appbi-pipeline")
    deleted = _fake_docker(
        module,
        containers={"appbi-pipeline-api": "appbi-pipeline"},
        volumes={"appbi-pipeline_pgdata": "appbi-pipeline"},
        networks={})
    module.need = lambda *a, **kw: None
    module.load_config = lambda path: {"profile": "single-host-demo"}
    manifest = tmp_path / "manifest.json"

    code = module.cmd_clean_room(argparse.Namespace(
        config="deploy/demo.yaml", apply=False, accept_data_loss=True,
        backup_dir=str(tmp_path), manifest=str(manifest), allow_insecure=False))

    assert code == 0
    assert deleted == [], deleted
    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert written["applied"] is False
    assert written["deleted"] == {"containers": [], "volumes": [], "networks": []}


def test_a_failed_backup_stops_the_teardown(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The RC1 teardown destroyed the only input its clean-room could have used.

    That was the owner's call and a legitimate one. What was missing was the
    step where somebody had to say so; `--accept-data-loss` is that step.
    """
    import argparse

    module = _production()
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "appbi-pipeline")
    _fake_docker(module,
                 containers={"appbi-pipeline-api": "appbi-pipeline"},
                 volumes={}, networks={})
    module.need = lambda *a, **kw: None
    module.load_config = lambda path: {"profile": "single-host-demo"}

    inner = module.run

    def failing_backup(command, **kwargs):
        if "backup.py" in " ".join(str(c) for c in command):
            class _Failed:
                returncode = 1
                stdout = stderr = "pg_dump: connection refused"
            return _Failed()
        return inner(command, **kwargs)

    module.run = failing_backup

    with pytest.raises(SystemExit):
        module.cmd_clean_room(argparse.Namespace(
            config="deploy/demo.yaml", apply=True, accept_data_loss=False,
            backup_dir=str(tmp_path), manifest=str(tmp_path / "m.json"),
            allow_insecure=False))


# ── the engine contract probe ────────────────────────────────────────────────

def _probe_module():
    spec = importlib.util.spec_from_file_location(
        "verify_engine_api", ROOT / "qa" / "probes" / "verify-engine-api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_in_network_probe_forwards_credentials_without_exposing_them(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """`-e NAME`, never `-e NAME=value`.

    The probe re-runs itself inside a container on the engine's Docker network,
    because the Config API is deliberately not published to the host. The inner
    container needs the credentials; the command line must not carry them,
    or they land in every process listing on the machine.
    """
    import argparse

    module = _probe_module()
    monkeypatch.setenv("AIRBYTE_API_USERNAME", "ops")
    monkeypatch.setenv("AIRBYTE_API_PASSWORD", "super-secret-value")

    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command

        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.probe_in_network(argparse.Namespace(
        url="http://airbyte-server:8001", in_network="appbi-pipeline_appbi",
        json=False))

    command = captured["command"]
    assert "AIRBYTE_API_PASSWORD" in command, "the inner probe gets no credential"
    assert "super-secret-value" not in " ".join(command), (
        "the value is on the command line, so it is in every process listing")


def test_the_probe_reads_the_adapter_from_the_repository_root() -> None:
    """It parses the adapter's own route list rather than keeping a copy.

    Two lists of endpoints drift, and the drift shows up as a probe that passes
    against an engine the adapter cannot actually use.
    """
    module = _probe_module()
    groups = module.alternative_groups()
    assert groups, "no alternative route groups found in the adapter"
    assert any("/api/v1/workspaces/list" in group for group in groups), groups
