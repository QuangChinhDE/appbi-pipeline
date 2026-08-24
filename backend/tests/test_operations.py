"""Tests for the operational surface: readiness, key rotation, metrics.

These cover the parts an operator depends on when something is wrong, which is
exactly when nobody is in a position to debug them. Each test here corresponds
to a decision that could plausibly have gone the other way, and states why it
did not.
"""

from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

repo_only = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(),
    reason="needs the repository layout",
)


# ── readiness: the shallow/deep split ────────────────────────────────────────
# The failure this prevents: pointing a load balancer at a check that fails when
# the engine is down, which removes every API instance from rotation during an
# engine outage. Nobody can then read run history, see why the engine is down,
# or acknowledge the alert saying so — a partial outage becomes a total one.

def test_shallow_readiness_does_not_require_the_engine() -> None:
    from app.core.readiness import DependencyState

    database = DependencyState("database", True)
    engine = DependencyState("engine", False, "unreachable", required=False)
    assert all(s.ok for s in (database, engine) if s.required)


def test_deep_readiness_does_require_the_engine() -> None:
    from app.core.readiness import DependencyState

    database = DependencyState("database", True)
    engine = DependencyState("engine", False, "unreachable", required=True)
    assert not all(s.ok for s in (database, engine) if s.required)


def test_a_dependency_state_reports_its_detail() -> None:
    """An operator needs the reason, not just the verdict."""
    from app.core.readiness import DependencyState

    assert DependencyState("engine", False, "boom").as_dict() == {
        "ok": False, "required": True, "detail": "boom",
    }
    # No detail key when there is nothing to say, so a healthy body stays terse.
    assert "detail" not in DependencyState("engine", True).as_dict()


@repo_only
def test_readiness_probe_is_wired_to_the_endpoint() -> None:
    """`/readyz` must go through probe(), not re-implement a DB ping."""
    import inspect

    from app import main

    source = inspect.getsource(main.readyz)
    assert "probe(" in source, "the endpoint has its own idea of readiness"
    assert "deep" in source, "there is no way to ask the deep question"
    assert "503" in source or "SERVICE_UNAVAILABLE" in source, (
        "a not-ready answer that returns 200 is not a readiness check")


@repo_only
def test_startup_does_not_die_on_an_unreachable_engine_by_default() -> None:
    """Configuration errors are fatal; unreachability is not.

    A fresh deployment usually starts alongside its engine. Dying because the
    engine has not finished booting produces a crash loop whose cause looks
    like the product rather than the ordering.
    """
    import inspect

    from app.core import readiness

    source = inspect.getsource(readiness.probe_engine_at_startup)
    assert "startup_require_engine" in source, (
        "there is no way for an operator to opt into fail-fast")
    raise_position = source.index("raise RuntimeError")
    guard_position = source.index("settings.startup_require_engine")
    assert guard_position < raise_position, "it raises unconditionally"


# ── KEK rotation ─────────────────────────────────────────────────────────────
# Envelope encryption exists so the master key can be rotated without touching
# the credentials. If rotation had to decrypt them, nobody would ever do it.

@pytest.mark.asyncio
async def test_rotation_rewraps_the_data_key_and_leaves_ciphertext_alone(
    monkeypatch,
) -> None:
    from cryptography.fernet import Fernet

    from app.core.secrets import build_kek

    old_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    new_key = base64.urlsafe_b64encode(os.urandom(32)).decode()

    data_key = Fernet.generate_key()
    ciphertext = Fernet(data_key).encrypt(b'{"password": "hunter2"}')
    wrapped_old = build_kek(old_key).encrypt(data_key)

    # What rewrap_all does to one record, without needing a database.
    rewrapped = build_kek(new_key).encrypt(build_kek(old_key).decrypt(wrapped_old))

    # The new key unwraps it, and the credential is still readable.
    recovered = build_kek(new_key).decrypt(rewrapped)
    assert recovered == data_key
    assert Fernet(recovered).decrypt(ciphertext) == b'{"password": "hunter2"}'

    # And the old key no longer does. Without this the rotation was a no-op.
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        build_kek(old_key).decrypt(rewrapped)


def test_a_weak_rotation_key_is_refused() -> None:
    """The policy that guards SECRET_ENCRYPTION_KEY must guard the new one too.

    Rotating onto a passphrase would quietly reduce every credential to that
    passphrase's entropy — the exact hole the startup check exists to close.
    """
    from app.core.config import settings
    from app.core.secrets import build_kek

    original = settings.allow_derived_encryption_key
    try:
        settings.allow_derived_encryption_key = False
        with pytest.raises(RuntimeError):
            build_kek("hunter2")
    finally:
        settings.allow_derived_encryption_key = original


