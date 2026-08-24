#!/usr/bin/env python
"""Generate the product connector registry from Airbyte's official OSS registry.

Airbyte publishes every connector it ships — image, pinned tag, JSON-Schema spec,
logo and support level — as one document. That document is the source of truth for
what a user can connect to, so the catalogue is generated from it rather than
hand-written.

What this script decides, and why:

* **Certification is ours, and it is read, not declared.** `compatibility.yaml`
  records what was actually verified per connector, so it is the only place that
  decides `SUPPORTED`. This script used to imply certification from "is it in
  CURATED", which published `source-file` as SUPPORTED while the evidence file
  said BETA. Airbyte's own rating travels alongside in `support_level`.
* **Pinned versions stay pinned.** For curated connectors the tag we tested wins
  over whatever the registry currently points at (§60 compatibility matrix).
* **No invented copy.** Curated connectors keep their hand-written description;
  the rest carry none, and the UI links to Airbyte's docs instead of showing a
  sentence nobody wrote.

Usage:
    python scripts/build-connector-registry.py [--offline]

`--offline` reuses a previously downloaded copy under the scratch path instead of
re-fetching, so a build without network access still works.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REGISTRY_URL = "https://connectors.airbyte.com/files/registries/v0/oss_registry.json"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "app" / "resources" / "connector_registry.json"
ICON_DIR = ROOT / "backend" / "app" / "resources" / "connector_icons"
CACHE = ROOT / ".cache" / "oss_registry.json"

# A logo is decoration; a multi-megabyte payload masquerading as one is not.
MAX_ICON_BYTES = 64 * 1024

def product_version() -> str:
    """Read the one number that defines this product build.

    Hard-coding it here is what let the registry claim 2.0.0 while the runtime
    and the compatibility matrix both said 1.0.0 — three files, three answers,
    and nothing to notice the drift.
    """
    text = (ROOT / "compatibility.yaml").read_text(encoding="utf-8")
    match = re.search(r'^product_version:\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("compatibility.yaml has no product_version")
    return match.group(1)

# Connectors we maintain metadata for by hand: a tested pin and copy someone
# wrote. This says nothing about certification — that is read from
# compatibility.yaml, which is where the evidence lives.
CURATED: dict[str, dict] = {
    "source-postgres": {
        "version": "3.8.5",
        "description": "Đọc bảng từ PostgreSQL; hỗ trợ full refresh và incremental theo cursor.",
        "icon": "postgres",
    },
    "source-faker": {
        "version": "7.2.1",
        "description": "Nguồn dữ liệu mẫu sinh bởi connector, dùng để kiểm thử pipeline "
                       "mà không cần hệ thống ngoài.",
        "icon": "faker",
    },
    "source-file": {
        "version": "0.6.0",
        "description": "Nạp một file từ URL công khai thành một bảng.",
        "icon": "file",
    },
    "destination-postgres": {
        "version": "3.0.17",
        "description": "Ghi dữ liệu vào PostgreSQL; bảng đích do connector tự tạo và quản lý.",
        "icon": "postgres",
    },
}

# Airbyte types sources but not destinations, so destinations are grouped by what
# they demonstrably are. Anything unmatched stays "Other" rather than guessing.
SOURCE_CATEGORY = {"api": "API", "database": "Database", "file": "File/Storage"}

DESTINATION_CATEGORY = [
    ("Warehouse", r"snowflake|bigquery|redshift|databricks|synapse|clickhouse|firebolt|"
                  r"teradata|exasol|vertica|starburst|dremio|motherduck|duckdb"),
    ("Database", r"postgres|mysql|mssql|oracle|mariadb|mongodb|cassandra|dynamodb|"
                 r"cockroach|singlestore|yugabyte|sqlite|rockset|scylla"),
    ("File/Storage", r"s3|gcs|azure.?blob|local|sftp|ftp|file|parquet|csv"),
    ("Search", r"elasticsearch|opensearch|typesense|meilisearch|solr"),
    ("Vector", r"pinecone|weaviate|qdrant|milvus|chroma|vectara|astra|pgvector"),
    ("Streaming", r"kafka|pulsar|kinesis|pubsub|rabbitmq|nats"),
    ("Cache", r"redis|memcach"),
    ("SaaS", r"salesforce|hubspot|marketo|iterable|braze|customer\.?io|amplitude|"
             r"mixpanel|sheets|airtable|notion"),
]


def certifications() -> dict[str, str]:
    """What compatibility.yaml says has been verified, per connector.

    Parsed with a narrow regex rather than a YAML dependency: this script runs
    from a bare checkout in CI, and the shape it needs is two fixed keys under a
    connector name.
    """
    text = (ROOT / "compatibility.yaml").read_text(encoding="utf-8")
    block = text.split("\nconnectors:", 1)
    if len(block) != 2:
        sys.exit("compatibility.yaml has no connectors section")

    levels: dict[str, str] = {}
    current: str | None = None
    for line in block[1].splitlines():
        name = re.match(r"^  ([a-z0-9][a-z0-9._-]*):\s*$", line)
        if name:
            current = name.group(1)
            continue
        level = re.match(r"^    certification:\s*([A-Z_]+)\s*$", line)
        if level and current:
            levels[current] = level.group(1)
    if not levels:
        sys.exit("compatibility.yaml declares no connector certifications")
    return levels


def fetch(offline: bool) -> dict:
    if offline:
        if not CACHE.exists():
            sys.exit(f"--offline given but no cached registry at {CACHE}")
        return json.loads(CACHE.read_text(encoding="utf-8"))

    print(f"fetching {REGISTRY_URL}")
    with urllib.request.urlopen(REGISTRY_URL, timeout=120) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(raw, encoding="utf-8")
    print(f"cached {len(raw):,} bytes at {CACHE}")
    return json.loads(raw)


def download_icons(connectors: list[dict]) -> int:
    """Vendor connector logos so the browser never calls the upstream registry.

    Icons are served by our own API (section 11.4: the catalogue must render when
    the registry is unreachable), which means they have to live in the image.
    """
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    targets = [(c["connector_key"], c["icon_url"]) for c in connectors if c.get("icon_url")]

    def fetch_one(item: tuple[str, str]) -> bool:
        key, url = item
        destination = ICON_DIR / f"{key}.svg"
        if destination.exists():
            return True
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "appbi-pipeline"})
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = response.read(MAX_ICON_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
        # Only trust something that actually looks like an SVG document.
        if len(payload) > MAX_ICON_BYTES or b"<svg" not in payload[:2048].lower():
            return False
        destination.write_bytes(payload)
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(fetch_one, targets))
    return sum(results)


def destination_category(key: str) -> str:
    for name, pattern in DESTINATION_CATEGORY:
        if re.search(pattern, key, re.IGNORECASE):
            return name
    return "Other"


def detect_cdc(spec: dict) -> bool:
    """True only when the spec offers an explicit CDC replication method.

    Matching the bare word anywhere in the spec produces false positives (a
    description mentioning CDC is not support for it), so this looks at the
    option titles under a replication-method property.
    """
    properties = spec.get("properties") or {}
    method = properties.get("replication_method") or properties.get("replication")
    if not isinstance(method, dict):
        return False
    branches = method.get("oneOf") or method.get("anyOf") or []
    for branch in branches:
        title = str(branch.get("title", ""))
        if re.search(r"\bCDC\b|change data capture|logical replication", title, re.IGNORECASE):
            return True
    return False


def entry(raw: dict, kind: str, certified: dict[str, str]) -> dict | None:
    repository = raw.get("dockerRepository") or ""
    key = repository.split("/")[-1]
    if not key or raw.get("tombstone"):
        return None

    spec = raw.get("spec") or {}
    connection_spec = spec.get("connectionSpecification") or {}
    if not connection_spec:
        # Without a spec the wizard cannot render a form, so the connector is
        # not offerable. Skipping is honest; shipping an unusable card is not.
        return None

    curated = CURATED.get(key)
    support_level = raw.get("supportLevel") or "community"

    if kind == "SOURCE":
        category = SOURCE_CATEGORY.get(raw.get("sourceType") or "", "Other")
        # Incremental is decided per stream by the discovered catalog, not the
        # spec, so the flag here reflects only that the mode is available at all.
        supports_incremental = True
        sync_modes: list[str] = []
    else:
        category = destination_category(key)
        supports_incremental = bool(spec.get("supportsIncremental", True))
        sync_modes = spec.get("supported_destination_sync_modes") or [
            "overwrite", "append", "append_dedup",
        ]

    return {
        "connector_key": key,
        "display_name": raw.get("name") or key,
        "connector_type": kind,
        "category": category,
        "icon": (curated or {}).get("icon") or key.split("-", 1)[-1],
        "icon_url": raw.get("iconUrl") or "",
        "documentation_url": raw.get("documentationUrl") or "",
        "description": (curated or {}).get("description", ""),
        "docker_repository": repository,
        "version": (curated or {}).get("version") or raw.get("dockerImageTag") or "latest",
        "release_stage": raw.get("releaseStage") or "alpha",
        "support_level": support_level,
        # Absent from the evidence file means nobody verified it.
        "certification": certified.get(key, "BETA"),
        "supports_oauth": "credentials" in connection_spec.get("properties", {}),
        "supports_incremental": supports_incremental,
        "supports_cdc": detect_cdc(connection_spec) if kind == "SOURCE" else False,
        "supports_namespaces": kind == "DESTINATION",
        "supported_destination_sync_modes": sync_modes,
        "spec_schema": connection_spec,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--icons", action="store_true",
                        help="also vendor connector logos into resources/connector_icons")
    args = parser.parse_args()

    registry = fetch(args.offline)

    certified = certifications()
    connectors: list[dict] = []
    for kind, bucket in (("SOURCE", "sources"), ("DESTINATION", "destinations")):
        for raw in registry.get(bucket, []):
            built = entry(raw, kind, certified)
            if built:
                connectors.append(built)

    # Curated first, then alphabetical, so the connectors we stand behind lead
    # the catalogue without hiding the rest.
    connectors.sort(key=lambda c: (c["certification"] != "SUPPORTED", c["display_name"].lower()))

    missing = set(CURATED) - {c["connector_key"] for c in connectors}
    if missing:
        sys.exit(f"curated connectors absent from the upstream registry: {sorted(missing)}")

    unrecorded = set(CURATED) - set(certified)
    if unrecorded:
        sys.exit(f"curated connectors with no entry in compatibility.yaml: {sorted(unrecorded)}")

    document = {
        "registry_version": registry.get("version", "v0"),
        "product_version": product_version(),
        "source": REGISTRY_URL,
        "connectors": connectors,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")

    if args.icons:
        stored = download_icons(connectors)
        print(f"vendored {stored} icons -> {ICON_DIR}")

    sources = sum(1 for c in connectors if c["connector_type"] == "SOURCE")
    destinations = len(connectors) - sources
    size = OUT.stat().st_size
    print(f"wrote {len(connectors)} connectors "
          f"({sources} sources, {destinations} destinations) -> {OUT} [{size:,} bytes]")
    for key in CURATED:
        pinned = next(c for c in connectors if c["connector_key"] == key)
        print(f"  curated {key:24s} {pinned['version']:10s} {pinned['certification']}")


if __name__ == "__main__":
    main()
