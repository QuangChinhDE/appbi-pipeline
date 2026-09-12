"""Import SQL into a dbt project: analyse, review, apply, build.

There is no table behind this. `app.transforms.models` opens by saying that
nothing in the Transform domain holds SQL, because project files are canonical,
and an import that parked uploads in a column for the duration of a review
would be the beginning of the thing that module exists to avoid. So the review
is stateless: analysis hands back what it found, the browser hands it back with
whatever the person changed, and the apply re-derives every structural fact
from the SQL a second time.

That re-derivation is the point rather than a cost. What the browser returns is
the uploaded text and a set of preferences -- names, layers, materializations.
It cannot return a `ref()` graph, so it cannot get one wrong, and nothing a
client sends decides which model depends on which.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import ValidationError
from app.core.logging import log_event
from app.transforms import files as file_service
from app.transforms.models import TransformProject
from app.transforms.sql_import import planner
from app.transforms.sql_import.analysis import DIALECTS, parse_file, rewrite
from app.transforms.sql_import.emitter import (
    SOURCES_FILE, Emitted, ModelDecision, emit, source_alias,
)
from app.transforms.sql_import.graph import Graph, build, default_layer

logger = logging.getLogger(__name__)

#: An upload is a handful of queries somebody wrote, not a data dump. The cap
#: is generous for that and small enough that the round trip through a review
#: stays cheap -- which is what lets the whole thing avoid a table.
MAX_FILES = 50
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024


@dataclass
class CandidateView:
    """One proposed model, as the review screen needs it."""

    key: str
    file_name: str
    suggested_name: str
    name: str
    layer: str
    materialized: str
    description: str
    depends_on: list[str]
    sources: list[str]
    tests: list[dict]
    #: The converted body, so the review can show what will be written.
    preview: str


@dataclass
class Analysis:
    candidates: list[CandidateView] = field(default_factory=list)
    #: `(file, why)` for everything that will not be converted.
    skipped: list[dict] = field(default_factory=list)
    #: Warehouse tables the upload reads but does not create.
    sources: list[str] = field(default_factory=list)
    #: Model names in a ref() cycle, when the upload has one.
    cycle: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def check_upload(files: dict[str, str]) -> None:
    """Refuse an upload that is not what this feature is for."""
    if not files:
        raise ValidationError(
            "Add at least one .sql file.", code="SQL_IMPORT_NO_FILES",
        )
    if len(files) > MAX_FILES:
        raise ValidationError(
            f"An import takes at most {MAX_FILES} files at a time.",
            code="SQL_IMPORT_TOO_MANY_FILES",
            details={"limit": MAX_FILES, "count": len(files)},
        )
    total = 0
    for name, text in files.items():
        size = len(text.encode("utf-8"))
        total += size
        if size > MAX_FILE_BYTES:
            raise ValidationError(
                f"`{name}` is larger than an import accepts.",
                code="SQL_IMPORT_FILE_TOO_LARGE",
                details={"path": name, "limit": MAX_FILE_BYTES},
            )
    if total > MAX_TOTAL_BYTES:
        raise ValidationError(
            "Those files come to more than an import accepts at once.",
            code="SQL_IMPORT_TOO_LARGE",
            details={"limit": MAX_TOTAL_BYTES, "size": total},
        )


def dialect_for(adapter: str | None) -> str:
    """The sqlglot dialect for a warehouse AppBI can run dbt against."""
    key = (adapter or "").strip().casefold()
    return key if key in DIALECTS else "postgres"


def _prepare(
    files: dict[str, str], *, dialect: str, source_schema: str,
    existing_sources_yml: str | None,
) -> tuple[Graph, dict[str, str]]:
    """Parse, resolve and rewrite -- the whole deterministic half."""
    parsed = [
        parse_file(name, text, dialect=dialect) for name, text in files.items()
    ]
    graph = build(
        parsed,
        default_source_schema=source_schema,
        # `source()` takes the block's name, which in an AppBI project is `raw`
        # whatever the schema is called.
        source_name_for=lambda schema: source_alias(
            existing_sources_yml, schema,
            fallback="raw" if schema == source_schema else schema,
        ),
    )
    bodies = {
        candidate.key: rewrite(
            files.get(candidate.file_name, ""), candidate.statement,
            candidate.replacements,
        )
        for candidate in graph.candidates
    }
    return graph, bodies


async def analyse(
    files: dict[str, str],
    *,
    adapter: str | None,
    source_schema: str,
    existing_sources_yml: str | None = None,
    actor_id: str,
    project_id: str | None = None,
) -> Analysis:
    """Work out what the upload becomes, and ask for an opinion on the rest."""
    check_upload(files)
    dialect = dialect_for(adapter)
    graph, bodies = _prepare(
        files, dialect=dialect, source_schema=source_schema,
        existing_sources_yml=existing_sources_yml,
    )
    decisions, notes = await planner.suggest(
        graph, bodies, actor_id=actor_id, project_id=project_id,
    )

    analysis = Analysis(
        skipped=[{"file": name, "reason": reason} for name, reason in graph.skipped],
        sources=[f"{schema}.{table}" for schema, table in graph.sources],
        cycle=graph.cycle,
        notes=list(notes),
    )
    if graph.cycle:
        analysis.notes.insert(0, (
            "These models read from each other in a loop, which dbt refuses to "
            "build: " + " -> ".join(graph.cycle)
        ))
    for candidate in graph.order:
        decision = decisions[candidate.key]
        analysis.candidates.append(CandidateView(
            key=candidate.key,
            file_name=candidate.file_name,
            suggested_name=candidate.name,
            name=decision.name,
            layer=decision.layer,
            materialized=decision.materialized,
            description=decision.description,
            depends_on=list(candidate.depends_on),
            sources=[f"{schema}.{table}" for schema, table in candidate.sources],
            tests=list(decision.tests),
            preview=bodies.get(candidate.key, ""),
        ))
    log_event(
        logger, logging.INFO, "transform.sql_import.analysed",
        project_id=project_id, files=len(files),
        models=len(analysis.candidates), skipped=len(analysis.skipped),
    )
    return analysis


def render(
    files: dict[str, str],
    decisions: list[dict],
    *,
    adapter: str | None,
    source_schema: str,
    existing_paths: set[str] | None = None,
    existing_sources_yml: str | None = None,
) -> Emitted:
    """The files to write, from the upload and the reviewed decisions.

    The decisions carry preferences only. Every structural fact -- which model
    reads which, what is a source -- is worked out here from the SQL, so a
    client cannot send a graph and cannot send a wrong one.
    """
    check_upload(files)
    dialect = dialect_for(adapter)
    graph, _bodies = _prepare(
        files, dialect=dialect, source_schema=source_schema,
        existing_sources_yml=existing_sources_yml,
    )
    if graph.cycle:
        raise ValidationError(
            "These models read from each other in a loop, which dbt cannot "
            "build: " + " -> ".join(graph.cycle),
            code="SQL_IMPORT_CYCLE", details={"models": ", ".join(graph.cycle)},
        )
    if not graph.candidates:
        raise ValidationError(
            "None of those files holds a query that can become a model.",
            code="SQL_IMPORT_NOTHING_TO_IMPORT",
        )

    chosen = {item.get("key"): item for item in decisions if item.get("key")}
    settled: dict[str, ModelDecision] = {}
    for candidate in graph.candidates:
        layer = default_layer(candidate)
        given = chosen.get(candidate.key) or {}
        settled[candidate.key] = ModelDecision(
            candidate_name=candidate.name,
            name=_identifier(given.get("name"), candidate.name),
            layer=_one_of(given.get("layer"), ("staging", "intermediate", "marts"), layer),
            materialized=_one_of(
                given.get("materialized"), ("view", "table", "ephemeral"),
                "view" if layer == "staging" else "table",
            ),
            description=str(given.get("description") or "").strip()[:300],
            tests=_tests(given.get("tests")),
        )
    return emit(
        graph, settled, texts=files,
        existing_paths=existing_paths,
        existing_sources_yml=existing_sources_yml,
        source_name="raw", default_schema=source_schema,
    )


def _identifier(value: object, fallback: str) -> str:
    text = str(value or "").strip().casefold()
    import re

    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,59}", text) else fallback


def _one_of(value: object, allowed: tuple[str, ...], fallback: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else fallback


def _tests(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:2]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        unique = bool(item.get("unique"))
        not_null = bool(item.get("not_null"))
        if name and (unique or not_null):
            out.append({"name": name, "unique": unique, "not_null": not_null})
    return out


async def existing_sources(
    session: AsyncSession, project: TransformProject,
) -> tuple[str | None, set[str]]:
    """A project's current `_sources.yml` and the paths already taken."""
    revision = await file_service.working_revision(session, project)
    paths = set(revision.manifest_index or {})
    if SOURCES_FILE not in paths:
        return None, paths
    data, _entry = await file_service.read_file(revision, SOURCES_FILE)
    return data.decode("utf-8", "replace"), paths


async def write_into(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    emitted: Emitted,
    *,
    expected_revision_id: uuid.UUID | None,
) -> object:
    """Add the imported files to a project as one revision."""
    changes = [
        file_service.FileChange(path=path, content=content.encode("utf-8"))
        for path, content in sorted(emitted.files.items())
    ]
    return await file_service.apply_changes(
        session, project, changes=changes,
        expected_revision_id=expected_revision_id, actor_id=ctx.user_id,
    )