@repo_only
def test_rotation_is_safe_to_re_run() -> None:
    """An interrupted rotation must be finishable, not restart-from-scratch.

    Records already rewrapped no longer unwrap with the old key. Treating that
    as an error would strand a half-done rotation; treating it as "already
    done" lets a second pass finish the job.
    """
    import inspect

    from app.core import secrets

    source = inspect.getsource(secrets.rewrap_all)
    assert "InvalidToken" in source, "a record that does not unwrap is not handled"
    assert "skipped" in source, "there is no way to see what was left alone"
    # Per-batch commits, so an interruption keeps the work already done.
    assert "await session.commit()" in source


# ── metrics ──────────────────────────────────────────────────────────────────

@repo_only
def test_metrics_report_a_failed_collection_instead_of_erroring() -> None:
    """A scrape endpoint that 500s takes the monitoring down with the thing it
    monitors. It must answer, and say it failed."""
    import inspect

    from app.api import metrics

    source = inspect.getsource(metrics.metrics)
    assert "appbi_metrics_up" in source
    assert "except Exception" in source, "a collection failure would 500"


@repo_only
def test_metrics_are_not_under_the_versioned_api() -> None:
    """Metrics describe the deployment, not a tenant, and are not part of the
    product contract. Putting them under /api/v1 would make them one."""
    import inspect

    from app import main

    source = inspect.getsource(main)
    mount = source.index("app.include_router(metrics_module.router)")
    # No prefix argument on that call.
    line_end = source.index("\n", mount)
    assert "prefix" not in source[mount:line_end]


# ── ops scripts exist and are honest about what they need ────────────────────

@repo_only
def test_the_backup_script_records_which_key_it_belongs_to() -> None:
    """A dump restored without its KEK is a database of unreadable secrets, and
    the symptom is every source failing with a decryption error rather than
    anything naming the real cause."""
    source = (ROOT / "scripts/backup.py").read_text(encoding="utf-8")
    assert "kek_fingerprint" in source
    assert "sha256" in source, "a backup with no integrity check is a hope"
    # The fingerprint must be a digest, never the key: the backup directory is
    # not where a key belongs.
    assert "hashlib.sha256" in source
    assert "SECRET_ENCRYPTION_KEY" in source


@repo_only
def test_the_restore_refuses_a_mismatched_key_by_default() -> None:
    source = (ROOT / "scripts/backup.py").read_text(encoding="utf-8")
    assert "accept_key_mismatch" in source, "a mismatch cannot be overridden at all"
    assert "KEK MISMATCH" in source, "a mismatch is not reported"


@repo_only
def test_every_ops_script_forces_utf8_output() -> None:
    """Windows starts Python on a legacy codepage, so the first Vietnamese
    character in an API response ends the script with a UnicodeEncodeError that
    reads like the product failed."""
    scripts = ["stack.py", "backup.py", "rotate-kek.py", "release-gate.py",
               "verify-egress.py", "airbyte-workspace.py", "e2e.py"]
    for name in scripts:
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "force_utf8" in source, f"{name} will die on non-ASCII output"


# ── what the deployment exposes ──────────────────────────────────────────────

