"""The production renderer, run for real with kubectl.

PM v10's sharpest point: `test_the_config_produces_the_manifests` searched the
source for function names and passed while `render_from_config()` could not
execute at all. Kustomize refuses a root outside its own tree, so the generated
overlay failed with "new root ... cannot be absolute" -- before secrets,
before migration, before apply. Four static Kustomize targets rendering green
covered none of it.

So these call the real function with the real binary and parse what comes out.
Skipped when kubectl is absent, and only there: the point is to run.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent

needs_kubectl = pytest.mark.skipif(
    shutil.which("kubectl") is None,
    reason="needs kubectl to render; this is the point of the test")
repo_only = pytest.mark.skipif(
    not (ROOT / "deploy" / "production.yaml.example").exists(),
    reason="needs the repository layout")

pytestmark = [needs_kubectl, repo_only]

CONFIGURED = {
    "<your-production-context>": "prod-eu",
    "<registry.example.com>": "registry.acme.io",
    "<appbi.example.com>": "appbi.acme.io",
    "<airbyte.internal.example.com>": "airbyte.internal.acme.io",
    "<workspace-uuid>": "8b8a2621-7f31-46f3-82e6-36774a9ff3a6",
    "<ops@example.com>": "ops@acme.io",
    "<the internal team or design partner>": "Internal Data Team",
    "<e.g. business hours, Asia/Bangkok>": "business hours, Asia/Bangkok",
}


def _module():
    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config() -> dict:
    """The shipped example with its placeholders answered, and nothing else.

    Deliberately not a hand-written fixture: PM found that the example carries
    `api_url` while the renderer only read `ingress_host`, so the ingress kept
    its example host on any deployment that followed the documentation. Reading
    the shipped file is what catches that class of gap.
    """
    text = (ROOT / "deploy" / "production.yaml.example").read_text(encoding="utf-8")
    for placeholder, value in CONFIGURED.items():
        text = text.replace(placeholder, value)
    return yaml.safe_load(text)


def _render() -> list[dict]:
    module = _module()
    with tempfile.TemporaryDirectory(prefix="appbi-render-test-") as tmp:
        rendered = module.render_from_config(_config(), Path(tmp))
        return [d for d in yaml.safe_load_all(rendered.read_text(encoding="utf-8")) if d]


def test_the_example_config_renders_after_replacing_placeholders() -> None:
    """The whole finding, as one assertion: it has to actually run.

    No hidden fields. Replace what the file marks as a placeholder and the
    renderer produces manifests.
    """
    documents = _render()
    assert len(documents) > 5, [d.get("kind") for d in documents]
    kinds = {d["kind"] for d in documents}
    for expected in ("Deployment", "ConfigMap", "Job", "Service"):
        assert expected in kinds, kinds


def test_every_config_field_reaches_the_rendered_manifests() -> None:
    """Config as source of truth, checked on the output rather than the patch.

    Each of these was decoration before: filling it in changed nothing, and the
    installer verified one engine while the Pods pointed at another.
    """
    config = _config()
    documents = _render()

    namespaces = {d["metadata"].get("namespace") for d in documents} - {None}
    assert namespaces == {config["product"]["namespace"]}, namespaces

    images = {
        container["image"]
        for d in documents if d["kind"] in ("Deployment", "Job")
        for spec in [d["spec"]["template"]["spec"]]
        for container in spec.get("containers", []) + spec.get("initContainers", [])
    }
    expected_image = (f"{config['product']['registry']}/"
                      f"{config['product']['image']}:{config['product']['tag']}")
    assert expected_image in images, sorted(images)
    # And the repository's example registry must be gone, not merely joined.
    assert not any("registry.internal" in image for image in images), sorted(images)

    settings = next(d for d in documents
                    if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "appbi-config")
    data = settings["data"]
    assert data["AIRBYTE_API_URL"] == config["engine"]["url"]
    # The workspace lived only in a Secret, so the installer verified a
    # workspace the Pod never saw.
    assert data["AIRBYTE_WORKSPACE_ID"] == config["engine"]["workspace_id"]
    assert data["COOKIE_SECURE"] == "true"
    assert data["SEED_DEMO_DATA"] == "false"
    assert data["APP_ENV"] == "production"

    ingress = next(d for d in documents if d["kind"] == "Ingress")
    hosts = [rule["host"] for rule in ingress["spec"]["rules"]]
    # From `api_url`, because that is the field the example actually carries.
    assert "appbi.acme.io" in hosts, hosts


def test_secret_references_become_explicit_key_bindings() -> None:
    """`envFrom` meant whatever Secret happened to have that name supplied the runtime.

    Naming each key means a missing one fails the deployment instead of
    starting a Pod with it silently absent.
    """
    documents = _render()
    api = next(d for d in documents
               if d["kind"] == "Deployment" and d["metadata"]["name"] == "appbi-api")
    env = api["spec"]["template"]["spec"]["containers"][0].get("env", [])
    bound = {
        entry["name"]: entry["valueFrom"]["secretKeyRef"]
        for entry in env if "valueFrom" in entry
    }
    # Client credentials, not Basic: Airbyte 1.x answers Basic with 401, so a
    # production deployment that only binds the Basic pair cannot talk to its
    # engine at all.
    # No REDIS_URL: nothing in the product imports a Redis client, so it left
    # V1 rather than staying as a container and a managed service that exist
    # only to satisfy a config key.
    for variable in ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL",
                     "DATABASE_URL_SYNC", "AIRBYTE_CLIENT_ID", "AIRBYTE_CLIENT_SECRET"):
        assert variable in bound, sorted(bound)
        assert bound[variable]["name"] and bound[variable]["key"]


def test_the_rendered_output_carries_no_placeholder() -> None:
    module = _module()
    with tempfile.TemporaryDirectory(prefix="appbi-render-test-") as tmp:
        rendered = module.render_from_config(_config(), Path(tmp))
        text = rendered.read_text(encoding="utf-8")
        for token in ("registry.internal", "appbi.example.internal",
                      "postgres.internal", "REPLACE_ME"):
            assert token not in text, f"{token!r} survived into the manifests"
        # And the checker agrees, which is what runs before apply.
        module.assert_rendered_matches(_config(), rendered)


def test_a_config_that_disagrees_with_the_render_is_refused() -> None:
    """`assert_rendered_matches` has to be able to fail.

    A checker that passes on everything is the same as no checker, and this one
    guards the gap between "the config says" and "the cluster gets".
    """
    module = _module()
    with tempfile.TemporaryDirectory(prefix="appbi-render-test-") as tmp:
        rendered = module.render_from_config(_config(), Path(tmp))
        lying = _config()
        lying["engine"]["url"] = "https://a-different-airbyte.example"
        with pytest.raises(SystemExit):
            module.assert_rendered_matches(lying, rendered)
