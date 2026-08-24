"""Making engine log lines fit to read.

Airbyte writes its logs for a terminal: colour is SGR escape sequences, and the
platform's own lines are tagged with a highlighted `platform >` prefix. Passed
through untouched they reach the browser as `[46mplatform[0m > ...`, which
looks like corruption rather than colour.

Stripping belongs here rather than in the frontend for two reasons. The escape
bytes are an engine detail, and the boundary that hides engine details is the
adapter (guardrail 3). And a log line is not only rendered — it is searched,
copied into a ticket and matched against error fingerprints, none of which
should have to know about SGR.
"""

from __future__ import annotations

import re

# CSI sequences (colour, cursor moves) and the two-character escapes that show
# up in container output. Deliberately not a general terminal emulator: the aim
# is a readable line, not a faithful replay of the stream.
_ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[@-Z\-_])")

# Bare control characters that survive the above. Tab is kept: connector logs
# use it for alignment and it renders fine.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_line(line: str) -> str:
    """One log line as a human should see it."""
    return _CONTROL.sub("", _ANSI.sub("", line)).rstrip("\r\n")


def clean_lines(lines: list[str]) -> list[str]:
    return [clean_line(line) for line in lines]
