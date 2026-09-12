"""The converter reads SQL; it does not rewrite it.

Every test here is really the same test asked about a different piece of
syntax: after conversion, is the query still the query somebody uploaded? The
easy implementation -- parse it and print it back out -- passes none of these,
because a round trip through any parser normalises what it understood and
quietly drops what it did not. For BigQuery that is a long list, and the way
you find out is a number being wrong in a report three weeks later.

So the rule this file defends is narrow and absolute: the only characters that
may change are the table names being pointed at dbt.
"""

from __future__ import annotations

import unittest

import pytest

sqlglot = pytest.importorskip("sqlglot")

from app.transforms.sql_import.analysis import (  # noqa: E402
    parse_file, rewrite,
)


def _one(text: str, *, dialect: str = "postgres"):
    parsed = parse_file("q.sql", text, dialect=dialect)
    assert parsed.problem is None, parsed.problem
    assert len(parsed.statements) == 1, [s.kind for s in parsed.statements]
    return parsed, parsed.statements[0]


def _rewritten(text: str, mapping: dict[str, str], *, dialect: str = "postgres") -> str:
    parsed, statement = _one(text, dialect=dialect)
    replacements = {
        ref.start: mapping[".".join(ref.parts)]
        for ref in statement.refs
        if ".".join(ref.parts) in mapping
    }
    return rewrite(parsed.text, statement, replacements)


class Reading(unittest.TestCase):
    """What a parser can prove about a file."""

    def test_a_create_table_names_what_it_creates(self):
        _parsed, statement = _one("CREATE TABLE analytics.daily AS SELECT 1 AS x")
        assert statement.kind == "CREATE_TABLE"
        assert statement.creates == ("analytics", "daily")

    def test_a_create_view_is_told_apart_from_a_table(self):
        _parsed, statement = _one("CREATE VIEW v AS SELECT 1 AS x")
        assert statement.kind == "CREATE_VIEW"

    def test_a_bare_select_creates_nothing(self):
        _parsed, statement = _one("SELECT 1 AS x")
        assert statement.kind == "SELECT"
        assert statement.creates is None

    def test_the_create_prefix_is_not_part_of_the_body(self):
        """dbt writes the CREATE itself, out of the materialization.

        Leaving one in the file gives you a model that creates a table that
        creates a table, and the failure reads as a permissions problem.
        """
        parsed, statement = _one(
            "CREATE OR REPLACE TABLE analytics.daily AS\nSELECT 1 AS x"
        )
        body = parsed.text[statement.body_start:statement.body_end].strip()
        assert body == "SELECT 1 AS x"

    def test_several_statements_in_one_file_are_read_separately(self):
        parsed = parse_file(
            "two.sql",
            "CREATE TABLE a AS SELECT 1 AS x;\nCREATE TABLE b AS SELECT * FROM a",
            dialect="postgres",
        )
        assert [s.creates for s in parsed.statements] == [("a",), ("b",)]

    def test_an_insert_is_refused_with_a_reason(self):
        """A dbt model owns what it writes. INSERT does not."""
        parsed = parse_file("i.sql", "INSERT INTO t SELECT 1", dialect="postgres")
        statement = parsed.statements[0]
        assert not statement.usable
        assert "INSERT" in statement.problem

    def test_something_that_is_not_sql_is_reported_not_raised(self):
        parsed = parse_file("x.sql", "}{ this is not sql", dialect="postgres")
        assert parsed.problem is not None

    def test_an_empty_file_says_so(self):
        assert parse_file("e.sql", "   \n", dialect="postgres").problem == "The file is empty."


class FindingTables(unittest.TestCase):
    """Which names are tables, and which only look like it."""

    def test_a_name_bound_by_with_is_not_a_table(self):
        _parsed, statement = _one(
            "WITH recent AS (SELECT * FROM raw.orders) SELECT * FROM recent"
        )
        assert [".".join(r.parts) for r in statement.refs] == ["raw.orders"]

    def test_a_qualified_name_is_a_table_even_when_a_cte_shares_its_name(self):
        _parsed, statement = _one(
            "WITH orders AS (SELECT 1 AS x) SELECT * FROM raw.orders"
        )
        assert [".".join(r.parts) for r in statement.refs] == ["raw.orders"]

    def test_a_subquery_is_not_a_name(self):
        _parsed, statement = _one("SELECT * FROM (SELECT 1 AS x) t")
        assert statement.refs == ()

    def test_from_inside_a_string_is_a_string(self):
        _parsed, statement = _one("SELECT * FROM raw.a WHERE note = 'FROM raw.decoy'")
        assert [".".join(r.parts) for r in statement.refs] == ["raw.a"]

    def test_from_inside_a_comment_is_a_comment(self):
        _parsed, statement = _one("-- FROM raw.decoy\nSELECT * FROM raw.a")
        assert [".".join(r.parts) for r in statement.refs] == ["raw.a"]

    def test_a_backticked_bigquery_name_is_one_reference(self):
        _parsed, statement = _one(
            "SELECT * FROM `proj.raw.orders`", dialect="bigquery",
        )
        assert [".".join(r.parts) for r in statement.refs] == ["proj.raw.orders"]

    def test_every_join_is_found(self):
        _parsed, statement = _one(
            "SELECT * FROM raw.a a LEFT JOIN raw.b b ON a.id = b.id "
            "CROSS JOIN raw.c"
        )
        assert [".".join(r.parts) for r in statement.refs] == ["raw.a", "raw.b", "raw.c"]


