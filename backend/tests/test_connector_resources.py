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
    lines = _suspect_truncation({"s": _Stat("ticket", count)})
    assert len(lines) == 1
    assert "ticket" in lines[0]
    assert str(count) in lines[0]


@pytest.mark.parametrize("count", [0, 1, 19, 21, 499, 501, 1234])
def test_ordinary_counts_are_left_alone(count: int) -> None:
    """The heuristic has to stay quiet on normal reads, or it becomes noise
    that people learn to skip past."""
    assert _suspect_truncation({"s": _Stat("ticket", count)}) == []


def test_every_suspicious_stream_is_named() -> None:
    stats = {
        "a": _Stat("service", 500),
        "b": _Stat("ticket", 4321),
        "c": _Stat("stage", 100),
    }
    lines = _suspect_truncation(stats)
    assert len(lines) == 2
    named = " ".join(lines)
    assert "service" in named and "stage" in named
    assert "ticket" not in named


def test_the_warning_says_what_to_do_about_it() -> None:
    """A warning nobody can act on is noise. It has to say why the number is
    suspicious and that the source is where to check."""
    line = _suspect_truncation({"s": _Stat("service", 500)})[0]
    assert "page size" in line
    assert "source" in line

