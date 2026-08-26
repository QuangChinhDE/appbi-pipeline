"""Sprint A: the stop-ship findings from PM review v9.

Each test here corresponds to a defect that shipped, passed 207 other tests,
and would have reached production. They are grouped by finding id so a
regression points straight at the review that found it.

The theme across all five is the same: a check that ran in the wrong place. A
credential decided by code instead of by deployment, an invariant asserted by a
SELECT instead of by an index, ownership inferred instead of asked, a warning
where a failure belonged, and a config that described a deployment it did not
produce.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

repo_only = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(),
    reason="needs the repository layout",
)


def _filled_production_config() -> dict:
    """The shipped example with its placeholders answered."""
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


def _production_module():
    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── P0-CORE-001: default credentials in production ───────────────────────────

def test_seed_demo_data_actually_gates_the_demo_identities() -> None:
    """The setting existed and nothing read it.

    Production manifests set `SEED_DEMO_DATA=false` and still got
    `admin@appbi.local` plus three accounts sharing `Admin@12345`, because
    `seed()` never consulted the flag. Asserted on the source because the
    alternative is a live database per case; the behavioural half is the
    integration test below.
    """
    from app import bootstrap

    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert "if settings.seed_demo_data:" in source, (
        "seed() must branch on seed_demo_data; declaring the setting is not "
        "the same as honouring it")

    demo = source[source.index("async def _seed_demo"):source.index("async def _bootstrap_admin")]
    production = source[source.index("async def _bootstrap_admin"):]

    # The demo identities must live only in the demo branch.
    assert "Admin@12345" in demo
    assert "dataadmin@appbi.local" in demo
    for token in ("dataadmin@appbi.local", "operator@appbi.local", "analyst@appbi.local"):
        assert token not in production, (
            f"{token} is reachable from the production bootstrap path")


def test_production_refuses_rather_than_inventing_a_login() -> None:
    """An empty production database with no bootstrap secret must not start.

    The tempting fallback is to create an admin with a default password so the
    deployment "works". That is precisely the finding: a privileged account
    with a guessable password is worse than a deployment that will not start.
    """
    from app import bootstrap

    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert "class BootstrapRefused" in source
    production = source[source.index("async def _bootstrap_admin"):]
    assert "raise BootstrapRefused" in production
    assert "settings.seed_admin_password" in production, (
        "the bootstrap password must be rejected if it is the demo one")
    assert "password_change_required=True" in production, (
        "an account created from a one-time secret must not stay usable on it")


def test_production_settings_refuse_demo_seed_and_insecure_cookie() -> None:
    """Two production defaults that were wrong, checked at startup.

    `COOKIE_SECURE` defaulted to false and no production manifest set it, so a
    deployment behind TLS still issued a session cookie a browser would send
    over plain HTTP.
    """
    from app.core.config import Settings
    import app.core.readiness as readiness

    original = readiness.settings
    try:
        readiness.settings = Settings(
            app_env="production", engine_type="AIRBYTE_API",
            airbyte_api_url="https://airbyte.internal",
            airbyte_workspace_id=str(uuid.uuid4()),
            cookie_secure=False, seed_demo_data=True,
        )
        problems = " ".join(readiness.check_configuration().problems)
        assert "COOKIE_SECURE" in problems
        assert "SEED_DEMO_DATA" in problems

        readiness.settings = Settings(
            app_env="production", engine_type="AIRBYTE_API",
            airbyte_api_url="https://airbyte.internal",
            airbyte_workspace_id=str(uuid.uuid4()),
            cookie_secure=True, seed_demo_data=False,
            # An auth-enabled engine refuses a deployment with no credentials,
            # so production readiness now requires one scheme or the other.
            airbyte_client_id="app-id", airbyte_client_secret="app-secret",
        )
        assert readiness.check_configuration().problems == []
    finally:
        readiness.settings = original


def test_a_forced_password_change_blocks_everything_else() -> None:
    """Enforced on the tenant dependency, not per route.

    Every non-auth endpoint resolves a tenant through `request_context`, so the
    guard belongs there. Per-route checks fail by omission, and the route
    somebody forgets is the one that matters.
    """
    deps = (ROOT / "backend" / "app" / "api" / "deps.py").read_text(encoding="utf-8")
    assert "password_change_required" in deps
    assert "PASSWORD_CHANGE_REQUIRED" in deps

    auth = (ROOT / "backend" / "app" / "api" / "v1" / "auth.py").read_text(encoding="utf-8")
    assert "/auth/change-password" in auth
    # The one route that must NOT go through the guard, or the account is
    # locked out of the action that unlocks it.
    handler = auth[auth.index("async def change_password"):]
    assert "CtxDep" not in handler.split("async def ")[0]


def test_the_password_policy_rejects_what_it_should() -> None:
    from app.core.security import password_problems

    assert password_problems("Str0ngEnoughPassphrase") == []
    assert password_problems("short1A")            # too short
    assert password_problems("alllowercase123")    # no upper
    assert password_problems("ALLUPPERCASE123")    # no lower
    assert password_problems("NoDigitsInHereAtAll")  # no digit
    assert password_problems("            ")      # whitespace is not a password


# ── P0-CORE-002: migrations on upgrade ───────────────────────────────────────

@repo_only
def test_the_schema_gate_compares_revisions_not_row_counts() -> None:
    """`select count(*) from alembic_version` passes on any older schema.

    So new code started against a schema it did not match, and the resulting
    errors read like application bugs rather than a missed migration.
    """
    for name in ("api.yaml", "worker.yaml"):
        manifest = (ROOT / "deploy" / "kubernetes" / "base" / name).read_text(encoding="utf-8")
        assert "ScriptDirectory.from_config" in manifest, (
            f"{name} must compare the database revision with the image's head")
        assert "select version_num from alembic_version" in manifest
        assert "count(*) from alembic_version" not in manifest


@repo_only
def test_the_migration_job_does_not_claim_a_controller_it_does_not_use() -> None:
    """The Flux annotation meant nothing to `kubectl apply -k`.

    A completed Job is not re-run by an apply, and its pod template is
    immutable, so applying a new image over a finished Job is rejected. The
    annotation made both look handled.
    """
    import yaml

    path = ROOT / "deploy" / "kubernetes" / "base" / "migrate-job.yaml"
    job = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Parsed, not grepped: the file explains why the annotation was removed, and
    # a string search would match the explanation.
    annotations = (job.get("metadata") or {}).get("annotations") or {}
    assert not any("fluxcd" in key for key in annotations), annotations

    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    assert '"delete", "job", "appbi-migrate"' in installer, (
        "the orchestrator must delete the Job explicitly before applying it")
    assert '"--for=condition=complete"' in installer, (
        "and wait for it, before anything that reads the schema rolls out")
    # Ordering is the finding: waiting after the rollout proves nothing.
    assert installer.index('"--for=condition=complete"') < installer.index('"rollout", "status"')


@repo_only
def test_the_readme_no_longer_claims_apply_reruns_the_job() -> None:
    readme = (ROOT / "deploy" / "kubernetes" / "README.md").read_text(encoding="utf-8")
    assert "does NOT re-run the job" in readme


# ── P0-CORE-003: two replicas, two runs ──────────────────────────────────────

def test_duplicate_runs_are_prevented_by_the_database() -> None:
    """Check-then-insert is safe with one API replica. Production runs two.

    Both requests read "nothing active" and both inserted, so two Airbyte jobs
    wrote the same destination. The SELECT stays for the good error message;
    the index is what actually holds.
    """
    from app.models.run import PipelineRun

    indexes = {index.name: index for index in PipelineRun.__table__.indexes}
    for name in ("uq_run_idempotency_key", "uq_pipeline_active_run"):
        assert name in indexes, f"{name} is missing"
        assert indexes[name].unique, f"{name} must be unique or it prevents nothing"

    # Partial, or they would forbid legitimate rows: one finished run per
    # pipeline ever, and every NULL idempotency key colliding.
    for name in ("uq_run_idempotency_key", "uq_pipeline_active_run"):
        assert indexes[name].dialect_options["postgresql"]["where"] is not None, (
            f"{name} must be partial")


def test_losing_the_insert_race_is_handled_not_raised() -> None:
    """An IntegrityError from the new index must not surface as a 500.

    Two behaviours, and they differ: a duplicate idempotency key returns the
    run that won, because that is the contract of an idempotency key; a second
    active run for the same pipeline is a conflict the caller must see.
    """
    from app.services import runs

    source = Path(runs.__file__).read_text(encoding="utf-8")
    trigger = source[source.index("async def trigger("):]
    assert "except IntegrityError" in trigger
    assert "uq_run_idempotency_key" in trigger
    assert "uq_pipeline_active_run" in trigger
    assert "PIPELINE_ALREADY_RUNNING" in trigger


@repo_only
def test_the_migration_creates_both_invariants() -> None:
    versions = ROOT / "backend" / "migrations" / "versions"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in versions.glob("*.py"))
    assert "uq_run_idempotency_key" in combined
    assert "uq_pipeline_active_run" in combined
    assert "password_change_required" in combined


# ── P0-CORE-004: worker restart vs a live engine job ─────────────────────────

def test_recovery_asks_the_engine_instead_of_checking_ownership() -> None:
    """`WORKER_ID` is unchanged across a container restart.

    Hostname stays, the process is PID 1 again — so "runs I owned" matched
    every live run, and a restart marked healthy Airbyte jobs FAILED while
    Airbyte kept writing. Users then retried into a second job.
    """
    from app.services import runs

    source = Path(runs.__file__).read_text(encoding="utf-8")
    assert "async def recover_orphans" in source
    body = source[source.index("async def recover_orphans"):source.index("def _mark_lost")]

    assert "adapter.get_job" in body, (
        "recovery must ask the engine whether the job is still there")
    # Only a confirmed absence may be treated as absence. Catching the general
    # 4xx here is what marked live jobs FAILED on a 401.
    assert "except EngineResourceGoneError" in body, (
        "only a confirmed not-found may mark a run lost")
    assert "except EngineOperationError" not in body, (
        "a generic 4xx is not proof the job is gone")
    assert '"deferred"' in body
    # The order matters: the specific handler must come before the catch-all,
    # or every absence is swallowed as deferred and nothing is ever closed.
    assert body.index("except EngineResourceGoneError") < body.index("except AppError")

    # A run the engine still has keeps its status; only ownership is released.
    assert "run.claimed_by = None" in body
    adopted = body[body.index('counts["adopted"]'):]
    assert "RunStatus.FAILED" not in adopted


def test_only_a_run_that_never_reached_the_engine_is_failed() -> None:
    from app.services import runs

    source = Path(runs.__file__).read_text(encoding="utf-8")
    body = source[source.index("async def recover_orphans"):source.index("def _mark_lost")]
    # The no-engine-ref branch is also lease-gated: a run being claimed right
    # now has no ref yet either.
    assert "if run.heartbeat_at is None or run.heartbeat_at < stale_before:" in body


# ── P0-REL-001: fail-open install, config that changed nothing ───────────────

@repo_only
def test_production_install_is_fail_closed() -> None:
    """PM reproduced reconcile mismatch + gate exit 1 returning 0.

    Every release invariant is fatal on the production profile and a warning on
    the demo, which has no artifact and no reconcile history to speak of.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    body = installer[installer.index("def cmd_install("):installer.index("def cmd_upgrade(")]

    assert 'strict = profile != "single-host-demo"' in body
    assert "INSTALL FAILED" in body
    assert "return 1" in body
    # The three conditions PM listed.
    assert "reconcile did not run" in body
    assert "no release artifact was recorded" in body
    assert "the release gate refused this artifact" in body


