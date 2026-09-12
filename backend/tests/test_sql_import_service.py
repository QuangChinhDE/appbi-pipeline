"""The import has no memory, and that is the security property.

The review is stateless: analysis hands back what it found, the browser hands
it back with whatever a person changed, and the apply works everything out
again from the SQL. So the interesting question is not what a well-behaved
client sends -- it is what a client sends that is trying to get somewhere it
should not, or that is simply stale.

The answer these tests pin down: the decisions carry preferences and nothing
else. A client cannot send a dependency graph, cannot send a file path, and
cannot make a model read from something the SQL does not read from.
"""

from __future__ import annotations

import unittest

import pytest
import yaml

pytest.importorskip("sqlglot")

from app.core.errors import ValidationError  # noqa: E402
from app.transforms.sql_import import service  # noqa: E402

UPLOAD = {
    "stg.sql": "CREATE TABLE stg_orders AS SELECT id, total FROM raw.orders",
    "daily.sql": "SELECT id FROM stg_orders",
}


def _render(files=None, decisions=None, **kwargs):
    # `files` is checked against None rather than falsiness: `{}` is a case
    # with its own test, and `or` would quietly turn it into the default.
    return service.render(
        UPLOAD if files is None else files, decisions or [],
        adapter=kwargs.pop("adapter", "postgres"),
        source_schema=kwargs.pop("source_schema", "raw"),
        **kwargs,
    )


class WhatAClientCannotDo(unittest.TestCase):
    def test_the_dependency_graph_comes_from_the_sql_not_the_request(self):
        """There is no field for it, and this is why there is no field for it."""
        out = _render(decisions=[
            {"key": "daily.sql#0", "name": "daily", "depends_on": ["something_else"]},
        ])
        body = out.files["models/marts/daily.sql"]
        assert "{{ ref('stg_orders') }}" in body
        assert "something_else" not in body

    def test_a_name_that_is_not_an_identifier_is_refused(self):
        out = _render(decisions=[{"key": "stg.sql#0", "name": "../../etc/passwd"}])
        assert "models/staging/stg_orders.sql" in out.files

    def test_a_layer_nobody_offers_falls_back(self):
        out = _render(decisions=[
            {"key": "stg.sql#0", "name": "stg_orders", "layer": "../../.."},
        ])
        assert "models/staging/stg_orders.sql" in out.files

    def test_a_materialization_nobody_offers_falls_back(self):
        out = _render(decisions=[
            {"key": "stg.sql#0", "name": "stg_orders", "materialized": "incremental"},
        ])
        assert "materialized='view'" in out.files["models/staging/stg_orders.sql"]

    def test_a_decision_for_a_key_that_does_not_exist_is_ignored(self):
        out = _render(decisions=[{"key": "invented#9", "name": "sneaky"}])
        assert "models/staging/sneaky.sql" not in out.files

    def test_a_candidate_with_no_decision_still_gets_written(self):
        """A stale review must not silently drop a query somebody uploaded."""
        out = _render(decisions=[{"key": "stg.sql#0", "name": "stg_orders"}])
        assert "models/marts/daily.sql" in out.files


class Refusals(unittest.TestCase):
    def test_an_empty_upload_is_refused(self):
        with pytest.raises(ValidationError):
            _render(files={})

    def test_too_many_files_is_refused(self):
        with pytest.raises(ValidationError):
            _render(files={f"f{i}.sql": "SELECT 1 AS x" for i in range(60)})

    def test_one_enormous_file_is_refused(self):
        with pytest.raises(ValidationError):
            _render(files={"big.sql": "SELECT 1 AS x -- " + "x" * 600_000})

    def test_a_loop_is_refused_rather_than_handed_to_dbt(self):
        with pytest.raises(ValidationError) as caught:
            _render(files={
                "a.sql": "CREATE TABLE a AS SELECT * FROM b",
                "b.sql": "CREATE TABLE b AS SELECT * FROM a",
            })
        assert caught.value.code == "SQL_IMPORT_CYCLE"

    def test_an_upload_with_nothing_convertible_says_so(self):
        with pytest.raises(ValidationError) as caught:
            _render(files={"x.sql": "INSERT INTO t SELECT 1"})
        assert caught.value.code == "SQL_IMPORT_NOTHING_TO_IMPORT"


class IntoAnExistingProject(unittest.TestCase):
    def test_a_taken_path_is_not_overwritten(self):
        out = _render(existing_paths={"models/staging/stg_orders.sql"})
        assert "models/staging/stg_orders_2.sql" in out.files
        assert "models/staging/stg_orders.sql" not in out.files

    def test_the_projects_own_source_name_is_used(self):
        """AppBI scaffolds the raw schema under the name `raw`. A model saying
        `source('landing', ...)` would compile to a source that is not there."""
        existing = yaml.safe_dump({
            "version": 2,
            "sources": [{"name": "raw", "schema": "landing", "tables": []}],
        })
        out = _render(
            files={"s.sql": "SELECT * FROM landing.orders"},
            source_schema="landing", existing_sources_yml=existing,
        )
        assert "{{ source('raw', 'orders') }}" in out.files["models/staging/s.sql"]


class Dialects(unittest.TestCase):
    def test_bigquery_is_read_as_bigquery(self):
        out = _render(
            files={"s.sql": "SELECT * EXCEPT (secret) FROM `proj.raw.orders`"},
            adapter="bigquery",
        )
        body = out.files["models/staging/s.sql"]
        assert "SELECT * EXCEPT (secret)" in body
        assert "{{ source('raw', 'orders') }}" in body

    def test_an_adapter_nobody_knows_is_read_as_postgres(self):
        assert service.dialect_for("duckdb") == "postgres"
        assert service.dialect_for(None) == "postgres"


class ReadingWhatWasUploaded(unittest.TestCase):
    """Two ways a list of files becomes a lossy dict, both closed."""

    def test_two_files_with_one_name_both_survive(self):
        """Different folders, or the same file picked twice. Keying a dict by
        name would keep the last and lose the other without saying so."""
        out = service.normalise([
            ("orders.sql", "SELECT 1 AS a"),
            ("orders.sql", "SELECT 2 AS b"),
            ("orders.sql", "SELECT 3 AS c"),
        ])
        assert list(out) == ["orders.sql", "orders (2).sql", "orders (3).sql"]
        assert list(out.values()) == ["SELECT 1 AS a", "SELECT 2 AS b", "SELECT 3 AS c"]

    def test_a_byte_order_mark_is_not_sql(self):
        """Windows editors add one. sqlglot reads it as part of the first
        token and the whole file fails to parse for an invisible reason."""
        out = service.normalise([("q.sql", "﻿SELECT 1 AS x")])
        assert out["q.sql"].startswith("SELECT")
        parsed = _render(files=out, decisions=[])
        assert "models/staging/q.sql" in parsed.files

    def test_a_nameless_file_still_gets_a_name(self):
        assert list(service.normalise([("", "SELECT 1 AS x")])) == ["query.sql"]

    def test_a_path_in_a_name_cannot_become_a_path(self):
        out = service.normalise([("../../etc/passwd.sql", "SELECT 1 AS x")])
        emitted = _render(files=out, decisions=[])
        assert all(path.startswith("models/") for path in emitted.files)
        assert not any(".." in path for path in emitted.files)
