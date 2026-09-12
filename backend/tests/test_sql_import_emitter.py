"""Writing the project, and above all not writing over it.

An import into an existing project is the case with something to lose. A
`_sources.yml` somebody has documented, a model file somebody has edited --
neither may be replaced because an upload happened to reuse a name.
"""

from __future__ import annotations

import unittest

import pytest
import yaml

pytest.importorskip("sqlglot")

from app.transforms.sql_import.analysis import parse_file  # noqa: E402
from app.transforms.sql_import.emitter import (  # noqa: E402
    ModelDecision, emit, merge_model_docs, merge_sources, source_alias,
)
from app.transforms.sql_import.graph import build, default_layer  # noqa: E402


def _prepared(files: dict[str, str], **kwargs):
    parsed = [parse_file(name, text, dialect="postgres") for name, text in files.items()]
    graph = build(parsed, **kwargs)
    decisions = {
        candidate.key: ModelDecision(
            candidate_name=candidate.name, name=candidate.name,
            layer=default_layer(candidate),
            materialized="view" if default_layer(candidate) == "staging" else "table",
        )
        for candidate in graph.candidates
    }
    return graph, decisions, files


class Writing(unittest.TestCase):
    def test_a_model_lands_in_the_layer_it_was_given(self):
        graph, decisions, texts = _prepared({
            "stg.sql": "CREATE TABLE stg_orders AS SELECT * FROM raw.orders",
            "mart.sql": "SELECT * FROM stg_orders",
        })
        out = emit(graph, decisions, texts=texts)
        assert "models/staging/stg_orders.sql" in out.files
        assert "models/marts/mart.sql" in out.files

    def test_the_body_is_the_uploaded_query_with_names_pointed_at_dbt(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT id FROM raw.orders"})
        body = out_body(emit(graph, decisions, texts=texts), "models/staging/s.sql")
        assert "SELECT id FROM {{ source('raw', 'orders') }}" in body

    def test_a_materialization_is_declared_on_the_model(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT 1 AS x"})
        body = out_body(emit(graph, decisions, texts=texts), "models/staging/s.sql")
        assert "{{ config(materialized='view') }}" in body

    def test_the_file_says_where_it_came_from(self):
        graph, decisions, texts = _prepared({"reports/daily.sql": "SELECT 1 AS x"})
        out = emit(graph, decisions, texts=texts)
        body = out_body(out, "models/staging/daily.sql")
        assert "reports/daily.sql" in body

    def test_sources_are_declared_for_what_the_upload_only_reads(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT * FROM raw.orders"})
        out = emit(graph, decisions, texts=texts)
        document = yaml.safe_load(out.files["models/_sources.yml"])
        tables = document["sources"][0]["tables"]
        assert [t["name"] for t in tables] == ["orders"]


class NotOverwriting(unittest.TestCase):
    def test_a_model_whose_name_is_taken_is_renamed_not_replaced(self):
        graph, decisions, texts = _prepared({"orders.sql": "SELECT 1 AS x"})
        out = emit(
            graph, decisions, texts=texts,
            existing_paths={"models/staging/orders.sql"},
        )
        assert "models/staging/orders.sql" not in out.files
        assert "models/staging/orders_2.sql" in out.files
        assert out.renamed == [("orders", "orders_2")]

    def test_an_existing_source_declaration_keeps_its_documentation(self):
        existing = yaml.safe_dump({
            "version": 2,
            "sources": [{
                "name": "raw", "schema": "raw",
                "tables": [{"name": "orders", "description": "hand written"}],
            }],
        })
        merged = yaml.safe_load(merge_sources(existing, [("raw", "orders"), ("raw", "new")]))
        tables = merged["sources"][0]["tables"]
        assert tables[0]["description"] == "hand written"
        assert [t["name"] for t in tables] == ["orders", "new"]

    def test_a_second_schema_gets_its_own_block(self):
        merged = yaml.safe_load(merge_sources(None, [("raw", "a"), ("other", "b")]))
        assert {b["schema"] for b in merged["sources"]} == {"raw", "other"}

    def test_an_already_documented_model_is_not_documented_twice(self):
        existing = yaml.safe_dump({
            "version": 2, "models": [{"name": "orders", "description": "mine"}],
        })
        merged = yaml.safe_load(merge_model_docs(existing, [{"name": "orders"}]))
        assert merged["models"] == [{"name": "orders", "description": "mine"}]

    def test_unreadable_yaml_is_not_silently_emptied(self):
        """Better to add a block beside what is there than to drop it."""
        merged = merge_sources("{{{ not yaml", [("raw", "a")])
        assert "raw" in merged


class SourceNaming(unittest.TestCase):
    """`source()` takes the block's name, which need not be the schema."""

    def test_an_existing_block_lends_its_name(self):
        existing = yaml.safe_dump({
            "version": 2,
            "sources": [{"name": "base", "schema": "landing", "tables": []}],
        })
        assert source_alias(existing, "landing") == "base"

    def test_an_unknown_schema_falls_back(self):
        assert source_alias(None, "landing", fallback="raw") == "raw"

    def test_the_graph_uses_the_name_it_is_given(self):
        """A project whose raw schema is declared as `raw` needs
        `source('raw', ...)` even when the schema is called something else."""
        graph, decisions, texts = _prepared(
            {"s.sql": "SELECT * FROM landing.orders"},
            source_name_for=lambda schema: "raw",
        )
        body = out_body(emit(graph, decisions, texts=texts), "models/staging/s.sql")
        assert "{{ source('raw', 'orders') }}" in body


class Documentation(unittest.TestCase):
    def test_a_requested_test_reaches_the_yaml(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT id FROM raw.t"})
        for decision in decisions.values():
            decision.tests = [{"name": "id", "unique": True, "not_null": True}]
            decision.description = "One row per order."
        out = emit(graph, decisions, texts=texts)
        document = yaml.safe_load(out.files["models/staging/_models.yml"])
        entry = document["models"][0]
        assert entry["description"] == "One row per order."
        assert entry["columns"][0]["data_tests"] == ["unique", "not_null"]

    def test_a_model_with_nothing_to_say_says_nothing(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT 1 AS x"})
        out = emit(graph, decisions, texts=texts)
        document = yaml.safe_load(out.files["models/staging/_models.yml"])
        assert document["models"] == [{"name": "s"}]


def out_body(emitted, path: str) -> str:
    assert path in emitted.files, sorted(emitted.files)
    return emitted.files[path]


class NothingThatLooksLikeJinja(unittest.TestCase):
    """dbt reads a model twice and refuses it if the readings disagree.

    The first read is a fast static scan of the raw text to find dependencies;
    the second is the real Jinja render. The scan does not know a comment is a
    comment. So a header that explained the conversion by naming `ref()` was
    read as a ref with no arguments, and every model with a genuine dependency
    failed to compile with "unable to infer all dependencies".

    It cost one end-to-end run to find and would never have shown up in a unit
    test of the emitter, because the file it produced looked perfect.
    """

    def test_the_header_names_no_jinja_function(self):
        graph, decisions, texts = _prepared({"s.sql": "SELECT 1 AS x"})
        body = out_body(emit(graph, decisions, texts=texts), "models/staging/s.sql")
        header = body.split("{{ config")[0]
        for token in ("ref(", "source(", "config(", "{{", "}}"):
            assert token not in header, f"{token!r} in the header"

    def test_a_description_cannot_smuggle_jinja_into_a_comment(self):
        """The description may have been written by a model, and a model will
        happily explain itself using the words it was told about."""
        graph, decisions, texts = _prepared({"s.sql": "SELECT 1 AS x"})
        for decision in decisions.values():
            decision.description = "Joins {{ ref('other') }} using source(x)."
        body = out_body(emit(graph, decisions, texts=texts), "models/staging/s.sql")
        header = body.split("{{ config")[0]
        assert "ref(" not in header and "{{" not in header
        assert "Joins" in header

    def test_a_filename_cannot_either(self):
        graph, decisions, texts = _prepared({"ref(evil).sql": "SELECT 1 AS x"})
        emitted = emit(graph, decisions, texts=texts)
        header = next(iter(emitted.files.values())).split("{{ config")[0]
        assert "ref(" not in header

    def test_the_only_jinja_in_the_file_is_the_conversion_and_the_config(self):
        graph, decisions, texts = _prepared({
            "stg.sql": "CREATE TABLE stg_orders AS SELECT id FROM raw.orders",
            "mart.sql": "SELECT id FROM stg_orders",
        })
        body = out_body(
            emit(graph, decisions, texts=texts), "models/marts/mart.sql",
        )
        assert body.count("ref(") == 1
        assert body.count("config(") == 1