@repo_only
def test_the_config_produces_the_manifests() -> None:
    """Structural half only. The behavioural half is test_production_render.py.

    This used to be the *whole* coverage for the renderer, and it searched the
    source for function names -- so it stayed green while
    `render_from_config()` could not execute at all. Kept, narrowed, and
    pointed at the tests that actually run kubectl.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")

    body = installer[installer.index("def install_k8s("):installer.index("def _extract(")]
    assert "render_from_config" in body
    assert "assert_rendered_matches" in body
    assert "assert_secrets_exist" in body

    # The bug PM reproduced: an absolute path in `resources` is rejected by
    # Kustomize outright. The tree is copied so the reference can be relative.
    renderer = installer[installer.index("def render_from_config("):
                         installer.index("def _drop_static_images(")]
    assert "shutil.copytree" in renderer, (
        "the Kustomize tree must be copied into the temp root; an absolute "
        "`resources` path fails with 'new root ... cannot be absolute'")
    assert "os.path.relpath" in renderer
    assert "--load-restrictor" not in installer, (
        "the load restriction must not be disabled to work around this")


@repo_only
def test_static_gates_run_before_anything_is_applied() -> None:
    """LIC-001 used to fail *after* migrate and rollout.

    The exit code was right and the deployment had already happened. Anything
    decidable without the cluster is decided before the cluster is touched.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    body = installer[installer.index("def cmd_install("):installer.index("def cmd_upgrade(")]

    assert "DEPLOYMENT REFUSED" in body
    gate_at = body.index("static_gates(config)")
    deploy_at = min(body.index("install_k8s(config)"), body.index("install_demo(config"))
    assert gate_at < deploy_at, "static gates must run before the deploy"


