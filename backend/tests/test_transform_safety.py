"""The rules a release review blocked on, kept as tests rather than as prose.

Each of these encodes a defect that reached a build: a connection check that
deleted a customer's table, a publish that made unverified code live, and an
upstream trigger that ran draft code unattended. They are cheap, offline
checks -- no warehouse, no database -- because the point is that a regression
fails the build rather than a review.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone

from app.models.enums import HealthLevel, RunStatus
from app.models.transform import Transform, TransformModel, TransformRelease, TransformRun
from app.transformation.base import TransformationRequest
from app.transformation.dbt_core import DbtCoreAdapter
from app.transformation.project import VALIDATION_MACRO, generate_project


def _transform() -> Transform:
    return Transform(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), destination_id=None,
        warehouse_connection_id=uuid.uuid4(),
        name="Sales", default_schema="analytics", dbt_core_version="1.12.3",
        dbt_adapter_name="dbt-bigquery", dbt_adapter_version="1.12.0",
    )


class ConnectionCheckIsNonDestructiveTests(unittest.TestCase):
    """Validation must never delete something it did not create."""

    def test_macro_never_drops_a_relation_it_found(self) -> None:
        # The old macro looked the probe up by a fixed name and dropped it if
        # it existed, so a customer relation that happened to be called
        # `__appbi_transform_probe` was destroyed by pressing Check connection.
        self.assertNotIn("drop_relation(existing_probe)", VALIDATION_MACRO)
        self.assertNotIn("'__appbi_transform_probe'", VALIDATION_MACRO)

    def test_macro_only_drops_the_relation_it_created(self) -> None:
        drops = [line.strip() for line in VALIDATION_MACRO.splitlines()
                 if "drop_relation" in line]
        self.assertEqual(len(drops), 1, drops)
        self.assertIn("drop_relation(probe)", drops[0])

    def test_macro_stops_rather_than_reusing_a_taken_name(self) -> None:
        self.assertIn("raise_compiler_error", VALIDATION_MACRO)

    def test_probe_names_are_unique_per_run(self) -> None:
        request = TransformationRequest(
            run_id=str(uuid.uuid4()), operation="VALIDATE", project_files={},
            profile={}, validate_schemas=["analytics"], validate_relations=[],
        )
        adapter = DbtCoreAdapter.__new__(DbtCoreAdapter)
        first = json.loads(adapter._command(request)[adapter._command(request).index("--args") + 1])
        second = json.loads(adapter._command(request)[adapter._command(request).index("--args") + 1])
        self.assertGreaterEqual(len(first["probe_names"]), 2,
                                "a collision needs somewhere to go")
        for name in first["probe_names"]:
            self.assertTrue(name.startswith("__appbi_probe_"), name)
        self.assertNotEqual(first["probe_names"], second["probe_names"],
                            "two checks must not reuse one name")


class ReleaseGateTests(unittest.TestCase):
    """A release is frozen code; being live requires proof that it compiles."""

    def test_generate_project_does_not_compile_anything(self) -> None:
        # Stated as a test because a comment once claimed the opposite, and the
        # gate exists precisely because rendering files proves nothing.
        transform = _transform()
        model = TransformModel(
            id=uuid.uuid4(), transform_id=transform.id, name="stg",
            layer="STAGING", materialization="VIEW",
            sql="select * from {{ ref('nope') }}", config_json={}, tests=[],
        )
        generated = generate_project(transform, [model], [])
        path = "models/staging/stg.sql"
        self.assertIn(path, generated.files)
        # Nothing here can have noticed that `nope` does not exist.
        self.assertIn("nope", generated.files[path])

    def test_a_new_release_starts_unverified(self) -> None:
        release = TransformRelease(
            id=uuid.uuid4(), transform_id=uuid.uuid4(), release_number=1,
            project_files={}, model_snapshot=[], source_version=1,
            default_schema="analytics", status="VERIFYING",
        )
        self.assertNotEqual(release.status, "READY")

    def test_settle_marks_ready_only_on_success(self) -> None:
        from app.services.transforms import _settle_release  # noqa: F401
        # The behaviour itself needs a session; what is asserted here is that
        # completion calls it at all, which is the wiring that was missing.
        import inspect

        from app.services import transforms as service
        self.assertIn("_settle_release", inspect.getsource(service.complete))


class UnattendedRunsUsePublishedCodeTests(unittest.TestCase):
    """Nothing that fires on its own may execute what someone is editing."""

    def test_after_upstream_never_falls_back_to_draft(self) -> None:
        import inspect

        from app.services import transforms as service
        source = inspect.getsource(service.enqueue_after_upstream)
        self.assertIn('source="RELEASE"', source)
        self.assertNotIn('else "DRAFT"', source)
        self.assertIn("active_release_id is None", source,
                      "a Transform with nothing published must be skipped")

    def test_scheduler_still_requires_an_active_release(self) -> None:
        import inspect

        from app import worker
        self.assertIn("active_release_id", inspect.getsource(worker))


class ClaimIsSerialisedTests(unittest.TestCase):
    """The parallelism cap has to survive a second worker replica."""

    def test_claim_takes_an_advisory_lock_before_counting(self) -> None:
        import inspect

        from app.services import transforms as service
        source = inspect.getsource(service.claim_next)
        lock = source.index("pg_advisory_xact_lock")
        count = source.index("transform_worker_max_parallel")
        self.assertLess(lock, count,
                        "counting before locking is the race this closes")


class AiDraftingFollowsTheConnectionTests(unittest.TestCase):
    """Drafting must work on a warehouse that has no Destination behind it."""

    def test_no_destination_lookup_remains(self) -> None:
        import inspect

        from app.services import transform_ai
        source = inspect.getsource(transform_ai)
        self.assertNotIn("transform.destination_id", source)
        self.assertIn("transform_connection", source)


class TransformMetricsAreExportedTests(unittest.TestCase):
    """A dashboard counting only PipelineRun stays green while nothing builds."""

    def test_transform_series_are_present(self) -> None:
        import inspect

        from app.api import metrics
        source = inspect.getsource(metrics)
        for name in (
            "appbi_transform_runs_active",
            "appbi_transform_runs_queued",
            "appbi_transform_oldest_queued_seconds",
            "appbi_transform_runs_total",
            "appbi_transform_worker_alive",
            "appbi_transform_run_duration_seconds",
        ):
            self.assertIn(name, source, name)


class LiveLogsAreActuallyLiveTests(unittest.TestCase):
    """The Logs tab is labelled live, so output must arrive before the exit."""

    def test_adapter_streams_instead_of_buffering(self) -> None:
        import inspect

        source = inspect.getsource(DbtCoreAdapter.execute)
        self.assertNotIn("process.communicate()", source)
        self.assertIn("log_sink", source)

    def test_partial_log_is_stored_where_every_pod_can_read_it(self) -> None:
        import inspect

        from app.services import transforms as service
        source = inspect.getsource(service.store_partial_log)
        self.assertIn("TransformArtifact", source,
                      "a path on the worker's disk is unreadable from the API pod")


class HealthIsNotTakenFromARehearsalTests(unittest.TestCase):
    def test_only_production_operations_move_health(self) -> None:
        import inspect

        from app.services import transforms as service
        self.assertIn("if run.operation in PRODUCTION_OPERATIONS:",
                      inspect.getsource(service.complete))

    def test_a_failed_build_is_unhealthy(self) -> None:
        from app.services.transforms import _apply_health
        from app.transformation.base import TransformationResult

        transform = _transform()
        run = TransformRun(
            id=uuid.uuid4(), workspace_id=transform.workspace_id,
            transform_id=transform.id, operation="BUILD",
            status=RunStatus.FAILED,
            ended_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        _apply_health(transform, run, TransformationResult(
            succeeded=False, error_summary="boom"))
        self.assertIs(transform.health_status, HealthLevel.ERROR)


if __name__ == "__main__":
    unittest.main()
