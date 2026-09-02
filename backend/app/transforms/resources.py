"""Query the resource index: explorer, inspector, lineage, docs.

Everything here reads rows the indexer wrote from a dbt artifact.  No SQL is
parsed, no dependency is inferred, no resource type is enumerated by the
product.  A dbt version that adds a resource type shows up in the tree the day
it lands, because "resource type" is a string that came out of the manifest.

Lineage deserves the same discipline.  ``parent_map`` and ``child_map`` are
dbt's answer to a question dbt has already had to answer in order to build in
the right order, and any second answer AppBI computed would be a different graph
from the one that actually runs.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.transforms.artifacts.catalog import ParsedCatalog
from app.transforms.artifacts.sources import ParsedSources
from app.transforms.models import (
    TransformArtifactBundle, TransformInvocationNode, TransformResourceEdge,
    TransformResourceIndex,
)

#: How much of the graph a lineage view shows before somebody asks for more.
#:
#: A 5,000-node graph rendered whole is unreadable and slow.  Two hops either
#: side of the selected resource is the neighbourhood a person is actually
#: reasoning about.
DEFAULT_DEPTH = 2


@dataclass(slots=True)
class ResourcePage:
    items: list[dict[str, Any]]
    total: int
    counts: dict[str, int]


async def list_resources(
    session: AsyncSession,
    bundle_id: uuid.UUID,
    *,
    resource_types: list[str] | None = None,
    search: str | None = None,
    tag: str | None = None,
    package: str | None = None,
    path_prefix: str | None = None,
    materialized: str | None = None,
    group: str | None = None,
    enabled_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> ResourcePage:
    """Filtered, paged resources for the explorer.

    Paged in SQL rather than in the browser because the target is a project with
    5,000 resources staying usable, and shipping all of them to filter client
    side is the thing that stops being usable first.
    """
    base = select(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle_id,
    )
    if resource_types:
        base = base.where(TransformResourceIndex.resource_type.in_(resource_types))
    if search:
        pattern = f"%{search.strip().lower()}%"
        base = base.where(or_(
            func.lower(TransformResourceIndex.name).like(pattern),
            func.lower(TransformResourceIndex.unique_id).like(pattern),
            func.lower(TransformResourceIndex.original_file_path).like(pattern),
            func.lower(TransformResourceIndex.description).like(pattern),
        ))
    if tag:
        base = base.where(TransformResourceIndex.tags_json.contains([tag]))
    if package:
        base = base.where(TransformResourceIndex.package_name == package)
    if path_prefix:
        base = base.where(
            TransformResourceIndex.original_file_path.like(f"{path_prefix}%")
        )
    if materialized:
        base = base.where(TransformResourceIndex.materialized == materialized)
    if group:
        base = base.where(TransformResourceIndex.group_name == group)
    if enabled_only:
        base = base.where(TransformResourceIndex.enabled.is_(True))

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = list((await session.scalars(
        base.order_by(
            TransformResourceIndex.resource_type, TransformResourceIndex.name,
        ).limit(limit).offset(offset)
    )).all())

    counts_rows = (await session.execute(
        select(TransformResourceIndex.resource_type, func.count())
        .where(TransformResourceIndex.bundle_id == bundle_id)
        .group_by(TransformResourceIndex.resource_type)
    )).all()

    return ResourcePage(
        items=[summary(row) for row in rows],
        total=int(total),
        counts={str(kind): int(count) for kind, count in counts_rows},
    )


async def get_resource(
    session: AsyncSession, bundle_id: uuid.UUID, unique_id: str,
) -> TransformResourceIndex:
    row = await session.scalar(select(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle_id,
        TransformResourceIndex.unique_id == unique_id,
    ))
    if row is None:
        raise NotFoundError("That resource is not in the current version of this project.")
    return row


def summary(row: TransformResourceIndex) -> dict[str, Any]:
    """The shape a list row needs -- deliberately not the whole config."""
    return {
        "unique_id": row.unique_id,
        "resource_type": row.resource_type,
        "name": row.name,
        "package_name": row.package_name,
        "path": row.original_file_path,
        "patch_path": _patch_path(row.patch_path),
        "materialized": row.materialized,
        "relation_name": row.relation_name,
        "database": row.database_name,
        "schema": row.schema_name,
        "alias": row.alias,
        "description": row.description,
        "tags": row.tags_json or [],
        "group": row.group_name,
        "enabled": row.enabled,
        "produced_by_pipeline_id": row.produced_by_pipeline_id,
    }


async def detail(
    session: AsyncSession,
    bundle: TransformArtifactBundle,
    unique_id: str,
    *,
    catalog: ParsedCatalog | None = None,
    freshness: ParsedSources | None = None,
    last_result: TransformInvocationNode | None = None,
) -> dict[str, Any]:
    """Everything the inspector shows for one resource.

    ``config`` comes back whole, including keys the product has no form for.
    That is the round-trip rule made visible: the inspector shows what is
    actually set, so a `contract` or a package option is displayed rather than
    silently absent -- and displayed read-only, because a form that cannot
    faithfully rewrite a value should not offer to.
    """
    row = await get_resource(session, bundle.id, unique_id)

    parents = list((await session.scalars(
        select(TransformResourceEdge.parent_unique_id).where(
            TransformResourceEdge.bundle_id == bundle.id,
            TransformResourceEdge.child_unique_id == unique_id,
        )
    )).all())
    children = list((await session.scalars(
        select(TransformResourceEdge.child_unique_id).where(
            TransformResourceEdge.bundle_id == bundle.id,
            TransformResourceEdge.parent_unique_id == unique_id,
        )
    )).all())

    tests = list((await session.scalars(select(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle.id,
        TransformResourceIndex.resource_type.in_(["test", "unit_test"]),
    ))).all())
    attached = [
        summary(test) for test in tests
        if unique_id in (test.config_json or {}).get("_attached", [unique_id])
        and _tests_this(test, unique_id, parents=parents)
    ]

    payload = {
        **summary(row),
        "config": row.config_json or {},
        "checksum": row.checksum,
        "columns": _merge_columns(row, catalog),
        "parents": parents,
        "children": children,
        "tests": attached,
        "last_result": _result_view(last_result),
    }

    if row.resource_type == "source" and freshness is not None:
        entry = freshness.results.get(unique_id)
        if entry is not None:
            payload["freshness"] = {
                "status": entry.status.upper(),
                "max_loaded_at": entry.max_loaded_at,
                "snapshotted_at": entry.snapshotted_at,
                "age_seconds": entry.age_seconds,
                "warn_after": entry.warn_after,
                "error_after": entry.error_after,
                "message": entry.message,
            }
    if catalog is not None:
        relation = catalog.relations.get(unique_id)
        if relation is not None:
            payload["warehouse"] = {
                "database": relation.database,
                "schema": relation.schema,
                "name": relation.name,
                "type": relation.relation_type,
                "owner": relation.owner,
                "comment": relation.comment,
                "stats": relation.stats,
            }
    return payload


def _tests_this(
    test: TransformResourceIndex, unique_id: str, *, parents: list[str],
) -> bool:
    """Whether a test row belongs to this resource.

    The index does not store `attached_node` as a column -- adding one per dbt
    concept is the pattern being removed -- so the relationship is recovered
    from the config the indexer preserved, falling back to a name match on the
    unique_id, which is how dbt names generic tests.
    """
    attached = (test.config_json or {}).get("attached_node")
    if attached:
        return attached == unique_id
    resource_name = unique_id.rsplit(".", 1)[-1]
    return f"_{resource_name}_" in test.unique_id or test.unique_id.endswith(
        f"_{resource_name}"
    )


def _merge_columns(
    row: TransformResourceIndex, catalog: ParsedCatalog | None,
) -> list[dict[str, Any]]:
    """Documented columns and real columns, side by side.

    The warehouse decides which columns exist and what type they are; the YAML
    decides what they mean.  Showing the union with a source marker on each is
    what lets a person see that `customer_tier` is documented but no longer in
    the table -- a state that presenting either source alone would hide.
    """
    documented = {
        str(item.get("name")): item for item in (row.columns_json or []) if item.get("name")
    }
    physical = {}
    if catalog is not None:
        for column in catalog.columns_for(row.unique_id):
            physical[column.name] = column

    names: list[str] = []
    if physical:
        names.extend(physical)
        names.extend(name for name in documented if name not in physical)
    else:
        names.extend(documented)

    merged: list[dict[str, Any]] = []
    for name in names:
        doc = documented.get(name, {})
        real = physical.get(name)
        merged.append({
            "name": name,
            "description": doc.get("description"),
            "data_type": (real.type if real else None) or doc.get("data_type"),
            "tags": doc.get("tags") or [],
            "constraints": doc.get("constraints") or [],
            "in_warehouse": real is not None,
            "documented": name in documented,
        })
    return merged


def _result_view(node: TransformInvocationNode | None) -> dict[str, Any] | None:
    if node is None:
        return None
    return {
        "status": node.status,
        "execution_time": node.execution_time,
        "message": node.message,
        "rows_affected": node.rows_affected,
        "bytes_processed": node.bytes_processed,
        "relation_name": node.relation_name,
    }


def _patch_path(value: str | None) -> str | None:
    """`patch_path` is `package://path`; only the path half is a file."""
    if not value:
        return None
    return value.split("://", 1)[-1]


# ── lineage ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LineageGraph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    total_nodes: int = 0


async def lineage(
    session: AsyncSession,
    bundle_id: uuid.UUID,
    *,
    focus: str | None = None,
    upstream_depth: int = DEFAULT_DEPTH,
    downstream_depth: int = DEFAULT_DEPTH,
    resource_types: list[str] | None = None,
    max_nodes: int = 400,
) -> LineageGraph:
    """A neighbourhood of the dependency graph, or all of it.

    ``focus`` is a unique_id.  Without one the whole graph comes back, capped at
    ``max_nodes`` -- the cap is honest rather than silent: ``truncated`` says so,
    and the UI offers the full graph as an explicit choice.
    """
    edge_rows = list((await session.execute(
        select(
            TransformResourceEdge.parent_unique_id,
            TransformResourceEdge.child_unique_id,
        ).where(TransformResourceEdge.bundle_id == bundle_id)
    )).all())

    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for parent, child in edge_rows:
        parents.setdefault(child, []).append(parent)
        children.setdefault(parent, []).append(child)

    if focus:
        selected = _neighbourhood(
            focus, parents, children,
            upstream=upstream_depth, downstream=downstream_depth,
        )
    else:
        selected = None

    query = select(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle_id,
    )
    if resource_types:
        query = query.where(TransformResourceIndex.resource_type.in_(resource_types))
    else:
        # Tests and macros are not lineage. A graph with a test node hanging off
        # every model is unreadable, and nobody traces impact through one.
        query = query.where(TransformResourceIndex.resource_type.notin_(
            ["test", "unit_test", "macro", "group", "operation"],
        ))
    if selected is not None:
        query = query.where(TransformResourceIndex.unique_id.in_(list(selected)))

    rows = list((await session.scalars(query)).all())
    total = len(rows)
    truncated = total > max_nodes
    if truncated:
        rows = rows[:max_nodes]

    present = {row.unique_id for row in rows}
    return LineageGraph(
        nodes=[
            {
                "unique_id": row.unique_id,
                "name": row.name,
                "resource_type": row.resource_type,
                "materialized": row.materialized,
                "package": row.package_name,
                "path": row.original_file_path,
                "tags": row.tags_json or [],
                "relation_name": row.relation_name,
                "enabled": row.enabled,
                "is_focus": row.unique_id == focus,
                "produced_by_pipeline_id": row.produced_by_pipeline_id,
            }
            for row in rows
        ],
        edges=[
            {"parent": parent, "child": child}
            for parent, child in edge_rows
            if parent in present and child in present
        ],
        truncated=truncated,
        total_nodes=total,
    )


def _neighbourhood(
    focus: str,
    parents: dict[str, list[str]],
    children: dict[str, list[str]],
    *,
    upstream: int,
    downstream: int,
) -> set[str]:
    """Breadth-first walk out from one node, N hops each way.

    Visited-set guarded, so a project with a cycle -- which dbt rejects, but a
    stale index might still describe -- terminates instead of hanging.
    """
    selected = {focus}
    for graph, depth in ((parents, upstream), (children, downstream)):
        frontier: deque[tuple[str, int]] = deque([(focus, 0)])
        seen = {focus}
        while frontier:
            node, level = frontier.popleft()
            if level >= depth:
                continue
            for neighbour in graph.get(node, []):
                selected.add(neighbour)
                if neighbour not in seen:
                    seen.add(neighbour)
                    frontier.append((neighbour, level + 1))
    return selected


async def facets(
    session: AsyncSession, bundle_id: uuid.UUID,
) -> dict[str, list[str]]:
    """Values the explorer's filters can offer, taken from what is there.

    Offering a filter with no matching resources is worse than offering none,
    so every option in the UI comes from a distinct query rather than a list in
    the code.
    """
    async def distinct(column) -> list[str]:
        rows = await session.scalars(
            select(column).where(TransformResourceIndex.bundle_id == bundle_id)
            .where(column.is_not(None)).distinct().order_by(column)
        )
        return [str(value) for value in rows.all() if value]

    tag_rows = await session.scalars(
        select(TransformResourceIndex.tags_json)
        .where(TransformResourceIndex.bundle_id == bundle_id)
    )
    tags: set[str] = set()
    for entry in tag_rows.all():
        for tag in entry or []:
            tags.add(str(tag))

    return {
        "resource_types": await distinct(TransformResourceIndex.resource_type),
        "packages": await distinct(TransformResourceIndex.package_name),
        "materializations": await distinct(TransformResourceIndex.materialized),
        "groups": await distinct(TransformResourceIndex.group_name),
        "tags": sorted(tags),
    }


async def last_results(
    session: AsyncSession, invocation_id: uuid.UUID,
) -> dict[str, TransformInvocationNode]:
    rows = list((await session.scalars(select(TransformInvocationNode).where(
        TransformInvocationNode.invocation_id == invocation_id,
    ))).all())
    return {row.unique_id: row for row in rows}