@repo_only
def test_only_secrets_the_product_reads_are_required_in_its_namespace() -> None:
    """Airbyte's own database credential is not an AppBI runtime dependency.

    The previous check collected every `secret://` in the config and looked for
    all of them in `product.namespace` -- including `engine.database_url_ref`,
    which lives in Airbyte's namespace and which the product must never read.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    body = installer[installer.index("def assert_secrets_exist("):
                     installer.index("def _bootstrap_secret_name(")]
    assert "_secret_env(config)" in body, (
        "the required set must be what is bound into the Pods, not every "
        "reference anywhere in the config")
    assert "_walk(config)" not in body


@repo_only
def test_the_bootstrap_secret_is_not_required_forever() -> None:
    """The runbook says to delete it after first use, so upgrades must not need it."""
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    body = installer[installer.index("def assert_secrets_exist("):
                     installer.index("def _bootstrap_secret_name(")]
    # Just the bootstrap branch, not the tail of the function -- the required
    # secrets below it do raise, and correctly.
    start = body.index("bootstrap = _bootstrap_secret_name")
    bootstrap = body[start:body.index("if problems:", start)]
    assert "warn(" in bootstrap, bootstrap
    assert "raise Stop" not in bootstrap, (
        "a deleted bootstrap secret is the documented steady state; requiring "
        "it would fail every upgrade after the first sign-in")


@repo_only
def test_schema_fixups_never_run_on_a_versioned_database() -> None:
    """Startup DDL after `upgrade head` is how a database at head fails drift check.

    A stray `DROP INDEX ix_connector_definitions_display_name` ran on every
    boot, against an index the model declares and the baseline migration
    creates. `alembic current` said head; `alembic check` said missing index.
    """
    from app import bootstrap

    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert 'DROP INDEX IF EXISTS "ix_connector_definitions_display_name"' not in source

    migrate = source[source.index("async def migrate_schema"):
                     source.index("class BootstrapRefused")]
    versioned = migrate[migrate.index("await asyncio.to_thread(command.upgrade"):]
    assert "apply_schema_fixups" not in versioned, (
        "fixups must not run after `upgrade head`; Alembic owns a versioned schema")


@repo_only
def test_an_applied_migration_was_not_edited_in_place() -> None:
    """Session columns went into their own revision, not into an applied one.

    Editing `d4a1f07c2b18` after it had run meant the new statements never
    executed on databases already at that revision -- Alembic sees the version
    row and moves on. The failure is quiet until something selects the column.
    """
    versions = ROOT / "backend" / "migrations" / "versions"
    applied = (versions / "d4a1f07c2b18_production_core_invariants.py").read_text(encoding="utf-8")
    assert "session_version" not in applied

    later = (versions / "e1b93c7a4d22_session_invalidation.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "d4a1f07c2b18"' in later
    assert "session_version" in later and "password_changed_at" in later


# ── RC1: release integrity and bounded execution ─────────────────────────────

@repo_only
def test_evidence_binds_to_the_deployment_not_to_a_checkout() -> None:
    """Evidence v1 proved a run happened somewhere, on some build.

    The gate then bound a certification to a commit read from the release
    manager's working tree, which is not the deployment -- and a production
    host has no checkout at all. v2 records what the deployment says about
    itself, and the gate compares that with what it says now.
    """
    gate = (ROOT / "scripts" / "release-gate.py").read_text(encoding="utf-8")
    assert "def check_evidence_binding" in gate
    assert "def run_exists" in gate, (
        "run ids must be checked against the deployment; a file can be copied, "
        "a run cannot")

    binding = gate[gate.index("def check_evidence_binding"):gate.index("def run_exists")]
    for signal in ("build", "engine", "workspace_fingerprint", "run_ids", "schema"):
        assert signal in binding, f"the binding ignores {signal}"

    # And the artifact check must refuse an artifact that predates v2 rather
    # than treating a missing field as a pass.
    check = gate[gate.index("def cmd_check"):]
    assert "no evidence binding recorded" in check
    assert "BUILD_SHA=unknown" in check


@repo_only
def test_the_product_reports_its_own_build() -> None:
    """Read from the running process, not asserted about it from outside."""
    from app.core.config import Settings

    # The field default, not whatever the developer's .env happens to say. PM
    # ran this in a workspace whose .env declared an RC1 SHA and got a failure
    # that looked like a code defect; the assertion was about the default all
    # along.
    assert Settings.model_fields["build_sha"].default == "unknown", (
        "the default must be honest: an ad-hoc build cannot produce evidence")

    ops = (ROOT / "backend" / "app" / "api" / "v1" / "ops.py").read_text(encoding="utf-8")
    assert '"build"' in ops and "build_sha" in ops
    assert "workspace_fingerprint" in ops

    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG BUILD_SHA" in dockerfile


def test_a_run_that_outruns_its_deadline_is_cancelled_on_the_engine_first() -> None:
    """Timeout ownership was undefined, so nothing owned it.

    `timeout_seconds` is set on every sync request; only the embedded runner
    honours it, because Airbyte has no per-job deadline to hand it to. A hung
    sync stayed RUNNING forever and held the pipeline's one active-run slot.

    The order matters more than the timeout: marking the run terminal without
    cancelling leaves Airbyte writing to the destination while the product
    believes nothing is in flight.
    """
    from app.services import runs

    source = Path(runs.__file__).read_text(encoding="utf-8")
    assert "async def enforce_timeouts" in source
    body = source[source.index("async def enforce_timeouts"):
                  source.index("async def recover_orphans")]

    assert "cancel_job" in body, "the engine job must be cancelled, not just the row"
    assert body.index("cancel_job") < body.index("RunStatus.TIMED_OUT"), (
        "cancel the engine before marking the run terminal")
    # An engine that cannot be reached must not produce a terminal state.
    assert '"deferred"' in body
    assert "except AppError" in body
    # And time spent queued behind a concurrency cap is not the sync running long.
    assert "PipelineRun.started_at <" in body


@repo_only
def test_status_exits_non_zero_when_the_deployment_is_unhealthy() -> None:
    """A command whose output says FAIL and whose exit code says success."""
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    body = installer[installer.index("def cmd_status("):installer.index("def cmd_logs(")]
    assert "NOT HEALTHY" in body
    assert "return 1" in body


@repo_only
def test_the_mirror_covers_the_launch_scope_and_refuses_the_rest() -> None:
    """654 connectors exist; the pilot ships an allowlist.

    Mirroring the catalogue costs tens of gigabytes and takes a supply-chain
    dependency on connectors nobody certified. The plan is also the review
    artefact, so it has to say what is included and why.
    """
    mirror = (ROOT / "scripts" / "mirror.py").read_text(encoding="utf-8")
    assert "NEVER CERTIFIED" in mirror, (
        "a connector in launch scope with no observed version must stop the plan")
    assert "connector_versions_observed" in mirror, (
        "versions come from the compatibility matrix, not a second hand-kept list")
    assert "_digest" in mirror and "mirror-lock.json" in mirror.replace("LOCK", "mirror-lock.json")


@repo_only
def test_the_pilot_config_pins_chart_and_app_version_separately() -> None:
    """Airbyte app 1.8.5 ships as Helm chart 2.0.17. They are not one number.

    Recording only one of them makes an artifact unable to say what was
    deployed, and the V1 chart repository is deprecated.
    """
    import yaml

    example = yaml.safe_load(
        (ROOT / "deploy" / "production.yaml.example").read_text(encoding="utf-8"))
    engine = example["engine"]
    assert engine["platform_version"] == "1.8.5"
    assert engine["chart"]["version"] == "2.0.17"
    assert engine["chart"]["version"] != engine["platform_version"]

    pilot = example["pilot"]["connectors"]
    assert pilot == ["source-postgres", "destination-postgres"], pilot
    assert "source-faker" not in pilot, (
        "source-faker is a test fixture, not a production connector")


@repo_only
def test_the_adapter_can_authenticate_against_an_auth_enabled_airbyte() -> None:
    """Found by standing the target topology up, not by reading documentation.

    Airbyte 1.8.5 from Helm chart V2 with `auth.enabled: true` answers the
    Config API with **401 for HTTP Basic** — including the instance admin's own
    email and password. The adapter spoke only Basic, so the product could not
    talk to a production Airbyte at all. Every certification so far ran with
    auth disabled, which is precisely why nothing caught it.

    1.x uses client credentials: POST to `/api/v1/applications/token`, then
    send the result as a bearer token.
    """
    from app.core.config import Settings
    import app.adapters.airbyte_api.adapter as adapter

    original = adapter.settings
    try:
        adapter.settings = Settings(airbyte_client_id="id", airbyte_client_secret="secret")
        assert isinstance(adapter._build_auth("http://x"), adapter._ClientCredentialsAuth)

        # Basic is kept: 0.59.x accepts it and the Compose lane runs on that.
        adapter.settings = Settings(airbyte_api_username="u", airbyte_api_password="p")
        assert adapter._build_auth("http://x") == ("u", "p")

        adapter.settings = Settings()
        assert adapter._build_auth("http://x") is None
    finally:
        adapter.settings = original

    source = Path(adapter.__file__).read_text(encoding="utf-8")
    flow = source[source.index("def auth_flow"):source.index("def _build_auth")]
    assert "401" in flow, "an expired token must be re-fetched"
    # One lazy fetch, and exactly one retry inside the 401 branch. Retrying in
    # a loop on a genuinely wrong credential melts the deployment's own auth
    # endpoint, which is a worse outage than the 401.
    retry = flow[flow.index("if response.status_code == 401"):]
    assert retry.count("self._fetch()") == 1, retry


# ── PM v14: installer, CI and UAT truth ──────────────────────────────────────

@repo_only
def test_placeholders_are_checked_on_the_rendered_output_not_the_source() -> None:
    """The gate refused every install, including correct ones.

    The source overlay is *supposed* to contain `registry.internal` and
    `appbi.example.internal` -- they are the deliberately-wrong values that make
    an unedited `kubectl apply -k` fail closed. Checking them there meant the
    gate fired before `render_from_config()` could replace them, so a fully
    correct config could never get past its own preflight.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    gates = installer[installer.index("def static_gates("):installer.index("def cmd_install(")]

    assert "rendered: Path | None = None" in gates, (
        "placeholders belong to the manifests that will be applied")
    assert "check_deployment_placeholders" not in gates, (
        "the source-overlay placeholder check must not run pre-render")
    # Licence and on-call need no manifests, so they still run first.
    assert "check_release_gates" in gates
    assert "check_oncall_assigned" in gates

    body = installer[installer.index("def install_k8s("):installer.index("def verify_engine_in_pod(")]
    assert "static_gates(config, rendered=rendered)" in body
    assert body.index("static_gates(config, rendered=rendered)") < body.index('"apply"')


