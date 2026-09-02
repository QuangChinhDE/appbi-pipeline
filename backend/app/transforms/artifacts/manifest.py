"""Read ``manifest.json``.

This is the canonical read model for everything about a project's structure:
which resources exist, where their files are, what they compile to, what they
depend on.  dbt has already resolved every `ref` and `source` -- including the
ones inside a macro, behind a `var`, or contributed by a package -- so nothing
in AppBI needs to, or should, look at SQL text to answer those questions.

The manifest is large.  Everything here streams through it once and keeps only
the fields the product actually renders; a 40 MB manifest becomes a few thousand
index rows, and the manifest itself stays in object storage for the cases that
need the whole document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from app.transforms.artifacts.schema_version import ArtifactVersion, artifact_version

#: Top-level manifest keys that hold resources, and how each maps to a type.
#:
#: `nodes` is heterogeneous -- models, tests, seeds, snapshots, analyses and
#: operations all live there, each carrying its own `resource_type` -- so it is
#: read from the node rather than the section.  The rest are homogeneous.
_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("nodes", None),
    ("sources", "source"),
    ("macros", "macro"),
    ("exposures", "exposure"),
    ("metrics", "metric"),
    ("groups", "group"),
    ("saved_queries", "saved_query"),
    ("semantic_models", "semantic_model"),
    ("unit_tests", "unit_test"),
)


@dataclass(slots=True)
class ManifestResource:
    """One resource, flattened to what the product renders.

    ``config`` is kept whole rather than picked apart.  The inspector shows the
    keys it has a form for and displays the rest as they are, which is the only
    way an unknown config stays visible instead of silently vanishing.
    """

    unique_id: str
    resource_type: str
    name: str
    package_name: str | None = None
    original_file_path: str | None = None
    patch_path: str | None = None
    database: str | None = None
    schema: str | None = None
    alias: str | None = None
    relation_name: str | None = None
    materialized: str | None = None
    description: str | None = None
    group_name: str | None = None
    tags: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    columns: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    checksum: str | None = None
    #: Only for sources; drives the freshness panel and the AppBI Pipeline link.
    source_name: str | None = None
    identifier: str | None = None
    #: Only for tests: which resource the test is attached to, and which column.
    attached_node: str | None = None
    column_name: str | None = None
    #: Only for models/analyses that were compiled in this invocation.
    compiled_code: str | None = None
    raw_code: str | None = None


@dataclass(slots=True)
class ParsedManifest:
    version: ArtifactVersion
    resources: dict[str, ManifestResource]
    #: unique_id -> parents, exactly as dbt resolved it.
    parent_map: dict[str, list[str]]
    child_map: dict[str, list[str]]
    #: Named selectors from `selectors.yml`, so the command bar can offer them.
    selectors: list[dict[str, Any]]
    #: Package names from `packages.yml` / `dependencies.yml` as installed.
    packages: list[str]
    project_name: str | None = None

    def by_type(self, resource_type: str) -> list[ManifestResource]:
        return [
            item for item in self.resources.values() if item.resource_type == resource_type
        ]

    def counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self.resources.values():
            totals[item.resource_type] = totals.get(item.resource_type, 0) + 1
        return totals

    def tests_for(self, unique_id: str) -> list[ManifestResource]:
        """Every test attached to a resource -- not only the four with a form.

        A project's own tests, a package's tests and singular tests all come
        back, because the manifest does not distinguish "tests AppBI can render
        a checkbox for" from the rest, and neither should the UI.
        """
        return [
            item for item in self.resources.values()
            if item.resource_type in ("test", "unit_test") and item.attached_node == unique_id
        ]


def parse_manifest(document: dict[str, Any]) -> ParsedManifest:
    version = artifact_version(document, "manifest")
    resources: dict[str, ManifestResource] = {}

    for section, fixed_type in _SECTIONS:
        entries = document.get(section)
        if not isinstance(entries, dict):
            continue
        for unique_id, node in entries.items():
            if not isinstance(node, dict):
                continue
            resource = _resource(unique_id, node, fixed_type)
            if resource is not None:
                resources[unique_id] = resource

    # `disabled` holds resources dbt parsed and excluded.  They belong in the
    # tree: a model that has silently stopped building because someone set
    # `enabled: false` in a package is exactly the thing a person is hunting
    # for, and omitting it from the explorer makes it unfindable.
    disabled = document.get("disabled")
    if isinstance(disabled, dict):
        for unique_id, nodes in disabled.items():
            if unique_id in resources or not isinstance(nodes, list) or not nodes:
                continue
            node = nodes[0]
            if not isinstance(node, dict):
                continue
            resource = _resource(unique_id, node, None)
            if resource is not None:
                resource.enabled = False
                resources[unique_id] = resource

    return ParsedManifest(
        version=version,
        resources=resources,
        parent_map=_edge_map(document.get("parent_map")),
        child_map=_edge_map(document.get("child_map")),
        selectors=_selectors(document.get("selectors")),
        packages=_packages(document),
        project_name=_project_name(document),
    )


def _resource(
    unique_id: str, node: dict[str, Any], fixed_type: str | None,
) -> ManifestResource | None:
    resource_type = fixed_type or _string(node.get("resource_type"))
    if not resource_type:
        # unique_id is `<type>.<package>.<name>`, which is the fallback when a
        # section entry omits the field -- true of `groups` in some versions.
        resource_type = unique_id.split(".", 1)[0] or None
    if not resource_type:
        return None

    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    name = _string(node.get("name")) or unique_id.rsplit(".", 1)[-1]

    resource = ManifestResource(
        unique_id=unique_id,
        resource_type=resource_type,
        name=name,
        package_name=_string(node.get("package_name")),
        original_file_path=_string(node.get("original_file_path")),
        patch_path=_string(node.get("patch_path")),
        database=_string(node.get("database")),
        schema=_string(node.get("schema")),
        alias=_string(node.get("alias")),
        relation_name=_string(node.get("relation_name")),
        materialized=_string(config.get("materialized")),
        description=_string(node.get("description")),
        group_name=_string(node.get("group") or config.get("group")),
        tags=_tags(node, config),
        config=dict(config),
        columns=_columns(node.get("columns")),
        enabled=bool(config.get("enabled", True)),
        checksum=_checksum(node.get("checksum")),
        raw_code=_string(node.get("raw_code")),
        compiled_code=_string(node.get("compiled_code")),
    )

    if resource_type == "source":
        resource.source_name = _string(node.get("source_name"))
        resource.identifier = _string(node.get("identifier"))
    if resource_type in ("test", "unit_test"):
        resource.attached_node = _attached_node(node)
        resource.column_name = _string(
            node.get("column_name") or (node.get("test_metadata") or {})
            .get("kwargs", {}).get("column_name")
            if isinstance(node.get("test_metadata"), dict) else node.get("column_name")
        )
    return resource


def _attached_node(node: dict[str, Any]) -> str | None:
    """Which resource a test is testing.

    ``attached_node`` exists on newer manifests.  Where it does not, the test's
    dependencies name it -- a generic test depends on exactly the resource it
    tests plus its macro, so the first non-macro ref is the subject.  A singular
    test may reference several models and has no single subject; ``None`` is the
    right answer there rather than an arbitrary pick.
    """
    direct = _string(node.get("attached_node"))
    if direct:
        return direct
    depends = node.get("depends_on")
    if isinstance(depends, dict):
        nodes = depends.get("nodes")
        if isinstance(nodes, list):
            candidates = [
                str(item) for item in nodes
                if isinstance(item, str) and not item.startswith("macro.")
            ]
            if len(candidates) == 1:
                return candidates[0]
            metadata = node.get("test_metadata")
            if isinstance(metadata, dict):
                # relationships tests depend on two models; the one being tested
                # is the one the test file sits beside, not the `to` argument.
                target = (metadata.get("kwargs") or {}).get("model")
                if isinstance(target, str):
                    for item in candidates:
                        if item.rsplit(".", 1)[-1] in target:
                            return item
            if candidates:
                return candidates[0]
    return None


def _edge_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): [str(item) for item in items if isinstance(item, str)]
        for key, items in value.items()
        if isinstance(items, list)
    }


def _columns(value: Any) -> list[dict[str, Any]]:
    """Column docs as authored in YAML, in declaration order.

    Types come from `catalog.json`, not from here: YAML records what somebody
    wrote down, the catalogue records what the warehouse actually has, and
    showing the first as though it were the second is how a stale `data_type`
    ends up presented as fact.
    """
    if not isinstance(value, dict):
        return []
    columns: list[dict[str, Any]] = []
    for name, column in value.items():
        if not isinstance(column, dict):
            continue
        columns.append({
            "name": _string(column.get("name")) or str(name),
            "description": _string(column.get("description")),
            "data_type": _string(column.get("data_type")),
            "tags": [str(item) for item in (column.get("tags") or []) if item],
            "meta": column.get("meta") if isinstance(column.get("meta"), dict) else {},
            "constraints": column.get("constraints") if isinstance(
                column.get("constraints"), list) else [],
        })
    return columns


def _tags(node: dict[str, Any], config: dict[str, Any]) -> list[str]:
    raw = node.get("tags") or config.get("tags") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw if item not in (None, "")]


def _checksum(value: Any) -> str | None:
    if isinstance(value, dict):
        return _string(value.get("checksum"))
    return _string(value)


def _selectors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return [
        {
            "name": str(name),
            "description": _string((body or {}).get("description")),
            "default": bool((body or {}).get("default", False)),
        }
        for name, body in value.items()
        if isinstance(body, dict) or body is None
    ]


def _packages(document: dict[str, Any]) -> list[str]:
    """Installed package names, taken from the macros that arrived with them.

    The manifest does not carry `packages.yml` verbatim, but every installed
    package contributes macros stamped with its own `package_name`, so the set
    of package names other than the root project is exactly the set installed.
    """
    root = _project_name(document)
    names: set[str] = set()
    macros = document.get("macros")
    if isinstance(macros, dict):
        for node in macros.values():
            if isinstance(node, dict):
                package = _string(node.get("package_name"))
                if package and package != root:
                    names.add(package)
    return sorted(names)


def _project_name(document: dict[str, Any]) -> str | None:
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        name = _string(metadata.get("project_name"))
        if name:
            return name
    # Older manifests do not stamp the project name; the root package is the
    # package every first-party node belongs to.
    nodes = document.get("nodes")
    if isinstance(nodes, dict):
        for node in nodes.values():
            if isinstance(node, dict):
                package = _string(node.get("package_name"))
                if package:
                    return package
    return None


def iter_compiled(document: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """(unique_id, compiled SQL) for everything dbt compiled.

    Yielded rather than collected: a compile of a large project holds every
    model's SQL twice if this returns a dict, and the caller only ever wants one.
    """
    for section in ("nodes",):
        entries = document.get(section)
        if not isinstance(entries, dict):
            continue
        for unique_id, node in entries.items():
            if isinstance(node, dict) and node.get("compiled_code"):
                yield unique_id, str(node["compiled_code"])


def _string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
