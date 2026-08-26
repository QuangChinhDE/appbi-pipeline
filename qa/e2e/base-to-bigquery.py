#!/usr/bin/env python3
"""Certify every Base.vn source through AppBI/Airbyte into BigQuery.

This is deliberately above the connector-protocol tests in base-connectors.py:
it creates the actors and pipelines through the product API, lets Airbyte own
execution, then asks BigQuery what actually landed.

Credentials are inputs only. The evidence contains names, ids, counts and
statuses, never request payloads, tokens or the service-account document.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from google.cloud import bigquery
from google.oauth2 import service_account

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.connectors.base_vn import BY_KEY, CONNECTORS  # noqa: E402

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL_SUCCESS", "TIMED_OUT"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


class ProductClient:
    def __init__(self, base_url: str, secrets: list[str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.secrets = [secret for secret in secrets if secret]

    def safe(self, text: str) -> str:
        clean = text
        for secret in self.secrets:
            clean = clean.replace(secret, "[REDACTED]")
        clean = re.sub(r"2329~[A-Za-z0-9_-]+", "[REDACTED]", clean)
        clean = re.sub(
            r"-----BEGIN PRIVATE KEY-----.*?-----END PRIVATE KEY-----",
            "[REDACTED PRIVATE KEY]", clean, flags=re.S)
        return clean[:1200]

    def call(self, method: str, path: str, *, expected: tuple[int, ...] = (200,),
             timeout: int = 900, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(
            method, f"{self.base_url}{path}", timeout=timeout, **kwargs)
        if response.status_code not in expected:
            raise RuntimeError(
                f"{method} {path} -> {response.status_code}: "
                f"{self.safe(response.text)}")
        return response.json() if response.content else {}


def selection(stream: dict[str, Any]) -> dict[str, Any]:
    modes = stream.get("supported_sync_modes") or []
    cursor = stream.get("default_cursor_field") or []
    primary_key = stream.get("source_defined_primary_key") or []
    incremental = "incremental" in modes and bool(cursor) and bool(primary_key)
    return {
        "name": stream["name"],
        "namespace": stream.get("namespace"),
        "selected": True,
        "sync_mode": "incremental" if incremental else "full_refresh",
        "destination_sync_mode": "append_dedup" if incremental else "overwrite",
        "cursor_fields": cursor if incremental else [],
        "primary_key_fields": primary_key,
    }


def wait_for_run(client: ProductClient, run_id: str, minutes: int) -> dict[str, Any]:
    deadline = time.monotonic() + minutes * 60
    previous = ""
    while time.monotonic() < deadline:
        run = client.call("GET", f"/api/v1/runs/{run_id}", timeout=60)
        status = str(run.get("status") or "")
        if status != previous:
            print(f"      run {run_id[:8]}: {status}", flush=True)
            previous = status
        if status in TERMINAL:
            return run
        time.sleep(10)
    raise TimeoutError(f"run {run_id} did not finish within {minutes} minutes")


def table_counts(client: bigquery.Client, project: str, dataset: str,
                 prefix: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in client.list_tables(f"{project}.{dataset}"):
        if not table.table_id.startswith(prefix):
            continue
        query = (
            f"SELECT COUNT(*) AS n FROM "
            f"`{project}.{dataset}.{table.table_id}`")
        counts[table.table_id] = int(next(iter(client.query(query).result())).n)
    return counts


def resolve_updated_from(value: str) -> str:
    """`0`, an epoch, or a window like `90d` / `12h` counted back from now.

    A window is what makes a smoke test finish: Base returns every record whose
    `last_update` is at or after this, so `0` reads the entire history of the
    workspace. That is correct for certifying a connector and far too much for
    checking that the product wires one up.
    """
    text = value.strip().lower()
    if text.isdigit():
        return text
    if text and text[-1] in "dh" and text[:-1].isdigit():
        seconds = int(text[:-1]) * (86400 if text[-1] == "d" else 3600)
        return str(int(datetime.now(timezone.utc).timestamp()) - seconds)
    raise SystemExit(f"--updated-from: expected an epoch or Nd/Nh, got {value!r}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", default="secrets/base-tokens.json")
    parser.add_argument("--key", default="secrets/base-testlab-01-581355a83adc.json")
    parser.add_argument("--domain", default="base.com.vn")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--dataset", default="")
    parser.add_argument("--app", action="append", default=[])
    parser.add_argument("--run-timeout-minutes", type=int, default=45)
    # Base filters on `updated_from`, epoch seconds. "0" means every record the
    # workspace has ever had, which is the right default for a certification
    # run and the wrong one for a smoke test: HRM alone will read for tens of
    # minutes. `--updated-from 90d` bounds it to recent changes.
    parser.add_argument("--updated-from", default="0",
                        help="epoch seconds, or Nd/Nh for the last N days/hours")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    token_path = (ROOT / args.tokens).resolve()
    key_path = (ROOT / args.key).resolve()
    tokens = json.loads(token_path.read_text(encoding="utf-8"))
    key_document = json.loads(key_path.read_text(encoding="utf-8"))
    apps = args.app or [connector.app for connector in CONNECTORS]
    missing = sorted(set(apps) - set(tokens))
    if missing:
        raise SystemExit(f"missing token(s): {', '.join(missing)}")

    env = {**read_env(ROOT / ".env"), **os.environ}
    email = env.get("NEXT_PUBLIC_DEMO_EMAIL") or "admin@appbi.local"
    password = env.get("APPBI_DEMO_PASSWORD") or env.get("NEXT_PUBLIC_DEMO_PASSWORD")
    if not password:
        raise SystemExit("APPBI_DEMO_PASSWORD is not available")

    updated_from = resolve_updated_from(args.updated_from)
    if updated_from != "0":
        since = datetime.fromtimestamp(int(updated_from), timezone.utc)
        print(f"Reading records changed since {since:%Y-%m-%d %H:%M} UTC "
              f"(updated_from={updated_from})")

    project = key_document["project_id"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dataset = args.dataset or f"appbi_base_e2e_{stamp}"
    evidence_path = Path(args.out or f"evidence-base-bigquery-{stamp}.json")

    credentials = service_account.Credentials.from_service_account_file(key_path)
    warehouse = bigquery.Client(project=project, credentials=credentials)
    dataset_ref = bigquery.Dataset(f"{project}.{dataset}")
    dataset_ref.location = args.location
    warehouse.create_dataset(dataset_ref, exists_ok=True)
    print(f"BigQuery: {project}.{dataset} ({args.location})", flush=True)

    product = ProductClient(
        args.base_url,
        [password, key_document.get("private_key", ""), *tokens.values()],
    )
    product.call("POST", "/api/v1/auth/login", json={
        "email": email, "password": password,
    })
    readiness = product.call("GET", "/readyz?deep=1")
    if readiness.get("engine_type") != "AIRBYTE_API":
        raise SystemExit(f"refusing to certify {readiness.get('engine_type')}")

    marker = f"Base E2E {stamp}"
    destination = product.call(
        "POST", "/api/v1/destinations", expected=(201,), timeout=1200,
        json={
            "name": f"{marker} BigQuery",
            "connector_key": "destination-bigquery",
            "configuration": {
                "project_id": project,
                "dataset_id": dataset,
                "dataset_location": args.location,
                "credentials_json": json.dumps(key_document, separators=(",", ":")),
                "loading_method": {"method": "Standard"},
            },
            "test_before_save": True,
        },
    )
    print(f"Destination: healthy ({destination['id'][:8]})", flush=True)

    evidence: dict[str, Any] = {
        "schema": "base-bigquery-evidence/v1",
        "started_at": utcnow(),
        "engine_type": readiness.get("engine_type"),
        "engine": readiness.get("dependencies", {}).get("engine", {}),
        "project": project,
        "dataset": dataset,
        "dataset_location": args.location,
        "destination_id": destination["id"],
        "connectors": {},
    }
    failed = False

    for app in apps:
        connector = BY_KEY[f"source-base-{app}"]
        print(f"\n{connector.title}", flush=True)
        item: dict[str, Any] = {
            "connector_key": connector.connector_key,
            "expected_streams": [stream.name for stream in connector.streams],
        }
        evidence["connectors"][app] = item
        try:
            source = product.call(
                "POST", "/api/v1/sources", expected=(201,), timeout=1200,
                json={
                    "name": f"{marker} {connector.title}",
                    "connector_key": connector.connector_key,
                    "configuration": {
                        "access_token_v2": tokens[app],
                        "domain": args.domain,
                        "updated_from": updated_from,
                    },
                    "test_before_save": True,
                },
            )
            item["source_id"] = source["id"]
            print(f"  source check: healthy ({source['id'][:8]})", flush=True)

            snapshot = product.call(
                "POST", f"/api/v1/sources/{source['id']}/discover",
                timeout=1200)
            names = [stream["name"] for stream in snapshot.get("streams", [])]
            expected = [stream.name for stream in connector.streams]
            item["snapshot_id"] = snapshot["id"]
            item["discovered_streams"] = names
            item["stream_contract_ok"] = set(names) == set(expected)
            if not item["stream_contract_ok"]:
                raise RuntimeError(
                    f"discover mismatch: missing={sorted(set(expected)-set(names))}, "
                    f"extra={sorted(set(names)-set(expected))}")
            no_pk = [s["name"] for s in snapshot["streams"]
                     if not s.get("source_defined_primary_key")]
            if no_pk:
                raise RuntimeError(f"streams without primary key: {no_pk}")
            print(f"  discover: {len(names)}/{len(expected)} streams", flush=True)

            stream_prefix = f"base_{app}_"
            pipeline = product.call(
                "POST", "/api/v1/pipelines", expected=(201,), timeout=1200,
                json={
                    "name": f"{marker} {app} to BigQuery",
                    "source_id": source["id"],
                    "destination_id": destination["id"],
                    "schema_snapshot_id": snapshot["id"],
                    "streams": [selection(stream) for stream in snapshot["streams"]],
                    "stream_prefix": stream_prefix,
                    "run_first_sync": False,
                },
            )
            item["pipeline_id"] = pipeline["id"]
            item["stream_prefix"] = stream_prefix
            run = product.call(
                "POST", f"/api/v1/pipelines/{pipeline['id']}/runs",
                expected=(202,), timeout=120)
            item["run_id"] = run["id"]
            run = wait_for_run(product, run["id"], args.run_timeout_minutes)
            item["run_status"] = run.get("status")
            item["records_synced"] = run.get("records_synced")
            item["bytes_synced"] = run.get("bytes_synced")
            item["stream_stats"] = run.get("stream_stats") or []
            if run.get("status") != "SUCCEEDED":
                error = run.get("error") or {}
                raise RuntimeError(
                    f"run {run.get('status')}: "
                    f"{product.safe(json.dumps(error, ensure_ascii=False))}")

            counts = table_counts(warehouse, project, dataset, stream_prefix)
            item["bigquery_tables"] = counts
            positive = {
                stat["stream_name"]: int(stat.get("records_emitted") or 0)
                for stat in item["stream_stats"]
                if int(stat.get("records_emitted") or 0) > 0
            }
            missing_positive = [
                name for name in positive
                if f"{stream_prefix}{name}" not in counts
            ]
            item["warehouse_verified"] = not missing_positive
            if missing_positive:
                raise RuntimeError(
                    f"positive streams missing in BigQuery: {missing_positive}")
            print(
                f"  sync: {run.get('records_synced')} records, "
                f"{len(counts)} BigQuery table(s)", flush=True)
        except Exception as exc:  # keep testing the remaining independent sources
            failed = True
            item["error"] = product.safe(str(exc))
            print(f"  FAIL: {item['error']}", flush=True)
        finally:
            evidence["updated_at"] = utcnow()
            evidence_path.write_text(
                json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    evidence["finished_at"] = utcnow()
    evidence["passed"] = not failed
    evidence_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nEvidence: {evidence_path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