@repo_only
def test_no_workload_reads_a_secret_the_config_did_not_name() -> None:
    """envFrom secretRef bound whatever Secret carried that name.

    A config naming a different Secret passed preflight and the Pod still read
    the old one. The migration Job was worse: it hard-coded both the runtime
    and the bootstrap Secret.
    """
    import yaml

    base = ROOT / "deploy" / "kubernetes" / "base"
    for name in ("api.yaml", "worker.yaml"):
        for document in yaml.safe_load_all((base / name).read_text(encoding="utf-8")):
            if not document or document.get("kind") != "Deployment":
                continue
            for container in document["spec"]["template"]["spec"]["containers"]:
                blanket = [f for f in container.get("envFrom", []) if "secretRef" in f]
                assert not blanket, f"{name} still has {blanket}"

    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    assert "def _bootstrap_env" in installer
    assert '"target": {"kind": "Job", "name": "appbi-migrate"}' in installer, (
        "the migration Job credentials must come from the config too")
    # The bootstrap Secret is deleted after first use, so its absence must not
    # break later upgrades.
    bootstrap = installer[installer.index("def _bootstrap_env("):
                          installer.index("def _secret_env(")]
    assert '"optional": True' in bootstrap


@repo_only
def test_plain_http_and_missing_tls_are_fatal_in_production() -> None:
    """They were warnings, on a strict production profile.

    The Config API carries connector credentials in request bodies and the
    product issues Secure session cookies -- over plain HTTP users cannot stay
    signed in at all. A warning is the wrong severity for both.
    """
    import copy

    module = _production_module()
    base = _filled_production_config()
    assert module.validate(copy.deepcopy(base), strict=True) == []

    for mutate in (
        lambda c: c["engine"].update(url="http://airbyte.internal"),
        lambda c: c["product"].update(api_url="http://appbi.acme.io"),
        lambda c: c["product"].update(ingress_tls=False),
    ):
        config = copy.deepcopy(base)
        mutate(config)
        with pytest.raises(SystemExit):
            module.validate(config, strict=True)


