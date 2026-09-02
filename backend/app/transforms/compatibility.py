"""The certified dbt Core + adapter version set.

One lock file, read once. The blueprint is explicit that the version pinning
pattern stays and that no version is hardcoded in a second place -- so every
question about "which dbt do we run" resolves here, and an upgrade is a change
to `transform_engine_lock.json` plus the fixture and parser checks that guard it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def lock() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "resources" / "transform_engine_lock.json"
    return json.loads(path.read_text(encoding="utf-8"))


def capability(connector_key: str) -> dict[str, Any] | None:
    item = lock().get("adapters", {}).get(connector_key)
    if not item or item.get("enabled") is False:
        return None
    return {**item, "dbt_core": lock()["dbt_core"]}


def is_supported(connector_key: str) -> bool:
    item = capability(connector_key)
    return bool(item and item.get("certification") == "SUPPORTED")
