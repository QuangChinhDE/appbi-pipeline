"""Dedicated queue worker for isolated transformation subprocesses."""

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
from app.services import transforms as transform_service
from app.transformation.dbt_core import DbtCoreAdapter

logger = logging.getLogger(__name__)
WORKER_ID = f"transform-{socket.gethostname()}-{os.getpid()}"
STALE_SWEEP_SECONDS = 60.0


class TransformWorker:
    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.adapter = DbtCoreAdapter()
        self.tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def run(self) -> None:
        configure_logging()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(signum, self.stopping.set)
        async with SessionLocal() as session:
            recovered = await transform_service.stale_runs(session)
        log_event(
            logger, logging.INFO, "transform_worker.startup",
            worker_id=WORKER_ID, dbt_core=self.adapter.runtime_version(), recovered=recovered,
        )
        next_sweep = asyncio.get_running_loop().time()
        while not self.stopping.is_set():
            self._reap()
            # A run orphaned while this worker was down holds its Transform's
            # active-build slot, so the sweep runs on a timer rather than only
            # at startup.
            if asyncio.get_running_loop().time() >= next_sweep:
                next_sweep = asyncio.get_running_loop().time() + STALE_SWEEP_SECONDS
                try:
                    async with SessionLocal() as session:
                        released = await transform_service.stale_runs(session)
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
                    run = await transform_service.claim_next(session, WORKER_ID)
                if run is not None:
                    started = True
                    self.tasks[run.id] = asyncio.create_task(
                        self._execute(run.id), name=f"transform-{run.id}",
                    )
            try:
                await asyncio.wait_for(
                    self.stopping.wait(),
                    timeout=0.1 if started else settings.transform_worker_poll_seconds,
                )
            except asyncio.TimeoutError:
                pass
        if self.tasks:
            done, pending = await asyncio.wait(self.tasks.values(), timeout=10)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        log_event(logger, logging.INFO, "transform_worker.stopped", worker_id=WORKER_ID)

    def _reap(self) -> None:
        for run_id, task in list(self.tasks.items()):
            if task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
                self.tasks.pop(run_id, None)

    async def _execute(self, run_id: uuid.UUID) -> None:
        try:
            async with SessionLocal() as session:
                run = await transform_service.get_claimed_run(session, run_id)
                request = await transform_service.build_request(session, run)
            async with SessionLocal() as session:
                await transform_service.mark_running(session, run_id)

            async def cancel_check() -> bool:
                async with SessionLocal() as heartbeat_session:
                    return await transform_service.heartbeat(heartbeat_session, run_id)

            result = await self.adapter.execute(request, cancel_check=cancel_check)
            async with SessionLocal() as session:
                await transform_service.complete(session, run_id, result)
            log_event(
                logger, logging.INFO, "transform_run.terminal",
                run_id=str(run_id), succeeded=result.succeeded,
                cancelled=result.cancelled, timed_out=result.timed_out,
            )
        except asyncio.CancelledError:
            async with SessionLocal() as session:
                await transform_service.fail_start(
                    session, run_id, RuntimeError("Transform worker stopped during execution."),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the queue
            log_event(
                logger, logging.ERROR, "transform_run.failed_to_start",
                run_id=str(run_id), error=f"{type(exc).__name__}: {exc}",
            )
            async with SessionLocal() as session:
                await transform_service.fail_start(session, run_id, exc)


async def main() -> None:
    await TransformWorker().run()


if __name__ == "__main__":
    asyncio.run(main())
