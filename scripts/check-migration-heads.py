#!/usr/bin/env python3
"""Assert the Alembic chain has exactly one head.

Two heads mean an installed database cannot upgrade: `alembic upgrade head` has
no single target and stops. The failure surfaces during a customer's update, not
during the change that caused it, which is why this is a gate rather than a
convention.

This lives in a script rather than inline in CI so the local verification run
and the CI job execute the same code. Exit 0 on success, 1 on failure.

    python scripts/check-migration-heads.py
"""

from __future__ import annotations

import pathlib
import re
import sys

VERSIONS = pathlib.Path(__file__).resolve().parent.parent / "backend" / "migrations" / "versions"

REVISION = re.compile(r'^revision(?::\s*\w+)?\s*=\s*"([^"]+)"', re.M)
IDENT = re.compile(r'"([0-9a-f]{12})"')


def main() -> int:
    if not VERSIONS.is_dir():
        print(f"no migrations directory at {VERSIONS}", file=sys.stderr)
        return 1

    revisions: set[str] = set()
    parents: set[str] = set()

    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = REVISION.search(text)
        if match:
            revisions.add(match.group(1))
        # `down_revision` is the link backwards. Read the window after it so a
        # revision id appearing elsewhere in the file is not mistaken for one.
        if "down_revision" in text:
            window = text.split("down_revision", 1)[1][:200]
            parents.update(IDENT.findall(window))

    heads = sorted(revisions - parents)
    print(f"{len(revisions)} revisions, heads: {heads}")

    if len(heads) != 1:
        print(f"expected exactly one migration head, found {heads}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
