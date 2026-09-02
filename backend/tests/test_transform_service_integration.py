from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.enums import RunStatus, TriggerType
from app.models.identity import Workspace
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.models.transform import DataAsset, Transform, TransformInput, TransformRun
from app.services.transforms import enqueue_after_upstream


@unittest.skipUnless(
    os.getenv("RUN_TRANSFORM_INTEGRATION_TESTS") == "1",
    "requires a migrated disposable AppBI database",
)
class TransformServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_after_upstream_is_idempotent_and_refreshes_asset(self) -> None:
        suffix = uuid.uuid4().hex
        async with SessionLocal() as session:
            workspace = Workspace(name="Transform integration", slug=f"transform-test-{suffix}")
            session.add(workspace)
            await session.flush()

            source = Source(
                workspace_id=workspace.id,
                name="Integration source",
                connector_key="source-postgres",
                configuration_json={},
            )
            destination = Destination(
                workspace_id=workspace.id,
                name="Integration destination",
                connector_key="destination-postgres",
                configuration_json={},
            )
            session.add_all([source, destination])
            await session.flush()

            pipeline = Pipeline(
                workspace_id=workspace.id,
                name="Integration pipeline",
                source_id=source.id,
                destination_id=destination.id,
            )
            session.add(pipeline)
            await session.flush()

            transform = Transform(
                workspace_id=workspace.id,
                destination_id=destination.id,
                name="Integration transform",
                default_schema="analytics",
                execution_trigger="AFTER_UPSTREAM",
                dbt_core_version="1.12.3",
                dbt_adapter_name="dbt-postgres",
                dbt_adapter_version="1.11.0",
            )
            asset = DataAsset(
                workspace_id=workspace.id,
                destination_id=destination.id,
                catalog_name="warehouse",
                schema_name="raw",
                relation_name="orders",
                owner_type="PIPELINE",
                owner_resource_id=pipeline.id,
                pipeline_id=pipeline.id,
                physical_identity=suffix,
                resolution_status="READY",
            )
            session.add_all([transform, asset])
            await session.flush()
            session.add(TransformInput(
                transform_id=transform.id,
                data_asset_id=asset.id,
                source_name="src_raw",
            ))

            pipeline_run = PipelineRun(
                workspace_id=workspace.id,
                pipeline_id=pipeline.id,
                trigger_type=TriggerType.MANUAL,
                status=RunStatus.SUCCEEDED,
            )
            session.add(pipeline_run)
            await session.flush()

            first = await enqueue_after_upstream(session, pipeline_run)
            second = await enqueue_after_upstream(session, pipeline_run)
            count = await session.scalar(select(func.count()).select_from(TransformRun).where(
                TransformRun.workspace_id == workspace.id,
                TransformRun.idempotency_key
                == f"upstream:{pipeline_run.id}:{transform.id}",
            ))

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(count, 1)
            self.assertEqual(first[0].trigger_type, TriggerType.AFTER_UPSTREAM)
            self.assertIsNotNone(asset.fresh_at)
            await session.rollback()


if __name__ == "__main__":
    unittest.main()
