"""The model generator: a form in, ordinary dbt files out.

The generator exists so a non-specialist can produce their first model without
first learning dbt's filing system. That only holds if what it writes is
genuinely ordinary -- a file someone can edit by hand afterwards, in the place
dbt looks for it -- and if adding a second model never damages the first.
"""

from __future__ import annotations

import unittest

import yaml

from app.core.errors import ValidationError
from app.transforms import generator, scaffold


COLUMNS = [
    {"name": "sku", "alias": "product_sku", "selected": True,
     "unique": True, "not_null": True},
    {"name": "name", "alias": "product_name", "selected": True, "not_null": True},
    {"name": "category", "selected": True},
    {"name": "updated_at", "selected": False},
]


class GeneratedSql(unittest.TestCase):
    def sql(self, **overrides):
        kwargs = dict(
            source_name="shop", table_name="products",
            columns=[c for c in COLUMNS if c.get("selected", True)],
            materialized="view",
        )
        kwargs.update(overrides)
        return scaffold.staging_model_sql(**kwargs)

    def test_it_selects_from_a_source_not_a_bare_table(self):
        # A staging model that reads the warehouse directly is the mistake this
        # is meant to prevent: dbt cannot build a lineage graph from it.
        self.assertIn("{{ source('shop', 'products') }}", self.sql())
        self.assertNotIn("from shop.products", self.sql())

    def test_aliases_are_rendered_and_unaliased_columns_are_not(self):
        sql = self.sql()
        self.assertIn("sku", sql)
        self.assertIn("as product_sku", sql)
        self.assertRegex(sql, r"\n    category,?\n")
        self.assertNotIn("as category", sql)

    def test_unselected_columns_do_not_appear(self):
        self.assertNotIn("updated_at", self.sql())

    def test_the_last_column_has_no_trailing_comma(self):
        # A trailing comma before FROM is a syntax error in every warehouse.
        body = self.sql().split("select\n")[1].split("from ")[0]
        self.assertFalse(body.rstrip().endswith(","), body)

    def test_materialization_is_declared(self):
        self.assertIn("materialized='view'", self.sql())
        self.assertIn("materialized='table'", self.sql(materialized="table"))

    def test_an_unknown_materialization_is_refused(self):
        with self.assertRaises(ValidationError):
            self.sql(materialized="incremental_but_wrong")

    def test_no_columns_is_refused(self):
        with self.assertRaises(ValidationError):
            self.sql(columns=[])


class GeneratedYaml(unittest.TestCase):
    def test_tests_land_on_the_alias_not_the_source_name(self):
        # The model exposes `product_sku`; a test naming `sku` would not resolve.
        entry = scaffold.model_yaml_entry(
            model_name="stg_products",
            columns=[c for c in COLUMNS if c.get("selected", True)],
        )
        names = [column["name"] for column in entry["columns"]]
        self.assertIn("product_sku", names)
        self.assertNotIn("sku", names)

    def test_source_tests_use_the_real_column_name(self):
        # The source is the warehouse table, where the column is still `sku`.
        entry = scaffold.source_yaml_entry(
            source_name="shop", schema_name="shop", table_name="products",
            columns=[c for c in COLUMNS if c.get("selected", True)],
        )
        names = [column["name"] for column in entry["tables"][0]["columns"]]
        self.assertIn("sku", names)
        self.assertNotIn("product_sku", names)

    def test_a_column_with_no_tests_is_left_out_of_the_yaml(self):
        entry = scaffold.model_yaml_entry(
            model_name="stg_products",
            columns=[{"name": "category", "selected": True}],
        )
        self.assertNotIn("columns", entry)


class YamlMerging(unittest.TestCase):
    """Adding a model must not damage what is already declared."""

    EXISTING = yaml.safe_dump({
        "version": 2,
        "sources": [{
            "name": "shop",
            "description": "Đã có mô tả từ trước.",
            "schema": "shop",
            "tables": [{"name": "customers", "description": "Giữ nguyên."}],
        }],
    }, allow_unicode=True).encode("utf-8")

    def test_an_existing_table_keeps_its_hand_written_description(self):
        merged = generator._merge_source(
            generator._load(self.EXISTING),
            scaffold.source_yaml_entry(
                source_name="shop", schema_name="shop", table_name="products",
                columns=[{"name": "sku", "unique": True}],
            ),
        )
        tables = merged["sources"][0]["tables"]
        customers = next(t for t in tables if t["name"] == "customers")
        self.assertEqual(customers["description"], "Giữ nguyên.")
        self.assertEqual(merged["sources"][0]["description"], "Đã có mô tả từ trước.")
        self.assertEqual({t["name"] for t in tables}, {"customers", "products"})

    def test_regenerating_the_same_table_replaces_it_rather_than_duplicating(self):
        document = generator._load(self.EXISTING)
        entry = scaffold.source_yaml_entry(
            source_name="shop", schema_name="shop", table_name="products",
            columns=[{"name": "sku", "unique": True}],
        )
        document = generator._merge_source(document, entry)
        document = generator._merge_source(document, entry)
        names = [t["name"] for t in document["sources"][0]["tables"]]
        self.assertEqual(names.count("products"), 1)

    def test_a_second_source_is_added_beside_the_first(self):
        merged = generator._merge_source(
            generator._load(self.EXISTING),
            scaffold.source_yaml_entry(
                source_name="crm", schema_name="crm", table_name="leads",
                columns=[{"name": "id", "unique": True}],
            ),
        )
        self.assertEqual({s["name"] for s in merged["sources"]}, {"shop", "crm"})

    def test_a_model_entry_is_replaced_not_duplicated(self):
        document = {"version": 2}
        for _ in range(2):
            document = generator._merge_model(
                document,
                scaffold.model_yaml_entry(
                    model_name="stg_products",
                    columns=[{"name": "sku", "unique": True}],
                ),
            )
        self.assertEqual(len(document["models"]), 1)

    def test_an_empty_file_becomes_a_valid_dbt_document(self):
        self.assertEqual(generator._load(None), {"version": 2})
        self.assertEqual(generator._load(b""), {"version": 2})

    def test_unreadable_yaml_is_refused_rather_than_overwritten(self):
        # Silently replacing a file somebody hand-wrote would lose their work.
        with self.assertRaises(ValidationError):
            generator._load(b"this: is: not: valid: yaml:\n  - [")


class Identifiers(unittest.TestCase):
    def test_a_usable_name_passes_through_lowercased(self):
        self.assertEqual(scaffold.validate_identifier("Stg_Orders", field="x"),
                         "stg_orders")

    def test_names_dbt_would_reject_are_refused(self):
        for bad in ("2_orders", "stg orders", "stg-orders", "", "stg;drop"):
            with self.assertRaises(ValidationError, msg=bad):
                scaffold.validate_identifier(bad, field="x")


if __name__ == "__main__":
    unittest.main()
