"""Product background worker (section 26).

Five independent loops, each with its own session and its own failure blast
radius:

  executor    claim QUEUED runs and drive the engine
  reconciler  fold engine status back into product runs, evaluate alerts
  scheduler   fire pipelines whose next_run_at has passed
  catalog     refresh connector specs on a slow cadence
  janitor     retry pending deletes, expire stale runs, freshness alerts

Long syncs never hold an HTTP request open: the API only ever writes a QUEUED
row (section 8.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.adapters.registry import close_adapter, get_adapter
from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import SessionLocal, engine as db_engine, utcnow
from app.services import oauth as oauth_service
from app.core.errors import AppError
from app.core.logging import configure_logging, log_event, new_trace_id, trace_id_var
from app.models.enums import (
    ACTIVE_RUN_STATUSES, PipelineStatus, ResourceStatus, RunStatus, TriggerType,
)
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.models.transform import Transform
from app.services import (
    alerts as alert_service, catalog, pipelines as pipeline_service, runs as run_service,
    scheduling, transforms as transform_service,
)

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


class Worker:
    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.inflight: dict[uuid.UUID, str] = {}
        self.semaphore = asyncio.Semaphore(settings.worker_max_parallel_syncs)

    # ── executor ─────────────────────────────────────────────────────────
    async def executor_loop(self) -> None:
        while not self.stopping.is_set():
            started_any = False
            if len(self.inflight) < settings.worker_max_parallel_syncs:
                async with SessionLocal() as session:
                    run = await run_service.claim_next(session, WORKER_ID)
                    if run is not None:
                        started_any = True
                        await self._start_run(session, run)
            await self._sleep(0.2 if started_any else settings.worker_poll_seconds)

    async def _start_run(self, session, run: PipelineRun) -> None:
        trace_id_var.set((run.technical_metadata or {}).get("trace_id") or new_trace_id())
        try:
            request = await run_service.build_sync_request(session, run)
        except Exception as exc:  # noqa: BLE001 - a bad run must not stall the loop
            log_event(logger, logging.ERROR, "run.prepare_failed",
                      run_id=str(run.id), error=str(exc))
            await run_service.mark_failed_to_start(session, run, exc)
            return

        try:
            job = await get_adapter().trigger_sync(request)
        except Exception as exc:  # noqa: BLE001
            log_event(logger, logging.ERROR, "run.trigger_failed",
                      run_id=str(run.id), error=str(exc))
            await run_service.mark_failed_to_start(session, run, exc)
            return

        await run_service.mark_started(session, run, job.ref)
        self.inflight[run.id] = job.ref
        log_event(logger, logging.INFO, "run.started", run_id=str(run.id), engine_job=job.ref)

    # ── reconciler ───────────────────────────────────────────────────────
    async def reconciler_loop(self) -> None:
        """Poll active runs and normalize engine state into the product model."""
        while not self.stopping.is_set():
            try:
                async with SessionLocal() as session:
                    active = list((await session.scalars(
                        select(PipelineRun).where(
                            PipelineRun.status.in_(
                                [RunStatus.STARTING, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED]
                            ),
                            PipelineRun.engine_job_ref.is_not(None),
                        )
                    )).all())
                    for run in active:
                        await self._reconcile(session, run)
                    # Runs that outran their deadline. Here rather than in a
                    # loop of its own: it needs the same cadence and the same
                    # session, and a separate loop is one more thing that can
                    # be running while the other is not.
                    timeouts = await run_service.enforce_timeouts(session)
                    if timeouts["timed_out"] or timeouts["deferred"]:
                        log_event(logger, logging.WARNING, "reconciler.timeouts",
                                  **timeouts)
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.ERROR, "reconciler.error", error=str(exc))
            await self._sleep(settings.reconcile_interval_seconds)

    async def _reconcile(self, session, run: PipelineRun) -> None:
        try:
            status = await get_adapter().get_job(run.engine_job_ref)
        except AppError as exc:
            log_event(logger, logging.WARNING, "reconciler.engine_unreachable",
                      run_id=str(run.id), error=str(exc))
            return
        terminal = await run_service.apply_engine_status(session, run, status)
        if not terminal:
            return

        self.inflight.pop(run.id, None)
        adapter = get_adapter()
        if hasattr(adapter, "forget"):
            adapter.forget(run.engine_job_ref)

        queued_transforms = await transform_service.enqueue_after_upstream(session, run)
        notifications = await alert_service.evaluate_run(session, run)
        await session.commit()
        if queued_transforms:
            log_event(
                logger, logging.INFO, "transforms.queued_after_upstream",
                run_id=str(run.id), count=len(queued_transforms),
            )
        if notifications:
            log_event(logger, logging.INFO, "alerts.created",
                      run_id=str(run.id), count=len(notifications))

    # ── scheduler ────────────────────────────────────────────────────────
    async def scheduler_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                async with SessionLocal() as session:
                    now = utcnow()
                    due = list((await session.scalars(
                        select(Pipeline).where(
                            Pipeline.status == PipelineStatus.ACTIVE,
                            Pipeline.deleted_at.is_(None),
                            Pipeline.next_run_at.is_not(None),
                            Pipeline.next_run_at <= now,
                        ).limit(50)
                    )).all())
                    for pipeline in due:
                        await self._fire(session, pipeline)
                    if due:
                        await session.commit()
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.ERROR, "scheduler.error", error=str(exc))
            await self._sleep(10)

    async def transform_scheduler_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                async with SessionLocal() as session:
                    now = utcnow()
                    due = list((await session.scalars(
                        select(Transform).where(
                            Transform.status == "ACTIVE",
                            Transform.deleted_at.is_(None),
                            Transform.next_run_at.is_not(None),
                            Transform.next_run_at <= now,
                        ).limit(50)
                    )).all())
                    for transform in due:
                        await self._fire_transform(session, transform)
                    if due:
                        await session.commit()
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.ERROR, "transform_scheduler.error", error=str(exc))
            await self._sleep(10)

    async def _fire_transform(self, session, transform: Transform) -> None:
        fired_for = transform.next_run_at
        # Advance first, exactly as the pipeline scheduler does, so a skipped
        # tick cannot become a retry loop.
        transform.next_run_at = scheduling.next_run_at(
            transform.schedule_type, transform.schedule_config, transform.timezone,
        )
        # A schedule runs published code. With nothing published there is
        # nothing safe to run unattended, so skip rather than fall back to a
        # draft somebody may be halfway through editing.
        if transform.active_release_id is None:
            log_event(logger, logging.WARNING, "transform_scheduler.skipped_unpublished",
                      transform_id=str(transform.id))
            return

        ctx = RequestContext.system(transform.workspace_id, new_trace_id(), transform.timezone)
        try:
            await transform_service.enqueue(
                session, ctx, transform, operation="BUILD", model_id=None,
                source="RELEASE", trigger_type=TriggerType.SCHEDULE,
                enforce_permission=False,
                idempotency_key=f"schedule:{transform.id}:{fired_for.isoformat()}",
            )
        except AppError as exc:
            log_event(logger, logging.INFO, "transform_scheduler.rejected",
                      transform_id=str(transform.id), error=str(exc))

    async def _fire(self, session, pipeline: Pipeline) -> None:
        # Always advance the clock first, so a skipped or rejected tick cannot
        # turn into a tight loop of retries.
        pipeline.next_run_at = scheduling.next_run_at(
            pipeline.schedule_type, pipeline.schedule_config, pipeline.timezone
        )
        running = await pipeline_service.active_run(session, pipeline.id)
        if running is not None:
            log_event(logger, logging.INFO, "scheduler.skipped_overlap",
                      pipeline_id=str(pipeline.id))
            return

        source = await session.get(Source, pipeline.source_id)
        destination = await session.get(Destination, pipeline.destination_id)
        if (source is None or destination is None
                or source.status is not ResourceStatus.ACTIVE
                or destination.status is not ResourceStatus.ACTIVE):
            log_event(logger, logging.WARNING, "scheduler.skipped_inactive_actor",
                      pipeline_id=str(pipeline.id))
            return

        ctx = RequestContext.system(pipeline.workspace_id, new_trace_id(), pipeline.timezone)
        try:
            await run_service.trigger(
                session, ctx, pipeline, trigger_type=TriggerType.SCHEDULE,
                enforce_permission=False,
            )
        except AppError as exc:
            log_event(logger, logging.INFO, "scheduler.trigger_rejected",
                      pipeline_id=str(pipeline.id), code=exc.code)

    # ── catalog refresh ──────────────────────────────────────────────────
    async def catalog_loop(self) -> None:
        # Give the daemon a moment before pulling images on a cold start.
        await self._sleep(30)
        while not self.stopping.is_set():
            try:
                async with SessionLocal() as session:
                    outcome = await catalog.refresh_specs(session)
                    await session.commit()
                    log_event(logger, logging.INFO, "catalog.refreshed", result=outcome)
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.WARNING, "catalog.refresh_error", error=str(exc))
            await self._sleep(settings.catalog_refresh_seconds)

    # ── janitor ──────────────────────────────────────────────────────────
    async def outbox_loop(self) -> None:
        """Put the engine and the Product DB back in agreement.

        An operation left open past its SLO means the request that started it
        is gone: the process died, the transaction rolled back, the connection
        dropped. Whatever the engine did in the meantime is invisible to the
        product until this runs.

        Separate from the janitor deliberately. The janitor retries deletes the
        product *asked* for; this deals with resources the product does not
        know it has, which is a different failure and a more urgent one -- an
        orphan holds customer credentials.
        """
        from app.services import outbox

        while not self.stopping.is_set():
            try:
                counts = await outbox.sweep(get_adapter())
                if counts["compensated"] or counts["failed"]:
                    log_event(logger, logging.WARNING, "outbox.sweep", **counts)
                elif counts["checked"]:
                    log_event(logger, logging.INFO, "outbox.sweep", **counts)

                # Alerting on compensation would page for the system working.
                # What deserves a page is an operation that several sweeps have
                # failed to resolve: an engine resource with credentials in it
                # that nobody can reach.
                stuck = await outbox.overdue()
                if stuck:
                    log_event(logger, logging.ERROR, "outbox.stuck",
                              count=len(stuck),
                              oldest=str(stuck[0].updated_at),
                              resources=[str(row.product_resource_id)
                                         for row in stuck[:10]])
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.ERROR, "outbox.error", error=str(exc))
            await self._sleep(60)

    async def janitor_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                async with SessionLocal() as session:
                    await self._retry_pending_deletes(session)
                    await self._freshness(session)
                    # An OAuth consent nobody finished with is a live refresh
                    # token attached to nothing. It expires on its own; this is
                    # what actually removes it and the secret it references.
                    purged = await oauth_service.purge_expired(session)
                    if purged:
                        log_event(logger, logging.INFO, "oauth.grants_purged",
                                  count=purged)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                log_event(logger, logging.ERROR, "janitor.error", error=str(exc))
            await self._sleep(120)

    async def _retry_pending_deletes(self, session) -> None:
        adapter = get_adapter()
        from app.models.enums import EngineResourceType, ProductResourceType
        from app.models.engine import EngineMapping
        from app.services import actors as actor_service

        for kind, model in ((actor_service.SOURCE, Source),
                            (actor_service.DESTINATION, Destination)):
            pending = list((await session.scalars(
                select(model).where(model.status == ResourceStatus.DELETE_PENDING)
            )).all())
            for actor in pending:
                ref = await session.scalar(
                    select(EngineMapping.engine_resource_ref).where(
                        EngineMapping.product_resource_id == actor.id,
                        EngineMapping.engine_resource_type == kind.engine_resource,
                    )
                )
                try:
                    if ref:
                        if kind.side == "SOURCE":
                            await adapter.delete_source(ref)
                        else:
                            await adapter.delete_destination(ref)
                except AppError:
                    continue
                actor.status = ResourceStatus.DELETED
                actor.deleted_at = utcnow()
                log_event(logger, logging.INFO, "janitor.delete_completed",
                          resource_id=str(actor.id), side=kind.side)

        pending_pipelines = list((await session.scalars(
            select(Pipeline).where(Pipeline.status == PipelineStatus.DELETE_PENDING)
        )).all())
        for pipeline in pending_pipelines:
            ref = await pipeline_service.connection_ref(session, pipeline.id)
            try:
                if ref:
                    await adapter.delete_connection(ref)
            except AppError:
                continue
            pipeline.status = PipelineStatus.DELETED
            pipeline.deleted_at = utcnow()

    async def _freshness(self, session) -> None:
        pipelines = list((await session.scalars(
            select(Pipeline).where(
                Pipeline.status == PipelineStatus.ACTIVE,
                Pipeline.deleted_at.is_(None),
                Pipeline.last_success_at.is_not(None),
            )
        )).all())
        for pipeline in pipelines:
            await alert_service.evaluate_freshness(session, pipeline)

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)

    async def startup_recovery(self) -> None:
        """Work out what the previous life of this worker left behind.

        Not "fail everything I owned". A container restart keeps the hostname
        and gets PID 1 again, so `WORKER_ID` is unchanged -- which used to mean
        a restart failed live Airbyte jobs, and users retried into a second
        job writing the same destination. The engine is asked instead.
        """
        async with SessionLocal() as session:
            counts = await run_service.recover_orphans(session, WORKER_ID)
            if any(counts.values()):
                log_event(logger, logging.WARNING, "worker.recovery",
                          adopted=counts["adopted"], lost=counts["lost"],
                          deferred=counts["deferred"])

    async def run(self) -> None:
        configure_logging()
        log_event(logger, logging.INFO, "worker.startup", worker_id=WORKER_ID,
                  engine_type=settings.engine_type,
                  max_parallel=settings.worker_max_parallel_syncs)

        for attempt in range(30):
            try:
                await self.startup_recovery()
                break
            except Exception as exc:  # noqa: BLE001 - postgres may still be starting
                log_event(logger, logging.INFO, "worker.waiting_for_db",
                          attempt=attempt + 1, error=str(exc)[:120])
                await asyncio.sleep(2)

        tasks = [
            asyncio.create_task(self.executor_loop(), name="executor"),
            asyncio.create_task(self.reconciler_loop(), name="reconciler"),
            asyncio.create_task(self.scheduler_loop(), name="scheduler"),
            asyncio.create_task(self.transform_scheduler_loop(), name="transform-scheduler"),
            asyncio.create_task(self.catalog_loop(), name="catalog"),
            asyncio.create_task(self.janitor_loop(), name="janitor"),
            asyncio.create_task(self.outbox_loop(), name="outbox"),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self.stopping.set()
            for task in tasks:
                task.cancel()
            await close_adapter()
            await db_engine.dispose()


def main() -> None:
    worker = Worker()
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
