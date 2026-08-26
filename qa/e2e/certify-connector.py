#!/usr/bin/env python3
"""Run a connector for real, and record what it actually did.

    python scripts/certify-connector.py source-bigquery --config secrets/bq.json
    python scripts/certify-connector.py source-postgres --config secrets/pg.json \
        --stream orders --out evidence-connectors.json

Moving a connector from `BETA` to `SUPPORTED` in `compatibility.yaml` is a
promise that the product can stand behind it. This is what turns that promise
into something checkable: it executes the Airbyte Protocol against the pinned
image, the same way the embedded engine does at runtime, and writes down the
result -- image digest included, so the evidence names the bytes that ran.

    spec      the connector starts and declares its configuration schema
    check     the credentials in --config actually reach the system
    discover  the connector can enumerate streams with those credentials
    read      one stream produces records (only with --stream)

`check` is the one that matters for a launch decision. A connector whose image
runs but whose `check` fails against a real account is not usable, and that
distinction is invisible to anything that only inspects the registry.

The config file holds live credentials. Keep it outside the repository, or in
a path `.gitignore` covers -- `secrets/` is ignored for this purpose. Nothing
from the config is written into the evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# `_console` is shared tooling and lives with the operational scripts;
# only this import crosses from `qa/` into `scripts/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "backend" / "app" / "resources" / "connector_registry.json"


def image_for(connector_key: str) -> str:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for entry in registry["connectors"]:
        if entry["connector_key"] == connector_key:
            return f"{entry['docker_repository']}:{entry['version']}"
    sys.exit(f"{connector_key} is not in the connector registry")


def digest_of(image: str) -> str | None:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return None
    try:
        for entry in json.loads(result.stdout or "[]"):
            if "@" in entry:
                return entry.split("@", 1)[1]
    except ValueError:
        pass
    return None


def protocol(image: str, command: list[str], mounts: dict[str, str],
             timeout: int) -> tuple[int, list[dict], str]:
    """Run one connector command and return its Airbyte Protocol messages.

    Connectors write protocol messages as JSON lines on stdout and human logs
    on stderr, and they mix non-JSON lines into stdout too. Anything that does
    not parse is a log line, not a failure.
    """
    argv = ["docker", "run", "--rm", "--network", "host"]
    for host_path, container_path in mounts.items():
        argv += ["-v", f"{host_path}:{container_path}:ro"]
    argv += [image, *command]

    environment = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=timeout, env=environment)
    except subprocess.TimeoutExpired:
        return 124, [], f"timed out after {timeout}s"

    messages = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            messages.append(json.loads(line))
        except ValueError:
            continue
    return result.returncode, messages, (result.stderr or "")[-2000:]


def first(messages: list[dict], kind: str) -> dict | None:
    return next((m for m in messages if m.get("type") == kind), None)


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("connector", help="registry key, e.g. source-bigquery")
    parser.add_argument("--config", required=True,
                        help="JSON config for this connector; never committed")
    parser.add_argument("--stream", default="",
                        help="also read this stream, to prove records flow")
    parser.add_argument("--records", type=int, default=10,
                        help="stop after this many records when reading")
    parser.add_argument("--out", default="evidence-connectors.json")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        sys.exit(f"{config_path} does not exist")

    image = image_for(args.connector)
    print(f"connector : {args.connector}")
    print(f"image     : {image}")

    pull = subprocess.run(["docker", "image", "inspect", image],
                          capture_output=True, text=True)
    if pull.returncode != 0:
        print("pulling...")
        if subprocess.run(["docker", "pull", image]).returncode != 0:
            sys.exit(f"could not pull {image}")

    digest = digest_of(image)
    print(f"digest    : {digest or 'not recorded locally'}")

    # The connector reads its config from a path inside the container.
    mounts = {str(config_path): "/secrets/config.json"}
    results: dict[str, object] = {}

    print("\nspec")
    code, messages, stderr = protocol(image, ["spec"], {}, args.timeout)
    spec = first(messages, "SPEC")
    results["spec"] = bool(spec)
    if spec:
        properties = ((spec.get("spec") or {}).get("connectionSpecification")
                      or {}).get("properties") or {}
        print(f"  ok    {len(properties)} configuration field(s)")
    else:
        print(f"  FAIL  exit {code}: {stderr[-300:]}")

    print("\ncheck")
    code, messages, stderr = protocol(
        image, ["check", "--config", "/secrets/config.json"], mounts, args.timeout)
    status_message = first(messages, "CONNECTION_STATUS")
    status = ((status_message or {}).get("connectionStatus") or {}).get("status", "")
    results["check"] = status == "SUCCEEDED"
    if status == "SUCCEEDED":
        print("  ok    SUCCEEDED")
    else:
        reason = ((status_message or {}).get("connectionStatus") or {}).get("message", "")
        print(f"  FAIL  {status or f'exit {code}'}: {(reason or stderr)[-400:]}")

    streams: list[str] = []
    catalog: dict = {}
    # Destinations have no `discover`: they are written to, not read from.
    # Running it anyway produced a Micronaut stack trace that read like a
    # broken connector rather than a command that does not exist.
    is_source = args.connector.startswith("source-")
    if not is_source:
        results["discover"] = None
        print("\ndiscover\n  not applicable to a destination")
    elif results["check"]:
        print("\ndiscover")
        code, messages, stderr = protocol(
            image, ["discover", "--config", "/secrets/config.json"], mounts,
            args.timeout)
        catalog_message = first(messages, "CATALOG")
        catalog = (catalog_message or {}).get("catalog") or {}
        streams = [s.get("name", "") for s in catalog.get("streams", [])]
        results["discover"] = bool(streams)
        print(f"  {'ok   ' if streams else 'FAIL '} {len(streams)} stream(s)"
              + (f": {', '.join(streams[:8])}" if streams else f" {stderr[-300:]}"))
    else:
        results["discover"] = None
        print("\ndiscover\n  skipped, because check did not pass")

    if args.stream and results.get("discover"):
        print(f"\nread {args.stream}")
        # A full-refresh catalog for one stream, written next to the config so
        # the same mount carries it.
        with tempfile.TemporaryDirectory(prefix="certify-") as tmp:
            catalog_path = Path(tmp) / "catalog.json"
            chosen = next((s for s in catalog.get("streams", [])
                           if s.get("name") == args.stream), None)
            if chosen is None:
                print(f"  FAIL  {args.stream} is not in the discovered catalog")
                results["read"] = False
            else:
                catalog_path.write_text(json.dumps({"streams": [{
                    "stream": chosen,
                    "sync_mode": "full_refresh",
                    "destination_sync_mode": "overwrite",
                }]}), encoding="utf-8")
                code, messages, stderr = protocol(
                    image, ["read", "--config", "/secrets/config.json",
                            "--catalog", "/secrets/catalog.json"],
                    {**mounts, str(catalog_path): "/secrets/catalog.json"},
                    args.timeout)
                records = [m for m in messages if m.get("type") == "RECORD"]
                results["read"] = bool(records)
                print(f"  {'ok   ' if records else 'FAIL '} {len(records)} record(s)"
                      + ("" if records else f": {stderr[-300:]}"))

    out = Path(args.out)
    evidence = {}
    if out.exists():
        try:
            evidence = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            evidence = {}
    evidence.setdefault("schema", "connector-evidence/v1")
    evidence.setdefault("connectors", {})
    evidence["connectors"][args.connector] = {
        "image": image,
        "digest": digest,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
        "streams": streams[:50],
    }
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"\nevidence written to {out}")

    # `check` is the launch-relevant outcome; spec passing on its own only says
    # the image starts.
    return 0 if results["check"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