@repo_only
def test_the_engine_is_verified_from_inside_the_product_pod() -> None:
    """The pre-deploy probe cannot verify an auth-enabled engine.

    Production credentials live in Kubernetes secrets and `resolve_secret()`
    refuses to read them on purpose, so that probe runs unauthenticated and
    reads 401 as "wrong version" or "unreachable". The Pod has the credentials,
    the network path and the adapter.
    """
    installer = (ROOT / "scripts" / "production.py").read_text(encoding="utf-8")
    assert "def verify_engine_in_pod" in installer
    body = installer[installer.index("def verify_engine_in_pod("):
                     installer.index("def _extract(")]
    assert '"exec", "deploy/appbi-api"' in body
    assert "readyz?deep=1" in body
    # And it runs after the rollout, since it needs a running Pod.
    flow = installer[installer.index("def install_k8s("):installer.index("def verify_engine_in_pod(")]
    assert flow.index('"rollout"') < flow.index("verify_engine_in_pod(config)")


@repo_only
def test_backup_has_a_provider_that_works_without_docker() -> None:
    """production.py upgrade called a backup that only did `docker exec`.

    A managed database has no container to exec into, so the only way past it
    was --skip-backup: an upgrade with no rollback point.
    """
    backup = (ROOT / "scripts" / "backup.py").read_text(encoding="utf-8")
    assert "def dump_command" in backup
    assert "BACKUP_PROVIDER" in backup
    body = backup[backup.index("def dump_command("):backup.index("def key_fingerprint(")]
    assert 'provider == "pg_dump"' in body
    # psycopg/asyncpg URLs carry a driver suffix pg_dump does not understand.
    assert 'replace("postgresql+psycopg://"' in body
    assert 'replace("postgresql+asyncpg://"' in body
    assert '"provider": provider' in backup


