"""Ask the model the questions a parser cannot answer.

Everything structural has been settled before this module runs, so what goes
to the provider is a description of the conversion rather than the conversion
itself, and what comes back is advice rather than code. Three consequences
follow, and all three are the reason the split was drawn here:

  * A failure is not fatal. `suggest` returns defaults when there is no API
    key, when the provider is down, and when it answers with something
    unusable. An import does not depend on somebody else's uptime.
  * A wrong answer is small. The worst a bad suggestion does is name a model
    badly, and a person is looking at the name when it happens.
  * The request is small, so a small model is genuinely enough. What is being
    asked is "is this staging or marts", not "write me a dbt project".
"""

from __future__ import annotations

import logging
import re

from app.core.config import settings
from app.core.logging import log_event
from app.services.builder_ai.client import OpenAIBuilderClient
from app.transforms.sql_import.emitter import ModelDecision
from app.transforms.sql_import.graph import Candidate, Graph, default_layer
from app.transforms.sql_import.schemas import ImportSuggestions

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
You are helping import existing SQL into a dbt project.

The queries have already been converted: table names now point at ref() and
source(), and the bodies are settled. You are not being asked to write or fix
SQL, and nothing you say will change a query. You are being asked only how the
resulting models should be organised.

For each candidate, decide:
  - name: keep the name the query already uses unless it cannot be a dbt
    identifier or is actively misleading. Somebody chose it; renaming things
    people recognise is not an improvement.
  - layer: staging when it only reads sources and mostly renames or casts,
    marts when it is built to be read, intermediate when it exists so another
    model can use it. A query that reads other models is not staging.
  - materialized: view unless it is plainly expensive. Never incremental.
  - description: one sentence on what a row is, or empty. An invented
    description is worse than none, because it will be believed.
  - tests: at most two columns, chosen only from columns the query plainly
    selects, and only where uniqueness or non-nullness is evident from the
    query itself. A test on a column that does not exist breaks the build for
    a reason unrelated to the data.

Copy each candidate_key exactly as given.
"""

#: A dbt model name. Anything else is discarded rather than repaired, because a
#: repaired name is a name nobody chose.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


def _fallback(graph: Graph) -> dict[str, ModelDecision]:
    """What the conversion does with no opinion from anybody.

    The convention dbt's own documentation opens with: a query that only reads
    the warehouse is staging and a view; one that reads other models is a mart
    and a table.
    """
    out: dict[str, ModelDecision] = {}
    for candidate in graph.candidates:
        layer = default_layer(candidate)
        out[candidate.key] = ModelDecision(
            candidate_name=candidate.name,
            name=candidate.name,
            layer=layer,
            materialized="view" if layer == "staging" else "table",
        )
    return out


def _describe(candidate: Candidate, graph: Graph, body: str) -> str:
    reads = ", ".join(candidate.depends_on) or "nothing in this upload"
    sources = ", ".join(f"{schema}.{table}" for schema, table in candidate.sources)
    return "\n".join([
        f"candidate_key: {candidate.key}",
        f"  file: {candidate.file_name}",
        f"  name the query uses: {candidate.name}",
        f"  reads models: {reads}",
        f"  reads warehouse tables: {sources or 'none'}",
        f"  query:\n{_indent(body)}",
    ])


def _indent(text: str, *, limit: int = 4000) -> str:
    clipped = text[:limit]
    if len(text) > limit:
        clipped += "\n    ... (truncated)"
    return "\n".join(f"    {line}" for line in clipped.splitlines())


def _clean(
    suggestions: ImportSuggestions, graph: Graph, defaults: dict[str, ModelDecision],
) -> dict[str, ModelDecision]:
    """Take what is usable from the answer and leave the rest.

    Every field is checked against what the conversion already knows. A name
    that is not an identifier, a key that names no candidate, a test on a
    column the query does not select -- each is dropped on its own, so one bad
    field costs one field rather than the whole answer.
    """
    by_key = {candidate.key: candidate for candidate in graph.candidates}
    taken = set()
    out = dict(defaults)

    for suggestion in suggestions.models:
        candidate = by_key.get(suggestion.candidate_key)
        if candidate is None:
            continue
        decision = out[candidate.key]
        name = suggestion.name.strip().casefold()
        if _IDENTIFIER.match(name) and name not in taken:
            decision.name = name
        taken.add(decision.name)
        decision.layer = suggestion.layer
        # Incremental needs a unique key and a filter nothing here can check,
        # and the schema says so -- but a schema is a request, not a promise.
        decision.materialized = (
            suggestion.materialized
            if suggestion.materialized in ("view", "table", "ephemeral")
            else decision.materialized
        )
        decision.description = suggestion.description.strip()[:300]
        decision.tests = [
            {"name": test.name.strip(), "unique": test.unique, "not_null": test.not_null}
            for test in suggestion.tests[:2]
            if test.name.strip() and (test.unique or test.not_null)
        ]
    return out


async def suggest(
    graph: Graph, bodies: dict[str, str], *, actor_id: str, project_id: str | None = None,
) -> tuple[dict[str, ModelDecision], list[str]]:
    """Decisions for every candidate, and any notes worth showing.

    Never raises. The defaults are returned whenever the provider cannot be
    reached or answers with something unusable, because an import failing
    because OpenAI is busy would be a worse product than one that names a
    model `orders` instead of `stg_orders`.
    """
    defaults = _fallback(graph)
    if not graph.candidates or not settings.openai_api_key.strip():
        return defaults, []

    prompt = "\n\n".join(
        _describe(candidate, graph, bodies.get(candidate.key, ""))
        for candidate in graph.order
    )
    try:
        client = OpenAIBuilderClient()
        answer = await client.structured(
            model=settings.openai_model_sql_import,
            instructions=INSTRUCTIONS,
            prompt=prompt,
            schema=ImportSuggestions,
            actor_id=actor_id,
            operation="sql_import_suggest",
            project_id=project_id,
        )
    except Exception as exc:  # the import carries on without an opinion
        log_event(
            logger, logging.WARNING, "transform.sql_import.suggest_failed",
            project_id=project_id, error_type=type(exc).__name__,
        )
        return defaults, []

    return _clean(answer, graph, defaults), [
        note.strip() for note in answer.notes[:3] if note.strip()
    ]
