"""Artifact parsers: manifest, run_results, sources, catalog.

These enforce the rework's central claim -- that AppBI reads dbt's answers
rather than computing its own. The most load-bearing assertions here are the
negative ones: lineage comes from `parent_map`, an unknown config survives, and
an unsupported schema version fails loudly instead of producing a resource tree
that is quietly missing resources.
"""

from __future__ import annotations

import unittest

from app.core.errors import ValidationError
from app.transforms.artifacts.catalog import parse_catalog
from app.transforms.artifacts.manifest import parse_manifest
from app.transforms.artifacts.run_results import parse_run_results
from app.transforms.artifacts.schema_version import SUPPORTED, artifact_version
from app.transforms.artifacts.sources import parse_sources


def metadata(kind: str, version: int = None, **extra):
    version = version or SUPPORTED[kind][0]
    return {
        "dbt_schema_version": f"https://schemas.getdbt.com/dbt/{kind}/v{version}.json",
        "dbt_version": "1.12.3",
        "adapter_type": "bigquery",
        "generated_at": "2026-09-02T10:00:00.000000Z",
        "invocation_id": "abc-123",
        "project_name": "acme",
        **extra,
    }


def manifest_document(**overrides):
    document = {
        "metadata": metadata("manifest"),
        "nodes": {
            "model.acme.stg_orders": {
                "resource_type": "model",
                "name": "stg_orders",
                "package_name": "acme",
                "original_file_path": "models/staging/stg_orders.sql",
                "patch_path": "acme://models/staging/_staging.yml",
                "database": "proj", "schema": "analytics", "alias": "stg_orders",
                "relation_name": "`proj`.`analytics`.`stg_orders`",
                "description": "Staged orders.",
                "tags": ["daily"],
                "config": {"materialized": "view", "enabled": True, "tags": ["daily"]},
                "columns": {
                    "order_id": {"name": "order_id", "description": "PK",
                                 "data_type": "int64"},
                },
                "checksum": {"name": "sha256", "checksum": "aa"},
                "compiled_code": "select 1",
                "raw_code": "select 1",
            },
            "model.acme.fct_orders": {
                "resource_type": "model",
                "name": "fct_orders",
                "package_name": "acme",
                "original_file_path": "models/marts/fct_orders.sql",
                "config": {
                    "materialized": "incremental",
                    "unique_key": "order_id",
                    # The keys AppBI has no form for. They must come back whole.
                    "contract": {"enforced": True},
                    "some_future_config": "preserve me",
                    "meta": {"owner": "finance"},
                },
            },
            "test.acme.not_null_stg_orders_order_id.abc": {
                "resource_type": "test",
                "name": "not_null_stg_orders_order_id",
                "package_name": "acme",
                "attached_node": "model.acme.stg_orders",
                "column_name": "order_id",
                "config": {},
                "depends_on": {
                    "nodes": ["model.acme.stg_orders"],
                    "macros": ["macro.dbt.test_not_null"],
                },
            },
        },
        "sources": {
            "source.acme.shopify.orders": {
                "resource_type": "source",
                "name": "orders",
                "source_name": "shopify",
                "identifier": "orders_raw",
                "package_name": "acme",
                "schema": "raw_shopify",
                "database": "proj",
                "config": {"enabled": True},
            },
        },
        "macros": {
            "macro.acme.cents_to_dollars": {
                "resource_type": "macro", "name": "cents_to_dollars",
                "package_name": "acme", "config": {},
            },
            "macro.dbt_utils.star": {
                "resource_type": "macro", "name": "star",
                "package_name": "dbt_utils", "config": {},
            },
        },
        "exposures": {},
        "parent_map": {
            "model.acme.fct_orders": ["model.acme.stg_orders"],
            "model.acme.stg_orders": ["source.acme.shopify.orders"],
            "test.acme.not_null_stg_orders_order_id.abc": [
                "model.acme.stg_orders", "macro.dbt.test_not_null",
            ],
            "source.acme.shopify.orders": [],
        },
        "child_map": {
            "model.acme.stg_orders": ["model.acme.fct_orders"],
            "source.acme.shopify.orders": ["model.acme.stg_orders"],
        },
        "selectors": {
            "nightly": {"description": "The nightly set", "default": False},
        },
        "disabled": {
            "model.acme.retired": [{
                "resource_type": "model", "name": "retired", "package_name": "acme",
                "original_file_path": "models/retired.sql",
                "config": {"enabled": False, "materialized": "table"},
            }],
        },
    }
    document.update(overrides)
    return document


