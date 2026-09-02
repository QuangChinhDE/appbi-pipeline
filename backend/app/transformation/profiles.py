"""Map product Destination configuration to ephemeral dbt profiles."""

from __future__ import annotations

import json
from typing import Any

from app.core.errors import ValidationError


def _required(configuration: dict[str, Any], key: str) -> Any:
    value = configuration.get(key)
    if value in (None, ""):
        raise ValidationError(
            f"Destination is missing `{key}` required by the transformation runtime.",
            code="TRANSFORM_DESTINATION_CONFIG_MISSING",
            details={"field": key},
        )
    return value


def build_profile(
    connector_key: str, configuration: dict[str, Any], output_schema: str,
) -> tuple[dict[str, Any], list[str]]:
    if connector_key == "destination-postgres":
        password = str(_required(configuration, "password"))
        ssl = configuration.get("ssl_mode") or configuration.get("ssl") or {}
        if isinstance(ssl, dict):
            sslmode = str(ssl.get("mode") or ssl.get("ssl_mode") or "prefer").lower()
        else:
            sslmode = str(ssl).lower()
        sslmode = {
            "disabled": "disable", "disable": "disable", "allow": "allow",
            "preferred": "prefer", "prefer": "prefer", "required": "require",
            "require": "require", "verify-ca": "verify-ca", "verify-full": "verify-full",
        }.get(sslmode, "prefer")
        output = {
            "type": "postgres",
            "host": _required(configuration, "host"),
            "port": int(configuration.get("port") or 5432),
            "user": _required(configuration, "username"),
            "password": password,
            "dbname": _required(configuration, "database"),
            "schema": output_schema,
            "threads": 4,
            "connect_timeout": 15,
            "sslmode": sslmode,
        }
        return {"appbi_runtime": {"target": "production", "outputs": {"production": output}}}, [password]

    if connector_key == "destination-bigquery":
        # Two ways to be BigQuery. A service account is a key the team holds; an
        # OAuth grant is a person's own access, refreshed on each run. dbt takes
        # either, and which one this is decides the whole output block -- so it
        # branches here rather than trying to accept both shapes at once.
        if configuration.get("auth_method") == "oauth":
            project = _required(configuration, "project_id")
            output = {
                "type": "bigquery",
                "method": "oauth-secrets",
                "project": project,
                "dataset": output_schema,
                "refresh_token": _required(configuration, "refresh_token"),
                "client_id": _required(configuration, "oauth_client_id"),
                "client_secret": _required(configuration, "oauth_client_secret"),
                "token_uri": configuration.get("token_uri")
                or "https://oauth2.googleapis.com/token",
                "threads": 4,
                "timeout_seconds": 300,
                "priority": "interactive",
            }
            if configuration.get("dataset_location"):
                output["location"] = configuration["dataset_location"]
            return (
                {"appbi_runtime": {"target": "production", "outputs": {"production": output}}},
                [str(output["refresh_token"]), str(output["client_secret"])],
            )

        raw_credentials = _required(configuration, "credentials_json")
        try:
            credentials = (
                json.loads(raw_credentials) if isinstance(raw_credentials, str) else raw_credentials
            )
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "The BigQuery service account JSON is invalid.",
                code="TRANSFORM_BIGQUERY_CREDENTIAL_INVALID",
            ) from exc
        project = configuration.get("project_id") or credentials.get("project_id")
        if not project:
            raise ValidationError(
                "BigQuery project_id is missing.", code="TRANSFORM_DESTINATION_CONFIG_MISSING",
                details={"field": "project_id"},
            )
        output = {
            "type": "bigquery",
            "method": "service-account",
            "project": project,
            "dataset": output_schema,
            "threads": 4,
            "timeout_seconds": 300,
            "priority": "interactive",
            "_service_account_json": credentials,
        }
        if configuration.get("dataset_location"):
            output["location"] = configuration["dataset_location"]
        secret_values = [str(value) for value in credentials.values() if isinstance(value, str)]
        if isinstance(raw_credentials, str):
            secret_values.append(raw_credentials)
        return {"appbi_runtime": {"target": "production", "outputs": {"production": output}}}, secret_values

    raise ValidationError(
        "This Destination is not supported by Transform.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
        details={"connector_key": connector_key},
    )
