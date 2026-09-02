"""Turn dbt artifacts into rows the UI can query.

The index is a cache and behaves like one: it is written only from an artifact,
never edited, and can be thrown away and rebuilt from the bundle it came from.
Its purpose is narrow -- letting a project with 5,000 resources be filtered,
counted and paged in SQL instead of shipping a manifest to the browser.

Two invariants are load-bearing:

*Idempotent per bundle.*  Indexing the same bundle twice produces the same rows.
Parse is debounced and can coalesce, retries happen, and a half-indexed bundle
that a retry doubles would show every resource twice in the explorer.

*Draft never overwrites live.*  DRAFT rows come from parsing the working
revision; RELEASE rows come from a release's own verified artifacts.  They are
separate row sets under one project, so somebody editing a model cannot change
the lineage graph production is running -- which is the state-separation rule
the blueprint sets out and the easiest one to violate by accident.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow
from app.transforms.artifacts.catalog import ParsedCatalog, parse_catalog
from app.transforms.artifacts.manifest import ParsedManifest, parse_manifest
from app.transforms.artifacts.run_results import parse_run_results
from app.transforms.artifacts.sources import parse_sources
from app.transforms.models import (
    TransformArtifactBundle, TransformInvocation, TransformInvocationNode,
    TransformResourceEdge, TransformResourceIndex,
)
from app.transforms.storage import ObjectStore, object_store

#: Resource types that own a warehouse relation, so a build result can be
#: matched back to a physical table.
RELATIONAL = frozenset({"model", "seed", "snapshot", "source"})


@dataclass(slots=True)
class IndexResult:
    bundle_id: uuid.UUID
    resources: int
    edges: int
    counts: dict[str, int]
    dbt_version: str | None
    adapter_type: str | None
    project_name: str | None


async def store_bundle(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    invocation_id: uuid.UUID | None,
    revision_id: uuid.UUID | None,
    scope: str,
    artifacts: dict[str, dict[str, Any]],
    log_text: str | None = None,
    store: ObjectStore | None = None,
) -> TransformArtifactBundle:
    """Put an invocation's artifacts in object storage and record the pointers.

    The documents are stored as they came out of dbt.  Storing a normalised
    subset instead would mean a future version of AppBI could not read anything
    a past version chose not to keep, which is exactly the trap the old
    generated-project design fell into.
    """
    store = store or object_store()
    bundle = TransformArtifactBundle(
        id=uuid.uuid4(),
        project_id=project_id,
        invocation_id=invocation_id,
        revision_id=revision_id,
        scope=scope,
        created_at=utcnow(),
    )

    keys = {
        "manifest": "manifest_storage_key",
        "run_results": "run_results_storage_key",
        "catalog": "catalog_storage_key",
        "sources": "sources_storage_key",
        "semantic_manifest": "semantic_manifest_storage_key",
    }
    for name, column in keys.items():
        document = artifacts.get(name)
        if document is None:
            continue
        payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
        setattr(bundle, column, await store.put_content(payload))

    if log_text:
        bundle.log_storage_key = await store.put_content(log_text.encode("utf-8"))

    manifest = artifacts.get("manifest")
    if isinstance(manifest, dict):
        metadata = manifest.get("metadata")
        if isinstance(metadata, dict):
            bundle.dbt_version = _string(metadata.get("dbt_version"))
            bundle.dbt_schema_version = _string(metadata.get("dbt_schema_version"))
            bundle.adapter_type = _string(metadata.get("adapter_type"))
            bundle.generated_at = None  # parsed below, where the format is known

    session.add(bundle)
    await session.flush()
    return bundle


async def index_manifest(
    session: AsyncSession,
    *,
    bundle: TransformArtifactBundle,
    manifest_document: dict[str, Any],
    pipeline_sources: dict[str, uuid.UUID] | None = None,
) -> IndexResult:
    """Write index and edge rows for one manifest.

    ``pipeline_sources`` maps `schema.identifier` (lower-cased) to the Pipeline
    that loads it, so a dbt source can show "Produced by AppBI Pipeline".  It is
    enrichment: absent, everything else still works, and its presence changes
    nothing about what the source means to dbt.
    """
    parsed = parse_manifest(manifest_document)

    # Idempotence. Deleting this bundle's own rows first means a retry replaces
    # rather than doubles, and because the scope is part of the row a DRAFT
    # rebuild cannot touch RELEASE rows for the same project.
    await session.execute(sa_delete(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle.id,
    ))
    await session.execute(sa_delete(TransformResourceEdge).where(
        TransformResourceEdge.bundle_id == bundle.id,
    ))

    pipeline_sources = pipeline_sources or {}
    rows: list[TransformResourceIndex] = []
    for resource in parsed.resources.values():
        pipeline_id = None
        if resource.resource_type == "source":
            lookup = f"{(resource.schema or '').lower()}.{(resource.identifier or resource.name).lower()}"
            pipeline_id = pipeline_sources.get(lookup)
        rows.append(TransformResourceIndex(
            id=uuid.uuid4(),
            project_id=bundle.project_id,
            bundle_id=bundle.id,
            revision_id=bundle.revision_id,
            scope=bundle.scope,
            unique_id=resource.unique_id,
            resource_type=resource.resource_type,
            name=resource.name,
            package_name=resource.package_name,
            original_file_path=resource.original_file_path,
            patch_path=resource.patch_path,
            database_name=resource.database,
            schema_name=resource.schema,
            alias=resource.alias,
            relation_name=resource.relation_name,
            materialized=resource.materialized,
            description=resource.description,
            group_name=resource.group_name,
            tags_json=resource.tags,
            config_json=resource.config,
            columns_json=resource.columns,
            enabled=resource.enabled,
            checksum=resource.checksum,
            produced_by_pipeline_id=pipeline_id,
        ))
    session.add_all(rows)

    # Edges from dbt's own parent_map.  Only edges whose endpoints are both in
    # this manifest are kept: parent_map includes macro dependencies, and a
    # lineage graph with a macro node in the middle of it is not what anybody
    # means by lineage.
    known = set(parsed.resources)
    edges: list[TransformResourceEdge] = []
    seen: set[tuple[str, str]] = set()
    for child, parents in parsed.parent_map.items():
        if child not in known:
            continue
        for parent in parents:
            if parent not in known or parent.startswith("macro."):
                continue
            pair = (parent, child)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(TransformResourceEdge(
                id=uuid.uuid4(),
                project_id=bundle.project_id,
                bundle_id=bundle.id,
                scope=bundle.scope,
                parent_unique_id=parent,
                child_unique_id=child,
            ))
    session.add_all(edges)

    if parsed.version.generated_at:
        bundle.generated_at = _timestamp(parsed.version.generated_at)
    bundle.dbt_version = parsed.version.dbt_version or bundle.dbt_version
    bundle.adapter_type = parsed.version.adapter_type or bundle.adapter_type
    bundle.dbt_schema_version = parsed.version.raw_schema_version or bundle.dbt_schema_version

    await session.flush()
    return IndexResult(
        bundle_id=bundle.id,
        resources=len(rows),
        edges=len(edges),
        counts=parsed.counts(),
        dbt_version=parsed.version.dbt_version,
        adapter_type=parsed.version.adapter_type,
        project_name=parsed.project_name,
    )


async def index_run_results(
    session: AsyncSession,
    *,
    invocation: TransformInvocation,
    run_results_document: dict[str, Any],
    manifest_document: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Write per-resource outcomes for one invocation and return the totals."""
    names: dict[str, tuple[str, str]] = {}
    if manifest_document:
        parsed_manifest = parse_manifest(manifest_document)
        names = {
            unique_id: (resource.name, resource.resource_type)
            for unique_id, resource in parsed_manifest.resources.items()
        }
    parsed = parse_run_results(run_results_document, names=names)

    await session.execute(sa_delete(TransformInvocationNode).where(
        TransformInvocationNode.invocation_id == invocation.id,
    ))
    session.add_all([
        TransformInvocationNode(
            id=uuid.uuid4(),
            invocation_id=invocation.id,
            unique_id=item.unique_id,
            name=item.name or item.unique_id.rsplit(".", 1)[-1],
            resource_type=item.resource_type or item.unique_id.split(".", 1)[0],
            status=item.status,
            execution_time=item.execution_time,
            relation_name=item.relation_name,
            message=item.message[:8000] if item.message else None,
            rows_affected=item.rows_affected,
            bytes_processed=item.bytes_processed,
            failures=item.failures,
            adapter_response=item.adapter_response,
            error_location=item.location,
        )
        for item in parsed.results
    ])

    counts = parsed.counts()
    invocation.nodes_total = counts["total"]
    invocation.nodes_succeeded = counts["succeeded"]
    invocation.nodes_failed = counts["failed"]
    invocation.nodes_skipped = counts["skipped"]
    invocation.tests_passed = counts["tests_passed"]
    invocation.tests_failed = counts["tests_failed"]
    invocation.tests_warned = counts["tests_warned"]
    invocation.rows_affected = parsed.rows_affected()
    if parsed.version.invocation_id:
        invocation.dbt_invocation_id = parsed.version.invocation_id
    await session.flush()
    return counts


