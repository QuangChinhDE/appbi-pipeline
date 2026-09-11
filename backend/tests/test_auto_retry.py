"""Retrying a transient failure without asking a person to press a button.

From a customer deployment, four rows of one pipeline's history:

    12:03:01  FAILED     records=1319
    12:04:20  FAILED     records=1319
    12:11:22  FAILED     records=1319
    12:16:41  SUCCEEDED  records=1319

Nothing was misconfigured. The source refused requests for about fifteen
minutes, and every one of those attempts was somebody noticing a failure and
pressing retry. The fourth worked because enough time had passed, which is the
one thing a person clicking sooner cannot supply.

So: retry by ourselves, wait longer each time, and only for failures that
could plausibly go away on their own. A rejected token will not become valid,
and retrying it three times only delays by several minutes the message that
says so.
"""

from __future__ import annotations

import pytest

from app.models.enums import RunStatus, TriggerType
from app.services.runs import (
    auto_retries_so_far,
    is_transient,
    retry_delay_seconds,
    should_auto_retry,
)


class _Run:
    def __init__(self, **kw) -> None:
        self.status = kw.get("status", RunStatus.FAILED)
        self.trigger_type = kw.get("trigger_type", TriggerType.SCHEDULE)
        self.error_code = kw.get("error_code", "CONNECTOR_STREAM_INTERRUPTED")
        self.remediation_action = kw.get("remediation_action", "RETRY_LATER")
        self.technical_metadata = kw.get("technical_metadata", {})


# ── which failures are worth repeating ────────────────────────────────────

def test_a_dropped_connection_is_transient() -> None:
    """What the deployment actually hit, and what the classifier already
    labels RETRY_LATER."""
    assert is_transient("CONNECTOR_STREAM_INTERRUPTED", "RETRY_LATER")


def test_a_rejected_token_is_not() -> None:
    """The case that must never be retried. Three attempts at a wrong token
    delay the useful message by several minutes and change nothing."""
    assert not is_transient("SOURCE_AUTHENTICATION_FAILED", "UPDATE_CREDENTIALS")


def test_a_schema_change_is_not() -> None:
    assert not is_transient("SCHEMA_CHANGED", "REDISCOVER_SCHEMA")


def test_running_out_of_memory_is_not() -> None:
    """It will fail identically every time until somebody raises the limit or
    lowers the page size."""
    assert not is_transient("CONNECTOR_OUT_OF_MEMORY", "INCREASE_CONNECTOR_MEMORY")


def test_a_staging_clash_is_not_retried_blindly() -> None:
    """Two pipelines writing to one table collide again on the next run. The
    fix is a prefix or a schema, not another attempt."""
    assert not is_transient("DESTINATION_STAGING_CONFLICT", "SEPARATE_DESTINATION")


@pytest.mark.parametrize("code", [
    "CONNECTOR_STREAM_INTERRUPTED", "SOURCE_TIMEOUT", "DESTINATION_TIMEOUT",
    "ENGINE_UNAVAILABLE", "RATE_LIMITED",
])
def test_known_transient_codes_stand_on_their_own(code: str) -> None:
    """A failure the classifier could not attach a remediation to is still
    recognisable by its code."""
    assert is_transient(code, None)


def test_an_unclassified_failure_is_left_alone() -> None:
    """UNKNOWN means nobody worked out what happened. Retrying it by reflex
    would hide a real problem behind three identical failures."""
    assert not is_transient(None, None)
    assert not is_transient("UNKNOWN", None)


# ── how long to wait ──────────────────────────────────────────────────────

def test_the_wait_doubles() -> None:
    assert retry_delay_seconds(1) == 60
    assert retry_delay_seconds(2) == 120
    assert retry_delay_seconds(3) == 240


def test_the_wait_is_capped() -> None:
    """A long chain must not push the next attempt into next week."""
    assert retry_delay_seconds(20) == 1800


def test_the_first_attempt_is_not_immediate() -> None:
    """Retrying instantly is what a person does, and it is the thing that did
    not work on the deployment this came from."""
    assert retry_delay_seconds(1) >= 30


# ── whether to retry this particular run ─────────────────────────────────

def test_a_transient_scheduled_failure_is_retried() -> None:
    assert should_auto_retry(_Run())


def test_a_successful_run_is_not() -> None:
    assert not should_auto_retry(_Run(status=RunStatus.SUCCEEDED))


def test_a_cancelled_run_is_not() -> None:
    """Somebody stopped it on purpose. Starting it again would be the product
    overruling them."""
    assert not should_auto_retry(_Run(status=RunStatus.CANCELLED))


def test_a_run_somebody_retried_by_hand_is_not() -> None:
    """They are watching it. A second, automatic attempt racing theirs turns
    one failure into two runs nobody asked for."""
    assert not should_auto_retry(_Run(trigger_type=TriggerType.RETRY))


def test_the_budget_is_spent_eventually() -> None:
    """Counted along the chain, not per run -- otherwise every automatic
    retry starts with a full budget and the loop never ends."""
    assert should_auto_retry(_Run(technical_metadata={"auto_retry_attempt": 2}))
    assert not should_auto_retry(_Run(technical_metadata={"auto_retry_attempt": 3}))


def test_an_automatic_retry_can_itself_be_retried() -> None:
    """Within budget. The chain is the point: 60s, then 120s, then 240s."""
    run = _Run(trigger_type=TriggerType.AUTO_RETRY,
               technical_metadata={"auto_retry_attempt": 1})
    assert should_auto_retry(run)


def test_a_permanent_failure_is_never_retried() -> None:
    assert not should_auto_retry(
        _Run(error_code="SOURCE_AUTHENTICATION_FAILED",
             remediation_action="UPDATE_CREDENTIALS"))


def test_turning_it_off_turns_it_off(monkeypatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "auto_retry_max_attempts", 0, raising=False)
    assert not should_auto_retry(_Run())


def test_the_counter_reads_a_missing_or_odd_value_as_zero() -> None:
    assert auto_retries_so_far(_Run(technical_metadata={})) == 0
    assert auto_retries_so_far(_Run(technical_metadata=None)) == 0
    assert auto_retries_so_far(_Run(technical_metadata={"auto_retry_attempt": None})) == 0


def test_the_trigger_type_is_distinguishable() -> None:
    """"Failed four times" and "failed once and recovered on its own" must not
    read the same on the runs list."""
    assert TriggerType.AUTO_RETRY.value == "AUTO_RETRY"
    assert TriggerType.AUTO_RETRY is not TriggerType.RETRY
