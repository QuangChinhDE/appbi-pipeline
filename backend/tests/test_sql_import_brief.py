"""The advice has to be advice that works.

`frontend/src/lib/sqlImportPrompt.ts` is a brief somebody pastes into an
assistant so the SQL that comes back imports cleanly. It is the cheapest part
of this feature to get wrong, because nothing fails when it drifts: the brief
goes on saying what used to be true, assistants go on producing what it asks
for, and the imports quietly get worse.

So the worked example inside the brief is extracted and put through the real
importer. If the conventions change, or the example stops demonstrating them,
this fails -- which is the only way a document gets kept honest.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import pytest

pytest.importorskip("sqlglot")

from app.transforms.sql_import.analysis import parse_file  # noqa: E402
from app.transforms.sql_import.graph import (  # noqa: E402
    build, default_layer, default_materialization,
)
from app.transforms.sql_import.service import normalise, render  # noqa: E402

BRIEF = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "lib" / "sqlImportPrompt.ts"
)

#: `File stg_orders.sql:` followed by an indented block.
_EXAMPLE = re.compile(
    r"^File (?P<name>[\w.]+\.sql):\n\n(?P<body>(?:    .*\n|\n)+)", re.M,
)


def _examples(language: str) -> dict[str, str]:
    """The SQL files the brief shows as correct output."""
    text = BRIEF.read_text(encoding="utf-8")
    start = text.index(f"  {language}: `")
    end = text.index("`,", start)
    section = text[start:end]
    return {
        match.group("name"): "\n".join(
            line[4:] for line in match.group("body").splitlines()
        ).strip() + "\n"
        for match in _EXAMPLE.finditer(section)
    }


class TheBriefIsFindable(unittest.TestCase):
    def test_it_exists_where_the_documentation_says(self):
        assert BRIEF.exists(), f"{BRIEF} is gone; docs/transform-sql-import.md points at it"

    def test_both_languages_carry_a_worked_example(self):
        for language in ("en", "vi"):
            assert len(_examples(language)) == 2, language


class TheExampleImportsCleanly(unittest.TestCase):
    """What the brief promises, put through the thing that has to deliver it."""

    def setUp(self):
        self.files = normalise(list(_examples("en").items()))
        self.graph = build(
            [parse_file(n, t, dialect="postgres") for n, t in self.files.items()],
            default_source_schema="raw",
        )

    def test_nothing_in_it_is_refused(self):
        assert self.graph.skipped == []
        assert self.graph.cycle == []

    def test_the_models_are_named_by_their_create_and_not_their_filename(self):
        """The first rule the brief gives, demonstrated by the example."""
        assert sorted(c.name for c in self.graph.candidates) == [
            "daily_revenue", "stg_orders",
        ]

    def test_the_second_file_reads_the_first(self):
        """The rule that makes two uploaded files into one project."""
        mart = next(c for c in self.graph.candidates if c.name == "daily_revenue")
        assert mart.depends_on == ["stg_orders"]

    def test_the_warehouse_tables_become_sources(self):
        assert sorted(self.graph.sources) == [("raw", "customers"), ("raw", "orders")]

    def test_the_layering_comes_out_as_the_brief_implies(self):
        layers = {
            c.name: (default_layer(c), default_materialization(default_layer(c)))
            for c in self.graph.candidates
        }
        assert layers["stg_orders"] == ("staging", "view")
        assert layers["daily_revenue"] == ("marts", "table")

    def test_it_produces_a_project_with_the_files_dbt_needs(self):
        emitted = render(self.files, [], adapter="postgres", source_schema="raw")
        assert "models/staging/stg_orders.sql" in emitted.files
        assert "models/marts/daily_revenue.sql" in emitted.files
        assert "models/_sources.yml" in emitted.files

    def test_the_example_query_survives_the_conversion(self):
        emitted = render(self.files, [], adapter="postgres", source_schema="raw")
        body = emitted.files["models/marts/daily_revenue.sql"]
        assert "{{ ref('stg_orders') }}" in body
        assert "{{ source('raw', 'customers') }}" in body
        # The comment the brief writes above the query is the kind of thing
        # the conversion promises to keep.
        assert "One row per country per day" in body


class BothLanguagesSayTheSameThing(unittest.TestCase):
    """A translation that drifts is worse than no translation."""

    def test_the_example_sql_is_identical_in_both(self):
        english, vietnamese = _examples("en"), _examples("vi")
        assert set(english) == set(vietnamese)
        for name in english:
            # Comments are translated; the SQL is not, and must not be.
            strip = lambda text: "\n".join(  # noqa: E731
                line for line in text.splitlines() if not line.strip().startswith("--")
            )
            assert strip(english[name]) == strip(vietnamese[name]), name

    def test_both_forbid_the_same_statements(self):
        text = BRIEF.read_text(encoding="utf-8")
        for keyword in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"):
            assert text.count(keyword) >= 2, f"{keyword} is not refused in both"