async def load_manifest(
    session: AsyncSession,
    bundle: TransformArtifactBundle,
    *,
    store: ObjectStore | None = None,
) -> ParsedManifest | None:
    document = await _load(bundle.manifest_storage_key, store)
    return parse_manifest(document) if document else None


async def load_catalog(
    bundle: TransformArtifactBundle, *, store: ObjectStore | None = None,
) -> ParsedCatalog | None:
    document = await _load(bundle.catalog_storage_key, store)
    return parse_catalog(document) if document else None


async def load_sources(
    bundle: TransformArtifactBundle, *, store: ObjectStore | None = None,
):
    document = await _load(bundle.sources_storage_key, store)
    return parse_sources(document) if document else None


async def load_raw(
    key: str | None, *, store: ObjectStore | None = None,
) -> dict[str, Any] | None:
    return await _load(key, store)


async def _load(
    key: str | None, store: ObjectStore | None,
) -> dict[str, Any] | None:
    if not key:
        return None
    from app.transforms.storage import ObjectNotFound

    store = store or object_store()
    try:
        return json.loads((await store.get(key)).decode("utf-8"))
    except (ObjectNotFound, ValueError):
        # A bundle whose artifact was reaped by retention is a normal state for
        # an old run, not an error -- the run row survives longer than its
        # artifacts by design.
        return None


