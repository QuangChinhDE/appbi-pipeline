"""A config whose shape the connector cannot map is refused before the engine.

Reported from a customer install: a PostgreSQL source failed its test with
`SOURCE_NETWORK_UNREACHABLE`, while psql from the connectors network reached the
same server. The connector's real answer was

    ConfigErrorException: failed to map valid json to class
    io.airbyte.integrations.source.postgres.config.PostgresSourceConfigurationSpecification

reproduced against airbyte/source-postgres:3.8.5 with `schemas: "public"` --
the only one of ten malformed configs that produced it. That message names no
field, and the classifier then let connection wording elsewhere in the output
call it a network fault. Two fixes, one test group each.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.error_mapper import classify
from app.adapters.registry import _registry_entries
from app.core.errors import ErrorCategory, ValidationError
from app.services.catalog import _shape_errors, apply_spec_defaults, validate_against_spec

REGISTRY = Path(__file__).resolve().parents[1] / "app" / "resources" / "connector_registry.json"


def _spec(key: str) -> dict:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return next(c for c in data["connectors"] if c["connector_key"] == key)["spec_schema"]


def _postgres_config(**overrides) -> dict:
    return {
        "host": "100.96.0.23", "port": 15432, "database": "products",
        "schemas": ["public"], "username": "readonly_user", "password": "x",
        "ssl_mode": {"mode": "prefer"},
        "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
        "replication_method": {"method": "Standard"},
        **overrides,
    }


# ── the shape check ──────────────────────────────────────────────────────

def test_a_scalar_where_postgres_wants_a_list_is_refused_by_name() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_against_spec(_spec("source-postgres"), _postgres_config(schemas="public"))
    error = caught.value
    assert error.code == "CONFIGURATION_FIELD_WRONG_TYPE"
    assert error.details["wrong_type_fields"] == ["schemas"]
    assert "Schemas" in error.details["fields"]


def test_a_scalar_where_an_object_belongs_is_refused() -> None:
    wrong = _shape_errors(_spec("source-postgres"), _postgres_config(ssl_mode="disable"))
    assert [path for path, _ in wrong] == ["ssl_mode"]


def test_a_nested_list_inside_the_chosen_branch_is_checked() -> None:
    config = {"database_config": {
        "cluster_type": "SELF_MANAGED_REPLICA_SET",
        "connection_string": "mongodb://h:27017/?replicaSet=rs0",
        "databases": "shop",
    }}
    wrong = _shape_errors(_spec("source-mongodb-v2"), config)
    assert [path for path, _ in wrong] == ["database_config.databases"]


def test_the_customer_config_that_works_is_accepted() -> None:
    validate_against_spec(_spec("source-postgres"), _postgres_config())


@pytest.mark.parametrize("loose", [{"port": "15432"}, {"check_privileges": "true"},
                                   {"max_db_connections": None}, {"jdbc_url_params": ""}])
def test_scalars_the_connector_coerces_are_not_refused(loose: dict) -> None:
    """Each of these was run against source-postgres:3.8.5 and checked
    SUCCEEDED. Refusing them would break sources that work today."""
    validate_against_spec(_spec("source-postgres"), _postgres_config(**loose))


# ── the default that caused it ───────────────────────────────────────────
#
# Measured against source-postgres:3.8.5: `ssl_mode: "require"` (a string)
# fails with "failed to map valid json"; `{"mode": "require"}` and an absent
# key both reach the server with SSL required. The spec ships the string as
# the default, and apply_spec_defaults used to copy it in verbatim, so every
# PostgreSQL source whose form left SSL untouched was broken by the product.

def test_postgres_ssl_default_becomes_the_branch_it_names() -> None:
    config = _postgres_config()
    del config["ssl_mode"]
    filled = apply_spec_defaults(_spec("source-postgres"), config)
    assert filled["ssl_mode"] == {"mode": "require"}
    validate_against_spec(_spec("source-postgres"), filled)


def test_a_stored_bare_string_is_repaired_on_the_way_through() -> None:
    filled = apply_spec_defaults(_spec("source-postgres"), _postgres_config(ssl_mode="disable"))
    assert filled["ssl_mode"] == {"mode": "disable"}


def test_a_default_naming_no_branch_is_not_applied() -> None:
    """source-mssql's ssl_mode default is "required"; no branch has that mode."""
    filled = apply_spec_defaults(_spec("source-mssql"), {"host": "h"})
    assert "ssl_mode" not in filled


def test_a_string_naming_no_branch_is_still_refused() -> None:
    filled = apply_spec_defaults(_spec("source-postgres"), _postgres_config(ssl_mode="sometimes"))
    with pytest.raises(ValidationError) as caught:
        validate_against_spec(_spec("source-postgres"), filled)
    assert caught.value.details["wrong_type_fields"] == ["ssl_mode"]


