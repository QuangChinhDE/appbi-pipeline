"""Read ``catalog.json`` -- what the warehouse actually contains.

The distinction from the manifest matters and is easy to blur.  The manifest
holds what somebody *wrote down* in YAML: a column's description, and a
`data_type` only if they typed one.  The catalogue holds what the warehouse
*reports*: the real columns, in real order, with real types, plus whatever
statistics the adapter exposes.

Where the two disagree, the catalogue is the fact.  A model whose YAML still
documents a column that was dropped three releases ago should show the drift,
not paper over it -- so the two are kept separate here and merged only at the
point of display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.transforms.artifacts.schema_version import ArtifactVersion, artifact_version


@dataclass(slots=True)
class CatalogColumn:
    name: str
    type: str | None = None
    index: int = 0
    comment: str | None = None


@dataclass(slots=True)
class CatalogRelation:
    unique_id: str
    database: str | None = None
    schema: str | None = None
    name: str | None = None
    relation_type: str | None = None
    owner: str | None = None
    comment: str | None = None
    columns: list[CatalogColumn] = field(default_factory=list)
    #: Adapter-reported statistics, already filtered to the ones dbt marked
    #: includable -- row counts, byte sizes, partitioning, whatever the warehouse
    #: chose to expose.  Kept as-is: which stats exist is adapter-specific and
    #: the product has no business enumerating them.
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedCatalog:
    version: ArtifactVersion
    relations: dict[str, CatalogRelation]

    def columns_for(self, unique_id: str) -> list[CatalogColumn]:
        relation = self.relations.get(unique_id)
        return relation.columns if relation else []


def parse_catalog(document: dict[str, Any]) -> ParsedCatalog:
    version = artifact_version(document, "catalog")
    relations: dict[str, CatalogRelation] = {}

    for section in ("nodes", "sources"):
        entries = document.get(section)
        if not isinstance(entries, dict):
            continue
        for unique_id, node in entries.items():
            if not isinstance(node, dict):
                continue
            metadata = node.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            relations[unique_id] = CatalogRelation(
                unique_id=unique_id,
                database=_string(metadata.get("database")),
                schema=_string(metadata.get("schema")),
                name=_string(metadata.get("name")),
                relation_type=_string(metadata.get("type")),
                owner=_string(metadata.get("owner")),
                comment=_string(metadata.get("comment")),
                columns=_columns(node.get("columns")),
                stats=_stats(node.get("stats")),
            )

    return ParsedCatalog(version=version, relations=relations)


def _columns(value: Any) -> list[CatalogColumn]:
    if not isinstance(value, dict):
        return []
    columns = [
        CatalogColumn(
            name=_string(column.get("name")) or str(name),
            type=_string(column.get("type")),
            index=int(column.get("index") or 0),
            comment=_string(column.get("comment")),
        )
        for name, column in value.items()
        if isinstance(column, dict)
    ]
    # Warehouse order, not dictionary order: a person reading a column list is
    # comparing it against `select *`, and that comes back ordinal.
    columns.sort(key=lambda item: item.index)
    return columns


def _stats(value: Any) -> dict[str, Any]:
    """Statistics dbt marked as worth showing.

    Every adapter reports a different set and each entry carries an `include`
    flag saying whether dbt considers it presentable; honouring that flag is
    what keeps `has_stats` -- a bookkeeping entry about the stats themselves --
    out of a panel meant to show row counts.
    """
    if not isinstance(value, dict):
        return {}
    stats: dict[str, Any] = {}
    for key, entry in value.items():
        if not isinstance(entry, dict) or not entry.get("include", False):
            continue
        stats[str(key)] = {
            "label": _string(entry.get("label")) or str(key),
            "value": entry.get("value"),
            "description": _string(entry.get("description")),
        }
    return stats


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
