#!/usr/bin/env python3
"""Does the engine still have what the product database says it has?

Run this after a restore, after a migration, or any time the product is pointed
at a different engine deployment than the one it was written against. Every row
in `engine_mappings` is a handle into one specific deployment; restore that
database beside a different Airbyte and none of them resolve.

    python scripts/reconcile.py                       # against the local stack
    python scripts/reconcile.py --url https://appbi.internal

Exit 0 consistent, 1 resources missing, 2 the engine could not be reached.
Three codes rather than two on purpose: "the engine is down" and "the engine
says these are gone" call for opposite actions, and a script that collapses
them into "failed" hands the operator the wrong one half the time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8010")
    parser.add_argument("--cookie", default=os.getenv("APPBI_COOKIE"),
                        help="session cookie for the product API (or APPBI_COOKIE)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/v1/engine/reconcile")
    if args.cookie:
        request.add_header("Cookie", args.cookie)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            report = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print("The product API refused this session. Log in and pass "
                  "--cookie or set APPBI_COOKIE.")
            return 2
        print(f"reconcile answered {exc.code}: {exc.read().decode(errors='replace')[:300]}")
        return 2
    except urllib.error.URLError as exc:
        print(f"Could not reach the product API at {args.url}: {exc.reason}")
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(report.get("detail", ""))
        for item in report.get("missing", []):
            print(f"  MISSING  {item['resource_type']:<12} {item['name']}")
        if report.get("consistent"):
            print(f"  {report.get('present', 0)} present, none missing")
        if report.get("foreign"):
            print(f"  {report['foreign']} mapping(s) belong to another engine "
                  "implementation and were not checked")

    if not report.get("engine_reachable", False):
        return 2
    return 1 if report.get("missing") else 0


if __name__ == "__main__":
    raise SystemExit(main())
