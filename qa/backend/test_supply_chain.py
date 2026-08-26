"""What runs in production, and where it is allowed to send traffic.

These are the checks a production review asked for: one version number, one
locked set of images, and a connector that cannot be pointed at our own
network.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.core.errors import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# Some of these read repo files (compose, scripts, the lock) that are not copied
# into the runtime image, so they run on a checkout and skip inside a container.
# CI runs both, which is where they matter.
repo_only = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(),
    reason="repo-level check: needs a checkout, not the runtime image",
)


# ── one product version, not three ─────────────────────────────────────────
# Before: the generated registry said 2.0.0 while the runtime config and the
# compatibility matrix both said 1.0.0, because the generator hard-coded its own.

def _compatibility_version() -> str:
    text = (ROOT / "compatibility.yaml").read_text(encoding="utf-8")
    match = re.search(r'^product_version:\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "compatibility.yaml lost its product_version"
    return match.group(1)


@repo_only
def test_every_file_agrees_on_the_product_version() -> None:
    from app.core.config import settings

    expected = _compatibility_version()
    registry = json.loads(
        (ROOT / "backend/app/resources/connector_registry.json").read_text(encoding="utf-8"))

    assert settings.product_version == expected, "runtime config disagrees"
    assert registry["product_version"] == expected, "generated registry disagrees"


@repo_only
def test_the_generator_reads_the_version_rather_than_declaring_one() -> None:
    source = (ROOT / "scripts/build-connector-registry.py").read_text(encoding="utf-8")
    assert "compatibility.yaml" in source
    assert not re.search(r'^PRODUCT_VERSION\s*=', source, re.MULTILINE), (
        "a second hard-coded version is how the first drift happened")


@repo_only
def test_certification_comes_from_the_evidence_file() -> None:
    """compatibility.yaml records what was verified; the registry must not
    invent a stronger claim than the evidence supports.

    Before: `source-file` shipped as SUPPORTED because it was hand-curated,
    while compatibility.yaml listed it as BETA with only check/discover/
    full_refresh verified.
    """
    text = (ROOT / "compatibility.yaml").read_text(encoding="utf-8")
    evidence: dict[str, str] = {}
    current = None
    for line in text.split(chr(10) + "connectors:", 1)[1].splitlines():
        name = re.match(r"^  ([a-z0-9][a-z0-9._-]*):\s*$", line)
        if name:
            current = name.group(1)
        level = re.match(r"^    certification:\s*([A-Z_]+)\s*$", line)
        if level and current:
            evidence[current] = level.group(1)

    registry = json.loads(
        (ROOT / "backend/app/resources/connector_registry.json").read_text(encoding="utf-8"))
    for entry in registry["connectors"]:
        expected = evidence.get(entry["connector_key"], "BETA")
        assert entry["certification"] == expected, (
            f"{entry['connector_key']}: registry says {entry['certification']}, "
            f"compatibility.yaml says {expected}")


@repo_only
def test_the_generator_does_not_imply_certification_from_curation() -> None:
    source = (ROOT / "scripts/build-connector-registry.py").read_text(encoding="utf-8")
    assert '"SUPPORTED" if curated' not in source, (
        "hand-writing metadata is not evidence of certification")


# ── the certified set is pinned by content, not by name ────────────────────

@repo_only
def test_the_lock_covers_every_supported_connector() -> None:
    registry = json.loads(
        (ROOT / "backend/app/resources/connector_registry.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "connector-lock.json").read_text(encoding="utf-8"))

    supported = {c["connector_key"] for c in registry["connectors"]
                 if c["certification"] == "SUPPORTED"}
    locked = {c["connector_key"] for c in lock["connectors"]}
    assert supported <= locked, f"unlocked SUPPORTED connectors: {supported - locked}"


@repo_only
def test_the_lock_records_a_digest_not_just_a_tag() -> None:
    """A tag can be repushed; a digest cannot. Locking only the tag records a
    promise the registry is free to break."""
    lock = json.loads((ROOT / "connector-lock.json").read_text(encoding="utf-8"))
    for entry in lock["connectors"]:
        assert entry["digest"], f"{entry['connector_key']} has no digest"
        assert entry["digest"].startswith("sha256:")
        assert ":" in entry["image"] and not entry["image"].endswith(":latest")


@repo_only
def test_the_declarative_runner_is_locked_too() -> None:
    """It executes every connector built in the product, so a drift there
    changes all of them at once."""
    lock = json.loads((ROOT / "connector-lock.json").read_text(encoding="utf-8"))
    runners = [c for c in lock["connectors"] if c["certification"] == "RUNNER"]
    assert runners, "the declarative runner is not locked"
    assert all(c["digest"] for c in runners)


# ── pre-pulling is scoped to what we actually support ──────────────────────

def _pull_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pull_engine_images", ROOT / "scripts" / "pull-engine-images.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@repo_only
def test_the_prepull_set_is_exactly_the_product_catalogue() -> None:
    """Pull what a user can select. Not less, and nowhere near the engine's list.

    Two failure modes, and this product has had both.

    Too many: the engine's bootloader seeds Airbyte's *current* catalogue, so
    `{source,destination}_definitions/list` answers with six hundred-odd
    connectors. Pre-pulling from that is tens of gigabytes of images nobody can
    choose in the wizard.

    Too few, which is the one that actually shipped: the script kept its own
    hardcoded set of four repositories while the product offered eight, so
    `source-bigquery`, `destination-bigquery`, `source-google-sheets` and
    `destination-google-sheets` were never pre-pulled and each stalled its
    first sync inside a job -- where the timeout surfaces as ENGINE_UNAVAILABLE
    and reads like a broken engine rather than a cold cache.

    So the set is derived, never written down twice.
    """
    import json

    registry = json.loads(
        (ROOT / "backend/app/resources/connector_registry.json").read_text(
            encoding="utf-8"))
    expected = {
        entry["docker_repository"]: entry["version"]
        for entry in registry["connectors"]
        if (entry.get("docker_repository") or "").startswith("airbyte/")
    }
    # The runner is not a catalogue entry -- no user selects it -- but every
    # connector built in the product runs inside it.
    from app.services.builder_manifest import RUNNER_VERSION

    expected["airbyte/source-declarative-manifest"] = RUNNER_VERSION

    assert _pull_script().bundled() == expected


@repo_only
def test_the_prepull_covers_beta_connectors_too() -> None:
    """A BETA connector is still selectable, so it still has to be local.

    `connector-lock.json` deliberately covers only SUPPORTED connectors -- a
    lock is a guarantee and BETA carries none. Reading the pull set from the
    lock would therefore skip `destination-google-sheets`, which a user can
    pick today. The pull set comes from the registry for exactly this reason.
    """
    import json

    lock = {
        entry["image"].rpartition(":")[0]
        for entry in json.loads(
            (ROOT / "connector-lock.json").read_text(encoding="utf-8"))["connectors"]
    }
    wanted = _pull_script().bundled()
    beta = set(wanted) - lock
    assert "airbyte/destination-google-sheets" in beta, (
        "the BETA destination is no longer outside the lock -- if it became "
        "SUPPORTED this test should assert the new shape, not be deleted")


# ── a connector may not be aimed at our own network ────────────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8000/",
    "http://localhost/",
    "http://10.0.0.5/api",
    "http://192.168.1.10/",
    "http://[::1]/",
])
def test_internal_targets_are_refused(url: str) -> None:
    from app.core import egress

    with pytest.raises(ValidationError) as caught:
        egress.check_url(url)
    assert caught.value.code in {"EGRESS_PRIVATE_ADDRESS", "EGRESS_DNS_FAILED"}


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/",
])
def test_only_http_schemes_are_allowed(url: str) -> None:
    from app.core import egress

    with pytest.raises(ValidationError) as caught:
        egress.check_url(url)
    assert caught.value.code == "EGRESS_SCHEME_BLOCKED"


def test_a_public_target_is_allowed() -> None:
    from app.core import egress

    egress.check_url("https://api.github.com/repos")


def test_the_builder_refuses_an_internal_literal_on_save() -> None:
    """A literal internal address needs no lookup to recognise, so the save path
    can reject it without depending on the network."""
    from app.services import builder_manifest as builder

    definition = {
        "name": "x", "base_url": "http://169.254.169.254",
        "auth": {"method": "none"},
        "streams": [{"name": "s", "path": "/", "pagination": {"mode": "none"}}],
    }
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "EGRESS_PRIVATE_ADDRESS"


def test_saving_a_draft_does_not_require_the_host_to_resolve() -> None:
    """Blocking a save because DNS is unavailable would make the editor unusable
    on a laptop, behind split-horizon DNS, or mid-typing."""
    from app.services import builder_manifest as builder

    definition = {
        "name": "x", "base_url": "https://api.not-registered-yet.invalid",
        "auth": {"method": "none"},
        "streams": [{"name": "s", "path": "/", "pagination": {"mode": "none"}}],
    }
    builder.validate(definition)          # must not raise


def test_the_resolving_check_still_catches_a_name_pointing_inward() -> None:
    from app.core import egress

    with pytest.raises(ValidationError) as caught:
        egress.check_url("http://localhost.localdomain/")
    assert caught.value.code == "EGRESS_PRIVATE_ADDRESS"


def test_an_operator_can_allow_an_internal_api_deliberately() -> None:
    """Blocking private ranges outright would make the product unusable for an
    internal API, so the escape hatch has to exist — and be explicit."""
    from app.core import egress
    from app.core.config import settings

    original = settings.egress_allowlist
    try:
        settings.egress_allowlist = "10.0.0.0/8"
        egress.check_url("http://10.0.0.5/api")
    finally:
        settings.egress_allowlist = original


# ── the network split is the layer that actually holds ─────────────────────

@repo_only
def test_connectors_run_on_their_own_network() -> None:
    """An application-layer check cannot survive DNS rebinding. The connector
    network not containing the API is what does.

    Parsed as YAML rather than grepped: an earlier version of this test searched
    the file as text and passed or failed on whether a comment happened to
    contain the word.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "connectors" in compose["networks"]

    from app.core.config import settings
    assert settings.engine_docker_network == "appbi-pipeline_connectors"

    # Redis is gone from V1 -- nothing imported a client, so it was a container
    # and a managed service that existed only to satisfy a config key.
    for service in ("api", "worker"):
        attached = compose["services"][service].get("networks") or []
        assert "connectors" not in attached, (
            f"{service} is reachable from the connector network")

    # Postgres is deliberately attached: a Postgres source must reach it, and it
    # demands credentials of its own.
    assert "connectors" in (compose["services"]["postgres"].get("networks") or [])


