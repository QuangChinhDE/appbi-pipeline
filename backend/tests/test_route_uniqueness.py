"""No router registers the same method and path twice.

This is the test that was missing on 2026-09-15, when `api/v1/auth.py` was found
carrying two copies of the same ten handlers -- lines 321-568 duplicated at
569-823. FastAPI registers both and serves the first, so the second set was dead
code that no longer matched the first: its `remove_member` still inlined the
pre-refactor body, called a `_assert_not_last_owner` that had been deleted, and
was missing the `assert_someone_can_administer` guard added when the logic moved
into `member_service.remove()`. Reading either copy gave you a confident and
wrong answer about what a DELETE on that path does.

Nothing caught it. FastAPI itself noticed -- generating the OpenAPI schema
emitted nine `Duplicate Operation ID` warnings -- but warnings on stderr during
a build nobody reads are not a gate.

Structural on purpose: it reads the decorators out of the source rather than
importing the app, so it needs no database, no settings and no engine, and runs
with the rest of the offline suite.
"""

from __future__ import annotations

import ast
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"

#: The decorator methods that register a route.
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _router_modules() -> list[pathlib.Path]:
    """Every module that registers routes: api/v1 plus Transform's own router."""
    paths = sorted((APP / "api").rglob("*.py"))
    paths += sorted(APP.glob("transforms/api.py"))
    return [p for p in paths if "__pycache__" not in p.parts]


def _registrations(path: pathlib.Path) -> dict[tuple[str, str, str], list[str]]:
    """(router name, METHOD, path) -> the functions registered against it."""
    found: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            # @<router>.<method>("<path>", ...)
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            route = decorator.args[0].value
            if not isinstance(route, str):
                continue
            found[(func.value.id, func.attr.upper(), route)].append(node.name)
    return found


def test_no_router_registers_the_same_route_twice() -> None:
    offenders: list[str] = []
    checked = 0

    for path in _router_modules():
        registrations = _registrations(path)
        checked += len(registrations)
        for (router, method, route), handlers in sorted(registrations.items()):
            if len(handlers) > 1:
                offenders.append(
                    f"{path.relative_to(ROOT)}: {router}.{method.lower()}({route!r}) "
                    f"registered by {', '.join(handlers)}"
                )

    assert checked, "parsed no route registrations at all -- the test is not looking"
    assert not offenders, (
        "FastAPI serves the first registration and silently keeps the rest, so a "
        "duplicate is dead code that drifts away from the live one:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_defines_the_same_handler_name_twice() -> None:
    """The other half: a redefined handler rebinds the name for direct callers.

    `_current_user_payload` existed twice in auth.py. Routing was unaffected --
    both copies were identical -- but any module-level call resolves to the
    second, so the live routes and the helper came from different copies.
    """
    offenders: list[str] = []

    for path in _router_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seen: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in seen:
                    offenders.append(
                        f"{path.relative_to(ROOT)}: {node.name}() defined at line "
                        f"{seen[node.name]} and again at {node.lineno}"
                    )
                seen[node.name] = node.lineno

    assert not offenders, "\n  ".join(["duplicate top-level definitions:"] + offenders)
