"""Connectors this product writes itself, grouped by who maintains them.

Everything here is Python compiled to an Airbyte declarative manifest at import.
Nothing is pulled from Airbyte's registry, nothing needs an image built per
connector, and nothing outside this package needs to know that a connector came
from here rather than from upstream -- `adapters.registry` joins both lists and
the rest of the product sees one catalogue.

Why a provider registry rather than a list in `adapters.registry`
-----------------------------------------------------------------

Because the list was already growing by hand. Adding KiotViet meant editing
`_registry_entries()` to say `base_entries() + kiotviet_entries()`, and the next
group would edit the same function again. Two things go wrong with that:

* **Collisions are invisible.** Nothing stopped two groups shipping the same
  `connector_key`, or claiming a category name that Airbyte's own registry
  already uses (`API`, `Database`, `Warehouse`, `File/Storage`, `SaaS`). The
  first symptom would be a connector silently shadowed in the catalogue.
* **The seam is in the wrong file.** A group of connectors is a self-contained
  thing -- its own request dialect, its own credentials, its own category -- and
  adding one should be adding a package, not editing a function in the adapter
  layer.

So each group declares a `PROVIDER` and this module finds them. `providers()`
scans the sub-packages once, in a stable order, and refuses two providers that
would tread on each other. Adding a group is: create the package, export
`PROVIDER`, add its icons. Nothing else changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

#: Categories Airbyte's own registry uses. A self-developed group must not
#: claim one of these: the catalogue groups by category, so reusing `API` would
#: mix twelve hand-written Base streams in with Facebook Marketing and make the
#: distinction the user cares about -- who supports this -- unreadable.
RESERVED_CATEGORIES = frozenset({
    "API", "Database", "Warehouse", "File/Storage", "SaaS", "Other", "Custom",
})


@dataclass(frozen=True)
class ConnectorProvider:
    """One self-maintained group of connectors.

    `key` is for error messages and ordering, not for anything user-facing.
    `category` is what the catalogue groups by and is owned exclusively by this
    provider -- see `RESERVED_CATEGORIES` and `providers()`.
    """

    key: str
    #: Catalogue heading. Exactly one provider may claim it.
    category: str
    #: Human name of the group, for operator-facing output.
    title: str
    #: Registry rows, in the same shape as the bundled connector registry.
    entries: Callable[[], list[dict[str, Any]]]

    def __post_init__(self) -> None:
        if not self.key or not self.category:
            raise ValueError(f"{self.key or '?'}: key and category are required")
        if self.category in RESERVED_CATEGORIES:
            raise ValueError(
                f"{self.key}: category {self.category!r} is one Airbyte's registry "
                f"already uses; pick a name that identifies who maintains these")


@lru_cache(maxsize=1)
def providers() -> tuple[ConnectorProvider, ...]:
    """Every group in this package, in a stable order.

    Discovered rather than listed: a package that exports `PROVIDER` is a
    provider. Sorted by key so the catalogue does not reorder itself because a
    filesystem enumerated differently.
    """
    found: list[ConnectorProvider] = []
    for module in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if not module.ispkg or module.name.startswith("_"):
            continue
        package = importlib.import_module(f"{__name__}.{module.name}")
        provider = getattr(package, "PROVIDER", None)
        if provider is None:
            continue
        if not isinstance(provider, ConnectorProvider):
            raise TypeError(
                f"{module.name}.PROVIDER is {type(provider).__name__}, "
                f"expected ConnectorProvider")
        found.append(provider)

    found.sort(key=lambda p: p.key)
    _refuse_collisions(found)
    return tuple(found)


def _refuse_collisions(found: list[ConnectorProvider]) -> None:
    """Two groups treading on each other is a startup failure, not a surprise.

    Checked at import so a duplicate cannot reach a customer's catalogue, where
    the symptom is one connector quietly winning over another.
    """
    by_category: dict[str, str] = {}
    by_key: dict[str, str] = {}
    for provider in found:
        owner = by_category.setdefault(provider.category, provider.key)
        if owner != provider.key:
            raise ValueError(
                f"category {provider.category!r} is claimed by both {owner!r} "
                f"and {provider.key!r}")
        for entry in provider.entries():
            key = entry["connector_key"]
            previous = by_key.setdefault(key, provider.key)
            if previous != provider.key:
                raise ValueError(
                    f"connector {key!r} is shipped by both {previous!r} and "
                    f"{provider.key!r}")
            if entry.get("category") != provider.category:
                raise ValueError(
                    f"{provider.key}: {key} declares category "
                    f"{entry.get('category')!r} but the provider owns "
                    f"{provider.category!r}")


def catalogue_entries() -> list[dict[str, Any]]:
    """Every self-written connector, as registry rows."""
    return [entry for provider in providers() for entry in provider.entries()]


__all__ = [
    "RESERVED_CATEGORIES", "ConnectorProvider", "catalogue_entries", "providers",
]
