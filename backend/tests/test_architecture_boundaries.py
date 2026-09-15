"""The three boundaries the product rests on, asserted instead of remembered.

Each of these is currently intact -- zero violations across the tree. That is
exactly why they are worth a test: an invariant at zero is invisible, nobody
notices the first exception, and by the tenth it is "how the code works".

They are structural. They read source text, cost nothing, and run offline with
the rest of the suite.

1. No engine-specific import outside `app/adapters/`.

   `IntegrationEngineAdapter` is the single boundary that understands an engine
   (guardrail 5). It is what makes "upgrade Airbyte" a change to one package
   plus a contract run, rather than a change spread across the product. One
   `from app.adapters.airbyte_protocol import ...` in a service is all it takes
   to convert the abstraction into decoration -- and it would read as perfectly
   reasonable in review. `SQL_DIRECT` exists to keep the interface honest; a
   service that imports Airbyte directly is a service `sql_direct` can never
   satisfy.

2. No `HTTPException` in `api/v1/`.

   Every failure the frontend can see travels as an `AppError` subclass, which
   carries a code, an `ErrorCategory` and the `remediation.action` the UI turns
   into its primary CTA. A raw `HTTPException` produces FastAPI's default
   `{"detail": ...}`, which the client's error handling does not understand, so
   the screen loses its remediation path and shows whatever string was handy.

3. Every workspace-scoped query key is prefixed `['workspace', ws]`.

   `queryKeys.ts` says it plainly: every key is workspace-scoped so a workspace
   switch can evict an entire tenant's cache in one call. A key that takes `ws`
   but does not put it first is not evicted by that call, so it survives the
   switch and serves one tenant's data to another.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
QUERY_KEYS = ROOT / "frontend" / "src" / "lib" / "queryKeys.ts"

#: Engine implementations. Importing one of these by name means the importer has
#: stopped depending on the Protocol and started depending on an engine.
ENGINE_MODULES = ("airbyte_protocol", "airbyte_api", "sql_direct")

#: `registry` is the sanctioned way in: it returns the Protocol, not an engine.
ADAPTERS = APP / "adapters"


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_engine_import_outside_adapters() -> None:
    pattern = re.compile(
        r"^\s*(?:from\s+app\.adapters\.(%s)|import\s+app\.adapters\.(%s))"
        % ("|".join(ENGINE_MODULES), "|".join(ENGINE_MODULES)),
        re.M,
    )
    offenders: list[str] = []
    for path in _python_files(APP):
        if ADAPTERS in path.parents or path == ADAPTERS:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            line = match.group(0).strip()
            offenders.append(f"{path.relative_to(ROOT)}: {line}")

    assert not offenders, (
        "These modules import an engine directly instead of depending on "
        "IntegrationEngineAdapter via adapters.registry.get_adapter():\n  "
        + "\n  ".join(offenders)
    )


def test_routers_raise_app_errors_not_http_exception() -> None:
    offenders: list[str] = []
    for path in _python_files(APP / "api" / "v1"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "HTTPException" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    assert not offenders, (
        "Raise an AppError subclass from app/core/errors.py -- or "
        "error_from_matrix(code) for a failure the UI must react to -- so the "
        "response keeps its category and remediation action:\n  "
        + "\n  ".join(offenders)
    )


#: `name: (ws: string, ...) => [...]` up to the closing bracket. The body is
#: matched across lines because the longer keys wrap.
QK_ENTRY = re.compile(
    r"^\s{2}(\w+):\s*\(\s*ws:\s*string[^)]*\)\s*=>\s*(\[.*?\])\s*as const",
    re.M | re.S,
)


def test_workspace_scoped_query_keys_carry_the_workspace_prefix() -> None:
    if not QUERY_KEYS.is_file():
        pytest.skip("frontend/src/lib/queryKeys.ts is not present")

    source = QUERY_KEYS.read_text(encoding="utf-8")
    entries = QK_ENTRY.findall(source)
    assert entries, "parsed no workspace-scoped keys out of queryKeys.ts"

    offenders = [
        f"qk.{name} -> {body.split(chr(10))[0].strip()}"
        for name, body in entries
        if not re.match(r"\[\s*'workspace'\s*,\s*ws\b", body)
    ]

    assert not offenders, (
        "A key that takes `ws` must start ['workspace', ws, ...] or a workspace "
        "switch will not evict it:\n  " + "\n  ".join(offenders)
    )
