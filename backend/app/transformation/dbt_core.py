"""Pinned dbt Core subprocess adapter.

Every request receives a private project/profile directory.  Credentials only
exist in that directory for the life of the process and are never copied into
AppBI product rows or exported projects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.transformation.base import CancelCheck, TransformationRequest, TransformationResult


class DbtCoreAdapter:
    def __init__(self) -> None:
        self.workspace_root = Path(settings.transform_workspace_dir)
        self.log_root = Path(settings.transform_log_dir)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def runtime_version(self) -> str:
        return settings.dbt_core_version

    def log_file(self, run_id: str) -> Path:
        return self.log_root / f"{run_id}.log"

    async def cancel(self, run_id: str) -> bool:
        process = self._processes.get(run_id)
        if process is None or process.returncode is not None:
            return False
        await self._terminate(process)
        return True

    async def execute(
        self, request: TransformationRequest, *, cancel_check: CancelCheck,
    ) -> TransformationResult:
        workdir = Path(tempfile.mkdtemp(prefix=f"appbi-{request.run_id[:8]}-", dir=self.workspace_root))
        log_path = self.log_file(request.run_id)
        process: asyncio.subprocess.Process | None = None
        try:
            self._write_files(workdir, request)
            command = self._command(request)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "DBT_PROFILES_DIR": str(workdir / ".profiles")},
                start_new_session=True,
            )
            self._processes[request.run_id] = process
            communicate = asyncio.create_task(process.communicate())
            cancelled = timed_out = False
            started = asyncio.get_running_loop().time()
            while not communicate.done():
                await asyncio.sleep(0.5)
                if await cancel_check():
                    cancelled = True
                    await self._terminate(process)
                    break
                if asyncio.get_running_loop().time() - started > settings.transform_timeout_seconds:
                    timed_out = True
                    await self._terminate(process)
                    break
            stdout, _ = await communicate
            sanitized = self._redact(stdout.decode("utf-8", errors="replace"), request.secret_values)
            log_path.write_text(sanitized, encoding="utf-8")
            manifest = self._read_json(workdir / "target" / "manifest.json")
            run_results = self._read_json(workdir / "target" / "run_results.json")
            compiled = self._compiled_sql(manifest)
            preview = self._preview(sanitized) if request.operation == "PREVIEW" else None
            succeeded = process.returncode == 0 and not cancelled and not timed_out
            error_summary, technical, location = self._error(run_results, sanitized)
            return TransformationResult(
                succeeded=succeeded,
                cancelled=cancelled,
                timed_out=timed_out,
                exit_code=process.returncode,
                log_path=str(log_path),
                log_text=sanitized[-2_000_000:],
                error_code=None if succeeded else self._error_code(cancelled, timed_out, request.operation),
                error_summary=None if succeeded else error_summary,
                technical_message=None if succeeded else technical,
                error_location=None if succeeded else (location or None),
                manifest=manifest,
                run_results=run_results,
                compiled_sql=compiled,
                preview=preview,
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await self._terminate(process)
            raise
        finally:
            self._processes.pop(request.run_id, None)
            shutil.rmtree(workdir, ignore_errors=True)

    def _write_files(self, workdir: Path, request: TransformationRequest) -> None:
        for relative, content in request.project_files.items():
            target = workdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        profile_dir = workdir / ".profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile = json.loads(json.dumps(request.profile))
        output = profile["appbi_runtime"]["outputs"]["production"]
        credentials = output.pop("_service_account_json", None)
        if credentials is not None:
            key_path = profile_dir / "service-account.json"
            key_path.write_text(json.dumps(credentials), encoding="utf-8")
            with contextlib.suppress(OSError):
                key_path.chmod(0o600)
            output["keyfile"] = str(key_path)
        profile_path = profile_dir / "profiles.yml"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        with contextlib.suppress(OSError):
            profile_path.chmod(0o600)

    def _command(self, request: TransformationRequest) -> list[str]:
        base = ["dbt", "--no-use-colors", "--log-format", "text"]
        selector = ["--select", request.selected_model] if request.selected_model else []
        if request.operation == "VALIDATE":
            return base + [
                "run-operation", "appbi_validate_write", "--args",
                json.dumps({
                    "schema_names": request.validate_schemas or [request.output_schema],
                    "relations": request.validate_relations,
                }),
            ]
        if request.operation == "COMPILE":
            return base + ["compile", *selector]
        if request.operation == "PREVIEW":
            return base + [
                "show", *selector, "--limit", str(request.preview_limit), "--output", "json",
                "--indirect-selection", "empty", "--quiet",
            ]
        refresh = ["--full-refresh"] if request.full_refresh else []
        if request.operation == "TEST":
            return base + ["test", *selector]
        if request.operation == "RUN_MODEL":
            # The bare model name, not `+model`: in dbt `+` means "and every
            # ancestor", so a button reading "Run model" would rebuild the whole
            # upstream chain. Running upstream too is a separate, explicit action.
            return base + ["build", "--select", request.selected_model or "*", *refresh]
        if request.operation == "RUN_UPSTREAM":
            selected = f"+{request.selected_model}" if request.selected_model else "*"
            return base + ["build", "--select", selected, *refresh]
        return base + ["build", *refresh]

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _compiled_sql(manifest: dict[str, Any] | None) -> dict[str, str]:
        if not manifest:
            return {}
        return {
            unique_id: node["compiled_code"]
            for unique_id, node in manifest.get("nodes", {}).items()
            if node.get("resource_type") == "model" and node.get("compiled_code")
        }

    @staticmethod
    def _preview(stdout: str) -> dict[str, Any] | None:
        """Recover the JSON document `dbt show` prints among its log lines.

        stderr is folded into this stream, so a single warning line is enough to
        break a whole-document parse -- and dbt 1.12 pretty-prints, which defeats
        a line-by-line parse too. Both are tried, then a brace-matching scan
        picks the JSON object out of the surrounding noise.
        """
        def accept(payload: Any) -> dict[str, Any] | None:
            if isinstance(payload, dict) and any(
                key in payload for key in ("data", "rows", "show")
            ):
                return payload
            return None

        try:
            found = accept(json.loads(stdout))
            if found is not None:
                return found
        except ValueError:
            pass
        for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
            try:
                found = accept(json.loads(line))
            except ValueError:
                continue
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

    @staticmethod
    def _redact(text: str, secrets: list[str]) -> str:
        sanitized = text
        for value in sorted({value for value in secrets if len(value) >= 4}, key=len, reverse=True):
            sanitized = sanitized.replace(value, "[REDACTED]")
        return sanitized

    # dbt reports a compile failure only in its log -- no run_results.json is
    # written -- so the one place the model and line number exist is this text.
    _LOCATION = re.compile(
        r"(?P<kind>\w+(?: \w+)*) Error in (?P<resource>\w+) (?P<name>[\w.]+)"
        r"(?: \((?P<path>[^)]+)\))?",
    )
    _LINE = re.compile(r"\bline (?P<line>\d+)", re.IGNORECASE)
    # dbt does not always name the model on an "... Error in model x" banner.
    # A missing ref -- the most common authoring mistake -- arrives as a bare
    # "Compilation Error" followed by a sentence naming both models, so the
    # banner alone leaves the user with nothing to act on.
    _MISSING_REF = re.compile(
        r"Model '(?:[\w.]*\.)?(?P<model>\w+)'"
        r"[^\n]*?depends on a node named '(?P<missing>[^']+)'"
        r" which was not found",
    )

    @classmethod
    def _error(
        cls, run_results: dict[str, Any] | None, logs: str,
    ) -> tuple[str, str, dict[str, Any]]:
        location: dict[str, Any] = {}
        if run_results:
            failures = [
                item for item in run_results.get("results", [])
                if item.get("status") not in ("success", "pass", "skipped")
            ]
            if failures:
                first = failures[0]
                message = str(first.get("message") or "Transformation node failed.")
                unique_id = str(first.get("unique_id") or "")
                if unique_id:
                    location = {
                        "unique_id": unique_id,
                        "resource_type": unique_id.split(".", 1)[0] or None,
                        "name": unique_id.rsplit(".", 1)[-1],
                    }
                line = cls._LINE.search(message)
                if line:
                    location["line"] = int(line.group("line"))
                return message[:1000], message[:4000], location
        tail_lines = logs.splitlines()[-80:]
        tail = "\n".join(tail_lines)
        summary = "dbt could not complete this operation."
        missing = cls._MISSING_REF.search(tail)
        if missing:
            return (
                "Model '{0}' references '{1}', which does not exist in this "
                "Transform.".format(missing.group("model"),
                                    missing.group("missing")),
                tail[:4000],
                {"name": missing.group("model"), "resource_type": "model",
                 "missing_ref": missing.group("missing")},
            )
        match = cls._LOCATION.search(tail)
        if match:
            location = {
                "name": match.group("name"),
                "resource_type": match.group("resource"),
                "path": match.group("path"),
            }
            line = cls._LINE.search(tail[match.end():])
            if line:
                location["line"] = int(line.group("line"))
            # The first non-empty line after the banner is dbt's actual reason.
            detail = next(
                (item.strip() for item in tail[match.end():].splitlines()
                 if item.strip() and not item.strip().startswith("line ")),
                "",
            )
            where = f"{match.group('name')}"
            if location.get("line"):
                where += f", line {location['line']}"
            summary = f"{match.group('kind')} Error in {where}"
            if detail:
                summary = f"{summary}: {detail}"
        return summary[:1000], tail[:4000], location

    @staticmethod
    def _error_code(cancelled: bool, timed_out: bool, operation: str) -> str:
        if cancelled:
            return "TRANSFORM_CANCELLED"
        if timed_out:
            return "TRANSFORM_TIMEOUT"
        return f"TRANSFORM_{operation}_FAILED"
