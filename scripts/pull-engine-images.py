#!/usr/bin/env python3
"""Pull the connector images a self-managed Airbyte says it will run.

In AIRBYTE_API mode the engine owns the connector version, not the product: our
`connector-lock.json` records what the embedded executor pins, and Airbyte pins
its own. Pulling from the lock file would download the right connectors at the
wrong tags, and the first sync would then stall on an image pull inside a job.

So this asks the deployment. Input is whatever
`/api/v1/{source,destination}_definitions/list` returned.

    python scripts/pull-engine-images.py sources.json destinations.json

A Kubernetes Airbyte runs connectors as pods, so the images have to be on the
*cluster's* nodes, not the laptop's Docker daemon. `--into-kind` pulls them
there instead:

    python scripts/pull-engine-images.py --into-kind appbi-cert-control-plane         sources.json destinations.json

Skipping this on a cold cluster does not fail cleanly: the first `check` starts
a pod, the pod spends minutes pulling, and the product times out with
ENGINE_UNAVAILABLE -- which reads like the engine is broken when it is only
cold. That is exactly how the first Kubernetes certification run failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Only what the contract suite and the e2e script actually exercise. Pulling all
# 600-odd definitions would take longer than the tests and prove nothing.
WANTED = {
    "airbyte/source-postgres",
    "airbyte/source-faker",
    "airbyte/destination-postgres",
    "airbyte/source-declarative-manifest",
}

FIELDS = ("sourceDefinitions", "destinationDefinitions")


def images(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for field in FIELDS:
            for entry in data.get(field) or []:
                repository = entry.get("dockerRepository")
                if repository in WANTED:
                    found.append(f"{repository}:{entry['dockerImageTag']}")
    return sorted(set(found))


def main() -> int:
    arguments = sys.argv[1:]
    node = ""
    if "--into-kind" in arguments:
        index = arguments.index("--into-kind")
        try:
            node = arguments[index + 1]
        except IndexError:
            print("--into-kind needs the node container name, e.g. "
                  "appbi-cert-control-plane", file=sys.stderr)
            return 2
        del arguments[index:index + 2]

    paths = arguments
    if not paths:
        print(__doc__)
        return 2

    wanted = images(paths)
    if not wanted:
        # Silence here would look like success and fail later, inside a job.
        print("no matching connector definitions found — is this the right "
              "Airbyte, and did the definitions list load?", file=sys.stderr)
        return 1

    for image in wanted:
        if node:
            # crictl on the node, rather than `docker pull` + `kind load`: the
            # load path round-trips the whole image through a tarball on disk,
            # and these are hundreds of megabytes each.
            print(f"pulling {image} onto {node}", flush=True)
            subprocess.run(["docker", "exec", node, "crictl", "pull", image],
                           check=True)
        else:
            print(f"pulling {image}", flush=True)
            subprocess.run(["docker", "pull", image], check=True)

    missing = WANTED - {image.split(":")[0] for image in wanted}
    if missing:
        # Not fatal: a deployment may legitimately not carry the declarative
        # runner until a connector is built. Say so rather than hiding it.
        print(f"not offered by this deployment: {', '.join(sorted(missing))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