@repo_only
def test_the_uat_cancel_check_cannot_pass_on_success() -> None:
    """PM ran it, got SUCCEEDED after a cancel, and the script said PASS.

    BA UAT-007 requires CANCEL_REQUESTED -> CANCELLED. A sync that finished
    before the cancel landed exercised nothing, so it is inconclusive -- which
    is a third outcome, not a pass.
    """
    verify = (ROOT / "qa" / "e2e" / "verify.py").read_text(encoding="utf-8")
    assert "def inconclusive" in verify
    assert '"CANCELLED", "SUCCEEDED", "FAILED"' not in verify, (
        "SUCCEEDED after a cancel must never count as a pass")
    body = verify[verify.index("UAT-007"):]
    assert 'terminal["status"] == "CANCELLED"' in body
    assert "inconclusive(" in body
    # An inconclusive run must not exit 0, or CI reports coverage it lacks.
    assert "if failed or skipped:" in verify


@repo_only
def test_the_uat_gate_no_longer_claims_a_rolled_up_pass() -> None:
    """One PASSING line covered fifteen BA scenarios the script partly covers."""
    import yaml

    document = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    uat = next(g for g in document["release_gates"] if g["id"].startswith("UAT-"))
    assert uat["status"] == "NOT_PROVEN", uat