class Rewriting(unittest.TestCase):
    """The promise: nothing but the table names moves."""

    def test_a_reference_becomes_a_ref(self):
        out = _rewritten(
            "SELECT * FROM raw.orders", {"raw.orders": "{{ ref('stg_orders') }}"},
        )
        assert out == "SELECT * FROM {{ ref('stg_orders') }}"

    def test_the_alias_after_a_table_survives(self):
        out = _rewritten(
            "SELECT o.id FROM raw.orders o", {"raw.orders": "{{ ref('x') }}"},
        )
        assert out == "SELECT o.id FROM {{ ref('x') }} o"

    def test_backticks_are_replaced_whole(self):
        out = _rewritten(
            "SELECT * FROM `proj.raw.orders` o",
            {"proj.raw.orders": "{{ source('raw', 'orders') }}"},
            dialect="bigquery",
        )
        assert out == "SELECT * FROM {{ source('raw', 'orders') }} o"

    def test_comments_formatting_and_case_all_survive(self):
        """The thing a parse-and-print round trip cannot promise."""
        original = (
            "-- daily orders, do not touch\n"
            "SELECT\n"
            "    o.id,          -- the order\n"
            "    o.Total\n"
            "FROM raw.orders o   -- source\n"
            "WHERE o.status <> 'x'\n"
        )
        out = _rewritten(original, {"raw.orders": "R"})
        assert out == original.replace("raw.orders", "R").strip()

    def test_dialect_syntax_the_parser_models_badly_is_still_untouched(self):
        """`EXCEPT (col)` is BigQuery's, and a round trip mangles it."""
        original = "SELECT * EXCEPT (secret) FROM `p.raw.t`"
        out = _rewritten(original, {"p.raw.t": "R"}, dialect="bigquery")
        assert out == "SELECT * EXCEPT (secret) FROM R"

    def test_a_reference_with_no_replacement_is_left_alone(self):
        out = _rewritten("SELECT * FROM raw.a, raw.b", {"raw.a": "R"})
        assert out == "SELECT * FROM R, raw.b"

    def test_two_references_to_the_same_table_both_move(self):
        out = _rewritten(
            "SELECT * FROM raw.a UNION ALL SELECT * FROM raw.a", {"raw.a": "R"},
        )
        assert out == "SELECT * FROM R UNION ALL SELECT * FROM R"


class FunctionsAreNotTables(unittest.TestCase):
    """A name after FROM is not always a table.

    `FROM generate_series(1, 10)` and `FROM my_udf(3)` read a function. Point
    dbt at one and it declares a source for a table nobody has, then fails
    looking for it.

    No parser can tell those from the archaic `FROM tbl (c1, c2)` column-alias
    form -- sqlglot gives both the same shape -- so this guesses toward the
    common case. Being wrong the other way is quiet: such a table keeps its
    literal name instead of becoming a source.
    """

    def test_a_set_returning_function_is_not_a_table(self):
        _parsed, statement = _one("SELECT * FROM generate_series(1, 10) AS g")
        assert statement.refs == ()

    def test_a_user_defined_function_is_not_a_table(self):
        _parsed, statement = _one("SELECT * FROM my_udf(3) t")
        assert statement.refs == ()

    def test_unnest_is_not_a_table(self):
        _parsed, statement = _one(
            "SELECT x FROM UNNEST([1, 2, 3]) AS x", dialect="bigquery",
        )
        assert statement.refs == ()

    def test_a_real_table_after_a_function_is_still_found(self):
        _parsed, statement = _one(
            "SELECT * FROM my_udf(1) a JOIN raw.b b ON 1 = 1"
        )
        assert [".".join(r.parts) for r in statement.refs] == ["raw.b"]

    def test_column_aliases_after_AS_do_not_hide_the_table(self):
        """`FROM raw.t AS x(c1, c2)` -- the parenthesis follows the alias."""
        _parsed, statement = _one("SELECT * FROM raw.t AS x(c1, c2)")
        assert [".".join(r.parts) for r in statement.refs] == ["raw.t"]


