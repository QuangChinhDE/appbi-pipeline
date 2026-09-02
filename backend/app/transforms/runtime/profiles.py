"""Build an ephemeral dbt profile for one environment.

A profile exists only inside one invocation's private directory and is deleted
with it.  Nothing here is ever written into a product row, a release snapshot or
an exported project -- an export contains the project and no credentials, which
is the only way a project is safe to hand to somebody.

The profile carries the *environment's* target name, not a fixed internal one.
That matters more than it looks: a real dbt project branches on
``{{ target.name }}`` to pick a dataset, skip an expensive incremental, or
disable a model outside production.  A runtime that always called itself
`production` would silently take the wrong branch of the user's own code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ValidationError

#: The profile name written into `profiles.yml`.
#:
#: A managed project's `dbt_project.yml` names this profile.  A Git project
#: names whatever its authors chose, so the profile is written under *their*
#: name as well -- see `profile_names`.
DEFAULT_PROFILE = "appbi_runtime"


@dataclass(slots=True)
class ResolvedProfile:
    """A profiles.yml document plus the secrets that must never be logged."""

    document: dict[str, Any]
    secret_values: list[str] = field(default_factory=list)
    #: Written to a file next to the profile and referenced by path, because
    #: BigQuery's service-account method takes a keyfile rather than inline JSON.
    keyfile_json: dict[str, Any] | None = None
    adapter_type: str = ""


def _required(configuration: dict[str, Any], key: str, label: str) -> Any:
    value = configuration.get(key)
    if value in (None, ""):
        raise ValidationError(
            f"This connection is missing `{label}`, which the warehouse needs.",
            code="TRANSFORM_CONNECTION_CONFIG_MISSING",
            details={"field": key},
        )
    return value


def build_profile(
    *,
    connector_key: str,
    configuration: dict[str, Any],
    schema: str,
    target_name: str,
    threads: int = 4,
    profile_names: list[str] | None = None,
) -> ResolvedProfile:
    """Render one target under every profile name the project might ask for.

    ``profile_names`` is normally ``[<the project's own profile>, "appbi_runtime"]``.
    Writing the same output block under each is what lets an unmodified Git
    project run: its `dbt_project.yml` says `profile: acme_analytics`, and
    rewriting that line to suit the runtime would be exactly the "convert the
    repository" behaviour this rework removes.
    """
    output, secrets, keyfile, adapter = _output(connector_key, configuration, schema, threads)
    names = [name for name in (profile_names or []) if name] or [DEFAULT_PROFILE]
    if DEFAULT_PROFILE not in names:
        names.append(DEFAULT_PROFILE)

    document: dict[str, Any] = {
        name: {"target": target_name, "outputs": {target_name: dict(output)}}
        for name in dict.fromkeys(names)
    }
    return ResolvedProfile(
        document=document, secret_values=secrets, keyfile_json=keyfile, adapter_type=adapter,
    )


def _output(
    connector_key: str, configuration: dict[str, Any], schema: str, threads: int,
) -> tuple[dict[str, Any], list[str], dict[str, Any] | None, str]:
    threads = max(1, min(int(threads or 4), 32))

    if connector_key == "destination-postgres":
        password = str(_required(configuration, "password", "password"))
        output = {
            "type": "postgres",
            "host": _required(configuration, "host", "host"),
            "port": int(configuration.get("port") or 5432),
            "user": _required(configuration, "username", "username"),
            "password": password,
            "dbname": _required(configuration, "database", "database"),
            "schema": schema,
            "threads": threads,
            "connect_timeout": 15,
            "sslmode": _sslmode(configuration),
            # dbt keeps a connection open per thread; without this a network
            # blip during a long build hangs rather than failing.
            "keepalives_idle": 30,
            "retries": 1,
        }
        return output, [password], None, "postgres"

    if connector_key == "destination-bigquery":
        # Two ways to be BigQuery.  A service account is a key the team holds;
        # an OAuth grant is a person's own access, refreshed on each run.  dbt
        # takes either, and which one this is decides the whole output block --
        # so it branches here rather than trying to accept both shapes at once.
        if configuration.get("auth_method") == "oauth":
            refresh_token = str(_required(configuration, "refresh_token", "refresh token"))
            client_secret = str(_required(configuration, "oauth_client_secret", "client secret"))
            output = {
                "type": "bigquery",
                # A flat refresh token, not Airbyte's nested
                # {"credentials": {...}} shape -- dbt reads these keys directly.
                "method": "oauth-secrets",
                "project": _required(configuration, "project_id", "project"),
                "dataset": schema,
                "refresh_token": refresh_token,
                "client_id": _required(configuration, "oauth_client_id", "client id"),
                "client_secret": client_secret,
                "token_uri": configuration.get("token_uri")
                or "https://oauth2.googleapis.com/token",
                "threads": threads,
                "timeout_seconds": 300,
                "priority": "interactive",
                "job_retries": 1,
            }
            if configuration.get("dataset_location"):
                output["location"] = configuration["dataset_location"]
            return output, [refresh_token, client_secret], None, "bigquery"

        raw = _required(configuration, "credentials_json", "service account JSON")
        try:
            credentials = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "The BigQuery service account JSON is not valid JSON.",
                code="TRANSFORM_BIGQUERY_CREDENTIAL_INVALID",
            ) from exc
        if not isinstance(credentials, dict):
            raise ValidationError(
                "The BigQuery service account JSON is not valid JSON.",
                code="TRANSFORM_BIGQUERY_CREDENTIAL_INVALID",
            )
        project = configuration.get("project_id") or credentials.get("project_id")
        if not project:
            raise ValidationError(
                "This connection does not say which BigQuery project to use.",
                code="TRANSFORM_CONNECTION_CONFIG_MISSING", details={"field": "project_id"},
            )
        output = {
            "type": "bigquery",
            "method": "service-account",
            "project": project,
            "dataset": schema,
            "threads": threads,
            "timeout_seconds": 300,
            "priority": "interactive",
            "job_retries": 1,
        }
        if configuration.get("dataset_location"):
            output["location"] = configuration["dataset_location"]
        secrets = [str(value) for value in credentials.values() if isinstance(value, str)]
        if isinstance(raw, str):
            secrets.append(raw)
        return output, secrets, credentials, "bigquery"

    raise ValidationError(
        "Transform does not support this kind of warehouse yet.",
        code="TRANSFORM_CONNECTION_UNSUPPORTED",
        details={"connector_key": connector_key},
    )


def _sslmode(configuration: dict[str, Any]) -> str:
    ssl = configuration.get("ssl_mode") or configuration.get("ssl") or {}
    if isinstance(ssl, dict):
        raw = str(ssl.get("mode") or ssl.get("ssl_mode") or "prefer").lower()
    else:
        raw = str(ssl).lower()
    return {
        "disabled": "disable", "disable": "disable", "allow": "allow",
        "preferred": "prefer", "prefer": "prefer", "required": "require",
        "require": "require", "verify-ca": "verify-ca", "verify_ca": "verify-ca",
        "verify-full": "verify-full", "verify_full": "verify-full",
    }.get(raw, "prefer")


def resolve_schema(
    *,
    strategy: str,
    base: str,
    prefix: str | None = None,
    suffix: str | None = None,
    user_token: str | None = None,
) -> str:
    """The schema an environment writes into.

    PER_USER exists so two developers on one project do not build over each
    other's tables.  The token is derived from the user id rather than an email
    or name: it has to be a legal identifier on every supported warehouse, and
    stable across a rename.
    """
    import re

    parts = [prefix or "", base, suffix or ""]
    if strategy.upper() == "PER_USER" and user_token:
        parts.append(user_token)
    name = "_".join(part.strip("_") for part in parts if part and part.strip("_"))
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not name or not (name[0].isalpha() or name[0] == "_"):
        name = f"s_{name}"
    # BigQuery caps dataset names at 1024 and Postgres identifiers at 63; the
    # smaller limit is the safe one to enforce for both.
    return name[:63]


def user_token(user_id: Any) -> str:
    """A short, stable, identifier-safe token for one user."""
    digest = str(user_id).replace("-", "")
    return f"u{digest[:8]}"
