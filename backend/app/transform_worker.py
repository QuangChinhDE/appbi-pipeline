"""Dedicated queue worker for isolated dbt subprocesses.

Claims one invocation at a time up to the deployment's parallelism cap, runs it
in a private workspace, and records what happened.  The queue loop is unchanged
in shape from V1 -- it was the right shape -- but what it executes is now a dbt
command against a named project revision rather than a product operation against
a database row.

Nothing in this process holds a database session across a subprocess.  A build
runs for minutes; a session held open for that long occupies a pool slot the API
needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, log_event
from app.transforms import executor, invocations as invocation_service
from app.transforms.runtime.dbt import DbtRuntime

logger = logging.getLogger(__name__)
WORKER_ID = f"transform-{socket.gethostname()}-{os.getpid()}"
STALE_SWEEP_SECONDS = 60.0


class TransformWorker:
    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.runtime = DbtRuntime()
        self.tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def run(self) -> None:
        configure_logging()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(signum, self.stopping.set)

        async with SessionLocal() as session:
            recovered = await invocation_service.stale(session)
        log_event(
            logger, logging.INFO, "transform_worker.startup",
            worker_id=WORKER_ID, dbt_core=self.runtime.runtime_version(),
            recovered=recovered, storage=settings.transform_storage_backend,
        )

        next_sweep = loop.time()
        while not self.stopping.is_set():
            self._reap()
            # An invocation orphaned while this worker was down holds its
            # project's write slot, so the sweep runs on a timer rather than
            # only at startup.
            if loop.time() >= next_sweep:
                next_sweep = loop.time() + STALE_SWEEP_SECONDS
                try:
                    async with SessionLocal() as session:
                        released = await invocation_service.stale(session)
                    if released:
                        log_event(
                            logger, logging.WARNING, "transform_worker.released_stale",
                            worker_id=WORKER_ID, released=released,
                        )
                except Exception as exc:  # noqa: BLE001 - the sweep must not kill the loop
                    log_event(
                        logger, logging.ERROR, "transform_worker.stale_sweep_failed",
                        worker_id=WORKER_ID, error=f"{type(exc).__name__}: {exc}",
                    )

            started = False
            if len(self.tasks) < settings.transform_worker_max_parallel:
                async with SessionLocal() as session:
                    invocation = await invocation_service.claim_next(session, WORKER_ID)
                if invocation is not None:
                    started = True
                    self.tasks[invocation.id] = asyncio.create_task(
                        self._execute(invocation.id), name=f"transform-{invocation.id}",
                    )
            try:
                await asyncio.wait_for(
                    self.stopping.wait(),
                    timeout=0.1 if started else settings.transform_worker_poll_seconds,
                )
            except asyncio.TimeoutError:
                pass

        if self.tasks:
            _, pending = await asyncio.wait(self.tasks.values(), timeout=15)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        log_event(logger, logging.INFO, "transform_worker.stopped", worker_id=WORKER_ID)

    def _reap(self) -> None:
        for invocation_id, task in list(self.tasks.items()):
            if task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
                self.tasks.pop(invocation_id, None)

    async def _execute(self, invocation_id: uuid.UUID) -> None:
        try:
            async with SessionLocal() as session:
                invocation = await invocation_service.get_claimed(session, invocation_id)
                prepared = await executor.prepare(session, invocation)
            async with SessionLocal() as session:
                await invocation_service.mark_running(session, invocation_id)

            async def cancel_check() -> bool:
                async with SessionLocal() as heartbeat_session:
                    return await invocation_service.heartbeat(
                        heartbeat_session, invocation_id,
                    )

            # Into the database, not onto this pod's disk. The API serving the
            # Logs panel is a different pod with a different filesystem, so a
            # path on the worker is unreadable from there.
            async def log_sink(text: str) -> None:
                async with SessionLocal() as log_session:
                    await executor.store_partial_log(log_session, invocation_id, text)

            result, artifacts = await executor.run(
                prepared, runtime=self.runtime,
                cancel_check=cancel_check, log_sink=log_sink,
            )

            async with SessionLocal() as session:
                await executor.record(
                    session, invocation_id, result=result, artifacts=artifacts,
                )
                await session.commit()

            log_event(
                logger, logging.INFO, "transform_invocation.terminal",
                invocation_id=str(invocation_id), command=prepared.command.command,
                succeeded=result.succeeded, cancelled=result.cancelled,
                timed_out=result.timed_out,
            )
        except asyncio.CancelledError:
            async with SessionLocal() as session:
                await invocation_service.fail_start(
                    session, invocation_id,
                    RuntimeError("The Transform worker stopped during this run."),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the queue
            log_event(
                logger, logging.ERROR, "transform_invocation.failed_to_start",
                invocation_id=str(invocation_id), error=f"{type(exc).__name__}: {exc}",
            )
            async with SessionLocal() as session:
                await invocation_service.fail_start(session, invocation_id, exc)


async def main() -> None:
    await TransformWorker().run()


if __name__ == "__main__":
    asyncio.run(main())
