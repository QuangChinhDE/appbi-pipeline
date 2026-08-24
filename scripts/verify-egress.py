#!/usr/bin/env python3
"""Measure what a connector container can actually reach.

Egress policy is easy to believe in and hard to be right about. The product's
preflight refuses private addresses, the connectors run on their own Docker
network, and both of those are true without telling you what a connector can
reach *at the moment it runs*. This asks, from inside that network, using the
same kind of container Airbyte starts.

    python scripts/verify-egress.py
    python scripts/verify-egress.py --expect-internet-blocked   # hardened profile

Exit code 1 means something reachable should not have been. That makes it usable
as a CI gate and as a post-deploy check, not just a thing to read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

NETWORK = "appbi-pipeline_connectors"
PROBE_IMAGE = "alpine:3.20"


@dataclass
class Target:
    label: str
    host: str
    port: int
    # What the deployment intends. Reaching something marked False is a finding;
    # failing to reach something marked True is a broken deployment.
    should_reach: bool
    why: str


# The product's own plane. A connector reaching either of these could call the
# API with a stolen session or drive the engine directly.
CONTROL_PLANE = [
    Target("product API", "appbi-pipeline-api", 8000, False,
           "a connector that can call the product API can act as the product"),
    Target("engine API", "appbi-airbyte-server", 8001, False,
           "the engine's own control API"),
]

# Deliberately reachable: these are the databases connectors exist to read and
# write. Access is still per-database and per-role.
DATA_PLANE = [
    Target("postgres", "appbi-pipeline-postgres", 5432, True,
           "the demo source and warehouse; connectors are supposed to reach it"),
]

# Off-network. Whether these should be reachable depends on the profile: a
# deployment whose sources are all internal can block them outright.
OUTSIDE = [
    Target("public internet", "1.1.1.1", 443, True,
           "SaaS connectors need this; an internal-only deployment does not"),
    Target("cloud metadata", "169.254.169.254", 80, False,
           "the classic SSRF target: instance credentials, never legitimate"),
]


def probe(target: Target, timeout: int = 4) -> bool:
    """Can a container on the connector network open a socket to this?"""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", NETWORK, PROBE_IMAGE,
         "timeout", str(timeout), "nc", "-z", target.host, str(target.port)],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def network_exists() -> bool:
    result = subprocess.run(["docker", "network", "inspect", NETWORK],
                            capture_output=True, text=True)
    return result.returncode == 0


def is_internal() -> bool:
    result = subprocess.run(
        ["docker", "network", "inspect", NETWORK, "--format", "{{.Internal}}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "true"


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-internet-blocked", action="store_true",
                        help="hardened profile: the internet must be unreachable too")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if not network_exists():
        print(f"No network named {NETWORK}. Bring the stack up first "
              "(python scripts/stack.py airbyte).", file=sys.stderr)
        return 2

    targets = CONTROL_PLANE + DATA_PLANE + list(OUTSIDE)
    if args.expect_internet_blocked:
        targets = [
            t if t.label != "public internet"
            else Target(t.label, t.host, t.port, False,
                        "hardened profile: this network is internal, so nothing "
                        "off it should answer")
            for t in targets
        ]

    print(f"probing from inside {NETWORK} "
          f"(internal={is_internal()})\n")

    findings: list[str] = []
    results = []
    for target in targets:
        reached = probe(target)
        ok = (reached == target.should_reach)
        verdict = "reachable" if reached else "blocked"
        mark = "ok " if ok else "!! "
        print(f"  {mark}{target.label:<18} {target.host}:{target.port:<6} {verdict}")
        results.append({"label": target.label, "host": target.host,
                        "port": target.port, "reachable": reached,
                        "expected": target.should_reach, "ok": ok})
        if not ok:
            findings.append(
                f"{target.label} ({target.host}:{target.port}) is "
                f"{verdict} but should be "
                f"{'reachable' if target.should_reach else 'blocked'} - {target.why}"
            )

    if args.json:
        print("\n" + json.dumps({"network": NETWORK, "internal": is_internal(),
                                 "results": results, "findings": findings}, indent=2))

    if findings:
        print("\nEGRESS FINDINGS:")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print("\negress policy holds for every target probed.")
    if not args.expect_internet_blocked:
        print("  Note: the internet is reachable by design here. Restricting "
              "which external hosts a connector may call is a host firewall or "
              "egress gateway decision - see docs/RUNBOOK-egress.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
