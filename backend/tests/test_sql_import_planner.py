"""What happens when the model is wrong, absent, or hostile.

The planner is the only part of the importer that talks to a provider, and it
is deliberately the part that matters least. These tests are the argument for
that arrangement: every one of them is a failure mode that would sink an
importer built the other way round, and here each costs at most one field.
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
    "orders.sql": "CREATE TABLE stg_orders AS SELECT id, total FROM raw.orders",
    "daily.sql": "SELECT * FROM stg_orders",
}


def _graph(files=None):
    parsed = [
        parse_file(name, text, dialect="postgres")
        for name, text in (files or UPLOAD).items()
    ]
    return build(parsed)


def _keys(graph):
    return {candidate.name: candidate.key for candidate in graph.candidates}


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


class BadAnswers(unittest.TestCase):
    """Each field is checked on its own, so one bad field costs one field."""

    def _clean(self, graph, suggestions):
        return planner._clean(
            ImportSuggestions(models=suggestions), graph, planner._fallback(graph),
        )

    def test_a_key_naming_nothing_is_ignored(self):
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key="invented", name="whatever", layer="marts",
            materialized="table", description="", tests=[],
        )])
        assert out[_keys(graph)["stg_orders"]].name == "stg_orders"

    def test_a_name_that_is_not_an_identifier_is_refused_not_repaired(self):
        """A repaired name is a name nobody chose."""
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key=_keys(graph)["stg_orders"], name="Daily Orders!",
            layer="staging", materialized="view", description="", tests=[],
        )])
        assert out[_keys(graph)["stg_orders"]].name == "stg_orders"

    def test_two_suggestions_cannot_claim_one_name(self):
        graph = _graph()
        keys = _keys(graph)
        out = self._clean(graph, [
            ModelSuggestion(candidate_key=keys["stg_orders"], name="orders",
                            layer="staging", materialized="view", description="", tests=[]),
            ModelSuggestion(candidate_key=keys["daily"], name="orders",
                            layer="marts", materialized="table", description="", tests=[]),
        ])
        assert out[keys["stg_orders"]].name == "orders"
        assert out[keys["daily"]].name == "daily"

    def test_incremental_is_refused_however_it_is_asked_for(self):
        """It needs a unique key and a filter nothing here can verify."""
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key=_keys(graph)["stg_orders"], name="stg_orders",
            layer="staging", materialized="incremental", description="", tests=[],
        )])
        assert out[_keys(graph)["stg_orders"]].materialized == "view"

    def test_a_wall_of_tests_is_cut_to_two(self):
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key=_keys(graph)["stg_orders"], name="stg_orders",
            layer="staging", materialized="view", description="",
            tests=[
                ColumnTest(name=f"c{i}", unique=True, not_null=True, reason="k")
                for i in range(9)
            ],
        )])
        assert len(out[_keys(graph)["stg_orders"]].tests) == 2

    def test_a_test_that_asserts_nothing_is_dropped(self):
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key=_keys(graph)["stg_orders"], name="stg_orders",
            layer="staging", materialized="view", description="",
            tests=[ColumnTest(name="id", unique=False, not_null=False, reason="")],
        )])
        assert out[_keys(graph)["stg_orders"]].tests == []

    def test_a_rambling_description_is_cut_short(self):
        graph = _graph()
        out = self._clean(graph, [ModelSuggestion(
            candidate_key=_keys(graph)["stg_orders"], name="stg_orders",
            layer="staging", materialized="view", description="x" * 900, tests=[],
        )])
        assert len(out[_keys(graph)["stg_orders"]].description) == 300


class NoProvider(unittest.IsolatedAsyncioTestCase):
    """An import does not depend on somebody else's uptime."""

    async def test_no_api_key_still_converts(self):
        from app.core.config import settings

        original = settings.openai_api_key
        settings.openai_api_key = ""
        try:
            graph = _graph()
            decisions, notes = await planner.suggest(graph, {}, actor_id="u")
        finally:
            settings.openai_api_key = original
        assert set(decisions) == {c.key for c in graph.candidates}
        assert notes == []

    async def test_a_provider_that_raises_leaves_the_defaults_standing(self):
        from app.core.config import settings

        class Exploding:
            def __init__(self, *_a, **_k):
                raise RuntimeError("provider down")

        original_key = settings.openai_api_key
        original_client = planner.OpenAIBuilderClient
        settings.openai_api_key = "sk-test"
        planner.OpenAIBuilderClient = Exploding
        try:
            graph = _graph()
            decisions, notes = await planner.suggest(graph, {}, actor_id="u")
        finally:
            settings.openai_api_key = original_key
            planner.OpenAIBuilderClient = original_client
        assert decisions[_keys(graph)["stg_orders"]].name == "stg_orders"
        assert notes == []
