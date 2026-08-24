"""Does the engine still have what this database says it has?

A restore is the case this exists for. `engine_mappings` rows are handles into
one specific engine deployment; restore that database beside a different
Airbyte and every handle names something that is not there. Nothing detects
that today -- the first symptom is a sync failing, hours later, with an error
that reads like the engine is broken.

The check itself is one adapter call per mapping. What makes it worth writing
down is the failure mode it must avoid: an engine that is *down* answers
nothing, and reporting "missing" then would send an operator off recreating
resources that are perfectly fine. Unreachable and absent are different
answers and this module keeps them apart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_adapter
from app.core.errors import AppError
from app.core.logging import log_event
from app.models.engine import EngineMapping
from app.models.enums import EngineResourceType, ProductResourceType
from app.models.integration import Destination, Pipeline, Source

logger = logging.getLogger(__name__)

# How many mappings to ask about at once. The engine is a shared dependency and
# a reconcile is not urgent; a burst of hundreds of lookups would compete with
# real syncs for the same connection pool.
_CONCURRENCY = 5


@dataclass
class ResourceVerdict:
    resource_type: str
    resource_id: uuid.UUID
    name: str
    present: bool


@dataclass
class ReconcileReport:
    """Product-level facts only.

    No engine reference appears here, deliberately. This crosses the API
    boundary and reaches the browser, and an engine id in a product payload is
    the leak guardrail 3 exists to prevent -- the operator needs to know *which
    source* is missing, not what Airbyte calls it.
    """

    checked: int = 0
    missing: list[ResourceVerdict] = field(default_factory=list)
    present: int = 0
    # Mappings written by a *different* engine implementation. Not missing and
    # not present: this reconcile cannot speak for them at all, and folding
    # them into "missing" is how a report becomes actively harmful -- the first
    # live run did exactly that, reporting seventeen resources as lost when
    # they were embedded-adapter rows the API adapter was never going to find.
    foreign: int = 0
    engine_reachable: bool = True
    detail: str = ""

    @property
    def consistent(self) -> bool:
        return self.engine_reachable and not self.missing


_MAPPING_TO_PRODUCT = {
    EngineResourceType.SOURCE: ProductResourceType.SOURCE,
    EngineResourceType.DESTINATION: ProductResourceType.DESTINATION,
    EngineResourceType.CONNECTION: ProductResourceType.PIPELINE,
}


async def _names(session: AsyncSession) -> dict[uuid.UUID, str]:
    """Product names for the ids a mapping points at.

    Three queries rather than a join per mapping: the report is for a human,
    and "source 3f2a..." is not something anyone can act on.
    """
    names: dict[uuid.UUID, str] = {}
    for model in (Source, Destination, Pipeline):
        for row in (await session.scalars(select(model))).all():
            names[row.id] = row.name
    return names


async def reconcile(session: AsyncSession, *, workspace_id: uuid.UUID | None = None) -> ReconcileReport:
    adapter = get_adapter()
    report = ReconcileReport()

    query = select(EngineMapping).where(
        EngineMapping.engine_resource_type != EngineResourceType.JOB)
    if workspace_id is not None:
        query = query.where(EngineMapping.workspace_id == workspace_id)
    every_mapping = (await session.scalars(query)).all()
    if not every_mapping:
        report.detail = "no engine mappings; nothing to reconcile"
        return report

    # Only rows this engine could possibly own. A deployment that has switched
    # adapters -- or run the embedded runner for a while, as staging has --
    # carries rows whose refs are not addresses on the running engine.
    mappings = [m for m in every_mapping if m.engine_type == adapter.engine_type]
    report.foreign = len(every_mapping) - len(mappings)
    if not mappings:
        report.detail = (f"none of the {report.foreign} mappings were written by "
                         f"{adapter.engine_type.value}; nothing this engine can be "
                         "asked about")
        return report

    names = await _names(session)
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def check(mapping: EngineMapping) -> tuple[EngineMapping, bool | None]:
        async with semaphore:
            try:
                return mapping, await adapter.resource_exists(
                    mapping.engine_resource_type, mapping.engine_resource_ref)
            except AppError:
                # Unreachable, unauthenticated, rate limited -- all the same
                # answer here: the engine did not tell us. Only a confirmed
                # absence comes back as False, from the adapter.
                return mapping, None

    results = await asyncio.gather(*(check(m) for m in mappings))

    for mapping, exists in results:
        if exists is None:
            # One unreachable answer invalidates the whole report. A partial
            # list of "missing" resources is worse than no list: it looks
            # authoritative and is not.
            report.engine_reachable = False
            report.detail = ("the engine did not answer; reconcile says nothing "
                             "until it is reachable")
            report.missing.clear()
            log_event(logger, logging.WARNING, "reconcile.engine_unavailable")
            return report
        report.checked += 1
        if exists:
            report.present += 1
        else:
            product_type = _MAPPING_TO_PRODUCT.get(mapping.engine_resource_type)
            report.missing.append(ResourceVerdict(
                resource_type=(product_type or mapping.engine_resource_type).value,
                resource_id=mapping.product_resource_id,
                name=names.get(mapping.product_resource_id, "(deleted)"),
                present=False,
            ))

    aside = (f" ({report.foreign} more belong to another engine implementation "
             "and were not checked)") if report.foreign else ""
    if report.missing:
        report.detail = (
            f"{len(report.missing)} of {report.checked} resources are not on this "
            f"engine{aside}. This is what a database restored beside a different "
            "deployment looks like: recreate them, or point the product back at "
            "the deployment the backup was taken from.")
    else:
        report.detail = (f"all {report.checked} mapped resources are present on "
                         f"the engine{aside}")
    log_event(logger, logging.INFO, "reconcile.complete",
              checked=report.checked, missing=len(report.missing))
    return report
