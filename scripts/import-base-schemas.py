#!/usr/bin/env python3
"""Re-derive the Base field contracts from the reviewed YAML, when you have it.

This is a *migration* tool, not a build step. The schemas it writes are checked
in under `backend/app/connectors/base_vn/schemas/` and shipped in the image;
nothing at build or run time reads `docs/`.

It used to be the other way round, and that was a mistake worth naming. The
runtime registry was generated from eleven YAML files under `docs/base-api`,
so a documentation folder was a build input. Delete it, or forget to push it,
and this script wrote an empty registry over a working one -- replacing the
field contracts of all ten Base connectors with nothing, while every test but
one stayed green. That happened.

Run it only when a reviewed YAML contract changes, and commit the result:

    python scripts/import-base-schemas.py                    # rewrite from YAML
    python scripts/import-base-schemas.py --check            # compare, do not write
    python scripts/import-base-schemas.py --source path/to/yaml

If the YAML is not present the script says so and changes nothing, because
"the source material is missing" and "the connectors have no schemas" are very
different situations and only one of them is a problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "base-api"
TARGET = ROOT / "backend" / "app" / "connectors" / "base_vn" / "schemas"

#: Shipped connectors only. `expense` has a reviewed contract and no connector.
EXCLUDED_APPS = {"expense"}


def build(source: Path) -> dict[str, dict]:
    import yaml

    registry: dict[str, dict] = {}
    for path in sorted(source.glob("base_*.yaml")):
        app = path.stem.removeprefix("base_")
        if app in EXCLUDED_APPS:
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        schemas = document.get("schemas") or {}
        if not isinstance(schemas, dict):
            raise ValueError(f"{path}: schemas must be an object")
        registry[app] = schemas
    return registry


def render(streams: dict) -> str:
    """One application per file, indented so a change is readable in a diff."""
    return json.dumps(streams, ensure_ascii=False, indent=1, sort_keys=True) + chr(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="compare against what is committed and write nothing")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()

    if not args.source.is_dir():
        print(f"{args.source} is not present; nothing to import. The committed "
              f"schemas under {TARGET.relative_to(ROOT)} are the source of truth "
              f"and are unaffected.")
        return 0

    registry = build(args.source)
    if not registry:
        print(f"{args.source} contains no base_*.yaml; refusing to write an "
              f"empty registry over {TARGET.relative_to(ROOT)}.")
        return 1

    TARGET.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []
    for app, streams in sorted(registry.items()):
        path = TARGET / f"{app}.json"
        expected = render(streams)
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual == expected:
            continue
        drifted.append(app)
        if not args.check:
            path.write_text(expected, encoding="utf-8")

    if args.check:
        if drifted:
            print(f"committed schemas differ from {args.source} for: "
                  f"{', '.join(drifted)}")
            return 1
        print(f"committed schemas match {args.source} ({len(registry)} applications)")
        return 0

    print(f"wrote {len(drifted) or 0} of {len(registry)} applications -> "
          f"{TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