class TheHardCases(unittest.TestCase):
    """Syntax a real analytics team actually writes."""

    def test_a_semicolon_inside_a_string_does_not_split_a_file(self):
        parsed = parse_file(
            "two.sql",
            "CREATE TABLE a AS SELECT 'x; y' AS n FROM raw.t;\n"
            "CREATE TABLE b AS SELECT 1 AS m FROM a",
            dialect="postgres",
        )
        assert [s.creates for s in parsed.statements] == [("a",), ("b",)]

    def test_a_semicolon_inside_a_dollar_quote_does_not_either(self):
        parsed = parse_file(
            "d.sql", "SELECT $tag$a ; b$tag$ AS note FROM raw.t", dialect="postgres",
        )
        assert len(parsed.statements) == 1
        assert [".".join(r.parts) for r in parsed.statements[0].refs] == ["raw.t"]

    def test_every_reference_in_a_subquery_is_found(self):
        _parsed, statement = _one(
            "SELECT (SELECT max(x) FROM raw.a) AS m FROM raw.b "
            "WHERE id IN (SELECT id FROM raw.a)"
        )
        assert [".".join(r.parts) for r in statement.refs] == [
            "raw.a", "raw.b", "raw.a",
        ]

    def test_a_self_join_replaces_both_sides(self):
        out = _rewritten(
            "SELECT * FROM raw.c a LEFT JOIN raw.c b ON a.id <> b.id",
            {"raw.c": "R"},
        )
        assert out == "SELECT * FROM R a LEFT JOIN R b ON a.id <> b.id"

    def test_a_chain_of_ctes_is_not_mistaken_for_tables(self):
        _parsed, statement = _one(
            "WITH base AS (SELECT * FROM raw.o), "
            "ranked AS (SELECT * FROM base) SELECT * FROM ranked"
        )
        assert [".".join(r.parts) for r in statement.refs] == ["raw.o"]

    def test_a_merge_is_refused(self):
        parsed = parse_file(
            "m.sql",
            "MERGE INTO t USING raw.u u ON t.id = u.id "
            "WHEN MATCHED THEN UPDATE SET t.x = u.x",
            dialect="postgres",
        )
        assert not parsed.statements[0].usable

    def test_a_delete_is_refused(self):
        parsed = parse_file("d.sql", "DELETE FROM t WHERE x < 1", dialect="postgres")
        assert not parsed.statements[0].usable

    def test_bigquery_qualify_and_except_survive(self):
        original = (
            "SELECT * EXCEPT (ingested_at)\n"
            "FROM `p.raw.price`\n"
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY sku ORDER BY v DESC) = 1"
        )
        out = _rewritten(original, {"p.raw.price": "R"}, dialect="bigquery")
        assert out == original.replace("`p.raw.price`", "R")

    def test_window_functions_survive_untouched(self):
        original = (
            "SELECT id,\n"
            "  ROW_NUMBER() OVER (PARTITION BY c ORDER BY d DESC) AS rn\n"
            "FROM raw.o"
        )
        out = _rewritten(original, {"raw.o": "R"})
        assert out == original.replace("raw.o", "R")


class TheAuthorsOwnWords(unittest.TestCase):
    """A comment above a query is the author saying what the model is for.

    The body begins at the SELECT, so a comment above `CREATE TABLE ... AS`
    falls outside it and would simply be lost -- the quietest kind of loss,
    because the file that comes out still looks complete.
    """

    def test_a_comment_above_a_create_is_kept(self):
        _parsed, statement = _one(
            "-- One row per paid order.\nCREATE TABLE stg AS SELECT 1 AS x"
        )
        assert statement.leading_comment == "One row per paid order."

    def test_several_comment_lines_become_one_sentence(self):
        _parsed, statement = _one(
            "-- One row per order.\n-- Paid ones only.\n"
            "CREATE TABLE stg AS SELECT 1 AS x"
        )
        assert statement.leading_comment == "One row per order. Paid ones only."

    def test_a_query_with_nothing_written_above_it_says_nothing(self):
        _parsed, statement = _one("CREATE TABLE stg AS SELECT 1 AS x")
        assert statement.leading_comment == ""

    def test_a_comment_inside_the_body_stays_in_the_body(self):
        parsed, statement = _one(
            "CREATE TABLE stg AS\nSELECT 1 AS x  -- the one\nFROM raw.t"
        )
        assert statement.leading_comment == ""
        body = parsed.text[statement.body_start:statement.body_end]
        assert "-- the one" in body
