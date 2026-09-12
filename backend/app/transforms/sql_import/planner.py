"""Ask the model the questions a parser cannot answer.

Everything structural is settled before this module runs, so what goes to the
provider is a description of the conversion rather than the conversion itself,
and what comes back is advice rather than code.

The arrangement assumes a small model, because a small model is all this needs
-- the question is "staging or marts", not "write me a dbt project". Assuming
one has consequences, and they are the design:

  Short input.  A small model's judgement falls off sharply with prompt
                length, so candidates go in batches and each query is trimmed
                to the part the questions are actually about -- the SELECT
                list. The FROM clause is summarised for it rather than
                shipped, because the dependency facts are already known here
                and re-deriving them from text is exactly what a small model
                does badly.

  Nothing inferred that is already known.  The column list is given rather
                than left to be read out of the SQL. Every fact the parser has
                is handed over, so the model is only ever doing the part that
                needs judgement.

  Every answer checked against what is known.  A test may only name a column
                the query actually selects, and that is enforced here rather
                than requested in the prompt. A test on a column that is not
                there fails `dbt build`, which is the report somebody is
                waiting for -- the worst possible thing to get wrong.

  Failure is per-batch.  One batch that times out or answers with nonsense
                costs its own candidates their suggestions and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.core.config import settings
from app.core.logging import log_event
from app.services.builder_ai.client import OpenAIBuilderClient
from app.transforms.sql_import.emitter import ModelDecision
from app.transforms.sql_import.graph import (
    Candidate, Graph, default_layer, default_materialization,
)
from app.transforms.sql_import.schemas import ImportSuggestions

logger = logging.getLogger(__name__)

#: Candidates per request. Small enough that the whole prompt stays short and
#: a small model keeps its footing; large enough that a normal upload is one
#: or two calls.
BATCH_SIZE = 6

#: Requests in flight. Bounded because an import is not a race, and a provider
#: answering three short questions well beats it answering nine badly.
CONCURRENCY = 3

#: Per query, in characters. The head of a SELECT is where the column names
#: and the intent are; past this it is filter logic that no question here is
#: about.
QUERY_BUDGET = 1200

#: A dbt model name. Anything else is discarded rather than repaired, because
#: a repaired name is a name nobody chose.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,59}$")

INSTRUCTIONS = """\
You are naming and documenting dbt models. You never write or change SQL.

Each query below has already been converted and is final. Nothing you return
changes a query. Where each model goes and how it is stored has already been
decided. You answer three things per candidate, and nothing else.

Answer for EVERY candidate. Copy its candidate_key character for character.

1. name
   Keep the name the query already uses. Change it ONLY if it is not a legal
   dbt name (lower case letters, digits, underscores, first character a
   letter) or it is genuinely meaningless -- `query1`, `tmp`, `untitled`,
   `final2`. Somebody chose that name and recognises it; a tidier name they do
   not recognise is not an improvement.
     stg_orders     -> stg_orders     (keep)
     Daily Revenue  -> daily_revenue  (illegal characters)
     query1         -> whatever the query is about
     tbl_cust_final -> tbl_cust_final (ugly, but it means something; keep it)

2. description
   ONE short sentence saying what a single row of the result is.
     good: "One row per order, with its customer and total."
     good: "One row per country per day, with that day's revenue."
     bad:  "This model selects data from the orders table." (says nothing)
     bad:  "Contains important business metrics."           (invented)
   If the query does not make the grain clear, return "". An empty description
   is honest; an invented one gets believed.

3. tests
   Default to []. Only propose a test when the query makes it evident, and
   ONLY name columns from that candidate's `columns:` line.
     propose:    a single id-like column the query groups by or selects first
                 -- unique + not_null
     do not:     amounts, dates, names, counts, flags, anything nullable
     do not:     a column that is not in the `columns:` line. Never invent one.
     do not:     more than two per model.
   If `columns:` says the columns are unknown, return [] -- the query selects
   `*` and nothing can be asserted about it.

