"""The ceiling a connector container runs under, and how a silent kill reads.

Two containers run per sync and neither had a limit, so one connector could
grow into all of the host's RAM and let the kernel pick a victim -- sometimes
the API, whose death then looks like a product bug. These tests pin the two
halves of the fix: the flags actually reaching `docker run`, and a SIGKILL with
no stderr turning into a sentence that names the cause.

No Docker and no database: the runner builds its argument list as pure data,
which is the part worth testing.
"""

from __future__ import annotations

import pytest

from app.adapters.airbyte_protocol.adapter import (
    _exit_without_explanation,
    _non_paginating_streams,
    _suspect_truncation,
)
from app.adapters.airbyte_protocol.docker_runner import DockerRunner
from app.adapters.error_mapper import classify
from app.core.config import settings


@pytest.fixture
def runner() -> DockerRunner:
    return DockerRunner()


def _args(runner: DockerRunner) -> list[str]:
    return runner.docker_args("some/image:1", ["spec"])


def test_memory_ceiling_reaches_docker(runner: DockerRunner, monkeypatch) -> None:
    monkeypatch.setattr(settings, "connector_memory_limit", "1g", raising=False)
    args = _args(runner)
    assert "--memory" in args
    assert args[args.index("--memory") + 1] == "1g"


def test_swap_is_pinned_to_the_memory_ceiling(runner: DockerRunner, monkeypatch) -> None:
    """Docker allows swap up to twice the memory limit by default, so a
    connector over budget would swap instead of failing -- and on a small VM
    that stalls the whole host until healthchecks start timing out."""
    monkeypatch.setattr(settings, "connector_memory_limit", "512m", raising=False)
    args = _args(runner)
    assert args[args.index("--memory-swap") + 1] == "512m"


def test_java_heap_is_raised_to_fit_the_ceiling(runner: DockerRunner, monkeypatch) -> None:
    """A container-aware JVM takes a quarter of the limit, which is 256 MB under
    a 1 GB ceiling -- not enough for the BigQuery destination, and it fails for
    a reason that looks nothing like the cause."""
    monkeypatch.setattr(settings, "connector_memory_limit", "1g", raising=False)
    monkeypatch.setattr(settings, "connector_java_opts", "-XX:MaxRAMPercentage=75.0",
                        raising=False)
    args = _args(runner)
    assert "JAVA_OPTS=-XX:MaxRAMPercentage=75.0" in args


def test_empty_limit_restores_unbounded_behaviour(runner: DockerRunner, monkeypatch) -> None:
    """An operator who wants the old behaviour must be able to have it, and
    must not get a stray --memory-swap or JAVA_OPTS on its own."""
    monkeypatch.setattr(settings, "connector_memory_limit", "", raising=False)
    monkeypatch.setattr(settings, "connector_cpu_limit", "", raising=False)
    args = _args(runner)
    for flag in ("--memory", "--memory-swap", "--cpus"):
        assert flag not in args
    assert not any(a.startswith("JAVA_OPTS=") for a in args)


def test_cpu_ceiling_is_independent_of_memory(runner: DockerRunner, monkeypatch) -> None:
    monkeypatch.setattr(settings, "connector_memory_limit", "", raising=False)
    monkeypatch.setattr(settings, "connector_cpu_limit", "1.5", raising=False)
    args = _args(runner)
    assert args[args.index("--cpus") + 1] == "1.5"
    assert "--memory" not in args


def test_image_and_command_stay_last(runner: DockerRunner, monkeypatch) -> None:
    """Resource flags are options, so they must land before the image. After it
    they would be passed to the connector as arguments instead."""
    monkeypatch.setattr(settings, "connector_memory_limit", "1g", raising=False)
    args = runner.docker_args("some/image:1", ["read", "--config", "c"])
    assert args[-4:] == ["some/image:1", "read", "--config", "c"]


# ── how a silent kill reads ────────────────────────────────────────────────

def test_sigkill_is_explained_as_memory(monkeypatch) -> None:
    monkeypatch.setattr(settings, "connector_memory_limit", "1g", raising=False)
    text = _exit_without_explanation("SOURCE", 137)
    assert "exceeding its memory" in text
    # The two settings that decide it, so the reader is not left searching.
    assert "CONNECTOR_MEMORY_LIMIT=1g" in text
    assert "MAX_CONCURRENT_RUNS_GLOBAL" in text


