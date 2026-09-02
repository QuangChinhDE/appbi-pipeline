"""What a dbt subprocess is allowed to see.

A dbt project is code.  Jinja can read environment variables (`env_var`), run
macros, and reach the network through the adapter.  Blacklisting Jinja functions
is a losing game; the durable control is that the process starts with almost
nothing in its environment and cannot reach AppBI's own secrets even if it tries.

So the subprocess environment is built by allowlist, never inherited.  The V1
adapter passed ``{**os.environ, ...}``, which handed every user-authored project
the API's ``DATABASE_URL``, ``SECRET_ENCRYPTION_KEY``, ``JWT_SECRET`` and
``OPENAI_API_KEY``.  Nothing was exploiting it; nothing needed to, for it to be
the wrong design.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Variables copied from the parent process when present.
#:
#: These are the ones a subprocess genuinely needs to function: where to find
#: executables, where its home is, how to decode text, where to put temporary
#: files.  Everything else is either supplied explicitly below or absent.
INHERITED = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    # Python needs these to find its own standard library when dbt is installed
    # in a virtualenv; without them the interpreter starts and cannot import.
    "PYTHONHOME",
    "VIRTUAL_ENV",
    # Windows cannot start a process without these.  Only relevant for a
    # developer running the worker outside a container.
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "TEMP",
    "TMP",
)

#: Never passed through, even if some future entry in INHERITED would match.
#: Belt and braces against a careless addition above.
DENIED = re.compile(
    r"(SECRET|PASSWORD|TOKEN|CREDENTIAL|PRIVATE_KEY|API_KEY|DATABASE_URL|"
    r"AIRBYTE|OPENAI|ANTHROPIC|JWT|ENCRYPTION|AWS_|GOOGLE_APPLICATION)",
    re.IGNORECASE,
)


def subprocess_env(
    *,
    profiles_dir: Path,
    project_dir: Path,
    target_path: Path,
    tmpdir: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the minimal environment for one dbt invocation.

    ``extra`` exists for adapter credentials that dbt only accepts through the
    environment.  It is applied after the allowlist and still filtered by
    :data:`DENIED`, so a caller cannot use it to reintroduce an AppBI secret by
    accident -- an adapter needing a genuinely secret-shaped variable must pass
    it through the profile instead, which is where credentials belong.
    """
    env: dict[str, str] = {}
    for name in INHERITED:
        value = os.environ.get(name)
        if value is not None and not DENIED.search(name):
            env[name] = value

    # A container image with no PATH would fail to find `dbt` at all, and the
    # error would arrive as FileNotFoundError rather than something diagnosable.
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", str(tmpdir))
    env.setdefault("LANG", "C.UTF-8")

    env.update({
        "DBT_PROFILES_DIR": str(profiles_dir),
        "DBT_PROJECT_DIR": str(project_dir),
        "DBT_TARGET_PATH": str(target_path),
        # Keep dbt's own caches inside the disposable workspace.  Left at the
        # default they land in $HOME and outlive the run.
        "DBT_LOG_PATH": str(target_path / "logs"),
        "TMPDIR": str(tmpdir),
        "TEMP": str(tmpdir),
        "TMP": str(tmpdir),
        # dbt phones home on start unless told not to.  A data warehouse tool
        # in somebody's VPC should not make an unsolicited outbound request.
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "False",
        "DO_NOT_TRACK": "1",
        # Deterministic, machine-readable output.
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
    })

    for name, value in (extra or {}).items():
        if DENIED.search(name):
            continue
        env[name] = str(value)
    return env


def redact(text: str, secrets: list[str]) -> str:
    """Remove credential values from text before it is stored or displayed.

    Longest first: a service account JSON contains its own private key as a
    substring, and replacing the short value first would leave the long one
    partially masked and still recognisable.
    """
    sanitized = text
    for value in sorted(
        {item for item in secrets if item and len(item) >= 8}, key=len, reverse=True,
    ):
        sanitized = sanitized.replace(value, "[REDACTED]")
    return sanitized