class SchemaVersionDispatch(unittest.TestCase):
    def test_reads_the_stamped_version(self):
        version = artifact_version({"metadata": metadata("manifest")}, "manifest")
        self.assertEqual(version.dbt_version, "1.12.3")
        self.assertEqual(version.adapter_type, "bigquery")
        self.assertEqual(version.invocation_id, "abc-123")

    def test_unsupported_version_fails_loudly(self):
        # A reader that "does its best" with an unknown schema produces a tree
        # that is quietly missing resources, and nothing in the UI says so.
        with self.assertRaises(ValidationError) as caught:
            artifact_version({"metadata": metadata("manifest", version=99)}, "manifest")
        self.assertEqual(
            caught.exception.code, "TRANSFORM_ARTIFACT_SCHEMA_UNSUPPORTED",
        )

    def test_wrong_artifact_kind_is_caught(self):
        with self.assertRaises(ValidationError) as caught:
            artifact_version({"metadata": metadata("run-results")}, "manifest")
        self.assertEqual(caught.exception.code, "TRANSFORM_ARTIFACT_KIND_MISMATCH")

    def test_missing_metadata_is_caught(self):
        with self.assertRaises(ValidationError):
            artifact_version({"nodes": {}}, "manifest")

    def test_unrecognised_schema_url_is_caught(self):
        with self.assertRaises(ValidationError):
            artifact_version(
                {"metadata": {"dbt_schema_version": "not-a-url"}}, "manifest",
            )


class ManifestParsing(unittest.TestCase):
    def setUp(self):
        self.manifest = parse_manifest(manifest_document())

    def test_reads_every_section(self):
        counts = self.manifest.counts()
        self.assertEqual(counts["model"], 3)  # two enabled plus one disabled
        self.assertEqual(counts["source"], 1)
        self.assertEqual(counts["macro"], 2)
        self.assertEqual(counts["test"], 1)

    def test_unknown_config_survives_whole(self):
        # The round-trip rule. A config AppBI has no form for must still be
        # visible, not silently dropped because the product does not model it.
        config = self.manifest.resources["model.acme.fct_orders"].config
        self.assertEqual(config["contract"], {"enforced": True})
        self.assertEqual(config["some_future_config"], "preserve me")
        self.assertEqual(config["meta"], {"owner": "finance"})
        self.assertEqual(config["unique_key"], "order_id")

    def test_lineage_comes_from_parent_map(self):
        self.assertEqual(
            self.manifest.parent_map["model.acme.fct_orders"],
            ["model.acme.stg_orders"],
        )
        self.assertEqual(
            self.manifest.child_map["source.acme.shopify.orders"],
            ["model.acme.stg_orders"],
        )

    def test_disabled_resources_are_kept_and_marked(self):
        # A model that stopped building because a package set `enabled: false`
        # is exactly what somebody is hunting for; omitting it makes it
        # unfindable.
        retired = self.manifest.resources["model.acme.retired"]
        self.assertFalse(retired.enabled)
        self.assertEqual(retired.name, "retired")

    def test_source_keeps_its_two_part_name(self):
        source = self.manifest.resources["source.acme.shopify.orders"]
        self.assertEqual(source.source_name, "shopify")
        self.assertEqual(source.identifier, "orders_raw")
        self.assertEqual(source.schema, "raw_shopify")

    def test_tests_resolve_to_the_resource_they_test(self):
        tests = self.manifest.tests_for("model.acme.stg_orders")
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0].column_name, "order_id")

    def test_attached_node_falls_back_to_dependencies(self):
        # Older manifests do not carry `attached_node`; the test's own
        # dependencies name the subject.
        document = manifest_document()
        del document["nodes"]["test.acme.not_null_stg_orders_order_id.abc"]["attached_node"]
        parsed = parse_manifest(document)
        self.assertEqual(len(parsed.tests_for("model.acme.stg_orders")), 1)

    def test_installed_packages_are_derived_from_macro_packages(self):
        self.assertEqual(self.manifest.packages, ["dbt_utils"])

    def test_selectors_are_surfaced(self):
        self.assertEqual(self.manifest.selectors[0]["name"], "nightly")

    def test_project_name(self):
        self.assertEqual(self.manifest.project_name, "acme")

    def test_columns_keep_declaration_order(self):
        columns = self.manifest.resources["model.acme.stg_orders"].columns
        self.assertEqual([item["name"] for item in columns], ["order_id"])
        self.assertEqual(columns[0]["description"], "PK")

    def test_checksum_is_unwrapped(self):
        self.assertEqual(
            self.manifest.resources["model.acme.stg_orders"].checksum, "aa",
        )