def test_sigkill_without_a_ceiling_says_so(monkeypatch) -> None:
    """Blaming the limit when there is none would send somebody to raise a
    setting that is not set. With no ceiling it was the operating system."""
    monkeypatch.setattr(settings, "connector_memory_limit", "", raising=False)
    text = _exit_without_explanation("DESTINATION", 137)
    assert "Chưa đặt CONNECTOR_MEMORY_LIMIT" in text


def test_other_exit_codes_are_not_blamed_on_memory() -> None:
    text = _exit_without_explanation("SOURCE", 1)
    assert "memory" not in text.lower()
    assert "code 1" in text


def test_the_explanation_classifies_as_a_memory_failure(monkeypatch) -> None:
    """The wording and the classifier have to agree, or the run shows a memory
    explanation under an UNKNOWN heading with no remediation button."""
    monkeypatch.setattr(settings, "connector_memory_limit", "1g", raising=False)
    failure = classify(_exit_without_explanation("SOURCE", 137), side="SOURCE")
    assert failure.code == "CONNECTOR_OUT_OF_MEMORY"
    assert failure.remediation_action == "INCREASE_CONNECTOR_MEMORY"
    assert "bộ nhớ" in failure.summary


@pytest.mark.parametrize("text", [
    "java.lang.OutOfMemoryError: Java heap space",
    "GC overhead limit exceeded",
    "MemoryError",
    "fork/exec: cannot allocate memory",
    "OOMKilled",
])
def test_real_connector_memory_messages_are_recognised(text: str) -> None:
    assert classify(text, side="DESTINATION").code == "CONNECTOR_OUT_OF_MEMORY"


def test_memory_wins_over_the_network_pattern() -> None:
    """A JVM out of heap prints frames full of sockets and connections. The
    network pattern would claim it and send somebody to check a healthy
    firewall, so the memory rule sits above it."""
    trace = (
        "java.lang.OutOfMemoryError: Java heap space\n"
        "\tat java.base/java.net.SocketInputStream.read(SocketInputStream.java:168)\n"
        "\tCaused by: could not connect to destination"
    )
    assert classify(trace, side="DESTINATION").code == "CONNECTOR_OUT_OF_MEMORY"


# ── a stream that stopped on a page boundary ───────────────────────────────

class _Stat:
    """Only the two fields the heuristic reads."""

    def __init__(self, stream_name: str, records_emitted: int) -> None:
        self.stream_name = stream_name
        self.records_emitted = records_emitted


@pytest.mark.parametrize("count", [20, 25, 50, 100, 200, 250, 500, 1000])
def test_common_page_sizes_are_called_out(count: int) -> None:
    """A connector that cannot page emits whatever one request returned. If the
    server capped it, there is no error and no log line -- the sync succeeds
    and the table is quietly short. A total that is exactly a page size is the
    only signal available without knowing the endpoint."""
    lines = _suspect_truncation({"s": _Stat("ticket", count)}, {"ticket"})
    assert len(lines) == 1
    assert "ticket" in lines[0]
    assert str(count) in lines[0]


@pytest.mark.parametrize("count", [0, 1, 19, 21, 499, 501, 1234])
def test_ordinary_counts_are_left_alone(count: int) -> None:
    """The heuristic has to stay quiet on normal reads, or it becomes noise
    that people learn to skip past."""
    assert _suspect_truncation({"s": _Stat("ticket", count)}, {"ticket"}) == []


def test_every_suspicious_stream_is_named() -> None:
    stats = {
        "a": _Stat("service", 500),
        "b": _Stat("ticket", 4321),
        "c": _Stat("stage", 100),
    }
    lines = _suspect_truncation(stats, {"service", "ticket", "stage"})
    assert len(lines) == 2
    named = " ".join(lines)
    assert "service" in named and "stage" in named
    assert "ticket" not in named


