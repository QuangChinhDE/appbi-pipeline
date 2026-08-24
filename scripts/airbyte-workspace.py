#!/usr/bin/env python3
"""Find, create and verify the Airbyte workspace this product writes into.

`AIRBYTE_WORKSPACE_ID` decides which Airbyte workspace every source,
destination and connection the product creates belongs to. Getting it wrong is
not a startup error — it is a deployment that quietly writes into someone
else's tenant — so it is configured explicitly rather than guessed, and this
script is how you find the value to configure.

    python scripts/airbyte-workspace.py list
    python scripts/airbyte-workspace.py create --name "AppBI Production"
    python scripts/airbyte-workspace.py verify --id <uuid>

The Airbyte URL comes from --url, else AIRBYTE_API_URL, else the staging
default. Basic auth comes from AIRBYTE_API_USERNAME / AIRBYTE_API_PASSWORD when
set. From outside the Compose network `airbyte-server` will not resolve; either
run this inside a container on that network or point --url at a published port.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

DEFAULT_URL = os.getenv("AIRBYTE_API_URL") or "http://localhost:8001"


def call(url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    user = os.getenv("AIRBYTE_API_USERNAME", "")
    password = os.getenv("AIRBYTE_API_PASSWORD", "")
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        raise SystemExit(f"Airbyte answered {exc.code} for {path}:\n  {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Could not reach Airbyte at {url}: {exc.reason}\n"
            "  Inside Compose the host is `airbyte-server:8001`; from the host "
            "machine it is whatever port the deployment publishes."
        )


def workspaces(url: str) -> list[dict]:
    return call(url, "/api/v1/workspaces/list", {}).get("workspaces", [])


def cmd_list(url: str) -> int:
    found = workspaces(url)
    if not found:
        print("No workspaces on this Airbyte. Create one:")
        print('  python scripts/airbyte-workspace.py create --name "AppBI"')
        return 1

    print(f"{len(found)} workspace(s) on {url}:\n")
    for entry in found:
        print(f"  {entry['workspaceId']}  {entry.get('name', '(unnamed)')}")

    print("\nSet the one this deployment owns:")
    print(f"  AIRBYTE_WORKSPACE_ID={found[0]['workspaceId']}")
    if len(found) > 1:
        # Picking for them here is how a deployment ends up in the wrong tenant.
        print("\n  More than one exists — choose deliberately. This value decides "
              "which tenant every source and connection lands in.")
    return 0


def cmd_create(url: str, name: str, email: str) -> int:
    for entry in workspaces(url):
        if entry.get("name") == name:
            print(f"A workspace named {name!r} already exists; not creating a second one.")
            print(f"  AIRBYTE_WORKSPACE_ID={entry['workspaceId']}")
            return 0

    created = call(url, "/api/v1/workspaces/create", {"name": name, "email": email})
    print(f"Created {created['workspaceId']}  {created.get('name')}")
    print(f"\n  AIRBYTE_WORKSPACE_ID={created['workspaceId']}")
    return 0


def cmd_verify(url: str, workspace_id: str) -> int:
    """Does this id exist here, and is it usable?

    Worth a command of its own: the failure this catches is a valid-looking
    UUID copied from a different Airbyte, which passes every static check the
    product makes and then fails on the first source anyone creates.
    """
    known = {entry["workspaceId"]: entry for entry in workspaces(url)}
    entry = known.get(workspace_id)
    if entry is None:
        print(f"{workspace_id} is not a workspace on {url}.")
        if known:
            print("  This Airbyte has: " + ", ".join(known))
        print("  A UUID from a different Airbyte passes every check the product "
              "makes at boot and fails on the first source anyone creates.")
        return 1

    print(f"OK  {workspace_id}  {entry.get('name', '(unnamed)')}")

    # Definitions are workspace-scoped in the Config API, so this also proves
    # the id is usable for the calls the adapter actually makes.
    sources = call(url, "/api/v1/source_definitions/list_for_workspace",
                   {"workspaceId": workspace_id}).get("sourceDefinitions", [])
    print(f"    {len(sources)} source definitions visible to it")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"Airbyte Config API base URL (default: {DEFAULT_URL})")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_url(subparser):
        # Argparse only accepts a parent-level option before the subcommand,
        # and `verify --url ... --id ...` is what anyone would type first.
        # SUPPRESS keeps the parent's value unless this one is actually given.
        subparser.add_argument("--url", default=argparse.SUPPRESS,
                               help="overrides the global --url")
        return subparser

    with_url(sub.add_parser("list", help="show the workspaces on this Airbyte"))

    create = with_url(sub.add_parser("create", help="create one if it does not exist"))
    create.add_argument("--name", required=True)
    create.add_argument("--email", default="platform@appbi.local")

    verify = with_url(sub.add_parser("verify", help="check an id exists and is usable"))
    verify.add_argument("--id", required=True, dest="workspace_id")

    args = parser.parse_args()
    if args.command == "list":
        return cmd_list(args.url)
    if args.command == "create":
        return cmd_create(args.url, args.name, args.email)
    return cmd_verify(args.url, args.workspace_id)


if __name__ == "__main__":
    raise SystemExit(main())
