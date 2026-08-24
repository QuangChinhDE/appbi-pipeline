#!/usr/bin/env bash
# Pre-pull connector images so the first "Test connection" is fast instead of
# downloading inline.
#
# Default is the SUPPORTED set only. The catalogue is the whole upstream
# registry — 650+ images, hundreds of gigabytes — and pulling all of it is
# neither a useful default nor evidence that any of it is supported.
#
#   bash scripts/pull-connectors.sh          # certified connectors + the runner
#   bash scripts/pull-connectors.sh --all    # everything in the registry
set -euo pipefail

REGISTRY="$(dirname "$0")/../backend/app/resources/connector_registry.json"
BUILDER="$(dirname "$0")/../backend/app/services/builder.py"
SCOPE="supported"
[ "${1:-}" = "--all" ] && SCOPE="all"

# `tr -d` guards against a Python on Windows writing CRLF to stdout.
images=$(python - "$REGISTRY" "$SCOPE" <<'PY' | tr -d ''
import json
import sys

registry = json.load(open(sys.argv[1], encoding="utf-8"))
scope = sys.argv[2]
for entry in registry["connectors"]:
    if scope == "supported" and entry.get("certification") != "SUPPORTED":
        continue
    print(f"{entry['docker_repository']}:{entry['version']}")
PY
)

# The declarative runner executes every connector built in the product, so it is
# part of the certified set even though it is not a catalogue entry.
if [ "$SCOPE" = "supported" ]; then
  runner_version=$(grep -oE 'RUNNER_VERSION = "[^"]+"' "$BUILDER" | cut -d'"' -f2)
  images="$images airbyte/source-declarative-manifest:$runner_version"
fi

count=0
for image in $images; do
  echo "==> $image"
  docker pull "$image"
  count=$((count + 1))
done

echo
if [ "$SCOPE" = "supported" ]; then
  echo "$count certified images are local. Run with --all for the full catalogue."
else
  echo "$count images are local (full catalogue; most are BETA and uncertified)."
fi
