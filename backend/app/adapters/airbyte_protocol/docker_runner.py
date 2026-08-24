"""Launching connector containers on the host Docker daemon.

Mirrors what the Airbyte worker does: the connector image is the unit of
execution, config is handed over as a file in a shared volume, and the process
speaks the Airbyte Protocol on stdio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import log_event

logger = logging.getLogger(__name__)

# Records can be large; the default 64 KiB StreamReader limit would raise
# LimitOverrunError on a wide row.
STREAM_LIMIT = 32 * 1024 * 1024


class DockerUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class ProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


def workspace_root() -> Path:
    root = Path(settings.engine_workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def log_root() -> Path:
    root = Path(settings.engine_log_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_job_dir(prefix: str) -> Path:
    path = workspace_root() / f"{prefix}-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # Connector containers may run as a non-root user.
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    return path


class DockerRunner:
    """Thin async wrapper over the docker CLI."""

    def __init__(self) -> None:
        self.binary = settings.engine_docker_binary
        self.network = settings.engine_docker_network
        self.volume = settings.engine_workspace_volume
        self.mount_target = settings.engine_workspace_dir

    # --- daemon ----------------------------------------------------------
    async def ping(self) -> tuple[bool, str]:
        try:
            result = await self._run([self.binary, "version", "--format", "{{.Server.Version}}"], timeout=15)
        except FileNotFoundError as exc:
            raise DockerUnavailable("docker CLI not available in this container") from exc
        if result.exit_code != 0:
            return False, (result.stderr or b"").decode(errors="replace").strip()
        return True, result.stdout.decode(errors="replace").strip()

    async def image_present(self, image: str) -> bool:
        result = await self._run([self.binary, "image", "inspect", image], timeout=60)
        return result.exit_code == 0

    async def pull(self, image: str, *, timeout: int = 1800) -> ProcessResult:
        log_event(logger, logging.INFO, "connector.image.pull", image=image)
        return await self._run([self.binary, "pull", "--quiet", image], timeout=timeout)

    async def ensure_image(self, image: str) -> None:
        if await self.image_present(image):
            return
        result = await self.pull(image)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or b"").decode(errors="replace")[-800:]
            raise DockerUnavailable(f"pull access denied or image not found: {image}. {detail}")

    async def kill(self, container_name: str) -> None:
        await self._run([self.binary, "kill", container_name], timeout=30)

    async def running_containers(self, prefix: str) -> list[str]:
        result = await self._run(
            [self.binary, "ps", "--filter", f"name={prefix}", "--format", "{{.Names}}"], timeout=30
        )
        if result.exit_code != 0:
            return []
        return [line for line in result.stdout.decode(errors="replace").splitlines() if line]

    # --- connector invocations -------------------------------------------
    def docker_args(
        self,
        image: str,
        command: list[str],
        *,
        container_name: str | None = None,
        interactive: bool = False,
        extra_mounts: list[str] | None = None,
    ) -> list[str]:
        args = [self.binary, "run", "--rm"]
        if interactive:
            args.append("-i")
        if container_name:
            args += ["--name", container_name]
        if self.network:
            args += ["--network", self.network]
        # The shared volume is mounted at the identical path inside the worker
        # and inside the connector, so a path written here resolves there.
        args += ["-v", f"{self.volume}:{self.mount_target}"]
        for mount in extra_mounts or []:
            args += ["-v", mount]
        # Labelled so orphan cleanup can find strays without touching anything
        # else on the daemon.
        args += ["--label", "app=appbi-pipeline"]
        args += [image, *command]
        return args

    async def run_connector(
        self,
        image: str,
        command: list[str],
        *,
        timeout: int,
        container_name: str | None = None,
    ) -> ProcessResult:
        await self.ensure_image(image)
        args = self.docker_args(image, command, container_name=container_name)
        log_event(logger, logging.INFO, "connector.exec", image=image, command=command[0])
        result = await self._run(args, timeout=timeout)
        if result.timed_out and container_name:
            await self.kill(container_name)
        return result

    async def spawn(
        self,
        image: str,
        command: list[str],
        *,
        container_name: str,
        interactive: bool = False,
    ) -> asyncio.subprocess.Process:
        """Start a connector and hand back the live process for streaming."""
        await self.ensure_image(image)
        args = self.docker_args(image, command, container_name=container_name, interactive=interactive)
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )

    # --- internals --------------------------------------------------------
    async def _run(self, args: list[str], *, timeout: int) -> ProcessResult:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ProcessResult(exit_code=124, stdout=b"", stderr=b"timeout", timed_out=True)
        return ProcessResult(exit_code=process.returncode or 0, stdout=stdout, stderr=stderr)
