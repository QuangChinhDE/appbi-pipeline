#!/usr/bin/env python
"""Pin the connectors this product stands behind, by content and not by name.

A tag is a mutable pointer. `airbyte/source-postgres:3.8.5` today and the same
string next month can be different bytes, and nothing in a version string says
otherwise. The lock records the digest that was actually certified, so a drifted
upstream image fails a check instead of quietly becoming what production runs.

Only `SUPPORTED` connectors are locked: those are the ones we make a claim
about. A `BETA` connector is offered best-effort and pinning it would suggest a
guarantee that does not exist.

    python scripts/build-connector-lock.py            # refresh from the registry
    python scripts/build-connector-lock.py --verify   # CI: fail on drift
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "backend" / "app" / "resources" / "connector_registry.json"
LOCK = ROOT / "connector-lock.json"

# The runner that executes every connector built in the product. It is not in
# the registry, but a drift here changes the behaviour of all of them at once.
EXTRA_IMAGES = ["airbyte/source-declarative-manifest"]

_DIGEST_RE = re.compile(r"@(sha256:[0-9a-f]{64})")


def supported_entries() -> list[dict]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entries = [c for c in registry["connectors"] if c.get("certification") == "SUPPORTED"]
    return sorted(entries, key=lambda c: c["connector_key"])


def runner_version() -> str:
    """The pinned runner tag, read from whichever module defines it.

    It used to live in `builder.py` and moved to `builder_manifest.py`. This
    read was pinned to the old path, so the lock builder exited before writing
    anything and the lock quietly went stale -- pinning three connectors while
    the registry certified more. Searching the package means the next move does
    not break it again.
    """
    services = ROOT / "backend" / "app" / "services"
    for path in sorted(services.glob("builder*.py")):
        match = re.search(r'RUNNER_VERSION\s*=\s*"([^"]+)"',
                          path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    sys.exit("no RUNNER_VERSION found in backend/app/services/builder*.py")


def digest_of(image: str) -> str | None:
    """The repo digest Docker recorded for a local image.

    Absent means the image has not been pulled here, which is a fact worth
    recording rather than an error: a developer machine is not a release build.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        digests = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return None
    for entry in digests:
        found = _DIGEST_RE.search(entry)
        if found:
            return found.group(1)
    return None


def build() -> dict:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    locked: list[dict] = []

    for entry in supported_entries():
        image = f"{entry['docker_repository']}:{entry['version']}"
        locked.append({
            "connector_key": entry["connector_key"],
            "image": image,
            "digest": digest_of(image),
            "spec_hash": spec_hash(entry.get("spec_schema") or {}),
            "certification": entry["certification"],
        })

    version = runner_version()
    for repository in EXTRA_IMAGES:
        image = f"{repository}:{version}"
        locked.append({
            "connector_key": repository.split("/")[-1],
            "image": image,
            "digest": digest_of(image),
            "spec_hash": None,          # the runner has no spec of its own
            "certification": "RUNNER",
        })

    return {
        "product_version": registry.get("product_version"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "connectors": locked,
    }


def spec_hash(spec: dict) -> str:
    import hashlib

    material = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def verify() -> int:
    """Fail when the registry and the lock disagree about what runs.

    Digests are compared only where both sides have one: CI that has not pulled
    the images can still catch a version or spec change, which is the common
    case, without pretending to have checked bytes it never saw.
    """
    if not LOCK.exists():
        print("no connector-lock.json — run this script without --verify first")
        return 1

    locked = {c["connector_key"]: c for c in json.loads(LOCK.read_text(encoding="utf-8"))["connectors"]}
    problems: list[str] = []

    for entry in supported_entries():
        key = entry["connector_key"]
        record = locked.get(key)
        if record is None:
            problems.append(f"{key}: marked SUPPORTED but absent from the lock")
            continue

        image = f"{entry['docker_repository']}:{entry['version']}"
        if record["image"] != image:
            problems.append(f"{key}: lock has {record['image']}, registry has {image}")

        expected = spec_hash(entry.get("spec_schema") or {})
        if record.get("spec_hash") and record["spec_hash"] != expected:
            problems.append(f"{key}: spec changed since it was certified")

        live = digest_of(image)
        if record.get("digest") and live and record["digest"] != live:
            problems.append(
                f"{key}: image digest drifted — tag {image} no longer points at "
                f"the bytes that were certified")

    for key, record in locked.items():
        if record.get("certification") == "RUNNER":
            continue
        if key not in {c["connector_key"] for c in supported_entries()}:
            problems.append(f"{key}: locked but no longer SUPPORTED")

    if problems:
        print("connector lock is out of date:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"connector lock OK ({len(locked)} entries)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="check the lock against the registry instead of rewriting it")
    args = parser.parse_args()

    if args.verify:
        sys.exit(verify())

    document = build()
    LOCK.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    missing = [c["connector_key"] for c in document["connectors"] if not c["digest"]]
    print(f"wrote {LOCK} with {len(document['connectors'])} entries")
    if missing:
        print(f"  no local digest for: {', '.join(missing)} (pull them to record one)")


if __name__ == "__main__":
    main()
