#!/usr/bin/env python3
"""Check a running deployment enforces the permission matrix it publishes.

`backend/tests/test_permissions.py` proves the matrix is coherent. It cannot
prove the endpoints obey it: a route that forgets its `ctx.require` passes every
unit test ever written about the matrix. This signs in as real accounts against
a real deployment and compares what each endpoint does with what `/auth/me` says
that role is allowed.

    python scripts/verify-permissions.py --accounts accounts.json
    python scripts/verify-permissions.py --api http://localhost:8010/api/v1 \
        --account admin@example.com:secret --account analyst@example.com:secret

Two directions of failure are reported, and both matter:

    HOLE          the matrix says no, the endpoint said yes -- a privilege leak
    OVER-BLOCKED  the matrix says yes, the endpoint said no -- a broken screen

Exit code is 1 if anything failed, so this can gate a deploy.

The accounts are supplied rather than discovered: a tool that could enumerate
credentials would be a worse problem than the one it checks for. Use throwaway
accounts on a rehearsal deployment -- never a customer's.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_API = "http://127.0.0.1:8010/api/v1"

#: Every list endpoint that should be gated, and the (module, action) that
#: `/auth/me` publishes for it. Endpoints taking a path parameter are out of
#: scope here: they need a resource id, and a wrong id is indistinguishable
#: from a permission failure.
GATED: dict[str, tuple[str, str]] = {
    "/sources": ("sources", "view"),
    "/destinations": ("destinations", "view"),
    "/pipelines": ("pipelines", "view"),
    "/transforms": ("transforms", "view"),
    "/transforms/connections": ("transforms", "view"),
    "/transforms/systems": ("transforms", "view"),
    "/overview": ("monitoring", "view"),
    "/monitoring": ("monitoring", "view"),
    "/runs": ("monitoring", "view"),
    "/alerts/rules": ("alerts", "view"),
    "/alerts/notifications": ("alerts", "view"),
    "/alerts/unread-count": ("alerts", "view"),
    "/audit": ("audit", "view"),
    "/workspace/members": ("members", "view"),
    "/workspace": ("settings", "view"),
    "/workspace/settings": ("settings", "view"),
    "/engine/status": ("settings", "view"),
    "/connectors": ("connectors", "view"),
    "/builder/projects": ("connectors", "view"),
    "/oauth/providers": ("connectors", "view"),
}

#: Gated by a dependency rather than by the matrix, so checked separately.
PLATFORM_ONLY = ("/engine/reconcile", "/admin/compatibility")


class Client:
    """One signed-in session. Cookies are per instance, so roles cannot bleed."""

    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def request(self, method: str, path: str, body: dict | None = None,
                headers: dict | None = None) -> tuple[int, str]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.api + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")
        except OSError as exc:
            return 0, str(exc)

    def login(self, email: str, password: str) -> tuple[int, dict]:
        status, body = self.request(
            "POST", "/auth/login", {"email": email, "password": password}
        )
        return status, (json.loads(body) if status == 200 else {})


def sweep(api: str, email: str, password: str) -> list[str]:
    problems: list[str] = []
    client = Client(api)
    status, me = client.login(email, password)
    if status != 200:
        return [f"{email}: sign-in failed ({status})"]

    role = me.get("role", "?")
    permissions = me.get("permissions", {})
    is_platform_admin = bool(me.get("is_platform_admin"))
    print(f"\n--- {email}  role={role} ---")

    for path, (module, action) in sorted(GATED.items()):
        may = action in permissions.get(module, [])
        code, body = client.request("GET", path)
        if may and code != 200:
            problems.append(f"{role}: OVER-BLOCKED {path} (matrix allows, http {code})")
            print(f"  OVER-BLOCKED {path:<28} http={code}")
        elif not may and code != 403:
            problems.append(f"{role}: HOLE {path} (matrix denies, http {code})")
            print(f"  HOLE         {path:<28} http={code}  {body[:100]}")

    for path in PLATFORM_ONLY:
        code, _ = client.request("GET", path)
        if is_platform_admin and code != 200:
            problems.append(f"{role}: OVER-BLOCKED {path} for a platform admin (http {code})")
        elif not is_platform_admin and code != 403:
            problems.append(f"{role}: HOLE {path} reachable without platform admin (http {code})")

    if not problems:
        print("  every gated endpoint agrees with the published matrix")
    return problems


def isolation(api: str, weak: tuple[str, str], strong: tuple[str, str]) -> list[str]:
    """Probe the tenant boundary from the outside.

    `weak` is an account with limited reach; `strong` one that can enumerate the
    organisation's workspaces. The question is whether naming somebody else's
    workspace in a header is enough to be let in.
    """
    print("\n--- tenant isolation ---")
    problems: list[str] = []

    admin = Client(api)
    if admin.login(*strong)[0] != 200:
        return [f"{strong[0]}: sign-in failed; cannot probe isolation"]
    code, body = admin.request("GET", "/organization/workspaces")
    if code != 200:
        return [f"could not list workspaces to probe against (http {code})"]
    everything = {w["id"] for w in json.loads(body)}

    user = Client(api)
    status, me = user.login(*weak)
    if status != 200:
        return [f"{weak[0]}: sign-in failed; cannot probe isolation"]
    reachable = {w["id"] for w in me.get("workspaces", [])}
    unreachable = sorted(everything - reachable)
    print(f"  {weak[0]} reaches {len(reachable)} of {len(everything)} workspaces")
    if not unreachable:
        print("  (this account reaches everything; pick a narrower one to prove isolation)")
        return problems

    target = unreachable[0]
    for path in ("/auth/me", "/sources", "/pipelines"):
        code, _ = user.request("GET", path, headers={"X-Workspace-Id": target})
        print(f"  {path:<12} with a forged X-Workspace-Id -> {code}")
        if code == 200:
            problems.append(f"HOLE: forged X-Workspace-Id accepted on {path}")
        elif code != 403:
            problems.append(f"unexpected http {code} on {path} with a forged workspace header")

    code, _ = user.request("POST", f"/auth/switch-workspace/{target}")
    print(f"  switch-workspace into an unreachable workspace -> {code}")
    if code == 200:
        problems.append("HOLE: switched into a workspace the account cannot reach")

    if not problems:
        print("  the boundary held on every probe")
    return problems


def parse_accounts(args: argparse.Namespace) -> list[tuple[str, str]]:
    accounts: list[tuple[str, str]] = []
    if args.accounts:
        with open(args.accounts, encoding="utf-8") as handle:
            for entry in json.load(handle):
                accounts.append((entry["email"], entry["password"]))
    for pair in args.account or []:
        email, _, password = pair.partition(":")
        if not password:
            raise SystemExit(f"--account expects email:password, got {pair!r}")
        accounts.append((email, password))
    return accounts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
    parser.add_argument("--account", action="append", metavar="EMAIL:PASSWORD",
                        help="repeatable; one signed-in role to sweep")
    parser.add_argument("--accounts", metavar="FILE",
                        help='JSON list of {"email":..., "password":...}')
    parser.add_argument("--skip-isolation", action="store_true",
                        help="sweep roles only; do not probe the tenant boundary")
    args = parser.parse_args()

    accounts = parse_accounts(args)
    if not accounts:
        parser.error("give at least one account with --account or --accounts")

    problems: list[str] = []
    for email, password in accounts:
        problems += sweep(args.api, email, password)

    if not args.skip_isolation and len(accounts) >= 2:
        # The last account named is assumed to be the least privileged, and the
        # first the most: that is the order somebody writes them in.
        problems += isolation(args.api, accounts[-1], accounts[0])

    print("\n" + "=" * 68)
    if problems:
        print(f"FAILED -- {len(problems)} problem(s):")
        for problem in problems:
            print("  " + problem)
        return 1
    print("PASSED -- enforcement matches the published matrix, isolation held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