@repo_only
def test_only_the_proxy_is_published_on_every_interface() -> None:
    """`/metrics` carries no authentication, by design — it is meant to be
    scraped on an internal network. That is only true if the API port is not
    on every interface. nginx is the public entry point and does not proxy
    /metrics; everything else binds to loopback.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    public = []
    for name, service in compose["services"].items():
        for mapping in service.get("ports") or []:
            text = str(mapping)
            if not text.startswith("127.0.0.1:"):
                public.append(f"{name}: {text}")

    assert public == [f"proxy: ${{PROXY_PORT:-8080}}:80"], (
        f"published on every interface: {public}")


@repo_only
def test_nginx_does_not_proxy_metrics() -> None:
    """The public edge must not carry an unauthenticated metrics endpoint."""
    config = (ROOT / "docker/nginx/nginx.conf").read_text(encoding="utf-8")
    assert "/metrics" not in config


# ── Kubernetes manifests ─────────────────────────────────────────────────────
# Not exercised on a cluster yet, so these guard the things that would be wrong
# regardless of cluster: settings the application does not have, probes pointed
# at the wrong endpoint, and a socket nobody should be granted.

def _k8s_documents() -> list[dict]:
    import yaml

    directory = ROOT / "deploy/kubernetes/base"
    documents: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        documents += [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    return documents


@repo_only
def test_kubernetes_config_names_only_real_settings() -> None:
    """Every key in the ConfigMap must be a setting the application reads.

    A typo here is invisible: the pod starts, the setting silently keeps its
    default, and the deployment behaves differently from what the manifest
    says it does.
    """
    from app.core.config import Settings

    known = {name.upper() for name in Settings.model_fields}
    # Set per-container rather than in the ConfigMap.
    known |= {"SERVICE_NAME"}

    config = next(d for d in _k8s_documents() if d["kind"] == "ConfigMap")
    unknown = sorted(set(config["data"]) - known)
    assert not unknown, f"ConfigMap sets settings the application does not have: {unknown}"


@repo_only
def test_kubernetes_readiness_is_not_the_deep_probe() -> None:
    """`?deep=1` requires the engine. Wiring it to readiness would remove every
    pod from the Service during an Airbyte outage, turning a partial outage
    into a total one — the whole reason the two probes are separate."""
    for document in _k8s_documents():
        if document["kind"] != "Deployment":
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            probe = (container.get("readinessProbe") or {}).get("httpGet") or {}
            path = probe.get("path", "")
            assert "deep" not in path, (
                f"{document['metadata']['name']}: readiness points at {path}")


@repo_only
def test_kubernetes_grants_no_docker_socket_and_no_root() -> None:
    """In AIRBYTE_API mode nothing here starts a container. A pod that can
    reach a container runtime is a privilege nobody needs."""
    for document in _k8s_documents():
        if document["kind"] not in ("Deployment", "Job"):
            continue
        spec = document["spec"]["template"]["spec"]
        name = document["metadata"]["name"]

        for volume in spec.get("volumes") or []:
            host_path = (volume.get("hostPath") or {}).get("path", "")
            assert "docker.sock" not in host_path and "containerd" not in host_path, (
                f"{name} mounts a container runtime socket")

        assert (spec.get("securityContext") or {}).get("runAsNonRoot") is True, (
            f"{name} does not require a non-root user")

        # initContainers included: they run with the same access as the rest of
        # the pod, and a privileged one is a privileged pod.
        for container in (spec.get("initContainers") or []) + spec["containers"]:
            security = container.get("securityContext") or {}
            assert security.get("allowPrivilegeEscalation") is False, (
                f"{name}/{container['name']}")
            assert security.get("readOnlyRootFilesystem") is True, (
                f"{name}/{container['name']}")


@repo_only
def test_kubernetes_egress_is_deny_by_default() -> None:
    """A policy that starts permissive and subtracts is one forgotten rule away
    from allowing everything."""
    policies = [d for d in _k8s_documents() if d["kind"] == "NetworkPolicy"]
    assert policies, "no NetworkPolicy at all"

    default_deny = [
        p for p in policies
        if p["spec"].get("podSelector") == {}
        and set(p["spec"].get("policyTypes") or []) == {"Ingress", "Egress"}
        and not p["spec"].get("egress") and not p["spec"].get("ingress")
    ]
    assert default_deny, "there is no default-deny policy"


@repo_only
def test_alert_rules_reference_metrics_that_exist() -> None:
    """An alert on a metric nobody emits never fires, and never says so.

    The rule loads, Prometheus evaluates it against no series, and the alert is
    silently dead — which is worse than having no alert, because the dashboard
    says it is covered.
    """
    import re

    import yaml

    from app.api import metrics as metrics_module

    emitted = set(re.findall(r'"(appbi_[a-z_]+)"',
                             inspect_source(metrics_module)))
    assert emitted, "no metric names found in the metrics module"

    document = yaml.safe_load(
        (ROOT / "deploy/monitoring/alerts.yaml").read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for group in document["groups"]:
        for rule in group["rules"]:
            referenced |= set(re.findall(r"(appbi_[a-z_]+)", rule["expr"]))

    unknown = sorted(referenced - emitted)
    assert not unknown, f"alert rules reference metrics that are not emitted: {unknown}"


@repo_only
def test_every_alert_rule_waits_before_firing() -> None:
    """`appbi_engine_reachable` drops to 0 during any Airbyte restart. Paging on
    a deploy is how people learn to ignore the pager."""
    import yaml

    document = yaml.safe_load(
        (ROOT / "deploy/monitoring/alerts.yaml").read_text(encoding="utf-8"))
    missing = [rule["alert"] for group in document["groups"]
               for rule in group["rules"] if not rule.get("for")]
    assert not missing, f"alert rules with no `for:` clause: {missing}"


def inspect_source(module) -> str:
    import inspect

    return inspect.getsource(module)


@repo_only
def test_kubernetes_pods_depend_on_no_third_party_image() -> None:
    """Every container runs an image this project builds.

    Found on a real cluster: an init container used `bitnami/kubectl:1.30` to
    wait for the migration Job, and that tag does not exist — the API sat in
    ImagePullBackOff. It also needed a ServiceAccount, a Role and a RoleBinding
    so a container could watch a Job. Asking the database whether the migration
    landed needs none of that and checks the thing we actually care about
    rather than a proxy for it.
    """
    ours = {"appbi-pipeline-backend", "appbi-pipeline-frontend"}
    for document in _k8s_documents():
        if document["kind"] not in ("Deployment", "Job"):
            continue
        spec = document["spec"]["template"]["spec"]
        containers = (spec.get("initContainers") or []) + spec["containers"]
        for container in containers:
            image = container["image"].split(":")[0]
            assert image in ours, (
                f"{document['metadata']['name']}/{container['name']} runs "
                f"{container['image']}, which this project does not build")


@repo_only
def test_kubernetes_sets_image_pull_policy_explicitly() -> None:
    """Kubernetes picks the default from the tag name — `Always` for `:latest`,
    `IfNotPresent` otherwise — so the behaviour changes silently with how an
    image happens to be tagged."""
    for document in _k8s_documents():
        if document["kind"] not in ("Deployment", "Job"):
            continue
        spec = document["spec"]["template"]["spec"]
        for container in (spec.get("initContainers") or []) + spec["containers"]:
            assert container.get("imagePullPolicy"), (
                f"{document['metadata']['name']}/{container['name']} "
                "inherits a tag-dependent pull policy")


# ── the RENDERED manifests ───────────────────────────────────────────────────
# Reading the source files misses everything Kustomize does to them. It missed
# a real one: `commonLabels` rewrites selectors as well as metadata, and it
# added a product label to the kube-dns podSelector. kube-dns has no such
# label, so on a cluster that enforces NetworkPolicy the DNS rule would match
# nothing and every other egress rule would fail looking like the destination
# was down. Source-level tests were green throughout.

import shutil
import subprocess

_kustomize_available = shutil.which("kubectl") is not None
rendered_only = pytest.mark.skipif(
    not _kustomize_available, reason="needs kubectl to render the kustomization")


def _rendered(overlay: str = "overlays/production") -> list[dict]:
    import yaml

    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy/kubernetes" / overlay)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"kustomize build failed: {result.stderr[:400]}"
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@repo_only
@rendered_only
def test_rendered_network_policy_leaves_foreign_selectors_alone() -> None:
    """A selector pointing at someone else's pods must survive rendering intact.

    kube-dns, the ingress controller and Airbyte are not labelled by this
    project. Any transformer that adds labels to their selectors turns the rule
    into one that matches nothing — silently, since the object still applies.
    """
    ours = "app.kubernetes.io/part-of"
    for document in _rendered():
        if document["kind"] != "NetworkPolicy":
            continue
        name = document["metadata"]["name"]
        for direction in ("ingress", "egress"):
            for rule in document["spec"].get(direction) or []:
                for peer in rule.get("to") or rule.get("from") or []:
                    selector = (peer.get("podSelector") or {}).get("matchLabels") or {}
                    assert ours not in selector, (
                        f"{name}: a product label was injected into a selector "
                        f"for pods this project does not own: {selector}")


@repo_only
@rendered_only
def test_rendered_dns_egress_still_matches_kube_dns() -> None:
    """The specific rule the bug broke. Without DNS nothing else resolves."""
    for document in _rendered():
        if document["kind"] != "NetworkPolicy":
            continue
        for rule in document["spec"].get("egress") or []:
            for peer in rule.get("to") or []:
                selector = (peer.get("podSelector") or {}).get("matchLabels") or {}
                if "k8s-app" in selector:
                    assert selector == {"k8s-app": "kube-dns"}, (
                        f"the DNS selector is no longer exactly kube-dns: {selector}")
                    return
    pytest.fail("no DNS egress rule found in the rendered policies")


@repo_only
@rendered_only
def test_rendered_images_are_rewritten_to_the_registry() -> None:
    """`kubectl apply -k` must not ship the local build tag to production."""
    for document in _rendered():
        if document["kind"] not in ("Deployment", "Job"):
            continue
        spec = document["spec"]["template"]["spec"]
        for container in (spec.get("initContainers") or []) + spec["containers"]:
            image = container["image"]
            assert image.startswith("registry.internal/"), (
                f"{container['name']} renders as {image}, not a registry reference")
            assert not image.endswith(":latest"), (
                f"{container['name']} renders as a floating tag: {image}")


@repo_only
@rendered_only
def test_rendered_objects_all_land_in_the_namespace() -> None:
    for document in _rendered():
        if document["kind"] in ("Namespace", "ClusterRole", "ClusterRoleBinding"):
            continue
        assert document["metadata"].get("namespace") == "appbi", (
            f"{document['kind']}/{document['metadata']['name']} has no namespace")


@repo_only
@rendered_only
def test_the_base_alone_is_not_shippable() -> None:
    """Applying the base by mistake must fail, not open egress somewhere wrong.

    The base carries a placeholder database CIDR and no registry. Both are
    environment facts; an overlay supplies them. If the base ever renders into
    something that looks deployable, someone will deploy it.
    """
    base = _rendered("base")

    images = [
        container["image"]
        for document in base if document["kind"] in ("Deployment", "Job")
        for container in ((document["spec"]["template"]["spec"].get("initContainers") or [])
                          + document["spec"]["template"]["spec"]["containers"])
    ]
    assert all(not image.startswith("registry.") for image in images), (
        f"the base already names a registry: {images}")


@repo_only
@rendered_only
def test_the_production_overlay_carries_no_placeholder() -> None:
    """The placeholder subnet must not survive into a rendered production
    manifest. `10.0.0.0/24` is nobody's real network, and a policy allowing it
    is either broken or accidentally wide."""
    for document in _rendered():
        if document["kind"] != "NetworkPolicy":
            continue
        for rule in document["spec"].get("egress") or []:
            for peer in rule.get("to") or []:
                cidr = (peer.get("ipBlock") or {}).get("cidr")
                if cidr:
                    assert cidr != "10.0.0.0/24", (
                        "the production overlay still renders the base's "
                        "placeholder CIDR")
                    prefix = int(cidr.split("/")[1])
                    assert prefix >= 16, (
                        f"{cidr} is wide enough to make deny-by-default "
                        "decorative; narrow it to the actual subnet")


# ── the product's database is its own ────────────────────────────────────────

def test_sharing_a_database_with_the_engine_is_detected() -> None:
    """Guardrail 2 was enforced by discipline until this existed.

    Measured on the staging stack: the product's role could read all 47 of
    Airbyte's tables. Nothing would have noticed a service starting to do so,
    and one convenient SELECT during an incident becomes a dependency on a
    schema nobody promised us. See docs/ADR-001-database-topology.md.
    """
    from app.core.readiness import AIRBYTE_TABLES

    # Names that only mean something in an Airbyte configuration database.
    assert {"actor", "connection", "workspace", "attempts"} <= AIRBYTE_TABLES

    # And none of them may collide with a table this product owns, or the check
    # would refuse to start against a perfectly good database.
    import app.models  # noqa: F401 - registers every table on the metadata
    from app.core.db import Base

    ours = set(Base.metadata.tables)
    collisions = AIRBYTE_TABLES & ours
    assert not collisions, (
        f"these names are used by both the product and Airbyte, so the "
        f"separation check would false-positive: {collisions}")


@repo_only
def test_sharing_a_database_is_fatal_everywhere() -> None:
    """Not a production-only rule.

    Sharing an *instance* is a cost decision someone may make knowingly, and is
    warned about. Sharing a *database* is not a decision anyone would defend —
    Airbyte migrates that schema on its own schedule — so it refuses to start
    regardless of APP_ENV.
    """
    import inspect

    from app.core import readiness

    source = inspect.getsource(readiness.enforce_at_startup)
    fatal = source.index("if separation_problems:")
    production_only = source.index("if settings.is_production:")
    assert fatal < production_only, (
        "the separation failure is gated behind the production check, so a "
        "staging deployment would start against Airbyte's own database")


@repo_only
def test_kubernetes_ships_no_database() -> None:
    """The manifests must not tempt anyone into running Postgres in-cluster
    beside the product — ADR-001 puts it on a managed instance of its own."""
    for document in _k8s_documents():
        if document["kind"] not in ("Deployment", "StatefulSet"):
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            image = container["image"].lower()
            assert "postgres" not in image and "redis" not in image, (
                f"{document['metadata']['name']} runs {container['image']}; "
                "ADR-001 keeps datastores out of these manifests")


# ── reconcile: what the engine still has ─────────────────────────────────────
# Written after a real cross-deployment run. The first version of this report
# said "17 resources are missing" when what it had actually found was rows
# belonging to a different adapter, and the remediation it suggested was to
# recreate them. A reconcile that is confidently wrong is worse than none.

class _FakeMapping:
    def __init__(self, engine_type, resource_type, ref, product_id):
        self.engine_type = engine_type
        self.engine_resource_type = resource_type
        self.engine_resource_ref = ref
        self.product_resource_id = product_id
        self.workspace_id = uuid.uuid4()


def _reconcile_source() -> str:
    from app.services import reconcile as module
    return Path(module.__file__).read_text(encoding="utf-8")


def test_an_unreachable_engine_reports_nothing_rather_than_everything() -> None:
    """The failure mode that makes a reconcile dangerous.

    If a 5xx counted as "absent", one engine restart would report every
    resource as lost -- and the action that follows from that report is to
    recreate them all. So a single unreachable answer discards the partial
    result instead of publishing it.
    """
    source = _reconcile_source()
    assert "report.missing.clear()" in source, (
        "an unreachable engine must discard partial findings, not report them")
    assert "engine_reachable = False" in source
    # And the discard has to come before the return, not after some of the list
    # has already been built up for display.
    clear_at = source.index("report.missing.clear()")
    return_at = source.index("return report", clear_at)
    assert clear_at < return_at


def test_mappings_from_another_engine_are_not_called_missing() -> None:
    """A ref written by the embedded adapter is not an address on Airbyte.

    Measured: a staging database with both had 17 such rows, and every one was
    reported as a lost resource until this partition existed.
    """
    source = _reconcile_source()
    assert "m.engine_type == adapter.engine_type" in source, (
        "reconcile must only ask the engine about rows that engine wrote")
    assert "report.foreign" in source


def test_the_reconcile_response_carries_no_engine_reference() -> None:
    """Guardrail 3, on a payload that is tempting to make debuggable.

    The obvious thing to put in this report is the engine id that was checked.
    That is exactly the leak the guardrail forbids, and the operator does not
    need it: they need to know which source is gone.
    """
    from app.schemas.domain import EngineReconcileItem

    fields = set(EngineReconcileItem.model_fields)
    assert fields == {"resource_type", "resource_id", "name"}, fields
    assert not any("engine" in name for name in fields)


@repo_only
def test_reconcile_has_three_exit_codes() -> None:
    """`missing` and `unreachable` call for opposite actions.

    Collapsing them into a single non-zero exit hands the operator the wrong
    one half the time: recreate resources, or wait for the engine to come back.
    """
    script = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    assert "return 2" in script and "return 1 if report.get" in script


# ── the compatibility claim ──────────────────────────────────────────────────

@repo_only
def test_every_tested_platform_has_a_certification_block() -> None:
    """A version in the matrix must point at evidence, not at optimism.

    `tested_platform_versions` is what the product tells the world it works
    against. It is one line to add and there is no natural moment at which
    anyone checks it again, so the check is here: each listed version needs a
    certification block recording what was actually exercised on it.
    """
    import yaml

    document = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))

    api = next(entry for entry in document["adapter"]["implementations"]
               if entry["id"] == "AIRBYTE_API")
    claimed = set(api["tested_platform_versions"])

    certified = {
        block["platform_version"]
        for key, block in document.items()
        if key.startswith("airbyte_api_certification") and isinstance(block, dict)
    }
    assert claimed == certified, (
        f"claimed {sorted(claimed)} but certification blocks cover "
        f"{sorted(certified)}")


@repo_only
def test_a_certification_block_claims_nothing_it_did_not_run() -> None:
    """Every operation marked true must be one the release gate requires.

    The failure this prevents is a block that quietly grows an operation the
    gate has never heard of — which then reads as certified and is gated on
    nothing. The reverse (the gate requiring more than a block claims) is
    already impossible: the gate derives its list from these blocks.
    """
    import yaml

    document = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    blocks = [block for key, block in document.items()
              if key.startswith("airbyte_api_certification") and isinstance(block, dict)]
    assert len(blocks) >= 2, "expected Compose and Kubernetes certifications"

    baseline = set(blocks[0]["verified"])
    for block in blocks[1:]:
        assert set(block["verified"]) == baseline, (
            f"{block['platform_version']} certifies a different set of "
            "operations; the gate reads one list and would miss the difference")


@repo_only
def test_the_oncall_runbook_names_alerts_that_exist() -> None:
    """One source of truth for alert names, checked rather than trusted.

    The runbook carried its own copy of the rules and it drifted: the page said
    `AppBIMetricsCollectionFailing` and `AppBIRunsStuck` while the rules file
    declared `AppBIMetricsDegraded` and `AppBIRunStuck`. Nothing failed. An
    operator searching their alerting system for a name from the runbook would
    simply have found nothing, at the worst possible moment.

    Only the alert table is compared. Prose may name a retired alert when
    explaining why it is retired -- forbidding that would push the history out
    of the document that needs it.
    """
    import re

    import yaml

    rules = yaml.safe_load(
        (ROOT / "deploy" / "monitoring" / "alerts.yaml").read_text(encoding="utf-8"))
    declared = {rule["alert"]
                for group in rules["groups"] for rule in group["rules"]}

    runbook = (ROOT / "docs" / "RUNBOOK-oncall.md").read_text(encoding="utf-8")
    tabled = {m.group(1)
              for line in runbook.splitlines() if line.startswith("| `AppBI")
              for m in [re.match(r"\| `(AppBI\w+)`", line)] if m}

    assert tabled, "the runbook's alert table went missing"
    assert tabled <= declared, (
        f"the runbook pages on alerts that do not exist: {sorted(tabled - declared)}")
    assert declared <= tabled, (
        f"rules exist that the runbook never tells anyone what to do about: "
        f"{sorted(declared - tabled)}")


@repo_only
def test_the_release_gate_reads_the_legal_gate() -> None:
    """`LIC-001` sat in the file the gate was reading and blocked nothing.

    The gate derived its required operations from `compatibility.yaml` and
    stopped there, so a gate declared in the same document -- "Airbyte
    licensing approved for the intended delivery model", status `NOT_CLEARED` --
    never entered the decision. Gating the tests is not gating the release.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "release_gate", ROOT / "scripts" / "release-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    failures = module.check_release_gates()
    assert any("LIC-001" in f for f in failures), (
        "LIC-001 is NOT_CLEARED in compatibility.yaml and must block a release")

    # And an unrecognised status must fail closed rather than pass by omission.
    source = (ROOT / "scripts" / "release-gate.py").read_text(encoding="utf-8")
    assert "passing = {" in source and "not in passing" in source, (
        "the check must allow-list passing statuses, not deny-list failing ones")


