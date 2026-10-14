"""The connector's real reason reaches the classifier and the screen.

Reported from a customer VM: creating a MongoDB -> Postgres pipeline stopped at
"Select data" with UNKNOWN_ENGINE_FAILURE and "Something went wrong in the
connector. See the logs for more details." That sentence is the `message` every
Java connector emits for every failure; the cause is in `internal_message`,
which the adapter read only when `message` was empty -- i.e. never.

Each TRACE line below was captured from airbyte/source-mongodb-v2:2.0.7.
"""

from __future__ import annotations

import json

import pytest

from app.adapters.airbyte_protocol import protocol as ap
from app.adapters.error_mapper import classify
from app.core.errors import ErrorCategory

GENERIC = "Something went wrong in the connector. See the logs for more details."


def _trace(internal: str, message: str = GENERIC) -> bytes:
    """One stdout line, as the adapter reads it from the container."""
    return json.dumps({"type": "TRACE", "trace": {"type": "ERROR", "emitted_at": 1.0,
                       "error": {"message": message, "internal_message": internal,
                                 "failure_type": "system_error"}}}).encode()


def _reason(line: bytes) -> str | None:
    """The adapter's path: parse the line, take the error, flatten it."""
    error = ap.trace_error(ap.parse_line(line))
    return ap.error_text(error) if error else None


# ── the internal message is kept ─────────────────────────────────────────

def test_the_internal_message_is_kept_beside_the_generic_one() -> None:
    text = _reason(_trace("com.mongodb.MongoSecurityException: Exception authenticating"))
    assert GENERIC in text
    assert "Exception authenticating" in text


def test_a_message_is_not_repeated_when_both_halves_say_the_same() -> None:
    assert ap.error_text({"message": "boom", "internal_message": "boom"}) == "boom"
    assert ap.error_text({"message": "", "internal_message": "only"}) == "only"
    assert ap.error_text({"message": "only"}) == "only"


def test_nothing_in_both_halves_is_still_nothing() -> None:
    """Callers test `is None` to fall back to their own wording."""
    assert ap.error_text({}) is None
    assert ap.error_text({"message": "", "internal_message": ""}) is None


# ── and it is what gets classified ───────────────────────────────────────

_MEASURED = [
    # wrong password, discover step: the check status said "Authentication
    # failed" but discover said only the generic sentence plus this.
    ("com.mongodb.MongoSecurityException: Exception authenticating MongoCredential"
     "{mechanism=SCRAM-SHA-256, userName='root', source='admin', password=<hidden>, "
     "mechanismProperties=<hidden>}",
     ErrorCategory.AUTHENTICATION, "SOURCE_AUTHENTICATION_FAILED"),
    # `docker run mongo`, no --replSet
    ("Target MongoDB instance is not a replica set cluster.",
     ErrorCategory.CONFIGURATION, "SOURCE_MONGODB_NOT_REPLICA_SET"),
    # rs.initiate() with localhost:27017 as the member host
    ("com.mongodb.MongoTimeoutException: Timed out after 30000 ms while waiting for a "
     "server that matches ReadPreferenceServerSelector{readPreference=primary}. Client "
     "view of cluster state is {type=REPLICA_SET, servers=[{address=localhost:27017, "
     "type=UNKNOWN, state=CONNECTING, exception={com.mongodb.MongoSocketOpenException: "
     "Exception opening socket}",
     ErrorCategory.NETWORK, "SOURCE_MONGODB_MEMBER_UNREACHABLE"),
]


@pytest.mark.parametrize("internal,category,code", _MEASURED)
def test_a_measured_mongodb_failure_is_classified_from_its_internal_message(
    internal: str, category: ErrorCategory, code: str,
) -> None:
    failure = classify(_reason(_trace(internal)), side="SOURCE")
    assert (failure.category, failure.code) == (category, code)


def test_before_the_fix_the_same_line_was_unclassified() -> None:
    """What the old `message or internal_message` produced, for the record."""
    line = _trace(_MEASURED[0][0])
    error = ap.trace_error(ap.parse_line(line))
    old = error.get("message") or error.get("internal_message")
    assert classify(old).code == "UNKNOWN_ENGINE_FAILURE"


def test_a_member_address_problem_is_not_called_a_timeout() -> None:
    """Retrying later never fixes an unreachable member address."""
    failure = classify(_MEASURED[2][0])
    assert failure.code != "ENGINE_TIMEOUT"
    assert "directConnection=true" in failure.summary


def test_the_unclassified_summary_does_not_claim_a_sync() -> None:
    """It is shown for Test and discover too, where nothing was syncing."""
    failure = classify("an error nobody has a rule for")
    assert failure.code == "UNKNOWN_ENGINE_FAILURE"
    assert "sync" not in failure.summary.lower()
