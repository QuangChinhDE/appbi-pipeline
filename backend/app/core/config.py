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
    redis_url: str = "redis://localhost:6379/0"

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
    # reach the API or Redis: a connector is user-controlled code pointed at a
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

    def connector_is_offered(self, connector_key: str, certification: str) -> bool:
        """Is this connector one this deployment is prepared to stand behind?

        Read by both the presenter and the create path. One function, because a
        catalogue that greys a connector out while the API still accepts it is
        not a launch scope -- it is a suggestion.
        """
        if certification in ("BLOCKED", "HIDDEN"):
            return False
        if self.connector_launch_scope.upper() == "FULL_CATALOG":
            return True
        return certification == "SUPPORTED" or connector_key in self.beta_allowlist

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
    seed_admin_password: str = "Admin@12345"
    seed_demo_data: bool = True

    # The production path: a one-time secret supplied by the deployment, used
    # once to create a single platform admin that must immediately change its
    # password. No default -- an empty production database with neither this
    # nor an existing admin refuses to start rather than inventing a login.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    @property
    def is_production(self) -> bool:
        """Production is opt-in by name, so a forgotten variable never silently
        turns strict checks off."""
        return (self.app_env or "").strip().lower() in {"production", "prod"}

    @property
    def is_embedded_engine(self) -> bool:
        return self.engine_type.upper() == "AIRBYTE_EMBEDDED"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