# ── the production entrypoint ────────────────────────────────────────────────
# PM's requirement was one command an operator can run, that stops early and
# specifically on anything a production deployment must not have. These test
# the refusals, because a bootstrap that only works when everything is already
# right is not the part that earns its keep.

def _production_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _filled_production_config() -> dict:
    """The shipped template with its placeholders answered."""
    import yaml

    text = (ROOT / "deploy" / "production.yaml.example").read_text(encoding="utf-8")
    for token, value in (
        ("<your-production-context>", "prod-eu"),
        ("<registry.example.com>", "registry.acme.io"),
        ("<appbi.example.com>", "appbi.acme.io"),
        ("<airbyte.internal.example.com>", "airbyte.internal.acme.io"),
        ("<workspace-uuid>", "8b8a2621-7f31-46f3-82e6-36774a9ff3a6"),
        ("<ops@example.com>", "ops@acme.io"),
        ("<the internal team or design partner>", "Internal Data Team"),
        ("<e.g. business hours, Asia/Bangkok>", "business hours, Asia/Bangkok"),
    ):
        text = text.replace(token, value)
    return yaml.safe_load(text)


@repo_only
def test_the_shipped_production_template_cannot_be_installed() -> None:
    """A template that deploys is a template that ships to production.

    Every placeholder in the example must stop the install, and the message
    must name the field — "config is invalid" sends someone reading YAML line
    by line.
    """
    import yaml

    module = _production_module()
    config = yaml.safe_load(
        (ROOT / "deploy" / "production.yaml.example").read_text(encoding="utf-8"))
    with pytest.raises(SystemExit):
        module.validate(config, strict=True)


