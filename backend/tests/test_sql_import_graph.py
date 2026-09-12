"""Which of the uploaded names are models, and which are the warehouse.

Getting this wrong is not a cosmetic failure. A `ref()` that should have been
a `source()` makes dbt look for a model that does not exist; a `source()` that
should have been a `ref()` builds the two models in whatever order dbt likes,
which on a good day is the right one.
"""

from __future__ import annotations

import unittest

import pytest

pytest.importorskip("sqlglot")

from app.transforms.sql_import.analysis import parse_file  # noqa: E402
from app.transforms.sql_import.graph import (  # noqa: E402
    build, default_layer, model_name_from,
)


def _graph(files: dict[str, str], *, dialect: str = "postgres", schema: str = "raw"):
    parsed = [parse_file(name, text, dialect=dialect) for name, text in files.items()]
    return build(parsed, default_source_schema=schema)


class Naming(unittest.TestCase):
    def test_what_the_query_creates_is_the_name(self):
        assert model_name_from("anything.sql", ("analytics", "daily_orders")) == "daily_orders"

    def test_otherwise_the_filename_is(self):
        assert model_name_from("daily_orders.sql", None) == "daily_orders"

    def test_an_ordering_prefix_is_not_part_of_the_name(self):
        """`01_` is how somebody hand-rolls a DAG. dbt does that part now."""
        assert model_name_from("01_stg_orders.sql", None) == "stg_orders"
        assert model_name_from("step2-marts.sql", None) == "marts"

    def test_a_name_that_cannot_be_an_identifier_becomes_one(self):
        assert model_name_from("Daily Orders (v2).sql", None) == "daily_orders_v2"

    def test_two_files_with_one_name_do_not_collide(self):
        graph = _graph({
            "a/orders.sql": "SELECT 1 AS x",
            "b/orders.sql": "SELECT 2 AS x",
        })
        assert sorted(c.name for c in graph.candidates) == ["orders", "orders_2"]


class RefsAndSources(unittest.TestCase):
    def test_a_table_another_file_creates_becomes_a_ref(self):
        graph = _graph({
            "stg.sql": "CREATE TABLE stg_orders AS SELECT * FROM raw.orders",
            "mart.sql": "SELECT * FROM stg_orders",
        })
        mart = next(c for c in graph.candidates if c.name == "mart")
        assert mart.depends_on == ["stg_orders"]
        assert list(mart.replacements.values()) == ["{{ ref('stg_orders') }}"]

    def test_a_table_nobody_creates_becomes_a_source(self):
        graph = _graph({"stg.sql": "SELECT * FROM raw.orders"})
        assert graph.sources == [("raw", "orders")]
        assert list(graph.candidates[0].replacements.values()) == [
            "{{ source('raw', 'orders') }}"
        ]

    def test_an_unqualified_external_table_takes_the_project_schema(self):
        graph = _graph({"s.sql": "SELECT * FROM orders"}, schema="landing")
        assert graph.sources == [("landing", "orders")]

    def test_bigquery_project_qualifier_is_not_the_schema(self):
        """`proj.raw.orders` names a billing project, which dbt does not want
        in a source declaration."""
        graph = _graph(
            {"s.sql": "SELECT * FROM `proj.raw.orders`"}, dialect="bigquery",
        )
        assert graph.sources == [("raw", "orders")]

    def test_a_qualified_name_matches_the_model_that_creates_it(self):
        graph = _graph({
            "a.sql": "CREATE TABLE analytics.daily AS SELECT 1 AS x",
            "b.sql": "SELECT * FROM analytics.daily",
        })
        assert next(c for c in graph.candidates if c.name == "b").depends_on == ["daily"]

    def test_an_ambiguous_bare_name_is_left_as_a_source(self):
        """Two uploads could both be `orders`. Picking one is how a DAG comes
        out wrong in a way nobody notices."""
        graph = _graph({
            "a/orders.sql": "CREATE TABLE a.orders AS SELECT 1 AS x",
            "b/orders.sql": "CREATE TABLE b.orders AS SELECT 2 AS x",
            "c.sql": "SELECT * FROM orders",
        })
        c = next(item for item in graph.candidates if item.name == "c")
        assert c.depends_on == []
        assert ("raw", "orders") in graph.sources

    def test_a_model_does_not_depend_on_itself(self):
        graph = _graph({
            "x.sql": "CREATE TABLE x AS SELECT * FROM x",
        })
        assert graph.candidates[0].depends_on == []


class Ordering(unittest.TestCase):
    def test_dependencies_come_first(self):
        graph = _graph({
            "3.sql": "CREATE TABLE c AS SELECT * FROM b",
            "1.sql": "CREATE TABLE a AS SELECT * FROM raw.t",
            "2.sql": "CREATE TABLE b AS SELECT * FROM a",
        })
        assert [c.name for c in graph.order] == ["a", "b", "c"]

    def test_a_cycle_is_named_rather_than_left_for_dbt(self):
        graph = _graph({
            "a.sql": "CREATE TABLE a AS SELECT * FROM b",
            "b.sql": "CREATE TABLE b AS SELECT * FROM a",
        })
        assert set(graph.cycle) >= {"a", "b"}

    def test_ordering_still_terminates_when_there_is_a_cycle(self):
        graph = _graph({
            "a.sql": "CREATE TABLE a AS SELECT * FROM b",
            "b.sql": "CREATE TABLE b AS SELECT * FROM a",
        })
        assert len(graph.order) == 2


class Layering(unittest.TestCase):
    def test_reading_only_the_warehouse_is_staging(self):
        graph = _graph({"s.sql": "SELECT * FROM raw.orders"})
        assert default_layer(graph.candidates[0]) == "staging"

    def test_reading_another_model_is_marts(self):
        graph = _graph({
            "s.sql": "CREATE TABLE s AS SELECT * FROM raw.orders",
            "m.sql": "SELECT * FROM s",
        })
        assert default_layer(next(c for c in graph.candidates if c.name == "m")) == "marts"


class Refusals(unittest.TestCase):
    def test_a_file_that_does_not_parse_is_reported_with_its_name(self):
        graph = _graph({"broken.sql": "}{ not sql"})
        assert graph.candidates == []
        assert graph.skipped and graph.skipped[0][0] == "broken.sql"

    def test_an_insert_is_skipped_and_the_rest_of_the_upload_still_converts(self):
        graph = _graph({
            "good.sql": "SELECT * FROM raw.a",
            "bad.sql": "INSERT INTO t SELECT 1",
        })
        assert [c.name for c in graph.candidates] == ["good"]
        assert graph.skipped[0][0] == "bad.sql"
