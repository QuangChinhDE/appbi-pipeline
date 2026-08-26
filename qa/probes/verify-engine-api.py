#!/usr/bin/env python3
"""Check that an Airbyte deployment offers everything the adapter calls.

Before certifying the product against a different Airbyte — a newer line, a
Kubernetes install, someone else's managed deployment — the first question is
whether the API surface the adapter depends on is still there. Reading release
notes answers that slowly and unreliably. This asks the deployment.

    python qa/probes/verify-engine-api.py --url http://airbyte-server:8001

Every endpoint is probed with an empty body. What matters is the distinction
between *absent* and *present but unhappy*:

    404          the endpoint is gone. The adapter will break here.
    400/422/500  present, and it rejected an empty payload. Expected.
    200          present and tolerant of an empty payload.

Exit 1 if anything the adapter needs is missing, so this can gate an upgrade.
It does not prove the *semantics* are unchanged — only the full contract suite
against a live deployment does that. It proves the endpoints exist, which is
the cheap half, and it fails fast when they do not.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# `_console` is shared tooling and lives with the operational scripts;
# only this import crosses from `qa/` into `scripts/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "backend/app/adapters/airbyte_api/adapter.py"

# GET, not POST. Most of the Config API is POST-shaped and these two are not —
# a mistake the adapter made once and paid for with a health check that always
# reported the engine offline.
GET_ENDPOINTS = {"/api/v1/health", "/api/v1/instance_configuration"}


def endpoints_the_adapter_uses() -> list[str]:
    """Read them out of the adapter rather than maintaining a second list.

    A hand-kept list drifts, and it drifts silently: the check keeps passing
    while the adapter starts calling something new.
    """
    source = ADAPTER.read_text(encoding="utf-8")
    found = set(re.findall(r'"(/api/v1/[a-z_/]+)"', source))
    return sorted(found)


def alternative_groups() -> list[frozenset[str]]:
    """Routes the adapter treats as interchangeable, declared by the adapter.

    Airbyte 1.8.5 answers /workspaces/list with 404; 0.59.1 has no
    /workspaces/list_by_organization_id. Both are in the adapter because it
    falls back, and neither absence is a defect. Reading the declaration keeps
    that judgement in the adapter, where the fallback lives, instead of in a
    special case here.
    """
    source = ADAPTER.read_text(encoding="utf-8")
    match = re.search(r"ALTERNATIVE_ROUTE_GROUPS[^=]*=\s*\((.*?)\n\)\n",
                      source, re.DOTALL)
    if not match:
        return []
    return [frozenset(re.findall(r'"(/api/v1/[a-z_/]+)"', group))
            for group in re.findall(r"\((.*?)\)", match.group(1), re.DOTALL)
            if re.findall(r'"(/api/v1/[a-z_/]+)"', group)]


_BEARER: dict[str, str] = {}


def bearer_token(url: str) -> str:
    """Exchange the Application credentials for a token, once per URL.

    Airbyte 1.x does not accept Basic here. This probe therefore ran
    unauthenticated against an auth-enabled engine, and every endpoint came
    back 401 -- which the report rendered as "the adapter's API surface is
    gone" rather than "nobody logged in". Same call the adapter makes, so a
    401 after this really is the credentials.
    """
    if url in _BEARER:
        return _BEARER[url]

    client_id = os.getenv("AIRBYTE_CLIENT_ID", "")
    client_secret = os.getenv("AIRBYTE_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        _BEARER[url] = ""
        return ""

    request = urllib.request.Request(
        url.rstrip("/") + "/api/v1/applications/token",
        data=json.dumps({"client_id": client_id,
                         "client_secret": client_secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = (json.loads(response.read().decode()) or {}).get("access_token", "")
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        print(f"  could not get a token from {url}: {exc}", file=sys.stderr)
        token = ""
    _BEARER[url] = str(token or "")
    return _BEARER[url]


def probe(url: str, path: str, timeout: int = 15) -> tuple[int | None, str]:
    method = "GET" if path in GET_ENDPOINTS else "POST"
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=None if method == "GET" else b"{}",
        headers={"Content-Type": "application/json"},
        method=method,
    )
    token = bearer_token(url)
    user, password = os.getenv("AIRBYTE_API_USERNAME", ""), os.getenv("AIRBYTE_API_PASSWORD", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    elif user and password:
        # 0.59.1 and other pre-1.x engines, which do accept Basic.
        basic = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {basic}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, "ok"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.reason or ""
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        # A read timeout is not a URLError, so this used to escape and take the
        # whole probe with it -- one slow endpoint and nothing got certified.
        # It is also not "missing": the server accepted the request and is
        # working on it. Airbyte 1.8.5 does this on the scheduler check routes,
        # which start a connector container before they answer.
        return -1, f"no answer in {timeout}s"



def probe_in_network(args: argparse.Namespace) -> int:
    """Run this same script inside the Docker network the API lives on.

    The Compose deployment keeps Airbyte's Config API off the host on purpose,
    so a probe run from a laptop reports connection refused and proves nothing.
    Rather than telling the reader to hand-assemble a `docker run`, do it.
    """
    url = args.url or "http://airbyte-server:8001"
    command = [
        "docker", "run", "--rm", "--network", args.in_network,
        "-v", f"{ROOT}:/repo:ro", "-w", "/repo",
    ]
    # Forward the credentials by name, never by value: `-e NAME` inherits from
    # this process, so the secret does not appear in the command line or in any
    # process listing. Without this the inner probe runs unauthenticated and
    # reports 401 on every endpoint as a missing API surface.
    for name in ("AIRBYTE_CLIENT_ID", "AIRBYTE_CLIENT_SECRET",
                 "AIRBYTE_API_USERNAME", "AIRBYTE_API_PASSWORD"):
        if os.getenv(name):
            command += ["-e", name]
    command += [
        "python:3.11-slim", "python", "qa/probes/verify-engine-api.py", "--url", url,
    ]
    if args.json:
        command.append("--json")

    print(f"probing from inside {args.in_network} "
          "(the API is not published to the host)")
    print()
    # MSYS_NO_PATHCONV keeps Git Bash from rewriting /repo into a Windows path.
    environment = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    try:
        return subprocess.run(command, env=environment).returncode
    except FileNotFoundError:
        print("docker is not on PATH; run the probe from somewhere that can "
              "reach the Airbyte API directly with --url.", file=sys.stderr)
        return 2


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("AIRBYTE_API_URL", ""))
    parser.add_argument("--in-network", metavar="NETWORK", nargs="?",
                        const="appbi-pipeline_appbi",
                        help="re-run this probe inside a container on the given "
                             "Docker network (default appbi-pipeline_appbi). "
                             "Needed for the Compose stack, where the Airbyte "
                             "API is deliberately not published to the host.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.in_network:
        return probe_in_network(args)

    if not args.url:
        # No default to localhost: the Compose stack does not publish the
        # Airbyte API, so a localhost default produces "connection refused" and
        # the reader concludes the probe is broken rather than misaimed.
        for line in (
            "No --url given, and AIRBYTE_API_URL is unset.",
            "  Compose stack (the API is internal-only):",
            "    python qa/probes/verify-engine-api.py --in-network",
            "  A reachable deployment:",
            "    python qa/probes/verify-engine-api.py --url https://airbyte.example",
        ):
            print(line, file=sys.stderr)
        return 2

    paths = endpoints_the_adapter_uses()
    if not paths:
        print(f"Found no endpoints in {ADAPTER}. Has the adapter moved?", file=sys.stderr)
        return 2

    print(f"probing {len(paths)} endpoint(s) on {args.url}\n")

    missing: list[str] = []
    unreachable = False
    results = []

    for path in paths:
        status, detail = probe(args.url, path)
        if status is None:
            print(f"  ?? {path:<52} unreachable: {detail}")
            unreachable = True
            verdict = "unreachable"
        elif status == -1:
            # Present and slow. Recorded as its own verdict rather than folded
            # into either bucket: calling it missing would fail a good
            # deployment, calling it present would hide a hung route.
            print(f"  .. {path:<52} {detail} (present, slow)")
            verdict = "slow"
        elif status == 404:
            print(f"  !! {path:<52} 404 NOT FOUND")
            missing.append(path)
            verdict = "missing"
        else:
            print(f"  ok {path:<52} {status}")
            verdict = "present"
        results.append({"path": path, "status": status, "verdict": verdict})

    # A group is satisfied when any member answered. Only then is a 404 on
    # another member a version difference the adapter already handles -- not a
    # gap. Done before the verdict so the JSON and the exit code agree.
    for group in alternative_groups():
        answered = [row["path"] for row in results
                    if row["path"] in group and row["verdict"] in ("present", "slow")]
        if not answered:
            continue
        for path in [p for p in missing if p in group]:
            missing.remove(path)
            for row in results:
                if row["path"] == path:
                    row["verdict"] = "absent-but-covered"
            print(f"  -- {path:<52} 404, covered by {answered[0]}")

    if args.json:
        print("\n" + json.dumps({"url": args.url, "results": results}, indent=2))

    if unreachable:
        print("\nCould not reach the deployment. Nothing was proven.", file=sys.stderr)
        return 2

    if missing:
        print(f"\n{len(missing)} endpoint(s) the adapter calls do not exist here:")
        for path in missing:
            print(f"  - {path}")
        print("\nThe adapter needs work before this deployment can be certified. "
              "Note which calls use them: `grep -n '<path>' "
              "backend/app/adapters/airbyte_api/adapter.py`")
        return 1

    print("\nEvery endpoint the adapter calls exists on this deployment.")
    print("  That is the cheap half. Run the contract suite for the semantics:")
    print("    RUN_ENGINE_CONTRACT=1 pytest tests/test_adapter_contract.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