@repo_only
def test_a_filled_production_config_validates() -> None:
    """The other half. A validator nothing satisfies teaches people to bypass it."""
    module = _production_module()
    assert module.validate(_filled_production_config(), strict=True) == []


@repo_only
@pytest.mark.parametrize("what,change", [
    ("a floating tag", lambda c: c["product"].update(tag="latest")),
    ("an uncertified engine", lambda c: c["engine"].update(platform_version="2.9.9")),
    ("a literal secret", lambda c: c["secrets"].update(jwt_secret_ref="hunter2")),
    ("a malformed workspace id", lambda c: c["engine"].update(workspace_id="nope")),
    ("one database for both",
     lambda c: c["engine"].update(database_url_ref=c["datastores"]["database_url_ref"])),
])
def test_production_install_refuses(what: str, change) -> None:
    """Each of these has a specific way of going wrong later and quietly.

    A floating tag invalidates a certification with nothing reporting it. An
    uncertified engine runs connector versions nobody tested. A literal secret
    ends up in a ticket. A malformed workspace id is caught here, but a
    well-formed one from the wrong Airbyte is only caught by doctor against the
    live engine. One database erodes the boundary the whole architecture rests
    on.
    """
    module = _production_module()
    config = _filled_production_config()
    change(config)
    with pytest.raises(SystemExit):
        module.validate(config, strict=True)


