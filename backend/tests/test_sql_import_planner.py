"""What happens when the model is wrong, absent, or hostile.

The planner is the only part of the importer that talks to a provider, and it
is deliberately the part that matters least. Every test here is a failure mode
that would sink an importer built the other way round, and here each costs at
most one field.

The arrangement assumes a small model, so these are mostly the checks that
make a small model safe to trust with the question: it is handed every fact
the parser already knows, and every answer it gives is checked back against
them.
"""

from __future__ import annotations

import unittest

import pytest

pytest.importorskip("sqlglot")

from app.transforms.sql_import import planner  # noqa: E402
from app.transforms.sql_import.analysis import parse_file  # noqa: E402
from app.transforms.sql_import.graph import build  # noqa: E402
from app.transforms.sql_import.schemas import (  # noqa: E402
    ColumnTest, ImportSuggestions, ModelSuggestion,
)

UPLOAD = {
    "stg.sql": "CREATE TABLE stg_orders AS SELECT id, total FROM raw.orders",
    "daily.sql": "SELECT id FROM stg_orders",
}


def _graph(files=None):
    parsed = [
        parse_file(name, text, dialect="postgres")
        for name, text in (files or UPLOAD).items()
    ]
    return build(parsed)


def _keys(graph):
    return {candidate.name: candidate.key for candidate in graph.candidates}


def _suggestion(key, **kwargs):
    return ModelSuggestion(**{
        "candidate_key": key, "name": "x", "description": "", "tests": [],
        **kwargs,
    })


def _apply(graph, suggestions):
    decisions = planner._fallback(graph)
    taken = {decision.name for decision in decisions.values()}
    planner._apply(ImportSuggestions(models=suggestions), graph, decisions, taken)
    return decisions


class Defaults(unittest.TestCase):
    """The answer when nobody is asked."""

    def test_a_query_reading_only_the_warehouse_is_a_staging_view(self):
        graph = _graph()
        decisions = planner._fallback(graph)
        staging = decisions[_keys(graph)["stg_orders"]]
        assert (staging.layer, staging.materialized) == ("staging", "view")

    def test_a_query_reading_another_model_is_a_mart_table(self):
        graph = _graph()
        decisions = planner._fallback(graph)
        mart = decisions[_keys(graph)["daily"]]
        assert (mart.layer, mart.materialized) == ("marts", "table")


class WhatTheModelIsTold(unittest.TestCase):
    """Everything the parser knows is handed over, not left to be inferred."""

    def test_the_column_list_is_given_rather_than_read_out_of_the_sql(self):
        graph = _graph()
        candidate = next(c for c in graph.candidates if c.name == "stg_orders")
        text = planner._describe(candidate, ["daily"], "SELECT id, total FROM x")
        assert "columns: id, total" in text

    def test_a_select_star_says_the_columns_are_unknown(self):
        graph = _graph({"s.sql": "SELECT * FROM raw.orders"})
        text = planner._describe(graph.candidates[0], [], "SELECT * FROM x")
        assert "unknown" in text

    def test_who_reads_a_model_is_given_because_it_separates_marts(self):
        graph = _graph()
        assert planner._read_by(graph)["stg_orders"] == ["daily"]

    def test_a_long_query_is_trimmed_to_its_head(self):
        """The SELECT list carries the names and the intent; past that it is
        filter logic no question here is about."""
        body = "SELECT a\n" + ("-- filler\n" * 800)
        trimmed = planner._trim(body)
        assert trimmed.startswith("SELECT a")
        assert len(trimmed) < planner.QUERY_BUDGET + 60

    def test_candidates_are_sent_in_small_batches(self):
        """A small model's judgement falls off with prompt length."""
        assert planner.BATCH_SIZE <= 8


class BadAnswers(unittest.TestCase):
    """Each field is checked on its own, so one bad field costs one field."""

    def test_a_key_naming_nothing_is_ignored(self):
        graph = _graph()
        out = _apply(graph, [_suggestion("invented", name="whatever")])
        assert out[_keys(graph)["stg_orders"]].name == "stg_orders"

    def test_a_name_that_is_not_an_identifier_is_refused_not_repaired(self):
        graph = _graph()
        out = _apply(graph, [
            _suggestion(_keys(graph)["stg_orders"], name="Daily Orders!"),
        ])
        assert out[_keys(graph)["stg_orders"]].name == "stg_orders"

    def test_two_suggestions_cannot_claim_one_name(self):
        graph = _graph()
        keys = _keys(graph)
        out = _apply(graph, [
            _suggestion(keys["stg_orders"], name="orders"),
            _suggestion(keys["daily"], name="orders"),
        ])
        assert out[keys["stg_orders"]].name == "orders"
        assert out[keys["daily"]].name == "daily"

    def test_the_model_cannot_express_a_layer_or_a_materialization(self):
        """Not validated away -- absent. Both follow from what a query reads
        and what reads it, so they are decided rather than suggested, and a
        field a model cannot answer is one it cannot answer wrongly."""
        fields = set(ModelSuggestion.model_fields)
        assert "layer" not in fields and "materialized" not in fields
        with pytest.raises(Exception):
            ModelSuggestion(
                candidate_key="k", name="n", description="", tests=[],
                materialized="incremental",
            )

    def test_a_rambling_description_is_cut_short_and_flattened(self):
        graph = _graph()
        out = _apply(graph, [_suggestion(
            _keys(graph)["stg_orders"], name="stg_orders",
            description="one\n\n  two   three " + "x" * 900,
        )])
        described = out[_keys(graph)["stg_orders"]].description
        assert len(described) == 300
        assert "\n" not in described