Also return `notes`: at most three, usually none. Only something a reader
would want to be told -- a join with no condition, a table name that looks
like a typo, a query that looks like it was meant to run incrementally. Do
not narrate what you did.

WORKED EXAMPLE

  candidate_key: 01_orders.sql#0
    name the query uses: stg_orders
    reads models: none
    read by: daily_revenue
    columns: order_id, customer_id, total, ordered_at
    query:
      SELECT id AS order_id, customer_id, total, created_at AS ordered_at
      FROM {{ source('raw', 'orders') }}

  correct answer:
    candidate_key: "01_orders.sql#0"
    name: "stg_orders"
    description: "One row per order, as it arrives from the source."
    tests: [{name: "order_id", unique: true, not_null: true,
             reason: "the grain of the table"}]

  candidate_key: 02_summary.sql#0
    name the query uses: query1
    reads models: stg_orders
    read by: nothing
    columns: (unknown -- the query selects *)
    query:
      SELECT * FROM {{ ref('stg_orders') }} WHERE total > 0

  correct answer:
    candidate_key: "02_summary.sql#0"
    name: "paid_orders"
    description: ""
    tests: []
"""


def _fallback(graph: Graph) -> dict[str, ModelDecision]:
    """Where every model starts, before anybody is asked anything.

    Layer and materialization stay at these values: they follow from what a
    query reads and what reads it, so they are decided rather than suggested.
    What a model may change is the name, the description and the tests.
    """
    out: dict[str, ModelDecision] = {}
    for candidate in graph.candidates:
        layer = default_layer(candidate)
        out[candidate.key] = ModelDecision(
            candidate_name=candidate.name,
            name=candidate.name,
            layer=layer,
            materialized=default_materialization(layer),
            # A comment written above the query is already a description, and
            # a better one than anything that could be generated: the person
            # who wrote it knew what the model was for. It also means a
            # project imported with no provider configured still arrives
            # documented.
            description=candidate.statement.leading_comment,
        )
    return out


def _trim(body: str) -> str:
    """The head of a query, which is the part the questions are about."""
    text = body.strip()
    if len(text) <= QUERY_BUDGET:
        return text
    return text[:QUERY_BUDGET].rstrip() + "\n... (query continues)"


def _describe(candidate: Candidate, read_by: list[str], body: str) -> str:
    """One candidate, with every fact the parser already knows spelled out."""
    statement = candidate.statement
    columns = (
        ", ".join(statement.columns[:40])
        if not statement.selects_star and statement.columns
        else ""
    )
    lines = [
        f"candidate_key: {candidate.key}",
        f"  name the query uses: {candidate.name}",
        f"  reads models: {', '.join(candidate.depends_on) or 'none'}",
        f"  read by: {', '.join(read_by) or 'nothing'}",
        f"  columns: {columns or '(unknown -- the query selects *)'}",
        "  query:",
        "\n".join(f"    {line}" for line in _trim(body).splitlines()),
    ]
    return "\n".join(lines)


def _read_by(graph: Graph) -> dict[str, list[str]]:
    """Who reads each model -- the fact that separates marts from
    intermediate, and one a model should not have to work out."""
    out: dict[str, list[str]] = {item.name: [] for item in graph.candidates}
    for candidate in graph.candidates:
        for parent in candidate.depends_on:
            if parent in out:
                out[parent].append(candidate.name)
    return out


def _apply(
    suggestions: ImportSuggestions,
    graph: Graph,
    decisions: dict[str, ModelDecision],
    taken: set[str],
) -> None:
    """Take what is usable from an answer and leave the rest.

    Every field is checked against what the conversion already knows, and each
    is dropped on its own -- so one bad field costs one field rather than the
    whole answer.
    """
    by_key = {candidate.key: candidate for candidate in graph.candidates}

    for suggestion in suggestions.models:
        candidate = by_key.get(suggestion.candidate_key)
        if candidate is None:
            continue
        decision = decisions[candidate.key]

        name = suggestion.name.strip().casefold()
        if _IDENTIFIER.match(name) and name not in taken:
            taken.discard(decision.name)
            decision.name = name
        taken.add(decision.name)

        decision.description = " ".join(suggestion.description.split())[:300]

        # The rule the prompt asks for, enforced rather than trusted. A test
        # naming a column the query does not select fails `dbt build` for a
        # reason that has nothing to do with the data.
        allowed = candidate.statement.testable_columns
        decision.tests = [
            {
                "name": test.name.strip(),
                "unique": bool(test.unique),
                "not_null": bool(test.not_null),
            }
            for test in suggestion.tests[:2]
            if test.name.strip()
            and test.name.strip().casefold() in allowed
            and (test.unique or test.not_null)
        ]


async def _ask(
    client: OpenAIBuilderClient, batch: list[Candidate], graph: Graph,
    bodies: dict[str, str], read_by: dict[str, list[str]],
    *, actor_id: str, project_id: str | None,
) -> ImportSuggestions | None:
    """One batch. Returns None when the provider could not answer usefully."""
    prompt = "\n\n".join(
        _describe(candidate, read_by.get(candidate.name, []), bodies.get(candidate.key, ""))
        for candidate in batch
    )
    try:
        return await client.structured(
            # Unset means follow the rest of the product rather than
            # naming a model this account may not have.
            model=(
                settings.openai_model_sql_import.strip()
                or settings.openai_model_planner
            ),
            instructions=INSTRUCTIONS,
            prompt=(
                f"{len(batch)} candidates follow. Answer for every one.\n\n{prompt}"
            ),
            schema=ImportSuggestions,
            actor_id=actor_id,
            operation="sql_import_suggest",
            project_id=project_id,
        )
    except Exception as exc:
        log_event(
            logger, logging.WARNING, "transform.sql_import.batch_failed",
            project_id=project_id, size=len(batch), error_type=type(exc).__name__,
        )
        return None


async def suggest(
    graph: Graph, bodies: dict[str, str], *, actor_id: str, project_id: str | None = None,
) -> tuple[dict[str, ModelDecision], list[str]]:
    """Decisions for every candidate, and any notes worth showing.

    Never raises. The defaults stand wherever the provider cannot be reached or
    answers with something unusable, because an import that fails because
    OpenAI is busy is a worse product than one that names a model `orders`
    instead of `stg_orders`.
    """
    decisions = _fallback(graph)
    if not graph.candidates or not settings.openai_api_key.strip():
        return decisions, []

    ordered = graph.order
    batches = [
        ordered[index:index + BATCH_SIZE]
        for index in range(0, len(ordered), BATCH_SIZE)
    ]
    read_by = _read_by(graph)

    try:
        client = OpenAIBuilderClient()
    except Exception as exc:
        log_event(
            logger, logging.WARNING, "transform.sql_import.no_client",
            project_id=project_id, error_type=type(exc).__name__,
        )
        return decisions, []

    gate = asyncio.Semaphore(CONCURRENCY)

    async def run(batch: list[Candidate]) -> ImportSuggestions | None:
        async with gate:
            return await _ask(
                client, batch, graph, bodies, read_by,
                actor_id=actor_id, project_id=project_id,
            )

    answers = await asyncio.gather(*(run(batch) for batch in batches))

    # Applied in batch order so a name claimed by an earlier model keeps it,
    # which makes the result the same however the requests happened to race.
    taken = {decision.name for decision in decisions.values()}
    notes: list[str] = []
    answered = 0
    for answer in answers:
        if answer is None:
            continue
        answered += 1
        _apply(answer, graph, decisions, taken)
        notes.extend(note.strip() for note in answer.notes if note.strip())

    log_event(
        logger, logging.INFO, "transform.sql_import.suggested",
        project_id=project_id, candidates=len(ordered),
        batches=len(batches), answered=answered,
    )
    return decisions, notes[:3]
