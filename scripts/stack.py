#!/usr/bin/env python3
"""Bring up only as much of the stack as the task needs.

The full certification stack is fourteen containers, because it runs both this
product and an Airbyte deployment on one machine. That is the right shape for
proving the two work together and the wrong shape for editing a React
component, and leaving it running all day is how a laptop ends up with 1.7GB of
Java it is not using.

    python scripts/stack.py lite       # 4 containers: the product's core
    python scripts/stack.py embedded   # 7 containers: local demo, full UI
    python scripts/stack.py airbyte    # 14 containers: real Airbyte, certification
    python scripts/stack.py status     # what is running, and what it costs
    python scripts/stack.py stop       # stop the Airbyte half, keep the product
    python scripts/stack.py stop --all # stop everything, keep the volumes

`stop` is the one to reach for. `down` also removes containers and networks;
neither touches volumes unless you ask, because losing a test warehouse to a
stray flag wastes an afternoon.

A note on why this is Python and not a Makefile: `make` is not installed on
every machine this runs on, and the same command has to work from PowerShell
and from a POSIX shell.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Containers from other Compose projects on the same machine start with the same
# few letters, and counting someone else's 2.3GB backend as ours is how a
# footprint review reaches the wrong conclusion. Match the project prefix.
PROJECT_PREFIXES = ("appbi-pipeline-", "appbi-airbyte-")

BASE = "docker-compose.yml"
EMBEDDED = "docker-compose.embedded.yml"
AIRBYTE = "docker-compose.airbyte.yml"

# What each mode is for, and the smallest set of services that delivers it.
MODES: dict[str, dict] = {
    "lite": {
        "files": [BASE],
        # No frontend and no proxy: the fast loop for UI work is `npm run dev`
        # against this API, which reloads in a second instead of rebuilding an
        # image. No engine either — nothing here starts connectors, so `lite`
        # is for API and schema work, not for running a sync.
        "services": ["postgres", "api", "worker"],
        "summary": "product core only (postgres, api, worker)",
        "next": [
            "cd frontend && npm run dev      # UI at http://localhost:3000",
            "API at http://localhost:{api_port}",
        ],
    },
    "embedded": {
        "files": [BASE, EMBEDDED],
        "services": [],  # everything in the two files
        "summary": "local demo: the product runs connector images itself",
        "next": [
            "open http://localhost:{proxy_port}",
            "the Docker socket is mounted here; this is a demo path, not production",
        ],
    },
    "airbyte": {
        "files": [BASE, AIRBYTE],
        "services": [],
        "summary": "certification stack: a real self-managed Airbyte runs the connectors",
        "next": [
            "wait for appbi-airbyte-server to report healthy (a minute or two)",
            "python scripts/e2e.py --source postgres --engine airbyte-api",
        ],
    },
}



def env_ports() -> dict[str, str]:
    """The ports the stack will actually publish.

    Read from .env the same way Compose does, so the printed URL is the one
    that works. An unresolved ${API_PORT:-8010} in the output looks like a URL
    and is not one.
    """
    values = {"api_port": "8000", "proxy_port": "8080"}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "API_PORT":
                values["api_port"] = value.strip() or values["api_port"]
            elif key.strip() == "PROXY_PORT":
                values["proxy_port"] = value.strip() or values["proxy_port"]
    # A real environment variable wins, as it does for Compose.
    values["api_port"] = os.getenv("API_PORT") or values["api_port"]
    values["proxy_port"] = os.getenv("PROXY_PORT") or values["proxy_port"]
    return values


def compose(files: list[str], *args: str, check: bool = True) -> int:
    command = ["docker", "compose"]
    for name in files:
        command += ["-f", name]
    command += list(args)

    # The .env in a local checkout sets COMPOSE_FILE, which would silently add
    # the embedded overlay on top of whatever was asked for here. The -f flags
    # above are the whole truth, so that variable is cleared for this call.
    environment = {**os.environ}
    environment.pop("COMPOSE_FILE", None)

    print(f"$ {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def cmd_up(mode: str, build: bool) -> None:
    spec = MODES[mode]
    args = ["up", "-d"]
    if build:
        args.append("--build")
    args += spec["services"]

    compose(spec["files"], *args)

    print()
    print(f"{mode}: {spec['summary']}")
    ports = env_ports()
    for line in spec["next"]:
        print("  " + line.format(**ports))
    print()
    print("  python scripts/stack.py status    # see what this is costing")


def cmd_status() -> None:
    """What is up, and what it costs — the second half is the point."""
    compose([BASE], "ps", "--format",
            "table {{.Name}}\t{{.Service}}\t{{.Status}}", check=False)

    print("\nresource use (all AppBI/Airbyte containers):")
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"],
        capture_output=True, text=True,
    )
    rows = [line for line in result.stdout.splitlines()
            if line.startswith(PROJECT_PREFIXES)]
    if not rows:
        print("  nothing running")
        return

    total_mib = 0.0
    for row in sorted(rows):
        print(f"  {row}")
        used = row.split("\t")[1].split("/")[0].strip()
        try:
            value = float(used[:-3] if used.endswith(("MiB", "GiB")) else used[:-1])
            total_mib += value * (1024 if used.endswith("GiB") else 1)
        except ValueError:
            # A container that reports nothing usable is not worth failing over.
            pass
    print(f"  -- {len(rows)} containers, about {total_mib / 1024:.1f} GiB")


def cmd_stop(everything: bool) -> None:
    """Stop, not down: containers keep their state and start again quickly."""
    if everything:
        compose([BASE, EMBEDDED, AIRBYTE], "stop", check=False)
        print("\nstopped everything. volumes are untouched; "
              "`python scripts/stack.py lite` brings the product back.")
        return

    # The common case: the product stays usable, the Java goes away.
    airbyte_services = [
        "airbyte-server", "airbyte-worker", "airbyte-temporal",
        "airbyte-minio", "airbyte-cron",
    ]
    compose([BASE, AIRBYTE], "stop", *airbyte_services, check=False)
    print("\nstopped the Airbyte half. The product is still up, but with "
          "ENGINE_TYPE=AIRBYTE_API it now has no engine: /readyz reports the "
          "engine down and syncs will fail until you start it again.")


def cmd_down(volumes: bool) -> None:
    if volumes:
        # Deleting a warehouse by accident costs an afternoon, so this asks.
        print("This removes volumes: the product database, the demo source and "
              "warehouse, and Airbyte's own state.")
        if input("Type 'delete' to confirm: ").strip() != "delete":
            print("cancelled")
            return
    args = ["down"] + (["-v"] if volumes else [])
    compose([BASE, EMBEDDED, AIRBYTE], *args, check=False)


def main() -> None:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for mode, spec in MODES.items():
        up = sub.add_parser(mode, help=spec["summary"])
        up.add_argument("--build", action="store_true",
                        help="rebuild the product images first")

    sub.add_parser("status", help="what is running, and what it costs")

    stop = sub.add_parser("stop", help="stop the Airbyte half (or everything)")
    stop.add_argument("--all", action="store_true", dest="everything",
                      help="stop the product too")

    down = sub.add_parser("down", help="remove containers and networks")
    down.add_argument("--volumes", action="store_true",
                      help="also delete the databases (asks for confirmation)")

    args = parser.parse_args()

    if args.command in MODES:
        cmd_up(args.command, args.build)
    elif args.command == "status":
        cmd_status()
    elif args.command == "stop":
        cmd_stop(args.everything)
    elif args.command == "down":
        cmd_down(args.volumes)


if __name__ == "__main__":
    main()