@repo_only
def test_airbyte_starts_connectors_on_the_isolated_network() -> None:
    """In AIRBYTE_API mode Airbyte's worker launches the connectors, not us.

    So the network split has to be asserted where Airbyte is told about it. If
    DOCKER_NETWORK goes missing the connectors land on the default bridge with
    the product's API, and every test in this file still passes — the isolation
    would be gone with nothing to show for it.
    """
    import yaml

    overlay = yaml.safe_load(
        (ROOT / "docker-compose.airbyte.yml").read_text(encoding="utf-8"))
    worker = overlay["services"]["airbyte-worker"]["environment"]
    assert worker.get("DOCKER_NETWORK") == "appbi-pipeline_connectors", (
        "Airbyte would start connector containers on the default network")

    # And the socket stays with the process that needs it. The product's own API
    # and worker must not acquire one just because Airbyte is in the stack.
    for name in ("api", "worker"):
        service = overlay["services"].get(name) or {}
        for volume in service.get("volumes") or []:
            assert "docker.sock" not in str(volume), (
                f"the Airbyte overlay hands {name} the Docker socket")


@repo_only
def test_the_airbyte_overlay_switches_the_engine() -> None:
    """An overlay that boots Airbyte but leaves the product on the embedded
    executor would look right and certify the wrong thing."""
    import yaml

    overlay = yaml.safe_load(
        (ROOT / "docker-compose.airbyte.yml").read_text(encoding="utf-8"))
    for name in ("api", "worker"):
        environment = overlay["services"][name].get("environment") or {}
        assert environment.get("ENGINE_TYPE") == "AIRBYTE_API", (
            f"{name} would run the embedded engine while Airbyte sits idle")
        assert environment.get("AIRBYTE_API_URL"), f"{name} has no engine to talk to"


