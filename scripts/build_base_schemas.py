#!/usr/bin/env python3
"""Build the runtime Base schema registry from the reviewed YAML contracts.

The YAML files are source material and live outside the backend image. Runtime
connectors therefore read a generated JSON resource beside their Python
definitions. Keeping this as a generator avoids hand-copying thousands of
field definitions or quietly replacing them with a three-column placeholder.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "base-api"
TARGET = ROOT / "backend" / "app" / "connectors" / "base_vn" / "schemas.json"
EXCLUDED_APPS = {"expense"}


def build() -> dict[str, dict[str, dict]]:
    registry: dict[str, dict[str, dict]] = {}
    for path in sorted(SOURCE.glob("base_*.yaml")):
        app = path.stem.removeprefix("base_")
        if app in EXCLUDED_APPS:
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        schemas = document.get("schemas") or {}
        if not isinstance(schemas, dict):
            raise ValueError(f"{path}: schemas must be an object")
        registry[app] = schemas
    return registry


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        actual = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if actual != expected:
            print(f"{TARGET} is stale; run {Path(__file__).name}")
            return 1
        print(f"{TARGET} is current")
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print(f"wrote {TARGET} ({len(build())} applications)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