class TestsMustNameRealColumns(unittest.TestCase):
    """The rule the prompt asks for, enforced rather than trusted.

    A test on a column the query does not select fails `dbt build` -- which is
    the report somebody is waiting for, so it is the worst thing to get wrong.
    """

    def test_a_column_the_query_selects_is_allowed(self):
        graph = _graph()
        out = _apply(graph, [_suggestion(
            _keys(graph)["stg_orders"], name="stg_orders",
            tests=[ColumnTest(name="id", unique=True, not_null=True, reason="grain")],
        )])
        assert out[_keys(graph)["stg_orders"]].tests == [
            {"name": "id", "unique": True, "not_null": True},
        ]

    def test_an_invented_column_is_dropped(self):
        graph = _graph()
        out = _apply(graph, [_suggestion(
            _keys(graph)["stg_orders"], name="stg_orders",
            tests=[ColumnTest(name="order_id", unique=True, not_null=True, reason="")],
        )])
        assert out[_keys(graph)["stg_orders"]].tests == []

    def test_a_query_selecting_star_gets_no_tests_at_all(self):
        """Its real columns live in a table nothing here has seen."""
        graph = _graph({"s.sql": "SELECT * FROM raw.orders"})
        out = _apply(graph, [_suggestion(
            graph.candidates[0].key, name="s",
            tests=[ColumnTest(name="id", unique=True, not_null=True, reason="")],
        )])
        assert out[graph.candidates[0].key].tests == []

    def test_case_does_not_decide_whether_a_column_is_real(self):
        graph = _graph()
        out = _apply(graph, [_suggestion(
            _keys(graph)["stg_orders"], name="stg_orders",
            tests=[ColumnTest(name="ID", unique=True, not_null=False, reason="")],
        )])
        assert out[_keys(graph)["stg_orders"]].tests[0]["name"] == "ID"

    def test_a_wall_of_tests_is_cut_to_two(self):
        graph = _graph({
            "s.sql": "SELECT a, b, c, d, e, f FROM raw.t",
        })
        out = _apply(graph, [_suggestion(
            graph.candidates[0].key, name="s",
            tests=[
                ColumnTest(name=letter, unique=True, not_null=True, reason="k")
                for letter in "abcdef"
            ],
        )])
        assert len(out[graph.candidates[0].key].tests) == 2

    def test_a_test_that_asserts_nothing_is_dropped(self):
        graph = _graph()
        out = _apply(graph, [_suggestion(
            _keys(graph)["stg_orders"], name="stg_orders",
            tests=[ColumnTest(name="id", unique=False, not_null=False, reason="")],
        )])
        assert out[_keys(graph)["stg_orders"]].tests == []


class WhenTheProviderIsNotThere(unittest.IsolatedAsyncioTestCase):
    """An import does not depend on somebody else's uptime."""

    def setUp(self):
        from app.core.config import settings

        self._key = settings.openai_api_key
        self._client = planner.OpenAIBuilderClient

    def tearDown(self):
        from app.core.config import settings

        settings.openai_api_key = self._key
        planner.OpenAIBuilderClient = self._client

    async def test_no_api_key_still_converts(self):
        from app.core.config import settings

        settings.openai_api_key = ""
        graph = _graph()
        decisions, notes = await planner.suggest(graph, {}, actor_id="u")
        assert set(decisions) == {c.key for c in graph.candidates}
        assert notes == []

    async def test_a_client_that_cannot_be_built_leaves_the_defaults(self):
        from app.core.config import settings

        settings.openai_api_key = "sk-test"

        class Exploding:
            def __init__(self, *_a, **_k):
                raise RuntimeError("no provider")

        planner.OpenAIBuilderClient = Exploding
        graph = _graph()
        decisions, notes = await planner.suggest(graph, {}, actor_id="u")
        assert decisions[_keys(graph)["stg_orders"]].name == "stg_orders"
        assert notes == []

    async def test_one_failing_batch_does_not_cost_the_others(self):
        """Isolation is the whole reason for batching at this size."""
        from app.core.config import settings

        settings.openai_api_key = "sk-test"
        files = {
            f"m{index}.sql": f"SELECT id FROM raw.t{index}"
            for index in range(planner.BATCH_SIZE * 2)
        }
        graph = _graph(files)
        calls = {"n": 0}

        class HalfBroken:
            def __init__(self, *_a, **_k):
                pass

            async def structured(self, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("first batch times out")
                keys = [
                    line.split("candidate_key: ", 1)[1].strip()
                    for line in kwargs["prompt"].splitlines()
                    if line.startswith("candidate_key: ")
                ]
                return ImportSuggestions(models=[
                    _suggestion(key, name=f"renamed_{index}")
                    for index, key in enumerate(keys)
                ])

        planner.OpenAIBuilderClient = HalfBroken
        decisions, _notes = await planner.suggest(graph, {}, actor_id="u")
        renamed = [d for d in decisions.values() if d.name.startswith("renamed_")]
        assert calls["n"] == 2
        assert len(renamed) == planner.BATCH_SIZE
        assert len(decisions) == planner.BATCH_SIZE * 2
