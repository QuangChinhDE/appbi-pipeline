"""Product-facing transformation adapter contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


CancelCheck = Callable[[], Awaitable[bool]]
#: Called with the redacted log so far while the engine is still running, so a
#: reader watching the Logs tab sees output before the process exits.
LogSink = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class TransformationRequest:
    run_id: str
    operation: str
    project_files: dict[str, str]
    profile: dict[str, Any]
    secret_values: list[str] = field(default_factory=list)
    selected_model: str | None = None
    preview_limit: int = 200
    output_schema: str = "analytics"
    # VALIDATE probes these explicitly rather than inferring them from the
    # compiled graph, which only sees sources a model already references.
    validate_schemas: list[str] = field(default_factory=list)
    validate_relations: list[dict[str, str]] = field(default_factory=list)
    # Rebuilds incremental models from scratch. Without it a model whose SQL or
    # columns changed can never be corrected from inside the product.
    full_refresh: bool = False


@dataclass(slots=True)
class TransformationResult:
    succeeded: bool
    cancelled: bool = False
    timed_out: bool = False
    exit_code: int | None = None
    log_path: str | None = None
    log_text: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    technical_message: str | None = None
    # {name, resource_type, path?, line?} when the engine identified where the
    # failure is, so the editor can point at it instead of printing a traceback.
    error_location: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    run_results: dict[str, Any] | None = None
    compiled_sql: dict[str, str] = field(default_factory=dict)
    preview: dict[str, Any] | None = None


class TransformationEngineAdapter(Protocol):
    async def execute(
        self, request: TransformationRequest, *, cancel_check: CancelCheck,
        log_sink: LogSink | None = None,
    ) -> TransformationResult: ...

    async def cancel(self, run_id: str) -> bool: ...

    def runtime_version(self) -> str: ...

    def log_file(self, run_id: str) -> Path: ...
