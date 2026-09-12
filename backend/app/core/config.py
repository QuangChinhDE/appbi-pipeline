"""Runtime configuration.

Everything the control plane needs comes from the environment so the same
image runs as api / worker / one-shot bootstrap.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict



def _bundled_product_version() -> str:
    """The version stamped into the bundled connector registry.

    `scripts/build-connector-registry.py` copies it out of compatibility.yaml,
    so the number the API reports is the same number the compatibility matrix
    documents. If the registry is unreadable the process still starts — an
    unknown version is a reporting problem, not a reason to refuse traffic.
    """
    path = Path(__file__).resolve().parent.parent / "resources" / "connector_registry.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["product_version"])
    except Exception:
        return "0.0.0-unknown"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    service_name: str = "product-api"
    log_level: str = "INFO"
    # Not a literal: compatibility.yaml is the release record, and the registry
    # build stamps its product_version into the bundled registry. Two places
    # claiming a version is one place too many — the one that ships with the
    # image wins. See _bundled_product_version below.
    product_version: str = Field(default_factory=_bundled_product_version)

    # --- storage ---
    database_url: str = "postgresql+asyncpg://appbi:appbi@localhost:5432/appbi_integration"
    database_url_sync: str = "postgresql+psycopg://appbi:appbi@localhost:5432/appbi_integration"

    # --- auth ---
    jwt_secret: str = "dev-jwt-secret-change-me"
    jwt_algorithm: str = "HS256"
    session_ttl_seconds: int = 60 * 60 * 12
    session_cookie_name: str = "appbi_session"
    cookie_secure: bool = False

    # --- secrets ---
    # urlsafe-base64 32-byte KEK. Data keys are generated per secret and
    # wrapped with this (envelope encryption), so rotating the KEK does not
    # require re-encrypting every payload.
    secret_encryption_key: str = "ZGV2LW9ubHktMzItYnl0ZS1rZXktZm9yLWFwcGJpLTAwMDA="

    # --- engine ---
    engine_type: str = "AIRBYTE_EMBEDDED"
    engine_workspace_dir: str = "/engine/workspace"
    engine_log_dir: str = "/engine/logs"
    # Connector containers run here, and this network deliberately does not
    # reach the product API: a connector is user-controlled code pointed at a
    # user-controlled URL.
    engine_docker_network: str = "appbi-pipeline_connectors"

    # Egress policy for connectors (see app/core/egress.py). Private targets are
    # refused unless an operator allows them explicitly.
    # Deriving the key-encryption key from a passphrase is a development
    # convenience that silently weakens every stored credential, so it is off
    # unless a deployment asks for it.
    allow_derived_encryption_key: bool = False

    egress_allow_private: bool = False
    egress_allowlist: str = ""
    engine_workspace_volume: str = "appbi-pipeline_engine_workspace"
    engine_docker_binary: str = "docker"

    # --- connector container resources ---
    #
    # Two containers run per sync -- a source and a destination -- and until
    # this existed neither had a ceiling. `docker run` without `--memory` lets
    # one connector grow into all of the host's RAM, and what happens next is
    # the kernel's OOM killer choosing a victim: often the connector, sometimes
    # this API or the worker. A product failing because something unrelated was
    # killed is the worst kind of bug to read, and Airbyte does not have it
    # because its worker passes JOB_MAIN_CONTAINER_MEMORY_LIMIT to every job.
    #
    # A ceiling converts that into a connector that fails on its own, with a
    # message naming memory, while everything else keeps running.
    #
    # Budget it as: MAX_CONCURRENT_RUNS_GLOBAL x 2 x this, plus ~800 MB for
    # AppBI and ~200 MB for Postgres, must fit in the host. Empty disables the
    # ceiling and restores the old unbounded behaviour.
    #
    # 2 GB rather than 1, and the number came from a measurement rather than a
    # guess. Base Service against a live tenant emitted 2,553 records totalling
    # 729 MB -- roughly 326 KB per ticket -- and pages 500 of them at a time, so
    # one page alone is ~163 MB before the CDK's own overhead. At 1 GB the
    # source was killed at record 820 with exit 137; at 2 GB the same sync
    # completed. A ceiling that the product's own flagship connectors cannot
    # work under is worse than no ceiling, because it fails on a healthy
    # tenant and blames memory for what looks like a product fault.
    connector_memory_limit: str = "2g"
    #: CPU shares per connector container, as `docker run --cpus`. Empty means
    #: unbounded. On a 2-core box an unbounded connector starves the API's
    #: event loop, which shows up as a UI that stops responding mid-sync.
    connector_cpu_limit: str = ""
    #: Airbyte's Java connectors (the BigQuery, Postgres and MSSQL
    #: destinations) size their heap from the container limit. A container-aware
    #: JVM defaults to a quarter of it, so a 1 GB ceiling would leave a 256 MB
    #: heap and the destination would fail for a reason that looks nothing like
    #: the cause. Raising the fraction is what makes the ceiling usable.
    #: Ignored by Python connectors, which read no JAVA_OPTS.
    connector_java_opts: str = "-XX:MaxRAMPercentage=75.0"
    adapter_contract_version: str = "1"

    # Readiness policy. Both default to the lenient answer on purpose; see
    # app/core/readiness.py for why a load balancer must not be told the control
    # plane is down merely because the engine is.
    readiness_require_engine: bool = False
    startup_require_engine: bool = False

    airbyte_api_url: str = ""
    airbyte_api_username: str = ""
    airbyte_api_password: str = ""
    airbyte_workspace_id: str = ""
    # Local convenience only. When the id is unset and this is on, the adapter
    # uses the workspace — singular — that the deployment has, and refuses if
    # there is any choice to make. Production sets the id explicitly; see
    # docs/RUNBOOK-airbyte-workspace.md.
    airbyte_workspace_auto: bool = False

    # --- launch scope -----------------------------------------------------
    # The registry carries 654 connectors; three of them are certified. Eleven
    # adapter operations proven against Postgres says the *engine integration*
    # works -- it says nothing about the other 651, which differ in auth, in
    # pagination, in incremental semantics and in how they fail.
    #
    # SUPPORTED_ONLY is the default because the alternative is a product that
    # offers 654 connectors and can stand behind 3. A deployment that wants the
    # full catalogue has to say so, and then owns that promise.
    connector_launch_scope: str = "SUPPORTED_ONLY"   # or FULL_CATALOG
    # Per-connector opt-in, for a deployment that has done its own UAT on a few
    # BETA connectors without adopting all of them.
    connector_beta_allowlist: str = ""

    @property
    def beta_allowlist(self) -> frozenset[str]:
        return frozenset(
            key.strip() for key in self.connector_beta_allowlist.split(",") if key.strip())

    def connector_is_offered(self, connector_key: str, certification: str,
                             spec_source: str | None = None) -> bool:
        """Is this connector one this deployment is prepared to stand behind?

        Read by both the presenter and the create path. One function, because a
        catalogue that greys a connector out while the API still accepts it is
        not a launch scope -- it is a suggestion.

        `spec_source == "BUILDER"` is exempt, and the distinction is the point of
        the setting. The launch scope exists so the product does not *offer*
        hundreds of upstream connectors it has never tested; a connector the
        workspace built in the Connector Builder is not upstream, is scoped to
        that workspace, and was published by a deliberate act. Applying the
        certification rule to it made Publish succeed and the create wizard then
        refuse the result with "contact your administrator" -- the Builder shipped
        something nobody could use. BLOCKED and HIDDEN still win, so an
        administrator can retire one.
        """
        if certification in ("BLOCKED", "HIDDEN"):
            return False
        # BUNDLED is exempt for the same reason, one step further: these are
        # the connectors this product wrote and ships in its own image. The
        # launch scope exists so hundreds of *upstream* connectors nobody here
        # has tested are not offered; a first-party connector is not upstream,
        # and gating it on `certification` made that field do two jobs at once.
        # It had to claim SUPPORTED to stay usable, which is why every bundled
        # connector claimed the strongest word in the vocabulary regardless of
        # what had actually been measured. Exempting it here is what lets
        # `certification` go back to being a statement about evidence.
        if (spec_source or "").upper() in ("BUILDER", "BUNDLED"):
            return True
        if self.connector_launch_scope.upper() == "FULL_CATALOG":
            return True
        return certification == "SUPPORTED" or connector_key in self.beta_allowlist

    # --- connector OAuth ---------------------------------------------------
    # The deployment's own registered application with each provider. These
    # identify *this installation* on the consent screen, so there is no
    # sensible default: a provider without them is simply not offered, and the
    # wizard shows the service-account path alone rather than a button that
    # cannot work.
    #
    # Service accounts remain the right credential for a warehouse -- they
    # belong to the organisation and survive people leaving. OAuth is for
    # somebody's own files, where a service account would need every user to
    # share each document with a robot address.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    #: `common` accepts work and personal accounts; a single-tenant deployment
    #: pins its own directory id here.
    microsoft_oauth_tenant_id: str = ""
    #: Must match the redirect registered with the provider, exactly.
    oauth_redirect_uri: str = ""
    #: Where to send the browser after the provider redirects back.
    frontend_base_url: str = "http://localhost:8080"

    # --- AI-assisted Connector Builder -----------------------------------
    # Optional by design: the manual Builder must remain usable when an
    # installation has not enabled OpenAI. AI endpoints fail with a focused
    # AI_NOT_CONFIGURED response instead of making the whole API fail at boot.
    openai_api_key: str = ""
    openai_model_planner: str = "gpt-5-mini"
    openai_model_agent: str = "gpt-5-mini"
    openai_model_vision: str = "gpt-5-mini"
    openai_timeout_seconds: float = 90.0
    builder_ai_source_max_bytes: int = 10 * 1024 * 1024
    builder_ai_crawl_max_pages: int = 30
    builder_ai_crawl_max_depth: int = 2
    builder_ai_crawl_max_bytes: int = 5 * 1024 * 1024
    builder_test_session_ttl_seconds: int = 20 * 60

    # --- policy / quota ---
    max_concurrent_runs_global: int = 4
    max_concurrent_runs_per_workspace: int = 3
    max_concurrent_runs_per_pipeline: int = 1
    min_schedule_interval_seconds: int = 300
    run_timeout_seconds: int = 7200
    check_timeout_seconds: int = 180
    discover_timeout_seconds: int = 300
    spec_timeout_seconds: int = 120
    stale_run_seconds: int = 60 * 60 * 6
    catalog_refresh_seconds: int = 6 * 3600
    reconcile_interval_seconds: int = 5

    # --- worker loops ---
    worker_poll_seconds: float = 2.0
    worker_max_parallel_syncs: int = 4

    # --- transformation engine -------------------------------------------
    # Whether this deployment ships the Transform runtime at all.
    #
    # The image can be built with WITH_TRANSFORM=0, which leaves dbt out and
    # saves about 560 MB -- worth it on a small VM for a module that deployment
    # may not use. Set from the Dockerfile's build arg. When false the module's
    # endpoints refuse politely and the UI hides the section, rather than
    # letting somebody queue a run no worker can execute.
    transform_runtime_available: bool = True
    # The API only queues work. A dedicated process reads these directories
    # and starts one isolated dbt subprocess per claimed run.
    transform_workspace_dir: str = "/transform/workspace"
    transform_log_dir: str = "/transform/logs"
    transform_timeout_seconds: int = 1800
    # A queued run that no worker ever claimed holds the active-build slot for
    # its Transform, so it is released far sooner than a running one.
    transform_stale_queue_seconds: int = 900
    transform_worker_max_parallel: int = 2
    transform_worker_poll_seconds: float = 1.0
    transform_preview_limit: int = 200
    dbt_core_version: str = "1.12.3"

    # --- Transform project storage ----------------------------------------
    # Project revisions and dbt artifacts live here, not in Postgres.  A
    # manifest for a mid-sized project is tens of megabytes and one is produced
    # per parse; the product database holds the metadata and index rows that
    # point at these objects.
    #
    # `local` is a directory and is the development default.  `s3` is any
    # S3-compatible endpoint and is what production should run.  Nothing else
    # in AppBI reads this store -- Pipeline and Airbyte are unaffected by it.
    transform_storage_backend: str = "local"
    transform_storage_local_dir: str = "/transform/objects"
    transform_storage_s3_bucket: str | None = None
    transform_storage_s3_region: str = "us-east-1"
    transform_storage_s3_access_key: str | None = None
    transform_storage_s3_secret_key: str | None = None
    #: Set for MinIO, R2, or any non-AWS gateway.  Leave empty for AWS S3.
    transform_storage_s3_endpoint_url: str | None = None
    transform_storage_s3_prefix: str = "transform"
    transform_storage_s3_force_path_style: bool = False

    # How many parse artifact bundles to keep per project.  Every save parses,
    # so this is the number that stops a busy editor from filling the store.
    transform_parse_artifact_retention: int = 5
    #: Editing a file bumps the working revision; only these many are kept.
    transform_revision_retention: int = 200
    #: A dbt subprocess reads its project from a private temp dir under here.
    transform_max_file_bytes: int = 2_000_000
    transform_max_project_files: int = 5_000

    # Airbyte application credentials. An auth-enabled Airbyte 1.x rejects HTTP
    # Basic on the Config API -- including the instance admin's own email and
    # password -- and expects a bearer token obtained from these. Basic is kept
    # for 0.59.x, which the Compose certification lane still runs on.
    airbyte_client_id: str = ""
    airbyte_client_secret: str = ""

    # --- build identity ---
    # Baked in at image build time and reported by the product about itself.
    # Release evidence binds to these: a certification recorded against one
    # build is not evidence for another, and before this there was no way to
    # tell which build produced a given evidence file. `git rev-parse` on the
    # machine running the release is not the answer -- production hosts have no
    # checkout, and the release manager's working tree is not the deployment.
    build_sha: str = "unknown"
    build_digest: str = ""      # the image digest, when the build pipeline knows it
    build_time: str = ""

    # --- bootstrap seed ---
    # Demo identities. These exist so a fresh checkout is explorable in one
    # command, and they are exactly what must never reach production: a known
    # email with a published password on an empty database is a privileged
    # account anyone can guess.
    #
    # `seed_demo_data` is what decides. It was declared and never read, so
    # production manifests set it to false and got the demo accounts anyway.
    seed_admin_email: str = "admin@appbi.local"
    #: Empty means "do not touch the password that is already there".
    #:
    #: This was "Admin@123456" -- a working credential, published in this
    #: repository, seeded by every install, and printed on the sign-in page.
    #: A default that is also a valid password is a default nobody changes,
    #: because nothing ever asks them to. `./run.sh` now generates one per
    #: deployment and writes it into .env.
    seed_admin_password: str = ""
    seed_demo_data: bool = True

    # The production path: a one-time secret supplied by the deployment, used
    # once to create a single platform admin that must immediately change its
    # password. No default -- an empty production database with neither this
    # nor an existing admin refuses to start rather than inventing a login.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    #: How many times the product retries a run by itself before leaving it
    #: failed for a person to look at. 0 turns it off.
    #:
    #: Only failures the classifier calls transient are retried -- a wrong
    #: token is not going to become right, and retrying it three times just
    #: delays the message saying so by several minutes.
    auto_retry_max_attempts: int = 3
    #: First wait, in seconds. Each attempt doubles it: 60, 120, 240.
    #: A source refusing requests needs time, not immediacy; measured on a
    #: customer deployment, the same sync succeeded thirteen minutes later.
    auto_retry_base_seconds: int = 60
    #: Cap, so a long chain cannot push the next attempt into next week.
    auto_retry_max_seconds: int = 1800

    #: Records per request for every declarative connector that declares a
    #: page size, unless a source overrides it. 0 leaves each connector's own
    #: choice alone, which is the default.
    #:
    #: One page is held whole in the source container before records are
    #: emitted, so this is the lever for a host that cannot give a connector
    #: 2 GB. Lower means less memory for proportionally more requests.
    connector_default_page_size: int = 0

    @property
    def is_production(self) -> bool:
        """Production is opt-in by name, so a forgotten variable never silently
        turns strict checks off."""
        return (self.app_env or "").strip().lower() in {"production", "prod"}

    @property
    def is_embedded_engine(self) -> bool:
        return self.engine_type.upper() == "AIRBYTE_EMBEDDED"

    @property
    def supports_destination_naming(self) -> bool:
        """Whether a destination table can be named something other than what
        the source emitted -- `stream_prefix` and `namespace_format`.

        True everywhere now. The embedded runner could not, because it handed
        one configured catalog to both sides and forwarded records untouched;
        it builds two catalogs and maps the stream on the way past, which is
        where Airbyte does the same work (`NamespacingMapper`, in the
        replication worker rather than in any connector).

        Kept as a predicate rather than deleted: it is what the API validates
        on and what `/auth/me` publishes to the frontend that draws the fields,
        and an engine that cannot rename would otherwise have to be discovered
        one failed save at a time.
        """
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
