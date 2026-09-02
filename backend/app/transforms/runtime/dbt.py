"""The dbt Core subprocess adapter.

One invocation, one private workspace, one process, one argv array built from a
validated command.  The V1 adapter's good properties are kept deliberately --
subprocess isolation, cancellation, timeout, streaming logs, artifact collection
-- and its ``_command()`` operation enum is gone, replaced by the typed command
contract in :mod:`app.transforms.runtime.commands`.

Two things changed for safety rather than features: the process environment is
now an allowlist instead of ``{**os.environ}``, and the log is streamed to a
sink and to storage rather than accumulated whole in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.transforms.runtime import security
from app.transforms.runtime.commands import DbtCommand, build_argv
from app.transforms.runtime.profiles import ResolvedProfile
from app.transforms.runtime.workspace import MaterialisedWorkspace

CancelCheck = Callable[[], Awaitable[bool]]
#: Called with the redacted log so far while the process is still running, so a
#: reader watching the Logs panel sees output before the process exits.
LogSink = Callable[[str], Awaitable[None]]

#: How much log text is kept in memory while a run is in flight.
#:
#: A `--full-refresh` build of a large project can emit hundreds of megabytes.
#: V1 held all of it in a list of chunks and joined the whole thing every two
#: seconds to publish a partial log, which is quadratic in the log size; on a
#: long run that alone can dominate the worker's CPU.  The tail is what anybody
#: reads live, and the complete log goes to object storage at the end.
LIVE_TAIL_BYTES = 256 * 1024


@dataclass(slots=True)
class DbtResult:
    succeeded: bool
    cancelled: bool = False
    timed_out: bool = False
    exit_code: int | None = None
    log_text: str = ""
    #: Set when the whole log was written to object storage.
    log_storage_key: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    technical_message: str | None = None
    #: {path?, line?, unique_id?, name?, resource_type?} when dbt said where.
    error_location: dict[str, Any] = field(default_factory=dict)
    #: Raw artifact documents dbt left in target/.  Parsed by the artifact
    #: readers, not here -- this class only collects what was produced.
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: `dbt show` output and `dbt ls` output, recovered from stdout.
    preview: dict[str, Any] | None = None
    listing: list[Any] = field(default_factory=list)


class DbtRuntime:
    """Runs dbt.  Holds no state beyond the processes currently in flight."""

    def __init__(self) -> None:
        self.workspace_root = Path(settings.transform_workspace_dir)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def runtime_version(self) -> str:
        return settings.dbt_core_version

    async def cancel(self, invocation_id: str) -> bool:
        process = self._processes.get(invocation_id)
        if process is None or process.returncode is not None:
            return False
        await self._terminate(process)
        return True

    async def execute(
        self,
        *,
        invocation_id: str,
        command: DbtCommand,
        workspace: MaterialisedWorkspace,
        profile: ResolvedProfile,
        target_name: str,
        cancel_check: CancelCheck,
        log_sink: LogSink | None = None,
        timeout_seconds: int | None = None,
    ) -> DbtResult:
        """Run one command in an already-materialised workspace.

        The caller owns the workspace and its cleanup: a `deps` followed by a
        `parse` reuses one workspace, and installing packages twice for what the
        user experienced as one action would double the wait.
        """
        self._write_profile(workspace, profile, target_name)
        argv = build_argv(
            command,
            target=target_name,
            profiles_dir=str(workspace.profiles_dir),
            project_dir=str(workspace.project_dir),
            target_path=str(workspace.target_path),
        )
        env = security.subprocess_env(
            profiles_dir=workspace.profiles_dir,
            project_dir=workspace.project_dir,
            target_path=workspace.target_path,
            tmpdir=workspace.tmpdir,
        )
        limit = timeout_seconds or settings.transform_timeout_seconds

        process: asyncio.subprocess.Process | None = None
        collector = _LogCollector(profile.secret_values)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workspace.project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                # A process group, so terminating a build kills the adapter's
                # child queries too rather than orphaning them.
                start_new_session=os.name != "nt",
            )
            self._processes[invocation_id] = process
            cancelled, timed_out = await self._supervise(
                process, collector, cancel_check=cancel_check, log_sink=log_sink,
                limit=limit,
            )
            await process.wait()

            log_text = collector.text()
            artifacts = self._collect_artifacts(workspace.target_path)
            succeeded = process.returncode == 0 and not cancelled and not timed_out
            summary, technical, location = _diagnose(artifacts.get("run_results"), log_text)

            return DbtResult(
                succeeded=succeeded,
                cancelled=cancelled,
                timed_out=timed_out,
                exit_code=process.returncode,
                log_text=log_text,
                error_code=None if succeeded else _error_code(
                    cancelled, timed_out, command.command,
                ),
                error_summary=None if succeeded else summary,
                technical_message=None if succeeded else technical,
                error_location={} if succeeded else location,
                artifacts=artifacts,
                preview=_preview(log_text) if command.command == "show" else None,
                listing=_listing(log_text) if command.command == "ls" else [],
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await self._terminate(process)
            raise
        finally:
            self._processes.pop(invocation_id, None)

    async def _supervise(
        self,
        process: asyncio.subprocess.Process,
        collector: "_LogCollector",
        *,
        cancel_check: CancelCheck,
        log_sink: LogSink | None,
        limit: int,
    ) -> tuple[bool, bool]:
        """Drain output, publish it periodically, honour cancel and timeout."""
        reader = asyncio.create_task(collector.drain(process))
        cancelled = timed_out = False
        loop = asyncio.get_running_loop()
        started = loop.time()
        next_publish = started
        published_at = -1

        while not reader.done():
            await asyncio.sleep(0.4)
            now = loop.time()
            # Publishing on a timer, not per line: a build emits thousands of
            # lines and one write per line would be one database round trip per
            # line for a reader who is polling every few seconds anyway.
            if log_sink is not None and now >= next_publish and collector.version != published_at:
                published_at = collector.version
                next_publish = now + 2.0
                with contextlib.suppress(Exception):
                    await log_sink(collector.text())
            if await cancel_check():
                cancelled = True
                await self._terminate(process)
                break
            if now - started > limit:
                timed_out = True
                await self._terminate(process)
                break

        with contextlib.suppress(asyncio.CancelledError):
            await reader
        if log_sink is not None:
            with contextlib.suppress(Exception):
                await log_sink(collector.text())
        return cancelled, timed_out

    @staticmethod
    def _write_profile(
        workspace: MaterialisedWorkspace, profile: ResolvedProfile, target_name: str,
    ) -> None:
        """Write profiles.yml, and a keyfile when the adapter needs one."""
        document = json.loads(json.dumps(profile.document))
        if profile.keyfile_json is not None:
            keyfile = workspace.profiles_dir / "service-account.json"
            keyfile.write_text(json.dumps(profile.keyfile_json), encoding="utf-8")
            with contextlib.suppress(OSError):
                keyfile.chmod(0o600)
            for entry in document.values():
                for output in entry.get("outputs", {}).values():
                    if output.get("method") == "service-account":
                        output["keyfile"] = str(keyfile)
        path = workspace.profiles_dir / "profiles.yml"
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)

    @staticmethod
    def _collect_artifacts(target_path: Path) -> dict[str, dict[str, Any]]:
        """Read whatever dbt wrote to target/.

        Which artifacts appear depends on the command: `parse` writes a
        manifest, `build` writes run_results too, `docs generate` writes a
        catalog, `source freshness` writes sources.json.  Absence is normal, so
        a missing file is not an error here.
        """
        wanted = {
            "manifest": "manifest.json",
            "run_results": "run_results.json",
            "catalog": "catalog.json",
            "sources": "sources.json",
            "semantic_manifest": "semantic_manifest.json",
        }
        found: dict[str, dict[str, Any]] = {}
        for key, filename in wanted.items():
            path = target_path / filename
            try:
                found[key] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return found

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if os.name == "nt":
                process.terminate()
            else:
                # The group, not the process: dbt spawns adapter children that
                # hold warehouse connections, and killing only the parent leaves
                # a query running and paying.
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            await process.wait()


class _LogCollector:
    """Accumulates a bounded tail of redacted output.

    Redaction happens once per published snapshot rather than per chunk: a
    service account JSON can straddle a chunk boundary, so redacting chunk by
    chunk would leave half a private key in the log.
    """

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = secrets
        self._buffer = bytearray()
        self._dropped = 0
        self.version = 0

    async def drain(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.read(16384)
            if not chunk:
                return
            self._buffer.extend(chunk)
            if len(self._buffer) > LIVE_TAIL_BYTES:
                excess = len(self._buffer) - LIVE_TAIL_BYTES
                del self._buffer[:excess]
                self._dropped += excess
            self.version += 1

    def text(self) -> str:
        body = security.redact(
            self._buffer.decode("utf-8", errors="replace"), self._secrets,
        )
        if self._dropped:
            return (
                f"[{self._dropped // 1024} KB of earlier output omitted; "
                "the full log is attached to this run]\n" + body
            )
        return body


# ── diagnostics ───────────────────────────────────────────────────────────

# dbt reports a compile failure only in its log -- no run_results.json is
# written -- so the one place the model and line number exist is this text.
_BANNER = re.compile(
    r"(?P<kind>\w+(?: \w+)*) Error in (?P<resource>\w+) (?P<name>[\w.]+)"
    r"(?: \((?P<path>[^)]+)\))?",
)
_LINE = re.compile(r"\bline (?P<line>\d+)", re.IGNORECASE)
# dbt does not always name the model on an "... Error in model x" banner.
# A missing ref -- the most common authoring mistake -- arrives as a bare
# "Compilation Error" followed by a sentence naming both models, so the banner
# alone leaves the user with nothing to act on.
_MISSING_REF = re.compile(
    r"Model '(?:[\w.]*\.)?(?P<model>\w+)'"
    r"[^\n]*?depends on a node named '(?P<missing>[^']+)'"
    r" which was not found",
)
_YAML_ERROR = re.compile(
    r"(?:Compilation Error|Parsing Error)\s*\n\s*(?P<detail>.+?)\n", re.DOTALL,
)


def _diagnose(
    run_results: dict[str, Any] | None, logs: str,
) -> tuple[str, str, dict[str, Any]]:
    """Turn a failure into something a person can act on.

    run_results is preferred when it exists: it names the resource and carries
    the adapter's own message.  A parse or compile failure never produces one,
    which is why the log is parsed at all.
    """
    if run_results:
        failures = [
            item for item in run_results.get("results", [])
            if isinstance(item, dict)
            and str(item.get("status", "")).lower() not in ("success", "pass", "skipped")
        ]
        if failures:
            first = failures[0]
            message = str(first.get("message") or "A resource failed.")
            unique_id = str(first.get("unique_id") or "")
            location: dict[str, Any] = {}
            if unique_id:
                location = {
                    "unique_id": unique_id,
                    "resource_type": unique_id.split(".", 1)[0] or None,
                    "name": unique_id.rsplit(".", 1)[-1],
                }
            line = _LINE.search(message)
            if line:
                location["line"] = int(line.group("line"))
            return message[:1000], message[:4000], location

    tail = "\n".join(logs.splitlines()[-120:])

    missing = _MISSING_REF.search(tail)
    if missing:
        # A missing ref gets the clearer message, but it should not cost the
        # reader the line number, so this branch reads the line itself.
        where: dict[str, Any] = {
            "name": missing.group("model"), "resource_type": "model",
            "missing_ref": missing.group("missing"),
        }
        line = _LINE.search(tail[missing.end():]) or _LINE.search(tail)
        if line:
            where["line"] = int(line.group("line"))
        return (
            f"`{missing.group('model')}` references "
            f"`{missing.group('missing')}`, which this project does not contain.",
            tail[:4000],
            where,
        )

    banner = _BANNER.search(tail)
    if banner:
        location = {
            "name": banner.group("name"),
            "resource_type": banner.group("resource"),
            "path": banner.group("path"),
        }
        line = _LINE.search(tail[banner.end():])
        if line:
            location["line"] = int(line.group("line"))
        detail = next(
            (item.strip() for item in tail[banner.end():].splitlines()
             if item.strip() and not item.strip().startswith("line ")),
            "",
        )
        where = banner.group("name")
        if location.get("line"):
            where += f", line {location['line']}"
        summary = f"{banner.group('kind')} Error in {where}"
        if detail:
            summary = f"{summary}: {detail}"
        return summary[:1000], tail[:4000], location

    yaml_error = _YAML_ERROR.search(tail)
    if yaml_error:
        detail = yaml_error.group("detail").strip()
        location = {}
        path = re.search(r"([\w./-]+\.ya?ml)", detail)
        if path:
            location["path"] = path.group(1)
        line = _LINE.search(detail)
        if line:
            location["line"] = int(line.group("line"))
        return detail[:1000], tail[:4000], location

    return "dbt could not complete this command.", tail[:4000], {}


def _error_code(cancelled: bool, timed_out: bool, command: str) -> str:
    if cancelled:
        return "TRANSFORM_CANCELLED"
    if timed_out:
        return "TRANSFORM_TIMEOUT"
    return f"TRANSFORM_{command.upper().replace('-', '_')}_FAILED"


def _preview(stdout: str) -> dict[str, Any] | None:
    """Recover the JSON document `dbt show` prints among its log lines.

    stderr is folded into this stream, so a single warning line is enough to
    break a whole-document parse -- and dbt pretty-prints, which defeats a
    line-by-line parse too.  Both are tried, then a brace-matching scan picks
    the JSON object out of the surrounding noise.
    """
    def accept(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict) and any(
            key in payload for key in ("data", "rows", "show")
        ):
            return payload
        return None

    with contextlib.suppress(ValueError):
        found = accept(json.loads(stdout))
        if found is not None:
            return found
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        with contextlib.suppress(ValueError):
            found = accept(json.loads(line))
            if found is not None:
                return found
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[index:])
        except ValueError:
            continue
        found = accept(payload)
        if found is not None:
            return found
    return None


def _listing(stdout: str) -> list[Any]:
    """`dbt ls --output json` prints one JSON object per line."""
    items: list[Any] = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        with contextlib.suppress(ValueError):
            items.append(json.loads(text))
    return items