class RunResultsParsing(unittest.TestCase):
    def document(self):
        return {
            "metadata": metadata("run-results"),
            "elapsed_time": 12.5,
            "args": {"select": ["fct_orders"]},
            "results": [
                {
                    "unique_id": "model.acme.stg_orders", "status": "success",
                    "execution_time": 1.5,
                    "adapter_response": {"rows_affected": 100, "bytes_processed": 2048},
                },
                {
                    "unique_id": "model.acme.fct_orders", "status": "error",
                    "execution_time": 0.4,
                    "message": "Syntax error at line 12",
                    "adapter_response": {},
                },
                {
                    "unique_id": "test.acme.not_null_stg_orders_order_id.abc",
                    "status": "fail", "failures": 3, "execution_time": 0.2,
                    "adapter_response": {},
                },
                {
                    "unique_id": "model.acme.downstream", "status": "skipped",
                    "adapter_response": {},
                },
            ],
        }

    def test_counts_separate_tests_from_models(self):
        names = {
            unique_id: (resource.name, resource.resource_type)
            for unique_id, resource in parse_manifest(manifest_document()).resources.items()
        }
        parsed = parse_run_results(self.document(), names=names)
        counts = parsed.counts()
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["failed"], 2)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["tests_failed"], 1)

    def test_names_come_from_the_manifest(self):
        # Without them the Results table shows `model.acme.fct_orders` where a
        # person expects `fct_orders`.
        names = {"model.acme.fct_orders": ("fct_orders", "model")}
        parsed = parse_run_results(self.document(), names=names)
        found = next(r for r in parsed.results if r.unique_id == "model.acme.fct_orders")
        self.assertEqual(found.name, "fct_orders")

    def test_adapter_response_is_kept(self):
        parsed = parse_run_results(self.document())
        first = parsed.results[0]
        self.assertEqual(first.rows_affected, 100)
        self.assertEqual(first.bytes_processed, 2048)

    def test_line_number_is_extracted_for_click_to_line(self):
        parsed = parse_run_results(self.document())
        failure = parsed.first_failure()
        self.assertEqual(failure.location.get("line"), 12)

    def test_rows_affected_sums_across_nodes(self):
        self.assertEqual(parse_run_results(self.document()).rows_affected(), 100)


class SourcesParsing(unittest.TestCase):
    def test_freshness_and_worst_status(self):
        parsed = parse_sources({
            "metadata": metadata("sources"),
            "elapsed_time": 2.0,
            "results": [
                {
                    "unique_id": "source.acme.shopify.orders", "status": "pass",
                    "max_loaded_at": "2026-09-02T09:00:00Z",
                    "snapshotted_at": "2026-09-02T10:00:00Z",
                    "age": 3600.0,
                    "criteria": {
                        "warn_after": {"count": 12, "period": "hour"},
                        "error_after": {"count": 24, "period": "hour"},
                    },
                },
                {
                    "unique_id": "source.acme.shopify.customers", "status": "error",
                    "message": "Relation not found",
                },
            ],
        })
        orders = parsed.results["source.acme.shopify.orders"]
        self.assertEqual(orders.age_seconds, 3600.0)
        self.assertEqual(orders.warn_after, {"count": 12, "period": "hour"})
        self.assertFalse(orders.failing)
        self.assertTrue(parsed.results["source.acme.shopify.customers"].failing)
        # One erroring source makes the set erroring; that is the summary a
        # person wants, not an average.
        self.assertEqual(parsed.worst_status(), "ERROR")


class CatalogParsing(unittest.TestCase):
    def test_columns_come_back_in_warehouse_order(self):
        parsed = parse_catalog({
            "metadata": metadata("catalog"),
            "nodes": {
                "model.acme.stg_orders": {
                    "metadata": {
                        "database": "proj", "schema": "analytics",
                        "name": "stg_orders", "type": "VIEW", "owner": "svc",
                    },
                    "columns": {
                        "amount": {"name": "amount", "type": "NUMERIC", "index": 2},
                        "order_id": {"name": "order_id", "type": "INT64", "index": 1},
                    },
                    "stats": {
                        "has_stats": {"id": "has_stats", "include": False,
                                      "label": "Has stats?", "value": True},
                        "num_rows": {"id": "num_rows", "include": True,
                                     "label": "Rows", "value": 1000},
                    },
                },
            },
            "sources": {},
        })
        relation = parsed.relations["model.acme.stg_orders"]
        self.assertEqual([c.name for c in relation.columns], ["order_id", "amount"])
        self.assertEqual(relation.columns[0].type, "INT64")
        # `has_stats` is bookkeeping about the stats, not a stat.
        self.assertIn("num_rows", relation.stats)
        self.assertNotIn("has_stats", relation.stats)


if __name__ == "__main__":
    unittest.main()
