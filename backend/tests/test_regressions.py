"""Regressions for defects the adversarial audit found.

Each test names the behaviour that was wrong, so a future change that
reintroduces it fails here rather than in production.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.core.params import as_enum
from app.models.enums import RunStatus, ScheduleType, TriggerType
from app.services import scheduling


# ── an invalid filter value is bad input, not a server fault ───────────────
# Before: `RunStatus(value.upper())` raised ValueError inside the request and
# surfaced as a 500 on ?status=NOT_A_STATUS.

def test_invalid_enum_filter_raises_validation_not_valueerror() -> None:
    with pytest.raises(ValidationError) as caught:
        as_enum("NOT_A_STATUS", RunStatus, field="status")
    assert caught.value.status_code == 422
    assert caught.value.code == "INVALID_FILTER_VALUE"
    # The response must tell the caller what is accepted.
    assert "SUCCEEDED" in caught.value.details["allowed"]


def test_valid_enum_filter_is_case_insensitive() -> None:
    assert as_enum("succeeded", RunStatus, field="status") is RunStatus.SUCCEEDED
    assert as_enum("  RETRY  ", TriggerType, field="trigger") is TriggerType.RETRY


def test_empty_filter_is_not_an_error() -> None:
    assert as_enum(None, RunStatus, field="status") is None
    assert as_enum("", RunStatus, field="status") is None


# ── an unknown timezone must be rejected, not silently treated as UTC ──────
# Before: `resolve_zone` swallowed the error, so the API accepted
# "Mars/Olympus", echoed it back, and computed the schedule in UTC.

def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        scheduling.require_zone("Mars/Olympus")
    assert caught.value.code == "INVALID_TIMEZONE"


def test_validate_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        scheduling.validate({"type": "DAILY", "time_of_day": "02:00",
                             "timezone": "Mars/Olympus"})


def test_known_timezone_is_preserved_exactly() -> None:
    normalized = scheduling.validate(
        {"type": "DAILY", "time_of_day": "02:00", "timezone": "Asia/Ho_Chi_Minh"}
    )
    assert normalized["timezone"] == "Asia/Ho_Chi_Minh"


def test_reading_a_stored_bad_timezone_still_works() -> None:
    """The read path stays lenient: a value that became invalid after an OS
    tzdata update must not take the page down."""
    zone = scheduling.resolve_zone("Mars/Olympus")
    assert str(zone) == "UTC"


def test_unknown_schedule_type_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        scheduling.validate({"type": "HOURLY_ISH", "timezone": "UTC"})
    assert caught.value.code == "INVALID_SCHEDULE_TYPE"


def test_manual_schedule_is_valid_with_no_other_fields() -> None:
    """An empty schedule body means MANUAL; that is a legitimate request."""
    normalized = scheduling.validate({})
    assert normalized["type"] == ScheduleType.MANUAL.value
    assert scheduling.preview(ScheduleType.MANUAL, normalized, "UTC") == []


# ── name uniqueness applies to live rows only ──────────────────────────────
# Before: the unique constraint ignored deleted_at, so deleting a source
# reserved its name forever and re-creating it produced a 500.

def test_live_name_uniqueness_is_a_partial_index() -> None:
    from app.models.integration import Destination, Pipeline, Source

    for model, expected in (
        (Source, "uq_source_ws_name_live"),
        (Destination, "uq_destination_ws_name_live"),
        (Pipeline, "uq_pipeline_ws_name_live"),
    ):
        index = next(
            (i for i in model.__table__.indexes if i.name == expected), None
        )
        assert index is not None, f"{model.__name__} lost its live-name index"
        assert index.unique, f"{expected} must be unique"
        where = index.dialect_options["postgresql"].get("where")
        assert where is not None and "deleted_at IS NULL" in str(where), (
            f"{expected} must exclude soft-deleted rows"
        )
        # The old unconditional constraint must not come back.
        assert not any(
            getattr(c, "name", "") in {
                "uq_source_ws_name", "uq_destination_ws_name", "uq_pipeline_ws_name",
            }
            for c in model.__table__.constraints
        )


def test_bootstrap_ships_the_matching_ddl() -> None:
    """create_all cannot alter an existing table, so the fixups must exist."""
    from app.bootstrap import SCHEMA_FIXUPS

    joined = " ".join(SCHEMA_FIXUPS)
    for name in ("uq_source_ws_name_live", "uq_destination_ws_name_live",
                 "uq_pipeline_ws_name_live"):
        assert f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}"' in joined
    for old in ("uq_source_ws_name", "uq_destination_ws_name", "uq_pipeline_ws_name"):
        assert f'DROP CONSTRAINT IF EXISTS "{old}"' in joined


# ── a successful check is reusable, and only for the same config ───────────

def test_check_token_round_trips_for_the_same_configuration() -> None:
    from app.services.actors import issue_check_token, verify_check_token

    config = {"host": "db", "port": 5432, "password": "s3cret"}
    token = issue_check_token("SOURCE", "source-postgres", config)
    assert verify_check_token(token, "SOURCE", "source-postgres", config)


def test_check_token_is_bound_to_the_exact_configuration() -> None:
    from app.services.actors import issue_check_token, verify_check_token

    token = issue_check_token("SOURCE", "source-postgres",
                              {"host": "db", "password": "s3cret"})
    # A changed credential must invalidate the proof.
    assert not verify_check_token(token, "SOURCE", "source-postgres",
                                  {"host": "db", "password": "different"})
    # So must a different connector or side.
    assert not verify_check_token(token, "DESTINATION", "source-postgres",
                                  {"host": "db", "password": "s3cret"})
    assert not verify_check_token(token, "SOURCE", "source-mysql",
                                  {"host": "db", "password": "s3cret"})


def test_check_token_rejects_garbage() -> None:
    from app.services.actors import verify_check_token

    for bad in (None, "", "nonsense", "abc.def", "0.deadbeef"):
        assert not verify_check_token(bad, "SOURCE", "source-postgres", {})


# ── a read-only role is offered no mutating action ─────────────────────────
# The UI hides buttons that `available_actions` omits, so this list is the
# real permission boundary as far as the browser is concerned.

def test_analyst_is_offered_no_mutating_pipeline_action() -> None:
    from app.core.permissions import Role
    from app.models.enums import PipelineHealth, PipelineStatus
    from app.services import pipelines as pipeline_service

    class _Ctx:
        def __init__(self, role: Role) -> None:
            self.role = role

        def can(self, module, action) -> bool:
            from app.core.permissions import MATRIX

            return action in MATRIX[self.role].get(module, set())

    class _Pipeline:
        status = PipelineStatus.ACTIVE

    actions = pipeline_service.available_actions(
        _Ctx(Role.ANALYST), _Pipeline(), PipelineHealth.HEALTHY
    )
    assert actions == [], f"read-only role was offered {actions}"

    # And an operator must still get the operational ones, or the check above
    # would pass simply because the function always returns nothing.
    operator = pipeline_service.available_actions(
        _Ctx(Role.OPERATOR), _Pipeline(), PipelineHealth.HEALTHY
    )
    assert "RUN_NOW" in operator and "PAUSE" in operator


# ── the catalogue is the whole upstream registry, and that changes the rules ──
# Before: the worker refreshed every connector by running its image, holding one
# transaction across all of them. At 4 connectors that was slow; at 650 it pulled
# hundreds of gigabytes and blocked DDL behind an hours-long transaction, which
# stalled every query on the table.

def test_blanket_refresh_is_bounded_to_connectors_in_use() -> None:
    import inspect

    from app.services import catalog

    source = inspect.getsource(catalog.refresh_specs)
    assert "usage_count > 0" in source, (
        "an unused connector's spec costs an image pull to learn nothing"
    )
    assert ".limit(" in source, "a refresh cycle must be bounded"
    # The read snapshot must be released before the engine is called.
    assert source.index("await session.commit()") < source.index("get_connector_spec"), (
        "the transaction must close before the first engine call"
    )


def test_refresh_specs_defaults_to_a_small_batch() -> None:
    import inspect

    from app.services import catalog

    limit = inspect.signature(catalog.refresh_specs).parameters["limit"]
    assert isinstance(limit.default, int) and 0 < limit.default <= 50


# ── a blocked schema fixup must not freeze the table for everyone ─────────────

def test_schema_fixups_run_with_a_lock_timeout() -> None:
    import inspect

    from app import bootstrap

    source = inspect.getsource(bootstrap.apply_schema_fixups)
    assert "lock_timeout" in source, "DDL that waits forever queues every reader behind it"
    # One transaction per statement, opened inside the loop: a fixup that blocks
    # must not discard the ones that already succeeded.
    assert "for statement in" in source
    opened_at = source.index("engine.begin()")
    assert source.index("for statement in") < opened_at


def test_lock_timeout_is_detected_by_sqlstate() -> None:
    from app.bootstrap import _is_lock_timeout

    class _Orig:
        sqlstate = "55P03"

    class _Other:
        sqlstate = "42P01"

    class _Exc:
        def __init__(self, orig) -> None:
            self.orig = orig

    assert _is_lock_timeout(_Exc(_Orig()))
    assert not _is_lock_timeout(_Exc(_Other()))
    assert not _is_lock_timeout(_Exc(None))


# ── the registry is generated, and the pins we tested must survive that ───────

def test_curated_connectors_keep_their_tested_pins() -> None:
    from app.adapters.registry import bundled_by_key

    for key, version in (
        ("source-postgres", "3.8.5"),
        ("source-faker", "7.2.1"),
        ("source-file", "0.6.0"),
        ("destination-postgres", "3.0.17"),
    ):
        metadata = bundled_by_key(key)
        assert metadata is not None, f"{key} vanished from the generated registry"
        assert metadata.version == version, (
            f"{key} drifted to {metadata.version}; the contract suite tested {version}"
        )


def test_catalogue_covers_the_upstream_registry() -> None:
    """A four-connector catalogue was the single biggest gap in the product."""
    from app.adapters.registry import bundled_connectors

    connectors = bundled_connectors()
    sources = [c for c in connectors if c.connector_type == "SOURCE"]
    destinations = [c for c in connectors if c.connector_type == "DESTINATION"]
    assert len(sources) > 300, f"only {len(sources)} sources"
    assert len(destinations) > 30, f"only {len(destinations)} destinations"
    # Every offered connector must be renderable, or the wizard dead-ends.
    assert all(c.spec_schema for c in connectors)


def test_only_verified_connectors_claim_certification() -> None:
    from app.adapters.registry import bundled_certifications

    supported = {k for k, v in bundled_certifications().items() if v == "SUPPORTED"}
    # source-file is deliberately absent: compatibility.yaml records only
    # check/discover/full_refresh for it, and e2e.py cannot even drive it, so
    # claiming production support would have been a claim with no evidence.
    assert supported == {
        "source-postgres", "source-faker", "destination-postgres",
    }, f"certification must mean this product tested it, got {sorted(supported)}"
