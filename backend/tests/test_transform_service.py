"""Service-level rules that do not need a database session.

These cover the invariants that are easy to regress and expensive to catch in
UAT: when an after-upstream Transform is allowed to run, and what its health
says afterwards.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.core.errors import ErrorCategory
from app.models.enums import HealthLevel, RunStatus
from app.models.transform import Transform, TransformRun
from app.services.transforms import _apply_health, _remediation
from app.transformation.base import TransformationResult


def _now() -> datetime:
    return datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _transform() -> Transform:
    return Transform(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), destination_id=uuid.uuid4(),
        name="Sales", default_schema="analytics", dbt_core_version="1.12.3",
        dbt_adapter_name="dbt-postgres", dbt_adapter_version="1.11.0",
    )


def _run(**overrides) -> TransformRun:
    run = TransformRun(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), transform_id=uuid.uuid4(),
        operation="BUILD", status=RunStatus.SUCCEEDED,
    )
    run.ended_at = _now()
    run.tests_failed = 0
    run.tests_warned = 0
    run.tests_passed = 0
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


class HealthSemanticsTests(unittest.TestCase):
    def test_a_build_that_succeeds_with_failed_tests_is_not_healthy(self) -> None:
        transform = _transform()
        # dbt can exit 0 with failing tests depending on severity and flags, so
        # health must consult the test counters rather than the exit code.
        _apply_health(transform, _run(tests_failed=2), TransformationResult(succeeded=True))

        self.assertIs(transform.health_status, HealthLevel.ERROR)
        self.assertIn("2", transform.health_message or "")

    def test_warnings_downgrade_to_warning_and_explain_themselves(self) -> None:
        transform = _transform()
        _apply_health(transform, _run(tests_warned=1), TransformationResult(succeeded=True))

        self.assertIs(transform.health_status, HealthLevel.WARNING)
        self.assertTrue(transform.health_message)

    def test_a_clean_build_is_healthy_and_records_success(self) -> None:
        transform = _transform()
        run = _run(tests_passed=3)
        _apply_health(transform, run, TransformationResult(succeeded=True))

        self.assertIs(transform.health_status, HealthLevel.HEALTHY)
        self.assertIsNone(transform.health_message)
        self.assertEqual(transform.last_success_at, run.ended_at)

    def test_a_failed_build_keeps_the_engine_summary(self) -> None:
        transform = _transform()
        _apply_health(
            transform, _run(status=RunStatus.FAILED),
            TransformationResult(succeeded=False, error_summary="Compilation Error"),
        )

        self.assertIs(transform.health_status, HealthLevel.ERROR)
        self.assertEqual(transform.health_message, "Compilation Error")


class RemediationTests(unittest.TestCase):
    def test_failed_tests_point_at_the_tests_rather_than_the_logs(self) -> None:
        run = _run(status=RunStatus.FAILED, tests_failed=1)
        run.error_category = ErrorCategory.ENGINE

        self.assertEqual(
            _remediation(run, TransformationResult(succeeded=False)),
            "REVIEW_TEST_FAILURES",
        )

    def test_permission_failures_point_at_the_destination(self) -> None:
        run = _run(status=RunStatus.FAILED)
        run.error_category = ErrorCategory.PERMISSION

        self.assertEqual(
            _remediation(run, TransformationResult(succeeded=False)),
            "CHECK_DESTINATION_CREDENTIALS",
        )

    def test_a_successful_run_needs_no_remediation(self) -> None:
        self.assertIsNone(_remediation(_run(), TransformationResult(succeeded=True)))


class FreshnessGateTests(unittest.IsolatedAsyncioTestCase):
    """`upstream_readiness` is the section 27/56 rule: every required input."""

    async def test_a_transform_waits_for_the_input_that_has_not_landed(self) -> None:
        from app.services import transforms as service

        transform = _transform()
        transform.last_success_at = _now()
        fresh = _asset(pipeline=True, fresh_at=_now() + timedelta(minutes=5))
        stale = _asset(pipeline=True, fresh_at=_now() - timedelta(hours=1))
        session = _FakeSession([_input(fresh, "src_a"), _input(stale, "src_b")])

        ready, report = await service.upstream_readiness(session, transform)

        self.assertFalse(ready)
        self.assertEqual(
            {item["source_name"]: item["state"] for item in report},
            {"src_a": "READY", "src_b": "STALE"},
        )

    async def test_all_required_inputs_fresh_releases_the_build(self) -> None:
        from app.services import transforms as service

        transform = _transform()
        transform.last_success_at = _now()
        later = _now() + timedelta(minutes=5)
        session = _FakeSession([
            _input(_asset(pipeline=True, fresh_at=later), "src_a"),
            _input(_asset(pipeline=True, fresh_at=later), "src_b"),
        ])

        ready, _ = await service.upstream_readiness(session, transform)

        self.assertTrue(ready)

    async def test_a_stale_optional_input_does_not_block(self) -> None:
        from app.services import transforms as service

        transform = _transform()
        transform.last_success_at = _now()
        session = _FakeSession([
            _input(_asset(pipeline=True, fresh_at=_now() + timedelta(minutes=5)), "src_a"),
            _input(
                _asset(pipeline=True, fresh_at=_now() - timedelta(hours=1)), "src_b",
                required=False,
            ),
        ])

        ready, _ = await service.upstream_readiness(session, transform)

        self.assertTrue(ready)

    async def test_a_plain_warehouse_relation_is_always_available(self) -> None:
        from app.services import transforms as service

        transform = _transform()
        transform.last_success_at = _now()
        # Nothing in this product loads it, so there is no freshness to judge.
        session = _FakeSession([_input(_asset(pipeline=False, fresh_at=None), "src_w")])

        ready, report = await service.upstream_readiness(session, transform)

        self.assertTrue(ready)
        self.assertEqual(report[0]["state"], "READY")

    async def test_a_transform_that_never_built_treats_loaded_inputs_as_ready(self) -> None:
        from app.services import transforms as service

        transform = _transform()
        transform.last_success_at = None
        session = _FakeSession([
            _input(_asset(pipeline=True, fresh_at=_now() - timedelta(days=2)), "src_a"),
        ])

        ready, _ = await service.upstream_readiness(session, transform)

        self.assertTrue(ready)


class SourceAliasTests(unittest.TestCase):
    """A dbt source is a schema; its tables live inside it."""

    def test_relations_in_one_schema_share_a_single_alias(self) -> None:
        from app.services.transforms import _source_name

        assigned: dict = {}
        names = [
            _source_name(_asset_named("base-testlab-01", "appbi_wf_raw", t), assigned)
            for t in ("job", "stage", "workflow")
        ]

        self.assertEqual(len(set(names)), 1, names)
        self.assertEqual(names[0], "src_appbi_wf_raw")

    def test_distinct_schemas_get_distinct_aliases(self) -> None:
        from app.services.transforms import _source_name

        assigned: dict = {}
        first = _source_name(_asset_named("p", "raw_crm", "deal"), assigned)
        second = _source_name(_asset_named("p", "raw_erp", "order"), assigned)

        self.assertNotEqual(first, second)

    def test_same_schema_name_in_two_catalogs_stays_separable(self) -> None:
        from app.services.transforms import _source_name

        assigned: dict = {}
        first = _source_name(_asset_named("project_a", "raw", "t"), assigned)
        second = _source_name(_asset_named("project_b", "raw", "t"), assigned)

        self.assertNotEqual(first, second)


def _asset_named(catalog: str | None, schema: str, relation: str) -> _FakeAsset:
    asset = _FakeAsset(True, None, "READY")
    asset.catalog_name = catalog
    asset.schema_name = schema
    asset.relation_name = relation
    return asset


class _FakeAsset:
    def __init__(self, pipeline: bool, fresh_at, resolution: str) -> None:
        self.id = uuid.uuid4()
        self.pipeline_id = uuid.uuid4() if pipeline else None
        self.fresh_at = fresh_at
        self.resolution_status = resolution
        self.catalog_name = None
        self.schema_name = "raw"
        self.relation_name = "orders"


class _FakeInput:
    def __init__(self, asset: _FakeAsset, source_name: str, required: bool) -> None:
        self.asset = asset
        self.source_name = source_name
        self.required = required


class _FakeScalars:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Only `scalars(...)` is exercised by upstream_readiness."""

    def __init__(self, rows) -> None:
        self._rows = rows

    async def scalars(self, _statement):
        return _FakeScalars(self._rows)


def _asset(*, pipeline: bool, fresh_at, resolution: str = "READY") -> _FakeAsset:
    return _FakeAsset(pipeline, fresh_at, resolution)


def _input(asset: _FakeAsset, source_name: str, required: bool = True) -> _FakeInput:
    return _FakeInput(asset, source_name, required)


if __name__ == "__main__":
    unittest.main()
