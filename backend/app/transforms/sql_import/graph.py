"""Which tables the uploaded files make, and which they only read.

That question is the whole of the conversion. A name a query selects from is
either something else in the upload -- in which case dbt wants `ref()`, and the
two models now have an order they must run in -- or it is something that was
already in the warehouse, in which case dbt wants `source()` and a declaration
to go with it.

Both answers are decidable from the files alone, so both are decided here. The
model is asked afterwards, and only about things that are matters of taste.

A consequence worth stating plainly: with no OPENAI_API_KEY set, this module
and the emitter beside it still produce a working dbt project. The model
improves names, writes descriptions and proposes tests. It is not load-bearing,
and an import does not fail because a provider is having a bad afternoon.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.transforms.sql_import.analysis import ParsedFile, Statement, TableRef

#: Leading `01_`, `1-`, `step3.` -- an ordering hint in a filename, not a name.
_ORDER_PREFIX = re.compile(r"^(?:step)?[-_. ]*\d+[-_. ]+", re.I)
_NOT_IDENTIFIER = re.compile(r"[^a-z0-9_]+")


def model_name_from(file_name: str, creates: tuple[str, ...] | None) -> str:
    """What to call the model.

    What the query creates, when it creates something -- somebody chose that
    name once already. Otherwise the filename, with any ordering prefix taken
    off, because `01_` is how a person hand-rolls a DAG and dbt does that part
    now.
    """
    if creates:
        raw = creates[-1]
    else:
        raw = file_name.rsplit("/", 1)[-1]
        if raw.lower().endswith(".sql"):
            raw = raw[:-4]
        raw = _ORDER_PREFIX.sub("", raw)
    cleaned = _NOT_IDENTIFIER.sub("_", raw.strip().casefold()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"model_{cleaned}" if cleaned else "model"
    return cleaned[:60]


@dataclass
class Candidate:
    """A statement that is going to become one dbt model."""

    file_name: str
    statement: Statement
    #: The unique model name, after collisions are resolved.
    name: str
    #: What the statement said it creates, if anything.
    creates: tuple[str, ...] | None
    #: Model names this one reads from, in the order they were found.
    depends_on: list[str] = field(default_factory=list)
    #: External tables it reads, as `(schema, table)`.
    sources: list[tuple[str, str]] = field(default_factory=list)
    #: `ref.start` -> the jinja that replaces it.
    replacements: dict[int, str] = field(default_factory=dict)
    #: Nothing reads this one.
    is_leaf: bool = True

    @property
    def key(self) -> str:
        """A name for this candidate that survives being renamed.

        Decisions travel to the browser and back, and the first thing somebody
        does in the review is rename a model. Addressing candidates by name
        would mean the answer no longer matches the question.
        """
        return f"{self.file_name}#{self.statement.index}"


@dataclass
class Graph:
    candidates: list[Candidate] = field(default_factory=list)
    #: Files and statements that could not be converted, and why.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Distinct external tables, as `(schema, table)`.
    sources: list[tuple[str, str]] = field(default_factory=list)
    #: Model names caught in a `ref()` cycle, if any.
    cycle: list[str] = field(default_factory=list)

    @property
    def order(self) -> list[Candidate]:
        """Candidates in an order where dependencies come first."""
        by_name = {item.name: item for item in self.candidates}
        seen: set[str] = set()
        out: list[Candidate] = []

        def visit(item: Candidate, path: set[str]) -> None:
            if item.name in seen:
                return
            for parent in item.depends_on:
                if parent in path or parent not in by_name:
                    continue
                visit(by_name[parent], path | {item.name})
            seen.add(item.name)
            out.append(item)

        for item in self.candidates:
            visit(item, {item.name})
        return out


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name}_{index}" in taken:
        index += 1
    return f"{name}_{index}"


def _source_parts(ref: TableRef, default_schema: str) -> tuple[str, str]:
    """`(schema, table)` for a name that is not in the upload.

    A bare `orders` has no schema of its own, so it takes the project's source
    schema. `raw.orders` says which. `proj.raw.orders` is BigQuery naming the
    project as well, and the project is not dbt's business here.
    """
    if len(ref.parts) >= 2:
        return ref.parts[-2], ref.parts[-1]
    return default_schema, ref.parts[-1] if ref.parts else "unknown"


def build(
    files: list[ParsedFile],
    *,
    default_source_schema: str = "raw",
    source_name_for: Callable[[str], str] | None = None,
) -> Graph:
    """Turn parsed files into models, dependencies and sources.

    `source_name_for` maps a schema to the name its `sources:` block goes by.
    dbt's first argument to `source()` is that block's name and not the schema,
    so a project whose raw schema is declared under the name `raw` -- which is
    what AppBI scaffolds -- needs `source('raw', ...)` however the schema is
    spelled. Getting this wrong compiles to a source that does not exist.
    """
    naming = source_name_for or (lambda schema: schema)
    graph = Graph()

    for parsed in files:
        if parsed.problem:
            graph.skipped.append((parsed.name, parsed.problem))
            continue
        for statement in parsed.statements:
            if not statement.usable:
                graph.skipped.append((
                    parsed.name,
                    statement.problem or "This statement cannot become a model.",
                ))

    taken: set[str] = set()
    for parsed in files:
        for statement in parsed.usable:
            name = _unique(model_name_from(parsed.name, statement.creates), taken)
            taken.add(name)
            graph.candidates.append(Candidate(
                file_name=parsed.name, statement=statement,
                name=name, creates=statement.creates,
            ))

    # Every name the upload could be asked for: a model's own name, the name
    # its query creates, and that name unqualified. A query saying
    # `analytics.daily` and one saying `daily` mean the same table often enough
    # that refusing to match would be the wrong kind of strict.
    #
    # Each is recorded with who claims it, because a name two models both
    # answer to is not a match -- it is a coin toss, and the way you find out
    # it landed wrong is a number being off in a report.
    claimants: dict[str, set[str]] = {}
    holder: dict[str, Candidate] = {}
    for candidate in graph.candidates:
        keys = {candidate.name}
        if candidate.creates:
            keys.add(".".join(candidate.creates))
            keys.add(candidate.creates[-1])
        for key in keys:
            claimants.setdefault(key, set()).add(candidate.name)
            holder.setdefault(key, candidate)

    def claimed_by(key: str) -> Candidate | None:
        return holder.get(key) if len(claimants.get(key, ())) == 1 else None

    seen_sources: dict[tuple[str, str], None] = {}

    for candidate in graph.candidates:
        for ref in candidate.statement.refs:
            # Most specific first: a qualified name says which one it means.
            target = claimed_by(".".join(ref.parts)) or claimed_by(ref.name)
            if target is not None and target is not candidate:
                candidate.replacements[ref.start] = f"{{{{ ref('{target.name}') }}}}"
                if target.name not in candidate.depends_on:
                    candidate.depends_on.append(target.name)
                target.is_leaf = False
                continue
            schema, table = _source_parts(ref, default_source_schema)
            candidate.replacements[ref.start] = (
                f"{{{{ source('{naming(schema)}', '{table}') }}}}"
            )
            if (schema, table) not in seen_sources:
                seen_sources[(schema, table)] = None
            if (schema, table) not in candidate.sources:
                candidate.sources.append((schema, table))

    graph.sources = list(seen_sources)
    graph.cycle = _cycle(graph.candidates)
    return graph


def _cycle(candidates: list[Candidate]) -> list[str]:
    """The first `ref()` cycle, if the upload has one.

    dbt refuses a cyclic project outright, so finding it here means saying
    which models are involved instead of handing somebody a parse error.
    """
    by_name = {item.name: item for item in candidates}
    state: dict[str, int] = {}
    trail: list[str] = []

    def walk(name: str) -> list[str] | None:
        state[name] = 1
        trail.append(name)
        for parent in by_name[name].depends_on:
            if parent not in by_name:
                continue
            if state.get(parent) == 1:
                return trail[trail.index(parent):] + [parent]
            if state.get(parent, 0) == 0:
                found = walk(parent)
                if found:
                    return found
        state[name] = 2
        trail.pop()
        return None

    for candidate in candidates:
        if state.get(candidate.name, 0) == 0:
            found = walk(candidate.name)
            if found:
                return found
    return []


def default_layer(candidate: Candidate) -> str:
    """Which layer a query belongs in, decided rather than guessed.

    dbt's own convention, and it is a function of two facts the parse already
    establishes -- what a query reads, and whether anything reads it:

        reads no models                         staging
        reads models, and nothing reads it      marts
        reads models, and another model reads   intermediate

    Deterministic, so it is computed here rather than asked of a model. A rule
    with no judgement in it is exactly where a small model occasionally says
    something silly for no benefit, and having it decided also shortens the
    prompt for the questions that do need judgement.
    """
    if not candidate.depends_on:
        return "staging"
    return "marts" if candidate.is_leaf else "intermediate"


def default_materialization(layer: str) -> str:
    """A view unless the model is what somebody reads.

    Cheap to rebuild and always current; a mart is the one worth the storage
    because it is read repeatedly and often by a dashboard.
    """
    return "table" if layer == "marts" else "view"