@repo_only
def test_the_backend_services_share_one_image() -> None:
    """`migrate`, `api` and `worker` must be the same build.

    Compose tags each service's build separately by default, so
    `docker compose build api worker` leaves `migrate` on the previous image.
    The next deploy then runs its migrations from stale code and reports
    success: the observed symptom was a new column missing after a deploy that
    had definitely been rebuilt.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    images = {name: compose["services"][name].get("image")
              for name in ("migrate", "api", "worker")}

    assert all(images.values()), f"a backend service has no explicit image: {images}"
    assert len(set(images.values())) == 1, (
        f"the backend services build to different images: {images}")



@repo_only
def test_the_default_deployment_mounts_no_docker_socket() -> None:
    """A container that can reach the daemon can start another one with the host
    filesystem mounted. The production engine never needs it, so the default
    deployment does not grant it."""
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for volume in service.get("volumes") or []:
            assert "docker.sock" not in str(volume), (
                f"{name} mounts the Docker socket in the default deployment")


@repo_only
def test_the_embedded_overlay_is_where_the_socket_lives() -> None:
    """The local-demo path still has to work, and it has to be opt-in."""
    import yaml

    overlay = yaml.safe_load(
        (ROOT / "docker-compose.embedded.yml").read_text(encoding="utf-8"))
    mounts = [
        volume
        for service in overlay["services"].values()
        for volume in (service.get("volumes") or [])
        if "docker.sock" in str(volume)
    ]
    assert mounts, "the overlay no longer provides the socket the demo needs"
    for service in overlay["services"].values():
        assert service.get("environment", {}).get("ENGINE_TYPE") == "AIRBYTE_EMBEDDED"


# ── migrations, not create_all ─────────────────────────────────────────────

@repo_only
def test_a_migration_baseline_exists() -> None:
    """`create_all` cannot bring an existing database forward, so a deployment
    holding real data needs migrations from the first release, not the first
    schema change."""
    versions = sorted((ROOT / "backend/migrations/versions").glob("*.py"))
    assert versions, "no Alembic revision has been generated"

    baseline = versions[0].read_text(encoding="utf-8")
    assert "down_revision: str | None = None" in baseline, "the first revision has a parent"
    # A baseline that only carries a diff would leave a fresh database empty.
    assert baseline.count("op.create_table") > 10


@repo_only
def test_migrations_ship_inside_the_image() -> None:
    """An image that cannot migrate the database it connects to is an image that
    has to be paired with a checkout at deploy time."""
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini" in dockerfile
    assert "COPY migrations" in dockerfile


@repo_only
def test_bootstrap_records_the_migration_head() -> None:
    """`create_all` leaves Alembic blind to what the database contains, so the
    first real migration would try to create tables that already exist.

    The fix is no longer a blind stamp — `migrate_schema` runs the migrations,
    and only adopts an existing unversioned database when its shape already
    matches the models. This asserts on that, because a stamp that is not
    conditional is the bug this test was written for, wearing a different name.
    """
    import inspect

    from app import bootstrap

    assert hasattr(bootstrap, "migrate_schema")
    source = inspect.getsource(bootstrap.migrate_schema)
    assert "upgrade" in source, "an empty or versioned database must be migrated"
    assert "stamp" in source, "an existing database has to be adopted, not recreated"
    assert "_schema_matches_models" in source, "adoption without a diff is a blind stamp"

    assert inspect.getsource(bootstrap.main).count("migrate_schema") >= 1


# ── the key-encryption key fails closed ────────────────────────────────────
# Before: a key that was not 32 bytes was hashed into one after a warning, so a
# short passphrase silently became the entropy protecting every credential.

def test_a_weak_encryption_key_is_refused() -> None:
    from app.core import secrets as secret_module
    from app.core.config import settings

    original_key = settings.secret_encryption_key
    original_flag = settings.allow_derived_encryption_key
    try:
        settings.secret_encryption_key = "hunter2"
        settings.allow_derived_encryption_key = False
        with pytest.raises(RuntimeError) as caught:
            secret_module._kek()
        assert "32-byte" in str(caught.value)
    finally:
        settings.secret_encryption_key = original_key
        settings.allow_derived_encryption_key = original_flag


def test_deriving_a_key_requires_saying_so() -> None:
    """The convenience still exists for local work, but a deployment has to ask
    for it by name rather than get it by accident."""
    from app.core import secrets as secret_module
    from app.core.config import settings

    original_key = settings.secret_encryption_key
    original_flag = settings.allow_derived_encryption_key
    try:
        settings.secret_encryption_key = "hunter2"
        settings.allow_derived_encryption_key = True
        assert secret_module._kek() is not None
    finally:
        settings.secret_encryption_key = original_key
        settings.allow_derived_encryption_key = original_flag


def test_a_proper_key_is_accepted() -> None:
    import base64
    import os

    from app.core import secrets as secret_module
    from app.core.config import settings

    original = settings.secret_encryption_key
    try:
        settings.secret_encryption_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        assert secret_module._kek() is not None
    finally:
        settings.secret_encryption_key = original


# ── no path may surface an unpinned connector as "latest" ──────────────────

def test_the_api_adapter_never_invents_a_latest_tag() -> None:
    """A release gate forbids `latest`. Defaulting a missing tag to it made an
    unknown version look like a deliberate pin."""
    import inspect

    from app.adapters.airbyte_api import adapter

    source = inspect.getsource(adapter)
    # Only executable defaults matter; the word appears in the comment that
    # explains why it is forbidden.
    code = chr(10).join(line for line in source.splitlines()
                        if not line.lstrip().startswith("#"))
    assert '"latest"' not in code, "the API adapter still falls back to latest"
    assert adapter.UNPINNED != "latest"


# ── every outbound URL goes through the egress gate ────────────────────────

def test_the_oauth_token_endpoint_is_covered_by_the_policy() -> None:
    """It is an outbound request like any other, and the one carrying the
    client secret. It used to be scheme-checked only."""
    from app.services import builder_manifest as builder

    definition = {
        "name": "x", "base_url": "https://api.example.com",
        "auth": {"method": "oauth2", "oauth": {"token_url": "http://169.254.169.254/token"}},
        "streams": [{"name": "s", "path": "/", "pagination": {"mode": "none"}}],
    }
    with pytest.raises(ValidationError) as caught:
        builder.validate(definition)
    assert caught.value.code == "EGRESS_PRIVATE_ADDRESS"
    assert caught.value.details["field"] == "auth.oauth.token_url"


def test_outbound_urls_is_the_single_list_the_policy_walks() -> None:
    """A new URL field has to be added in one place, or the checks fall behind
    the compiler — which is how the token endpoint was missed."""
    from app.services import builder_manifest as builder

    definition = {
        "name": "x", "base_url": "https://api.example.com",
        "auth": {"method": "oauth2",
                 "oauth": {"token_url": "https://auth.example.com/token"}},
        "streams": [{"name": "s", "path": "/", "pagination": {"mode": "none"}}],
    }
    fields = {field for field, _ in builder.outbound_urls(definition)}
    assert fields == {"base_url", "auth.oauth.token_url"}


# ── launch scope: the settings have to be reachable ──────────────────────────

def test_every_launch_scope_setting_can_actually_be_set() -> None:
    """`Settings` had them; Compose did not pass them; so nobody could set them.

    `docker-compose.yml` enumerates the backend environment explicitly rather
    than passing the whole `.env` through, which is the right call -- but it
    means a new setting is inert until it is added here. `CONNECTOR_LAUNCH_SCOPE`
    and `CONNECTOR_BETA_ALLOWLIST` were read by the presenter and the create
    path and could not be configured on any Compose deployment: every one of
    them silently got the default.

    The same hole swallowed `AIRBYTE_CLIENT_ID`/`AIRBYTE_CLIENT_SECRET`, which
    is the only way `AIRBYTE_API` mode can authenticate to Airbyte 1.x.
    """
    import yaml

    root = ROOT
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    passed = set(compose["x-backend-env"])

    for name in ("CONNECTOR_LAUNCH_SCOPE", "CONNECTOR_BETA_ALLOWLIST",
                 "AIRBYTE_CLIENT_ID", "AIRBYTE_CLIENT_SECRET",
                 "AIRBYTE_API_URL", "AIRBYTE_WORKSPACE_ID", "ENGINE_TYPE"):
        assert name in passed, (
            f"{name} is a real setting that no Compose deployment can set")


def test_the_offered_connectors_are_the_ones_with_recorded_evidence() -> None:
    """The catalogue's promise and the evidence file have to be the same list.

    654 connectors ship in the registry. The ones the product offers by default
    are those `compatibility.yaml` records a real `check` for -- anything else
    is a claim about software nobody here has run.
    """
    import yaml

    root = ROOT
    compatibility = yaml.safe_load(
        (root / "compatibility.yaml").read_text(encoding="utf-8"))
    registry = json.loads(
        (root / "backend" / "app" / "resources" / "connector_registry.json")
        .read_text(encoding="utf-8"))

    supported_in_registry = {c["connector_key"] for c in registry["connectors"]
                             if c.get("certification") == "SUPPORTED"}
    supported_in_evidence = {key for key, entry in compatibility["connectors"].items()
                             if entry.get("certification") == "SUPPORTED"}
    assert supported_in_registry == supported_in_evidence, (
        supported_in_registry ^ supported_in_evidence)

    # And every SUPPORTED connector claims a passing check, since that is what
    # "we can stand behind this" reduces to operationally.
    for key in supported_in_evidence:
        assert compatibility["connectors"][key]["verified"].get("check") is True, key


def test_every_supported_connector_is_pinned_by_digest() -> None:
    """A tag is a mutable pointer; the lock is what says which bytes ran."""
    import yaml

    root = ROOT
    lock = json.loads((root / "connector-lock.json").read_text(encoding="utf-8"))
    compatibility = yaml.safe_load(
        (root / "compatibility.yaml").read_text(encoding="utf-8"))

    locked = {entry["connector_key"]: entry for entry in lock["connectors"]}
    for key, entry in compatibility["connectors"].items():
        if entry.get("certification") != "SUPPORTED":
            continue
        assert key in locked, f"{key} is SUPPORTED but absent from connector-lock.json"
        assert locked[key]["digest"], f"{key} is locked by tag only"
        assert locked[key]["image"].endswith(str(entry["pinned_version"])), (
            key, locked[key]["image"], entry["pinned_version"])


# ── the vendored engine ──────────────────────────────────────────────────────

def test_the_engine_is_pinned_by_digest_and_archived() -> None:
    """The engine has to survive Airbyte deleting it.

    0.59.1 is the last Airbyte that runs a sync under Docker Compose, so it is
    the version this product is built on — and a version that old will not stay
    on Docker Hub forever. `docker pull` is somebody else's decision about what
    to keep.
    """
    import json as _json

    lock_path = ROOT / "engine-lock.json"
    assert lock_path.exists(), "no engine-lock.json"
    lock = _json.loads(lock_path.read_text(encoding="utf-8"))

    assert lock["platform_version"] == "0.59.1"
    images = {entry["image"]: entry["digest"] for entry in lock["images"]}

    # The orchestrator is the one that gets forgotten: it is not in the Compose
    # file, the worker spawns it per job, and a machine without it starts
    # cleanly and fails on the first sync.
    assert "airbyte/container-orchestrator:0.59.1" in images

    for image, digest in images.items():
        assert digest.startswith("sha256:"), f"{image} is pinned by tag only"


def test_the_compose_stack_pins_the_same_digests() -> None:
    """A tag is a mutable pointer; the compose file must not rely on one."""
    import json as _json

    import yaml

    lock = {e["image"]: e["digest"]
            for e in _json.loads((ROOT / "engine-lock.json").read_text(encoding="utf-8"))["images"]}
    compose = yaml.safe_load(
        (ROOT / "docker-compose.airbyte.yml").read_text(encoding="utf-8"))

    for name, service in compose["services"].items():
        image = service.get("image", "")
        if not any(image.startswith(f"{repo}@") or image.startswith(f"{repo.split(':')[0]}:")
                   for repo in lock):
            continue
        if "airbyte/" not in image and "minio/" not in image:
            continue
        assert "@sha256:" in image, f"{name} runs {image}, pinned by tag only"
        tagged = image.split("@")[0]
        if tagged in lock:
            assert image.split("@")[1] == lock[tagged], (
                f"{name} pins a digest the engine lock does not know")


def test_the_engine_connector_versions_are_re_pinned_on_every_boot() -> None:
    """Airbyte 0.59.1 re-seeds its connector definitions from the *current*
    upstream catalogue on every start.

    So an old platform comes up offering brand-new connectors it cannot run:
    `destination-postgres` arrives as 3.x, which needs the `generationId` of
    the refresh protocol, and this platform never sends it. The sync then dies
    at the first record with `getGenerationId(...) must not be null`, having
    passed `check` and looked healthy throughout.

    A pin applied by hand is wiped by the next `docker compose up`. This is the
    check that the stack re-applies it itself, and that the product waits for
    it rather than racing an unpinned engine.
    """
    import yaml

    compose = yaml.safe_load(
        (ROOT / "docker-compose.airbyte.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "airbyte-connector-pin" in services, (
        "nothing re-pins the engine's connector versions")
    pin = services["airbyte-connector-pin"]
    assert pin["depends_on"]["airbyte-server"]["condition"] == "service_healthy"

    # The product must not start against an unpinned engine.
    for name in ("api", "worker"):
        condition = services[name]["depends_on"]["airbyte-connector-pin"]["condition"]
        assert condition == "service_completed_successfully", (
            f"{name} does not wait for the connector pin")

    assert (ROOT / "scripts" / "pin-engine-connectors.py").exists()


def test_the_destination_pins_match_what_the_platform_can_run() -> None:
    """Destinations are held below upstream, and that is the platform's doing.

    Anything declaring `supportsRefreshes` needs `generationId`, which 0.59.1
    does not send. Raising one of these means raising the platform, which means
    Kubernetes — so the constraint is recorded here rather than rediscovered.
    """
    import yaml

    compatibility = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    pins = {k: str(v["pinned_version"])
            for k, v in compatibility["connectors"].items()}

    assert pins["destination-postgres"].startswith("2."), pins["destination-postgres"]
    assert pins["destination-bigquery"].startswith("2."), pins["destination-bigquery"]
    assert "0.59.1" in compatibility["runtime"]["engine"]
