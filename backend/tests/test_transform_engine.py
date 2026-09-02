from __future__ import annotations

import json
import uuid
import unittest

from app.models.transform import (
    DataAsset,
    Transform,
    TransformInput,
    TransformModel,
    TransformTest,
)
from app.transformation.base import TransformationRequest
from app.transformation.dbt_core import DbtCoreAdapter
from app.transformation.project import generate_project


def transform_fixture() -> tuple[Transform, list[TransformModel], list[TransformInput]]:
    transform = Transform(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        destination_id=uuid.uuid4(),
        name="Sales Transform",
        default_schema="analytics",
        dbt_core_version="1.12.3",
        dbt_adapter_name="dbt-postgres",
        dbt_adapter_version="1.11.0",
    )
    asset = DataAsset(
        id=uuid.uuid4(),
        workspace_id=transform.workspace_id,
        destination_id=transform.destination_id,
        catalog_name="warehouse",
        schema_name="raw",
        relation_name="deals",
        owner_type="WAREHOUSE",
        owner_resource_id=uuid.uuid4(),
        physical_identity="asset-hash",
        resolution_status="READY",
        schema_metadata={"columns": [{"name": "id"}, {"name": "status"}]},
    )
    transform_input = TransformInput(
        transform_id=transform.id,
        data_asset_id=asset.id,
        source_name="src_raw",
        required=True,
    )
    transform_input.asset = asset
    model = TransformModel(
        id=uuid.uuid4(),
        transform_id=transform.id,
        name="stg_deals",
        layer="STAGING",
        materialization="VIEW",
        sql="select * from {{ source('src_raw', 'deals') }}",
        tags=[],
        config_json={},
        version=1,
    )
    model.tests = [
        TransformTest(
            model_id=model.id,
            column_name="id",
            rule="NOT_NULL",
            severity="ERROR",
            config_json={},
        )
    ]
    return transform, [model], [transform_input]


class TransformEngineTests(unittest.TestCase):
    def test_project_is_portable_and_uses_exact_asset_identity(self) -> None:
        transform, models, inputs = transform_fixture()
        project = generate_project(transform, models, inputs)

        self.assertIn("warehouse", project.files["models/sources.yml"])
        self.assertIn("schema: raw", project.files["models/sources.yml"])
        self.assertIn("name: deals", project.files["models/sources.yml"])
        self.assertIn("asset-hash", project.files["models/sources.yml"])
        self.assertIn("not_null", project.files["models/schema.yml"])
        self.assertIn(
            "generate_schema_name", project.files["macros/generate_schema_name.sql"]
        )
        # The probe name is no longer fixed -- it is chosen per run so that a
        # check can never drop a relation that already holds the name.
        self.assertIn(
            "appbi_validate_write", project.files["macros/appbi_validation.sql"]
        )
        self.assertNotIn(
            "__appbi_transform_probe", project.files["macros/appbi_validation.sql"]
        )
        self.assertTrue(
            all("password" not in content.lower() for content in project.files.values())
        )

    def test_preview_command_excludes_indirect_tests(self) -> None:
        request = TransformationRequest(
            run_id=str(uuid.uuid4()),
            operation="PREVIEW",
            project_files={},
            profile={},
            selected_model="stg_deals",
        )
        command = DbtCoreAdapter.__new__(DbtCoreAdapter)._command(request)

        self.assertEqual(command[0], "dbt")
        self.assertEqual(command[command.index("--select") + 1], "stg_deals")
        self.assertEqual(command[command.index("--indirect-selection") + 1], "empty")
        self.assertEqual(command[command.index("--output") + 1], "json")

    def test_validate_probes_every_declared_schema_and_relation(self) -> None:
        request = TransformationRequest(
            run_id=str(uuid.uuid4()),
            operation="VALIDATE",
            project_files={},
            profile={},
            output_schema="analytics",
            validate_schemas=["analytics", "analytics_mart"],
            validate_relations=[
                {"database": "warehouse", "schema": "raw", "identifier": "deals"},
            ],
        )
        command = DbtCoreAdapter.__new__(DbtCoreAdapter)._command(request)
        args = json.loads(command[command.index("--args") + 1])

        # An input attached but not yet referenced by SQL still has to be
        # read-probed, and a per-model schema override still has to be
        # write-probed -- neither is visible in the compiled graph.
        self.assertEqual(args["schema_names"], ["analytics", "analytics_mart"])
        self.assertEqual(args["relations"][0]["identifier"], "deals")

    def test_compile_failure_reports_model_and_line(self) -> None:
        log = (
            "01:02:03  Running with dbt=1.12.3\n"
            "Compilation Error in model fct_sales (models/core/fct_sales.sql)\n"
            "  Model 'model.p.fct_sales' depends on a node named 'stg_dealz'"
            " which was not found\n"
            "  line 12\n"
        )
        summary, technical, location = DbtCoreAdapter._error(None, log)

        # A compile failure writes no run_results.json, so the log is the only
        # place the model and line exist.
        self.assertEqual(location["name"], "fct_sales")
        self.assertEqual(location["line"], 12)
        self.assertIn("fct_sales", summary)
        self.assertNotEqual(summary, "dbt could not complete this operation.")
        self.assertIn("stg_dealz", technical)

    def test_preview_survives_log_lines_around_the_json(self) -> None:
        stdout = (
            "01:02:03  Running with dbt=1.12.3\n"
            "01:02:04  Found 2 models\n"
            '{\n  "show": [{"id": 1}]\n}\n'
            "01:02:05  Done.\n"
        )
        # stderr is folded into stdout, so a single log line must not defeat
        # the parser and silently blank the preview.
        self.assertEqual(DbtCoreAdapter._preview(stdout), {"show": [{"id": 1}]})

    def test_preview_parser_accepts_pretty_json_and_redacts_secrets(self) -> None:
        payload = """{
          "node": "stg_deals",
          "show": [{"id": 1}, {"id": 2}]
        }"""
        parsed = DbtCoreAdapter._preview(payload)

        self.assertEqual(parsed, {"node": "stg_deals", "show": [{"id": 1}, {"id": 2}]})
        self.assertEqual(
            DbtCoreAdapter._redact(
                "password=very-secret and token=abcd", ["very-secret", "abcd"]
            ),
            "password=[REDACTED] and token=[REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
