"""Every secret field of every connector we ship, driven with a sentinel.

PM v16's P0-SECRET. `split_configuration()` said "walked recursively" and
descended exactly one level, through exactly one `oneOf`. Airbyte specs nest
further than that: `destination-bigquery` puts its HMAC secret at
`loading_method.credential.hmac_key_secret`, two levels down and two `oneOf`s
in. That secret went into plain configuration -- stored unencrypted, written to
the audit trail, and returned by an endpoint a VIEW-only role can call.

A test for the one path PM found would be worth little, because the next
connector nests differently. So this reads the shipped spec of every
`SUPPORTED` connector, finds *every* path carrying a secret marker at any
depth and through any branch, and drives each one with a unique sentinel.

Two properties per path, and both matter:

  * the sentinel is nowhere in the non-secret half -- not stored, not audited,
    not returned
  * the configuration rebuilds exactly -- a split the merge cannot undo hands
    the connector a config with a hole in it, and that failure looks like a
    bad credential rather than a bug here
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

from app.services.catalog import (
    _marked_secret, merge_configuration, split_configuration,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
REGISTRY = ROOT / "backend" / "app" / "resources" / "connector_registry.json"


def _supported_specs() -> list[tuple[str, dict]]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return [(c["connector_key"], c.get("spec_schema") or {})
            for c in registry["connectors"]
            if c.get("certification") == "SUPPORTED"]


def _secret_paths(node: dict, prefix: tuple = (), seen: set | None = None
                  ) -> list[tuple[str, ...]]:
    """Every path under `node` whose property carries a secret marker.

    Walks `properties`, all of `oneOf`/`anyOf`/`allOf`, and array `items`.
    Paths are returned as tuples of keys; array hops are elided because the
    payload builder only needs one element to prove the behaviour.
    """
    if not isinstance(node, dict) or len(prefix) > 8:
        return []
    seen = seen if seen is not None else set()
    identity = id(node), prefix
    if identity in seen:
        return []
    seen.add(identity)

    found: list[tuple[str, ...]] = []
    branches = [node]
    for keyword in ("oneOf", "anyOf", "allOf"):
        branches += [b for b in (node.get(keyword) or []) if isinstance(b, dict)]

    for branch in branches:
        for key, child in (branch.get("properties") or {}).items():
            if not isinstance(child, dict):
                continue
            path = prefix + (key,)
            if _marked_secret(child):
                if path not in found:
                    found.append(path)
                continue
            found += [p for p in _secret_paths(child, path, seen) if p not in found]
        items = branch.get("items")
        if isinstance(items, dict):
            found += [p for p in _secret_paths(items, prefix, seen) if p not in found]
    return found


def _payload_for(path: tuple[str, ...], sentinel: str) -> dict:
    payload: dict = {}
    node = payload
    for key in path[:-1]:
        node[key] = {}
        node = node[key]
    node[path[-1]] = sentinel
    return payload


SUPPORTED = _supported_specs()

# Connectors that legitimately take no credential. Named rather than inferred:
# "this spec has no secret" is exactly what the bug looked like, so the empty
# case has to be a deliberate entry on a list somebody edited.
CREDENTIAL_FREE = {"source-faker"}


def test_the_launch_scope_is_what_we_think_it_is() -> None:
    """If this list ever empties, every test below passes vacuously."""
    keys = {key for key, _ in SUPPORTED}
    assert keys == {
        "source-postgres", "destination-postgres", "source-faker",
        "source-bigquery", "destination-bigquery", "source-google-sheets",
    }, sorted(keys)
    assert all(spec for _, spec in SUPPORTED), "a connector shipped with no spec"


@pytest.mark.parametrize("connector_key,spec", SUPPORTED,
                         ids=[key for key, _ in SUPPORTED])
def test_no_secret_path_reaches_plain_configuration(connector_key: str,
                                                    spec: dict) -> None:
    paths = _secret_paths(spec)
    if connector_key in CREDENTIAL_FREE:
        assert not paths, (
            f"{connector_key} was listed as needing no credential but declares "
            f"{['.'.join(p) for p in paths]}")
        return
    assert paths, f"{connector_key} declares no secret at all, which is suspicious"

    for index, path in enumerate(paths):
        sentinel = f"SENTINEL-{connector_key}-{index}-{'.'.join(path)}"
        payload = _payload_for(path, sentinel)
        config, secrets = split_configuration(spec, payload)

        assert sentinel not in json.dumps(config), (
            f"{connector_key}: {'.'.join(path)} was written to plain configuration")
        assert sentinel in json.dumps(secrets), (
            f"{connector_key}: {'.'.join(path)} did not reach the encrypted payload")
        assert merge_configuration(config, secrets) == payload, (
            f"{connector_key}: {'.'.join(path)} does not round-trip to the adapter")


def test_the_bigquery_hmac_path_specifically() -> None:
    """PM's reproduction, kept as its own test because it is the one that shipped.

    Two levels down and two `oneOf`s in, alongside non-secret siblings that
    must stay in plain configuration.
    """
    spec = dict(SUPPORTED)["destination-bigquery"]
    payload = {
        "project_id": "base-testlab-01",
        "dataset_id": "appbi_snapshots",
        "dataset_location": "us-central1",
        "credentials_json": "SENTINEL-SERVICE-ACCOUNT",
        "loading_method": {
            "method": "GCS Staging",
            "gcs_bucket_name": "a-bucket",
            "credential": {
                "credential_type": "HMAC_KEY",
                "hmac_key_access_id": "GOOGTS7C7FUP3AIRVJTE2BCD",
                "hmac_key_secret": "SENTINEL-HMAC-SECRET",
            },
        },
    }
    config, secrets = split_configuration(spec, payload)
    plain = json.dumps(config)

    assert "SENTINEL-HMAC-SECRET" not in plain
    assert "SENTINEL-SERVICE-ACCOUNT" not in plain
    # The non-secret siblings must survive, or the connector loses its config.
    assert config["loading_method"]["gcs_bucket_name"] == "a-bucket"
    assert config["loading_method"]["credential"]["credential_type"] == "HMAC_KEY"
    assert merge_configuration(config, secrets) == payload


def test_secrets_inside_arrays_are_found() -> None:
    """Array-of-object configuration is common and was never walked at all."""
    spec = {
        "properties": {
            "endpoints": {
                "type": "array",
                "items": {
                    "properties": {
                        "url": {"type": "string"},
                        "token": {"type": "string", "airbyte_secret": True},
                    }
                },
            }
        }
    }
    payload = {"endpoints": [{"url": "https://a", "token": "SENTINEL-A"},
                             {"url": "https://b", "token": "SENTINEL-B"}]}
    config, secrets = split_configuration(spec, payload)

    plain = json.dumps(config)
    assert "SENTINEL-A" not in plain and "SENTINEL-B" not in plain
    assert [e["url"] for e in config["endpoints"]] == ["https://a", "https://b"]
    assert merge_configuration(config, secrets) == payload


def test_a_branch_that_disagrees_is_read_strictly() -> None:
    """When one `oneOf` branch calls a field secret and another does not.

    Real specs do this. Treating the field as public because some branch says
    so writes a credential to disk; treating it as secret because some branch
    says so encrypts a field that may not need it. Only one of those is
    recoverable.
    """
    spec = {
        "properties": {
            "auth": {
                "oneOf": [
                    {"properties": {"token": {"type": "string"}}},
                    {"properties": {"token": {"type": "string",
                                              "airbyte_secret": True}}},
                ]
            }
        }
    }
    config, secrets = split_configuration(spec, {"auth": {"token": "SENTINEL"}})
    assert "SENTINEL" not in json.dumps(config)
    assert secrets["auth"]["token"] == "SENTINEL"


def test_every_supported_connector_declares_the_secret_it_needs() -> None:
    """Cross-check against compatibility.yaml's recorded auth mode.

    A connector certified as using a service account whose spec exposes no
    secret path would mean the credential is travelling somewhere unexamined.
    """
    compatibility = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    for connector_key, spec in SUPPORTED:
        entry = compatibility["connectors"].get(connector_key, {})
        if entry.get("auth") != "service_account_json":
            continue
        paths = {".".join(p) for p in _secret_paths(spec)}
        assert any("credential" in p or "service_account" in p for p in paths), (
            connector_key, sorted(paths))