@pytest.mark.parametrize("pick", [0, 1, 2])
def test_defaults_never_produce_a_shape_the_connector_cannot_map(pick: int) -> None:
    """The net that would have caught this: for every shipped connector, the
    product's own defaults over a config from the spec must stay mappable."""
    broken = {}
    for key, spec in _shipped_specs():
        sample = _sample(spec or {}, pick)
        sparse = {k: v for k, v in sample.items() if not (spec.get("properties") or {}).get(k, {}).get("default")}
        wrong = _shape_errors(spec or {}, apply_spec_defaults(spec or {}, sparse))
        if wrong:
            broken[key] = wrong
    assert broken == {}


# ── no false refusals across the whole catalogue ──────────────────────────

def _sample(schema: dict, pick: int) -> object:
    branches = schema.get("oneOf") or schema.get("anyOf")
    if branches:
        return _sample(branches[min(pick, len(branches) - 1)], pick)
    declared = schema.get("type")
    kind = declared[0] if isinstance(declared, list) else declared
    structured = kind in ("object", "array") or "properties" in schema
    if "const" in schema:
        return schema["const"]
    # Upstream specs put item-shaped examples and defaults on list fields
    # (facebook-marketing `account_ids`); those describe an entry, not the
    # field, so a structured field is built from its own shape instead.
    if not structured:
        if "default" in schema:
            return schema["default"]
        if schema.get("examples"):
            return schema["examples"][0]
        if schema.get("enum"):
            return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        return {key: _sample(prop, pick)
                for key, prop in (schema.get("properties") or {}).items()
                if isinstance(prop, dict)}
    if kind == "array":
        items = schema.get("items")
        return [_sample(items, pick)] if isinstance(items, dict) else []
    return {"integer": 1, "number": 1.5, "boolean": True}.get(kind, "value")


def _shipped_specs() -> list[tuple[str, dict]]:
    return [(entry["connector_key"], entry["spec_schema"]) for entry in _registry_entries()]


@pytest.mark.parametrize("pick", [0, 1, 2])
def test_no_shipped_connector_refuses_a_config_built_from_its_own_spec(pick: int) -> None:
    """Every connector, every top branch position: a config shaped the way its
    spec describes must pass. This is the guard against the check becoming a
    second, stricter spec that rejects what the connector accepts."""
    refused = {}
    for key, spec in _shipped_specs():
        wrong = _shape_errors(spec or {}, _sample(spec or {}, pick))
        if wrong:
            refused[key] = wrong
    assert refused == {}
    assert len(_shipped_specs()) >= 20


# ── classification ───────────────────────────────────────────────────────

_MAPPING_FAILURE = (
    "INFO main i.a.c.i.b.IntegrationRunner Could not connect with provided "
    "configuration. Retrying...\n"
    "io.airbyte.cdk.ConfigErrorException: failed to map valid json to class "
    "io.airbyte.integrations.source.postgres.config."
    "PostgresSourceConfigurationSpecification"
)


def test_a_mapping_failure_is_configuration_even_beside_network_wording() -> None:
    failure = classify(_MAPPING_FAILURE, side="SOURCE")
    assert failure.category == ErrorCategory.CONFIGURATION
    assert failure.code == "SOURCE_CONFIGURATION_INVALID"
    assert failure.remediation_action == "OPEN_CONFIGURATION"


def test_the_destination_side_gets_its_own_code() -> None:
    assert classify(_MAPPING_FAILURE, side="DESTINATION").code == "DESTINATION_CONFIGURATION_INVALID"


def test_a_server_refusing_ssl_is_a_setting_not_a_network_fault() -> None:
    """Measured in the product: SSL `require` against a server without SSL.
    The server answered, so "could not be reached" was the wrong thing to say."""
    text = ("Could not connect with provided configuration. Error: State code: 08004; "
            "Message: The server does not support SSL.")
    failure = classify(text, side="SOURCE")
    assert failure.category == ErrorCategory.CONFIGURATION
    assert failure.code == "SOURCE_SSL_MODE_MISMATCH"
    assert "SSL mode" in failure.summary
    assert classify(text, side="DESTINATION").code == "DESTINATION_SSL_MODE_MISMATCH"


def test_a_real_network_failure_is_still_network() -> None:
    """Mongo's TLS failure opens with "configuration is not valid" and is a
    network fault. The new rule is narrow enough not to take it."""
    text = ("Connector configuration is not valid. Unable to connect: Timed out after "
            "30000 ms ... javax.net.ssl.SSLHandshakeException: Remote host terminated "
            "the handshake")
    assert classify(text, side="SOURCE").category == ErrorCategory.NETWORK
    assert classify("Connection to 127.0.0.1:5432 refused.",
                    side="DESTINATION").category == ErrorCategory.NETWORK
