"""The dbt command contract.

The browser never sends a command line.  It sends a structured request -- a
command name from a closed set, a selector string, a few typed options -- and
this module turns that into an argv array.  There is no interpolation anywhere
in the path from request to process, and no shell, so a selector containing
`; rm -rf /` is a selector that matches nothing rather than a second command.

Selectors are passed to dbt verbatim and deliberately not parsed here.  dbt's
node-selection syntax has graph operators, set unions, `tag:`/`path:`/`config:`
methods and state comparison; re-implementing any of it would produce a subtly
different graph from the one dbt actually runs.  Validation is confined to what
argv safety requires -- length, control characters, no leading dash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ValidationError

#: Every command the product exposes, and how it behaves.
#:
#: `writes` decides serialisation: two concurrent builds against one
#: project+environment can race on the same relations, so those take a lock.
#: Reads do not, or the editor would feel broken while a build ran.
#:
#: `privileged` marks commands that can execute arbitrary maintenance SQL and
#: are gated on OPERATE rather than EDIT.
@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    writes: bool = False
    supports_select: bool = True
    supports_exclude: bool = True
    supports_full_refresh: bool = False
    privileged: bool = False
    #: Whether the invocation is expected to leave a run_results.json behind.
    produces_run_results: bool = True
    label: str = ""


COMMANDS: dict[str, CommandSpec] = {
    "parse": CommandSpec(
        "parse", ("parse",), supports_select=False, supports_exclude=False,
        produces_run_results=False, label="Parse",
    ),
    "deps": CommandSpec(
        "deps", ("deps",), supports_select=False, supports_exclude=False,
        produces_run_results=False, label="Install dependencies",
    ),
    "debug": CommandSpec(
        "debug", ("debug",), supports_select=False, supports_exclude=False,
        produces_run_results=False, label="Check connection",
    ),
    "ls": CommandSpec(
        "ls", ("ls",), produces_run_results=False, label="List resources",
    ),
    "compile": CommandSpec("compile", ("compile",), label="Compile"),
    "show": CommandSpec(
        "show", ("show",), supports_exclude=False, produces_run_results=False,
        label="Preview",
    ),
    "run": CommandSpec(
        "run", ("run",), writes=True, supports_full_refresh=True, label="Run",
    ),
    "build": CommandSpec(
        "build", ("build",), writes=True, supports_full_refresh=True, label="Build",
    ),
    "test": CommandSpec("test", ("test",), writes=True, label="Test"),
    "seed": CommandSpec(
        "seed", ("seed",), writes=True, supports_full_refresh=True, label="Load seeds",
    ),
    "snapshot": CommandSpec("snapshot", ("snapshot",), writes=True, label="Snapshot"),
    "source-freshness": CommandSpec(
        "source-freshness", ("source", "freshness"), label="Source freshness",
    ),
    "docs-generate": CommandSpec(
        "docs-generate", ("docs", "generate"), produces_run_results=False,
        label="Build docs",
    ),
    "clone": CommandSpec("clone", ("clone",), writes=True, privileged=True, label="Clone"),
    "run-operation": CommandSpec(
        "run-operation", ("run-operation",), writes=True, privileged=True,
        supports_select=False, supports_exclude=False, produces_run_results=False,
        label="Run macro",
    ),
    "retry": CommandSpec(
        "retry", ("retry",), writes=True, supports_select=False, supports_exclude=False,
        label="Retry",
    ),
}

#: Commands a schedule or a release verification may fire.  `run-operation` and
#: `clone` are excluded: unattended execution of arbitrary macros is not
#: something a cron entry should be able to arrange.
SCHEDULABLE = frozenset({"build", "run", "test", "seed", "snapshot", "source-freshness"})

#: Commands allowed against a protected (production) environment from the UI.
#: Everything else in production goes through a release.
PRODUCTION_ALLOWED = frozenset({
    "build", "run", "test", "seed", "snapshot", "source-freshness", "compile",
    "parse", "deps", "docs-generate", "ls", "debug", "retry",
})

# A selector is opaque to us but must still survive being an argv element.
# Control characters and NULs cannot appear in one; a leading `-` would be read
# by dbt as a flag rather than a value.
_SELECTOR_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")
_MACRO_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")
_TARGET_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MAX_SELECTOR_LENGTH = 1000


@dataclass(slots=True)
class DbtCommand:
    """A validated command, ready to become argv."""

    command: str
    selector: str | None = None
    exclude: str | None = None
    full_refresh: bool = False
    #: dbt `--vars`, merged from the environment and the request.
    vars: dict[str, Any] = field(default_factory=dict)
    #: `dbt show --limit`
    limit: int | None = None
    #: `dbt run-operation <macro> --args <json>`
    macro: str | None = None
    macro_args: dict[str, Any] = field(default_factory=dict)
    #: `dbt ls --output json` and friends.
    output_json: bool = False
    #: A named selector out of `selectors.yml`, mutually exclusive with `selector`.
    selector_name: str | None = None
    #: `dbt build --defer --state <path>` for state comparison.
    defer_state: bool = False

    @property
    def spec(self) -> CommandSpec:
        return COMMANDS[self.command]

    @property
    def writes(self) -> bool:
        return self.spec.writes

    @property
    def privileged(self) -> bool:
        return self.spec.privileged


def validate_command(
    command: str,
    *,
    selector: str | None = None,
    exclude: str | None = None,
    full_refresh: bool = False,
    vars: dict[str, Any] | None = None,
    limit: int | None = None,
    macro: str | None = None,
    macro_args: dict[str, Any] | None = None,
    selector_name: str | None = None,
    output_json: bool = False,
    defer_state: bool = False,
) -> DbtCommand:
    """Turn a request into a command, or explain exactly why it is not one."""
    key = (command or "").strip().lower()
    spec = COMMANDS.get(key)
    if spec is None:
        raise ValidationError(
            f"`{command}` is not a dbt command this product runs.",
            code="TRANSFORM_COMMAND_UNKNOWN",
            details={"command": command, "supported": sorted(COMMANDS)},
        )

    selector = _selector(selector, "select") if selector else None
    exclude = _selector(exclude, "exclude") if exclude else None

    if selector and not spec.supports_select:
        raise ValidationError(
            f"`dbt {spec.name}` does not take a selector.",
            code="TRANSFORM_COMMAND_SELECTOR_UNSUPPORTED", details={"command": key},
        )
    if exclude and not spec.supports_exclude:
        raise ValidationError(
            f"`dbt {spec.name}` does not take an exclusion.",
            code="TRANSFORM_COMMAND_EXCLUDE_UNSUPPORTED", details={"command": key},
        )
    if selector and selector_name:
        raise ValidationError(
            "Use either a selector expression or a named selector, not both.",
            code="TRANSFORM_COMMAND_SELECTOR_CONFLICT",
        )
    if selector_name is not None and not _MACRO_NAME.match(selector_name):
        raise ValidationError(
            "That named selector is not a valid name.",
            code="TRANSFORM_COMMAND_SELECTOR_NAME_INVALID",
        )
    if full_refresh and not spec.supports_full_refresh:
        raise ValidationError(
            f"`dbt {spec.name}` has no full refresh.",
            code="TRANSFORM_COMMAND_FULL_REFRESH_UNSUPPORTED", details={"command": key},
        )
    if key == "run-operation":
        if not macro or not _MACRO_NAME.match(macro):
            raise ValidationError(
                "Running a macro needs the macro's name.",
                code="TRANSFORM_COMMAND_MACRO_REQUIRED",
            )
    elif macro:
        raise ValidationError(
            "Only `run-operation` takes a macro name.",
            code="TRANSFORM_COMMAND_MACRO_UNSUPPORTED",
        )
    if limit is not None and (limit < 1 or limit > 5000):
        raise ValidationError(
            "Preview row limit must be between 1 and 5000.",
            code="TRANSFORM_COMMAND_LIMIT_INVALID",
        )

    return DbtCommand(
        command=key,
        selector=selector,
        exclude=exclude,
        full_refresh=full_refresh,
        vars=_vars(vars or {}),
        limit=limit,
        macro=macro,
        macro_args=macro_args or {},
        selector_name=selector_name,
        output_json=output_json,
        defer_state=defer_state,
    )


def build_argv(
    command: DbtCommand,
    *,
    target: str,
    profiles_dir: str,
    project_dir: str,
    target_path: str,
    state_path: str | None = None,
) -> list[str]:
    """The exact argv dbt is executed with.

    Every element is either a literal from this module or a value already
    validated above.  Nothing is joined into a string, so there is no quoting to
    get wrong and nothing for a shell to re-interpret -- there is no shell.
    """
    import json

    spec = COMMANDS[command.command]
    argv: list[str] = [
        "dbt", "--no-use-colors", "--log-format", "text",
        *spec.argv,
        "--project-dir", project_dir,
        "--profiles-dir", profiles_dir,
        "--target", _target(target),
        "--target-path", target_path,
    ]

    if command.selector:
        argv += ["--select", command.selector]
    if command.selector_name:
        argv += ["--selector", command.selector_name]
    if command.exclude:
        argv += ["--exclude", command.exclude]
    if command.full_refresh:
        argv.append("--full-refresh")
    if command.vars:
        argv += ["--vars", json.dumps(command.vars, separators=(",", ":"))]

    if command.command == "show":
        argv += [
            "--limit", str(command.limit or 200),
            "--output", "json",
            # Without this a `show` of a model also selects the tests attached
            # to it, and dbt then refuses because tests have nothing to show.
            "--indirect-selection", "empty",
            "--quiet",
        ]
    if command.command == "ls":
        argv += ["--output", "json", "--quiet"]
    if command.command == "run-operation":
        argv.append(str(command.macro))
        if command.macro_args:
            argv += ["--args", json.dumps(command.macro_args, separators=(",", ":"))]
    if command.defer_state and state_path:
        argv += ["--defer", "--state", state_path]

    return argv


def _selector(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValidationError(
            f"The {field_name} expression is empty.",
            code="TRANSFORM_COMMAND_SELECTOR_INVALID",
        )
    if len(text) > MAX_SELECTOR_LENGTH:
        raise ValidationError(
            f"That {field_name} expression is too long.",
            code="TRANSFORM_COMMAND_SELECTOR_INVALID",
        )
    if _SELECTOR_FORBIDDEN.search(text):
        raise ValidationError(
            f"The {field_name} expression contains characters that are not allowed.",
            code="TRANSFORM_COMMAND_SELECTOR_INVALID",
        )
    if text.startswith("-"):
        # dbt would read this as a flag.  Exclusion has its own field.
        raise ValidationError(
            f"A {field_name} expression cannot start with `-`. "
            "Use the exclude field to leave resources out.",
            code="TRANSFORM_COMMAND_SELECTOR_INVALID",
        )
    return text


def _vars(value: dict[str, Any]) -> dict[str, Any]:
    """dbt vars, checked for shape rather than meaning.

    Values stay whatever JSON type they were -- a var may legitimately be a
    number, a list or an object -- but a key has to be a plausible identifier,
    because the alternative is smuggling YAML structure through a key name.
    """
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if not _VAR_NAME.match(name):
            raise ValidationError(
                f"`{name}` is not a valid variable name.",
                code="TRANSFORM_COMMAND_VAR_INVALID", details={"var": name},
            )
        cleaned[name] = item
    return cleaned


def _target(value: str) -> str:
    if not _TARGET_NAME.match(value or ""):
        raise ValidationError(
            "Environment target name is invalid.",
            code="TRANSFORM_ENVIRONMENT_TARGET_INVALID",
        )
    return value
