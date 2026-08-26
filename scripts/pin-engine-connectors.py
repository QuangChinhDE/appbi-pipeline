#!/usr/bin/env python3
"""Make the engine run the connector versions this product pinned.

Runs as `airbyte-connector-pin` in the Compose stack, after the server is
healthy and before the product is allowed to start. Also runnable by hand:

    python scripts/pin-engine-connectors.py --lock connector-lock.json \
        --api http://localhost:8001

Why this has to run every time
------------------------------

Airbyte 0.59.1's bootloader seeds its connector definitions from Airbyte's
*current* catalogue, and it does it on every start — not once at first boot. So
a platform from 2024 comes up offering connectors published this month.

That is not merely untidy. `destination-postgres` arrives as 3.x, which
implements Airbyte's refresh protocol and requires `generationId` in the
configured catalog. Platform 0.59.1 predates the protocol and never sends it,
so the destination dies on the first record:

    BeanInstantiationException: PostgresWriter
    Caused by: NullPointerException: getGenerationId(...) must not be null

Nothing warns about the mismatch. The definition looks healthy, `check`
succeeds, and the failure arrives at replication time. Pinning by hand works
and is undone by the next `docker compose up`.

`connector-lock.json` is the product's decision about which connector bytes
run. This pushes that decision into the engine on every boot, so there is one
answer to "what version is this" rather than two that disagree.

Deliberately stdlib-only: it runs in a bare `python:3.11-slim` with nothing
installed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SIDES = (
    ("sources", "source_definitions", "sourceDefinitions", "sourceDefinitionId"),
    ("destinations", "destination_definitions", "destinationDefinitions",
     "destinationDefinitionId"),
)


def call(api: str, path: str, payload: dict | None = None, *,
         timeout: int = 30) -> dict:
    """POST with a body, GET without one.

    The Config API is POST for everything except `/health`, which is GET and
    answers 405 to a POST. Sending the wrong verb there makes the readiness
    wait fail forever against a perfectly healthy engine.
    """
    if payload is None:
        request = urllib.request.Request(f"{api.rstrip('/')}{path}", method="GET")
    else:
        request = urllib.request.Request(
            f"{api.rstrip('/')}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def wait_for(api: str, attempts: int = 60) -> bool:
    """The compose healthcheck says the port answers; this says the API does."""
    for attempt in range(attempts):
        try:
            call(api, "/api/v1/health", timeout=10)
            return True
        except Exception:                                   # noqa: BLE001
            if attempt == 0:
                print("waiting for the engine API ...", flush=True)
            time.sleep(3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default="connector-lock.json")
    parser.add_argument("--api", default="http://airbyte-server:8001")
    args = parser.parse_args()

    with open(args.lock, encoding="utf-8") as handle:
        lock = json.load(handle)
    wanted = {}
    for entry in lock["connectors"]:
        repository, _, tag = entry["image"].rpartition(":")
        wanted[repository] = tag
    if not wanted:
        print("the lock names no connectors", file=sys.stderr)
        return 1

    if not wait_for(args.api):
        print(f"the engine API at {args.api} never answered", file=sys.stderr)
        return 1

    changed = aligned = 0
    for _, path, collection, id_field in SIDES:
        try:
            data = call(args.api, f"/api/v1/{path}/list", {})
        except Exception as exc:                            # noqa: BLE001
            print(f"could not list {path}: {exc}", file=sys.stderr)
            return 1

        for definition in data.get(collection, []):
            repository = definition.get("dockerRepository", "")
            target = wanted.get(repository)
            if not target:
                continue
            running = definition.get("dockerImageTag")
            if running == target:
                aligned += 1
                continue
            try:
                call(args.api, f"/api/v1/{path}/update",
                     {id_field: definition[id_field], "dockerImageTag": target})
            except Exception as exc:                        # noqa: BLE001
                # Fail loudly: the product waits on this service, and starting
                # it against an unpinned engine is the failure being prevented.
                print(f"could not pin {repository} to {target}: {exc}",
                      file=sys.stderr)
                return 1
            changed += 1
            print(f"  pinned {repository} {running} -> {target}", flush=True)

    print(f"engine connectors: {changed} pinned, {aligned} already correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
