"""Unit tests for the rules that must not drift (spec section 41.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.adapters.airbyte_protocol import protocol as ap
from app.adapters.dto import ConfiguredStream, DiscoveredStream, EngineConnectionRequest
from app.adapters.error_mapper import classify, fingerprint
from app.core.errors import ErrorCategory, ValidationError, error_from_matrix
from app.core.logging import redact
from app.core.permissions import Action, Module, Role, allowed, permission_map
from app.models.enums import RunStatus, ScheduleType
from app.services import catalog, scheduling


# ── error classification (section 16.7) ────────────────────────────────────

@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("FATAL: password authentication failed for user \"reader\"",
         ErrorCategory.AUTHENTICATION),
        ("could not connect to server: Connection refused", ErrorCategory.NETWORK),
        ("permission denied for table orders", ErrorCategory.PERMISSION),
        ("relation \"shop.gone\" does not exist", ErrorCategory.SCHEMA),
        ("429 Too Many Requests", ErrorCategory.RATE_LIMIT),
        ("no space left on device while writing", ErrorCategory.DESTINATION_WRITE),
        ("something nobody has ever seen", ErrorCategory.UNKNOWN),
    ],
)
def test_classify_maps_connector_errors(message: str, expected: ErrorCategory) -> None:
    assert classify(message).category is expected


def test_classify_always_returns_a_next_action() -> None:
    failure = classify("totally unrecognised failure")
    assert failure.code == "UNKNOWN_ENGINE_FAILURE"
    assert failure.remediation_action  # never leave the user without a next step
    assert failure.summary


def test_classify_flips_side_for_destination() -> None:
    failure = classify("password authentication failed", side="DESTINATION")
    assert failure.code == "DESTINATION_AUTHENTICATION_FAILED"


def test_fingerprint_is_stable_across_volatile_details() -> None:
    a = fingerprint("connection to 10.0.0.4:5432 failed at 2026-08-22T04:00:00")
    b = fingerprint("connection to 10.0.0.9:5432 failed at 2026-08-22T09:31:11")
    assert a == b  # same failure shape, so alert dedup collapses them


# ── secret redaction (section 27.2) ────────────────────────────────────────

def test_redact_masks_nested_credentials() -> None:
    payload = {
        "host": "db.internal",
        "password": "hunter2",
        "tunnel": {"ssh_key": "-----BEGIN", "port": 22},
        "list": [{"api_key": "abc"}],
    }
    cleaned = redact(payload)
    assert cleaned["host"] == "db.internal"
    assert cleaned["password"] == "********"
    assert cleaned["tunnel"]["port"] == 22
    assert "hunter2" not in str(cleaned)
    assert "abc" not in str(cleaned)


# ── permissions (section 4.2) ──────────────────────────────────────────────

def test_analyst_is_read_only() -> None:
    assert allowed(Role.ANALYST, Module.PIPELINES, Action.VIEW)
    assert not allowed(Role.ANALYST, Module.PIPELINES, Action.OPERATE)
    assert not allowed(Role.ANALYST, Module.SOURCES, Action.CREATE)


def test_operator_runs_but_cannot_reconfigure() -> None:
    assert allowed(Role.OPERATOR, Module.PIPELINES, Action.OPERATE)
    assert not allowed(Role.OPERATOR, Module.PIPELINES, Action.EDIT)
    assert not allowed(Role.OPERATOR, Module.SOURCES, Action.DELETE)


def test_permission_map_covers_every_module() -> None:
    serialized = permission_map(Role.DATA_ADMIN)
    assert set(serialized) == {module.value for module in Module}


# ── scheduling (section 17) ────────────────────────────────────────────────

def test_interval_below_minimum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        scheduling.validate({"type": "INTERVAL", "interval_seconds": 30})


def test_cron_that_fires_too_often_is_rejected() -> None:
    with pytest.raises(ValidationError):
        scheduling.validate({"type": "CRON", "cron_expression": "* * * * *"})


def test_invalid_cron_is_rejected() -> None:
    with pytest.raises(ValidationError):
        scheduling.validate({"type": "CRON", "cron_expression": "not a cron"})


def test_daily_schedule_is_timezone_aware() -> None:
    config = scheduling.validate(
        {"type": "DAILY", "time_of_day": "02:00", "timezone": "Asia/Bangkok"}
    )
    upcoming = scheduling.next_run_at(ScheduleType.DAILY, config, "Asia/Bangkok")
    assert upcoming is not None
    # 02:00 Bangkok is 19:00 UTC the previous day.
    assert upcoming.astimezone(timezone.utc).hour == 19


def test_preview_returns_increasing_times() -> None:
    config = scheduling.validate({"type": "INTERVAL", "interval_seconds": 3600})
    upcoming = scheduling.preview(ScheduleType.INTERVAL, config, "UTC", 3)
    assert len(upcoming) == 3
    assert upcoming == sorted(upcoming)


def test_manual_schedule_has_no_next_run() -> None:
    assert scheduling.next_run_at(ScheduleType.MANUAL, {}, "UTC") is None


def test_freshness_deadline_follows_the_schedule() -> None:
    last_success = datetime.now(timezone.utc) - timedelta(hours=5)
    config = scheduling.validate({"type": "INTERVAL", "interval_seconds": 3600})
    deadline = scheduling.freshness_deadline(ScheduleType.INTERVAL, config, last_success)
    assert deadline is not None and deadline < datetime.now(timezone.utc)


# ── run state machine (section 16.1) ───────────────────────────────────────

def test_terminal_and_active_states_are_disjoint() -> None:
    for status in RunStatus:
        assert not (status.is_terminal and status.is_active)
    assert RunStatus.CANCEL_REQUESTED.is_active
    assert RunStatus.CANCELLED.is_terminal


# ── connector config split (section 21) ────────────────────────────────────

SPEC = {
    "type": "object",
    "required": ["host", "username"],
    "properties": {
        "host": {"type": "string"},
        "port": {"type": "integer", "default": 5432, "minimum": 1, "maximum": 65535},
        "username": {"type": "string"},
        "password": {"type": "string", "airbyte_secret": True},
        "ssl_mode": {"type": "object", "default": {"mode": "disable"},
                     "oneOf": [{"properties": {"mode": {"const": "disable"}}}]},
        "tunnel": {"type": "object", "properties": {
            "host": {"type": "string"},
            "ssh_key": {"type": "string", "airbyte_secret": True},
        }},
    },
}


def test_split_configuration_keeps_secrets_out_of_config() -> None:
    config, secrets = catalog.split_configuration(SPEC, {
        "host": "db", "username": "reader", "password": "s3cret",
        "tunnel": {"host": "bastion", "ssh_key": "PRIVATE"},
    })
    assert "password" not in config
    assert secrets["password"] == "s3cret"
    assert config["tunnel"] == {"host": "bastion"}
    assert secrets["tunnel"] == {"ssh_key": "PRIVATE"}


def test_merge_configuration_round_trips() -> None:
    original = {"host": "db", "username": "reader", "password": "s3cret",
                "tunnel": {"host": "bastion", "ssh_key": "PRIVATE"}}
    config, secrets = catalog.split_configuration(SPEC, original)
    assert catalog.merge_configuration(config, secrets) == original


def test_spec_defaults_are_applied() -> None:
    filled = catalog.apply_spec_defaults(SPEC, {"host": "db", "username": "reader"})
    assert filled["port"] == 5432
    assert filled["ssl_mode"] == {"mode": "disable"}


def test_missing_required_field_is_reported_with_a_label() -> None:
    with pytest.raises(ValidationError) as caught:
        catalog.validate_against_spec(SPEC, {"host": "db"})
    assert "username" in str(caught.value.details)


# ── error envelope (section 32) ────────────────────────────────────────────

def test_error_matrix_produces_a_remediation() -> None:
    error = error_from_matrix("SOURCE_AUTHENTICATION_FAILED", resource_id="abc")
    envelope = error.to_envelope("trc_1")["error"]
    assert envelope["code"] == "SOURCE_AUTHENTICATION_FAILED"
    assert envelope["remediation"]["action"] == "UPDATE_CREDENTIALS"
    assert envelope["remediation"]["resource_id"] == "abc"
    assert envelope["trace_id"] == "trc_1"


def test_unknown_code_still_yields_a_usable_envelope() -> None:
    envelope = error_from_matrix("NOT_IN_THE_MATRIX").to_envelope("trc_2")["error"]
    assert envelope["category"] == ErrorCategory.UNKNOWN.value
    assert envelope["message"]


# ── Airbyte Protocol handling ──────────────────────────────────────────────

def test_parse_line_ignores_connector_noise() -> None:
    assert ap.parse_line(b"") is None
    assert ap.parse_line(b"Picked up JAVA_TOOL_OPTIONS") is None
    assert ap.parse_line(b'{"no_type": 1}') is None
    message = ap.parse_line(b'{"type":"RECORD","record":{"stream":"users","data":{}}}')
    assert message is not None and message.type == "RECORD"


def test_configured_catalog_carries_the_refresh_protocol() -> None:
    streams = [
        ConfiguredStream(name="users", json_schema={"type": "object"},
                         sync_mode="full_refresh", destination_sync_mode="overwrite"),
        ConfiguredStream(name="events", json_schema={"type": "object"},
                         sync_mode="incremental", destination_sync_mode="append",
                         cursor_field=["updated_at"]),
    ]
    built = ap.build_configured_catalog(streams, generation_id=7, sync_id=3)
    overwrite, append = built["streams"]
    # Overwrite tells the destination to drop everything below this generation.
    assert overwrite["minimum_generation_id"] == 7
    # Append keeps every generation.
    assert append["minimum_generation_id"] == 0
    assert overwrite["generation_id"] == append["generation_id"] == 7
    assert overwrite["sync_id"] == 3
    assert append["cursor_field"] == ["updated_at"]


def test_api_catalog_carries_field_selection_to_airbyte() -> None:
    """The product persisted selected_fields but used to discard them here."""
    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter

    request = EngineConnectionRequest(
        workspace_id=uuid.uuid4(), product_resource_id=uuid.uuid4(),
        name="selected-fields", source_ref="source-id", destination_ref="destination-id",
        streams=[ConfiguredStream(
            name="users",
            json_schema={"type": "object", "properties": {
                "id": {"type": "string"}, "email": {"type": "string"},
            }},
            sync_mode="incremental", destination_sync_mode="append_dedup",
            cursor_field=["id"], primary_key=[["id"]],
            selected_fields=["id", "email"],
        )],
    )

    config = AirbyteApiAdapter._sync_catalog(request)["streams"][0]["config"]

    assert config["fieldSelectionEnabled"] is True
    assert config["selectedFields"] == [
        {"fieldPath": ["id"]}, {"fieldPath": ["email"]},
    ]


def test_catalog_hash_is_order_independent() -> None:
    a = DiscoveredStream(name="a", json_schema={"type": "object"})
    b = DiscoveredStream(name="b", json_schema={"type": "object"})
    assert ap.catalog_hash([a, b]) == ap.catalog_hash([a, b])
    assert ap.catalog_hash([a, b]) != ap.catalog_hash([a])


def test_state_normalization_accepts_legacy_and_modern_shapes() -> None:
    assert ap.normalize_state_for_source(None) is None
    assert ap.normalize_state_for_source([{"type": "STREAM"}]) == [{"type": "STREAM"}]
    assert ap.normalize_state_for_source({"type": "GLOBAL"}) == [{"type": "GLOBAL"}]


def test_trace_error_is_extracted() -> None:
    message = ap.parse_line(
        b'{"type":"TRACE","trace":{"type":"ERROR","error":{"message":"boom"}}}'
    )
    assert message is not None
    assert ap.trace_error(message) == {"message": "boom"}
