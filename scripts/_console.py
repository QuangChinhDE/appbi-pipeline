"""Print what actually happened, on any console.

Windows still starts Python with a legacy codepage — cp1252 on the machines
this runs on — so the first non-ASCII character in an API response, a container
name or a box-drawing character ends the script with a UnicodeEncodeError. That
reads like the thing being tested failed, and it did not.

Every script here that prints anything it did not author should call
`force_utf8()` first. Importing this module does not do it implicitly: a
side effect that reconfigures global streams is exactly the kind of thing that
is impossible to find later.
"""

from __future__ import annotations

import sys


def force_utf8() -> None:
    """Make stdout and stderr carry UTF-8, replacing what they cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