def test_the_warning_says_what_to_do_about_it() -> None:
    """A warning nobody can act on is noise. It has to say why the number is
    suspicious and that the source is where to check."""
    line = _suspect_truncation({"s": _Stat("service", 500)}, {"service"})[0]
    assert "page size" in line
    assert "source" in line


def test_a_paginating_stream_is_never_warned_about() -> None:
    """The false positive that made this necessary.

    The demo warehouse holds exactly 500 customers, read across five pages by a
    connector that pages correctly, and the first version of the check warned
    about it on a healthy sync. Where a paginator exists a round total is
    arithmetic, not evidence.
    """
    assert _suspect_truncation({"s": _Stat("customers", 500)}, set()) == []
    assert _suspect_truncation({"s": _Stat("customers", 500)}, {"other"}) == []


def test_streams_that_cannot_page_are_read_off_the_manifest() -> None:
    manifest = {
        "definitions": {
            "streams": {
                "service": {"retriever": {"paginator": {"type": "NoPagination"}}},
                "ticket": {"retriever": {"paginator": {"type": "DefaultPaginator"}}},
                "odd": {"retriever": {}},
            }
        }
    }
    assert _non_paginating_streams(manifest) == {"service"}


def test_an_image_connector_contributes_no_stream_names() -> None:
    """An ordinary Airbyte image has no manifest to inspect, pages properly,
    and so must produce no warnings at all rather than guesses."""
    assert _non_paginating_streams(None) == set()
    assert _non_paginating_streams({}) == set()


# ── what a live tenant actually answered ───────────────────────────────────

def test_base_token_refusal_is_an_authentication_failure() -> None:
    """Measured against a live Base tenant: every one of the ten applications
    answers a token from the wrong installation with this string, and it was
    landing in UNKNOWN -- so the screen said "lỗi chưa phân loại" while the
    connector's own explanation sat unread in the technical detail."""
    failure = classify(
        "Stream service is not available: Base rejected this request: "
        "access_token_v2_invalid_3. An `access_token_v2_invalid` message means "
        "the token is not accepted for this application.",
        side="SOURCE",
    )
    assert failure.code == "SOURCE_AUTHENTICATION_FAILED"
    assert failure.remediation_action == "UPDATE_CREDENTIALS"


def test_a_mid_sync_disconnect_says_to_try_again() -> None:
    """Seen once in twenty scheduled runs: failed after 1,603 records, and the
    identical run succeeded with 2,793 a minute later. UNKNOWN's remediation is
    "view technical details", which invites investigating something that has
    already fixed itself."""
    failure = classify("Connection lost", side="SOURCE")
    assert failure.code == "CONNECTOR_STREAM_INTERRUPTED"
    assert failure.remediation_action == "RETRY_LATER"
    assert "tạm" in failure.summary


def test_a_dropped_stream_is_not_reported_as_a_network_problem() -> None:
    """NETWORK's summary sends somebody to check a firewall. What broke was a
    pipe between two local processes."""
    failure = classify("broken pipe", side="DESTINATION")
    assert failure.category is not None
    assert "Không thể kết nối tới máy chủ" not in failure.summary


def test_a_destination_staging_clash_is_not_a_source_schema_change() -> None:
    """Observed four times in thirty-six scheduled runs against a live tenant.

    Two pipelines whose sources both expose a stream called `stage` -- Base
    Service and Base Workflow do -- wrote into one schema and collided on the
    `stage_airbyte_tmp` staging table. Postgres says `column "data" of relation
    "stage_airbyte_tmp" does not exist`, the schema rule claimed it, and the
    run offered to re-discover a source that had not changed.
    """
    failure = classify(
        'org.postgresql.util.PSQLException: ERROR: column "data" of relation '
        '"stage_airbyte_tmp" does not exist',
        side="DESTINATION",
    )
    assert failure.code == "DESTINATION_STAGING_CONFLICT"
    assert failure.remediation_action != "REDISCOVER_SCHEMA"
    assert "đích" in failure.summary


def test_a_real_source_schema_change_is_still_recognised() -> None:
    """The narrower rule must not swallow the case it was carved out of."""
    failure = classify('column "email" does not exist', side="SOURCE")
    assert failure.code == "SCHEMA_CHANGED"
    assert failure.remediation_action == "REDISCOVER_SCHEMA"