@repo_only
def test_the_demo_profile_refuses_to_be_production() -> None:
    """The easiest way to run a demo in production is to make it convenient."""
    import yaml

    module = _production_module()
    config = yaml.safe_load((ROOT / "deploy" / "demo.yaml").read_text(encoding="utf-8"))

    # As shipped it validates, with a warning that says what it is not.
    warnings = module.validate(config, strict=True)
    assert any("not a production topology" in w for w in warnings)

    config["app_env"] = "production"
    with pytest.raises(SystemExit):
        module.validate(config, strict=True)


@repo_only
def test_the_demo_profile_pins_a_certified_engine_version() -> None:
    """Both profiles go through one validator, so the demo cannot drift.

    If the demo were exempted, the version check would only ever run in
    production — the one place nobody wants to discover it is broken.
    """
    import yaml

    module = _production_module()
    config = yaml.safe_load((ROOT / "deploy" / "demo.yaml").read_text(encoding="utf-8"))
    assert config["engine"]["platform_version"] in module.certified_platform_versions()


# ── launch scope ─────────────────────────────────────────────────────────────

def test_the_catalogue_offers_only_what_the_release_stands_behind() -> None:
    """654 connectors in the registry, three certified.

    Eleven adapter operations proven against Postgres demonstrates the engine
    integration. It demonstrates nothing about the other 651, which differ in
    auth, pagination, incremental semantics and failure modes. Default to the
    curated set; a deployment that wants the full catalogue says so and owns
    the promise.
    """
    from app.core.config import Settings

    curated = Settings(connector_launch_scope="SUPPORTED_ONLY")
    assert curated.connector_is_offered("source-postgres", "SUPPORTED")
    assert not curated.connector_is_offered("source-hubspot", "BETA")
    assert not curated.connector_is_offered("source-anything", "BLOCKED")

    everything = Settings(connector_launch_scope="FULL_CATALOG")
    assert everything.connector_is_offered("source-hubspot", "BETA")
    # BLOCKED means blocked regardless of scope: it is a decision about that
    # connector, not about the release.
    assert not everything.connector_is_offered("source-anything", "BLOCKED")

    opted_in = Settings(connector_launch_scope="SUPPORTED_ONLY",
                        connector_beta_allowlist="source-hubspot, source-stripe")
    assert opted_in.connector_is_offered("source-hubspot", "BETA")
    assert not opted_in.connector_is_offered("source-zendesk", "BETA")


@repo_only
def test_launch_scope_is_enforced_on_the_create_path_not_just_the_card() -> None:
    """A greyed-out card with a working endpoint behind it is decoration.

    `require_usable` is the single chokepoint every source and destination
    creation passes through, so the rule belongs there. The presenter applies
    the same function so the catalogue agrees with the API rather than
    reimplementing the judgement.
    """
    catalog = (ROOT / "backend" / "app" / "services" / "catalog.py").read_text(encoding="utf-8")
    presenter = (ROOT / "backend" / "app" / "api" / "v1" / "presenters.py").read_text(encoding="utf-8")

    assert "connector_is_offered" in catalog, (
        "require_usable must apply the launch scope; without it the API "
        "accepts connectors the catalogue refuses to show")
    assert "CONNECTOR_NOT_IN_LAUNCH_SCOPE" in catalog
    assert "connector_is_offered" in presenter, (
        "the presenter must use the same function, not a second copy of the rule")