async def latest_bundle(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    scope: str = "DRAFT",
    revision_id: uuid.UUID | None = None,
    require_manifest: bool = True,
) -> TransformArtifactBundle | None:
    """The newest bundle for a project in one scope.

    ``revision_id`` pins it to a specific revision, which is what the Develop
    view wants: the resource tree must describe the code on screen, not a parse
    of something newer that a colleague just saved.
    """
    query = select(TransformArtifactBundle).where(
        TransformArtifactBundle.project_id == project_id,
        TransformArtifactBundle.scope == scope,
    )
    if revision_id is not None:
        query = query.where(TransformArtifactBundle.revision_id == revision_id)
    if require_manifest:
        query = query.where(TransformArtifactBundle.manifest_storage_key.is_not(None))
    return await session.scalar(
        query.order_by(TransformArtifactBundle.created_at.desc()).limit(1)
    )


async def prune_draft_bundles(
    session: AsyncSession, project_id: uuid.UUID, *, keep: int | None = None,
) -> int:
    """Drop all but the newest N draft bundles for a project.

    Every save parses, so without this an active editing session accumulates a
    manifest per keystroke-batch forever.  Release bundles are never pruned:
    they are the record of what production ran.
    """
    keep = keep or settings.transform_parse_artifact_retention
    stale = list((await session.scalars(
        select(TransformArtifactBundle.id)
        .where(
            TransformArtifactBundle.project_id == project_id,
            TransformArtifactBundle.scope == "DRAFT",
        )
        .order_by(TransformArtifactBundle.created_at.desc())
        .offset(max(keep, 1))
    )).all())
    if not stale:
        return 0
    # Index and edge rows cascade from the bundle.  The blobs themselves are
    # content-addressed and shared between revisions, so they are left to a
    # separate sweep rather than deleted here -- deleting a blob another bundle
    # still points at would corrupt that bundle.
    await session.execute(sa_delete(TransformArtifactBundle).where(
        TransformArtifactBundle.id.in_(stale),
    ))
    return len(stale)


async def resource_counts(
    session: AsyncSession, bundle_id: uuid.UUID,
) -> dict[str, int]:
    rows = (await session.execute(
        select(TransformResourceIndex.resource_type, func.count())
        .where(TransformResourceIndex.bundle_id == bundle_id)
        .group_by(TransformResourceIndex.resource_type)
    )).all()
    return {str(kind): int(count) for kind, count in rows}


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _timestamp(value: str):
    from datetime import datetime

    try:
        # dbt writes RFC3339 with a trailing Z, which fromisoformat rejects
        # before 3.11 and accepts after; normalising covers both.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