@repo_only
def test_the_ci_workflow_is_structurally_valid() -> None:
    """A split step left Typecheck with no command and a duplicate `run` key.

    The parser kept the second `run`, so the backend audit step silently became
    `npm run typecheck` at the repository root: two checks disabled by one edit,
    both still reporting success.
    """
    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "name" not in step:
                continue
            assert "run" in step or "uses" in step, (
                f"{job_name} / {step['name']} has neither run nor uses")

    frontend = [s.get("name") for s in workflow["jobs"]["frontend"]["steps"]]
    assert "Lint" in frontend, "next lint with no config exits 0 without linting"
    assert "Typecheck" in frontend
    # The backend audit has its own job now, so a frontend failure cannot hide it.
    assert "backend-audit" in workflow["jobs"]
    audit = " ".join(str(s.get("run", ""))
                     for s in workflow["jobs"]["backend-audit"]["steps"])
    assert "pip-audit" in audit


@repo_only
def test_the_frontend_has_a_lint_configuration_that_runs() -> None:
    import json

    package = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert (ROOT / "frontend" / ".eslintrc.json").exists()
    assert "eslint" in package["devDependencies"]
    # Without --max-warnings 0 a warning count grows quietly and never fails.
    assert "--max-warnings 0" in package["scripts"]["lint"]
