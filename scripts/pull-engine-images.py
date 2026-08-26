#!/usr/bin/env python3
"""Pull the connector images this deployment can actually run — and only those.

Airbyte 0.59.1's bootloader seeds its catalogue from Airbyte's *current*
registry, so the engine comes up offering six hundred-odd connectors. The
product offers seven, plus one runner image that executes every connector built
inside the product. Pulling the engine's whole catalogue would be tens of
gigabytes of images for connectors nobody can select.

So the set is taken from the product's own catalogue, never from the engine's:

    python scripts/pull-engine-images.py

That works with the stack down, which is the case that matters — a new machine
pre-pulls before the first sync rather than discovering the wait inside a job,
where the timeout surfaces as ENGINE_UNAVAILABLE and reads like a broken engine.

Given the engine's definition lists, it pulls the tags the engine will *really*
start, still filtered to the product's repositories:

    python scripts/pull-engine-images.py sources.json destinations.json

That is the stricter check. `connector-lock.json` says what should run and the
definitions list says what will; a disagreement means a pin did not apply, and
this reports it rather than pulling over it.

There is deliberately no fourth list of connector names here. `WANTED` used to
be hardcoded and had drifted to four images while the product shipped eight, so
`source-bigquery`, `destination-bigquery`, `source-google-sheets` and
`destination-google-sheets` were silently never pre-pulled.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "backend" / "app" / "resources" / "connector_registry.json"
LOCK = ROOT / "connector-lock.json"

FIELDS = ("sourceDefinitions", "destinationDefinitions")


def bundled() -> dict[str, str]:
    """Every connector image the product can run, repository -> pinned tag.

    The registry is the catalogue a user picks from, at every certification
    level: a BETA connector is still selectable, so a machine that has not
    pre-pulled it still stalls on the first sync. `destination-google-sheets`
    is exactly that case, and is why this reads the registry rather than the
    lock, which by design covers only SUPPORTED connectors.
    """
    wanted: dict[str, str] = {}
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for entry in registry["connectors"]:
        repository = entry.get("docker_repository") or ""
        if repository.startswith("airbyte/") and entry.get("version"):
            wanted[repository] = entry["version"]

    # The runner that executes every connector built in the product. It is not
    # in the registry -- no user selects it -- but without it every Base.vn
    # connector fails on its first job.
    for entry in json.loads(LOCK.read_text(encoding="utf-8"))["connectors"]:
        repository, _, tag = entry["image"].rpartition(":")
        if repository not in wanted:
            wanted[repository] = tag
    return wanted


def from_definitions(paths: list[str], wanted: dict[str, str]) -> list[str]:
    """The tags the engine will really start, limited to our repositories."""
    found: list[str] = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for field in FIELDS:
            for entry in data.get(field) or []:
                repository = entry.get("dockerRepository")
                if repository not in wanted:
                    continue
                tag = entry["dockerImageTag"]
                if tag != wanted[repository]:
                    # Not fatal here -- this script pulls, it does not pin --
                    # but silence would let a failed pin look like a clean run.
                    print(f"  ! {repository} is {tag} on the engine, "
                          f"{wanted[repository]} in the product catalogue; "
                          f"pulling {tag}", file=sys.stderr)
                found.append(f"{repository}:{tag}")
    return sorted(set(found))


def main() -> int:
    wanted = bundled()
    if not wanted:
        print("the product catalogue names no connector images", file=sys.stderr)
        return 1

    paths = sys.argv[1:]
    if paths:
        images = from_definitions(paths, wanted)
        if not images:
            # Silence here would look like success and fail later, inside a job.
            print("none of the product's connectors are in these definition "
                  "lists — is this the right Airbyte, and did the lists load?",
                  file=sys.stderr)
            return 1
        missing = set(wanted) - {i.rsplit(":", 1)[0] for i in images}
        if missing:
            print(f"not offered by this deployment: {', '.join(sorted(missing))}")
    else:
        images = sorted(f"{r}:{t}" for r, t in wanted.items())
        print(f"pulling {len(images)} image(s) from the product catalogue "
              f"(engine not consulted)")

    for image in images:
        print(f"pulling {image}", flush=True)
        subprocess.run(["docker", "pull", image], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
