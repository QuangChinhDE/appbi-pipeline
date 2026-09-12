"""Turn a settled plan into the files a dbt project is made of.

Nothing here decides anything. Every judgement -- the name, the layer, the
materialization, which column is worth a test -- has already been made, either
by the defaults in `graph` or by a model, and in both cases a person has seen
it and had the chance to change it. This module only writes.

The one thing it is careful about is not overwriting. An import into an
existing project merges: a `_sources.yml` that already declares `raw.orders`
keeps its declaration, and a model file whose name is taken gets a suffix
rather than replacing somebody's work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from app.transforms.sql_import.analysis import rewrite
from app.transforms.sql_import.graph import Candidate, Graph

#: dbt reads any .yml under models/; these names sort to the top of a listing,
#: which is where a reader looks for them.
SOURCES_FILE = "models/_sources.yml"
LAYERS = ("staging", "marts", "intermediate")


@dataclass
class ModelDecision:
    """What a person settled on for one model."""

    #: Which candidate this is about.
    candidate_name: str
    #: What the model ends up called. May differ from the candidate's name.
    name: str
    layer: str = "staging"
    materialized: str = "view"
    description: str = ""
    #: Columns to test, as `{"name": ..., "unique": bool, "not_null": bool}`.
    tests: list[dict] = field(default_factory=list)


@dataclass
class Emitted:
    files: dict[str, str] = field(default_factory=dict)
    #: Paths that were renamed because the project already had that name.
    renamed: list[tuple[str, str]] = field(default_factory=list)


def model_path(layer: str, name: str) -> str:
    folder = layer if layer in LAYERS else "staging"
    return f"models/{folder}/{name}.sql"


def _load_yaml(raw: str | None) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dump_yaml(document: dict) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def merge_sources(
    existing: str | None,
    sources: list[tuple[str, str]],
    *,
    source_name: str = "raw",
    default_schema: str | None = None,
) -> str:
    """Add table declarations without disturbing the ones already there.

    A project that already knows about `raw.orders` keeps whatever description
    and tests somebody wrote for it. Only names that are genuinely new are
    appended.
    """
    document = _load_yaml(existing)
    document.setdefault("version", 2)
    blocks = document.get("sources")
    if not isinstance(blocks, list):
        blocks = []
    document["sources"] = blocks

    by_schema: dict[str, dict] = {}
    for block in blocks:
        if isinstance(block, dict) and block.get("schema"):
            by_schema[str(block["schema"]).casefold()] = block

    for schema, table in sources:
        key = schema.casefold()
        block = by_schema.get(key)
        if block is None:
            # A block's name is what `source()` will ask for. AppBI's own
            # scaffold declares the project's raw schema under the name `raw`,
            # so that is the fallback; anything else is named after itself.
            block = {
                "name": source_name if schema == default_schema else schema,
                "schema": schema,
                "tables": [],
            }
            blocks.append(block)
            by_schema[key] = block
        tables = block.setdefault("tables", [])
        if not isinstance(tables, list):
            tables = []
            block["tables"] = tables
        known = {
            str(item.get("name", "")).casefold()
            for item in tables if isinstance(item, dict)
        }
        if table.casefold() not in known:
            tables.append({"name": table})
    return _dump_yaml(document)


def source_alias(existing: str | None, schema: str, *, fallback: str = "raw") -> str:
    """The `source()` name a schema already goes by in this project.

    dbt's first argument is the source block's name, not the schema, and a
    project that calls its raw schema `base` needs `source('base', ...)`. Using
    the schema regardless would compile to a source that does not exist.
    """
    for block in (_load_yaml(existing).get("sources") or []):
        if isinstance(block, dict) and str(block.get("schema", "")).casefold() == schema.casefold():
            name = str(block.get("name") or "").strip()
            if name:
                return name
    return fallback


def merge_model_docs(
    existing: str | None, entries: list[dict],
) -> str:
    """Append `models:` entries, leaving any that are already described."""
    document = _load_yaml(existing)
    document.setdefault("version", 2)
    models = document.get("models")
    if not isinstance(models, list):
        models = []
    document["models"] = models
    known = {
        str(item.get("name", "")).casefold()
        for item in models if isinstance(item, dict)
    }
    for entry in entries:
        if str(entry.get("name", "")).casefold() not in known:
            models.append(entry)
    return _dump_yaml(document)


def _docs_entry(decision: ModelDecision) -> dict:
    entry: dict = {"name": decision.name}
    if decision.description.strip():
        entry["description"] = decision.description.strip()
    columns = []
    for column in decision.tests:
        tests = []
        if column.get("unique"):
            tests.append("unique")
        if column.get("not_null"):
            tests.append("not_null")
        if tests and column.get("name"):
            columns.append({"name": column["name"], "data_tests": tests})
    if columns:
        entry["columns"] = columns
    return entry


#: Text that must not reach a model file, even inside a comment.
#:
#: dbt reads a model twice: a fast static scan of the raw text to find its
#: dependencies, and then the real Jinja render. If the two disagree about
#: which refs a model has, dbt refuses the model outright -- and the scan does
#: not know a comment is a comment. A header that helpfully explained the
#: conversion by naming `ref()` was read as a ref with no arguments, and every
#: model with a genuine dependency failed to compile.
_JINJA_LOOKALIKE = re.compile(r"\{\{|\}\}|\b(?:ref|source|config|var|env_var)\s*\(")


def _comment_safe(text: str) -> str:
    """One line of prose that cannot be mistaken for Jinja."""
    flattened = " ".join(str(text).split())
    return _JINJA_LOOKALIKE.sub("", flattened).strip()


def _header(candidate: Candidate, decision: ModelDecision) -> str:
    """Where this model came from, written where somebody will read it.

    Six months from now the question about a model is always the same one --
    where did this come from -- and the answer is cheapest to record now.

    Everything here goes through `_comment_safe`, including the description,
    which may have been written by a model and may say anything at all.
    """
    lines = [
        f"-- Imported by AppBI from {_comment_safe(candidate.file_name)}.",
        "-- The query below is exactly the one that was uploaded. Only the "
        "table names",
        "-- were changed, so dbt can work out what has to be built first.",
    ]
    # What the author wrote above their query outranks anything a model says
    # about it: they know what the thing is for, and the sentence was already
    # there. A suggested description is only used when there was no comment.
    described = _comment_safe(
        candidate.statement.leading_comment or decision.description
    )
    if described:
        lines.insert(1, f"-- {described}")
    return "\n".join(lines)


def emit(
    graph: Graph,
    decisions: dict[str, ModelDecision],
    *,
    texts: dict[str, str],
    existing_paths: set[str] | None = None,
    existing_sources_yml: str | None = None,
    source_name: str = "raw",
    default_schema: str | None = None,
) -> Emitted:
    """Write every model, its documentation, and the source declarations."""
    taken = {path.casefold() for path in (existing_paths or set())}
    out = Emitted()
    per_layer: dict[str, list[dict]] = {}

    for candidate in graph.order:
        decision = decisions.get(candidate.key) or decisions.get(candidate.name)
        if decision is None:
            continue
        body = rewrite(
            texts.get(candidate.file_name, ""), candidate.statement,
            candidate.replacements,
        )
        path = model_path(decision.layer, decision.name)
        if path.casefold() in taken:
            suffix = 2
            while model_path(decision.layer, f"{decision.name}_{suffix}").casefold() in taken:
                suffix += 1
            renamed = f"{decision.name}_{suffix}"
            out.renamed.append((decision.name, renamed))
            decision.name = renamed
            path = model_path(decision.layer, renamed)
        taken.add(path.casefold())

        config = f"{{{{ config(materialized='{decision.materialized}') }}}}"
        out.files[path] = f"{_header(candidate, decision)}\n\n{config}\n\n{body}\n"
        per_layer.setdefault(decision.layer, []).append(_docs_entry(decision))

    if graph.sources:
        out.files[SOURCES_FILE] = merge_sources(
            existing_sources_yml, graph.sources,
            source_name=source_name, default_schema=default_schema,
        )

    for layer, entries in per_layer.items():
        if entries:
            out.files[f"models/{layer}/_models.yml"] = merge_model_docs(None, entries)
    return out
