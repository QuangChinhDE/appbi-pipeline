"""Audit trail (section 20).

Writes are best-effort in latency but never silently dropped: they go in the
same transaction as the mutation they describe, so a committed change always
has its audit row.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.logging import redact
from app.models.enums import ActorType, AuditResult
from app.models.ops import AuditEvent

# Fields that must never appear in a before/after summary even after redaction.
_DROP_KEYS = {"credentials", "secret_ref", "password", "configuration_secrets"}


def sanitize(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    cleaned = {k: v for k, v in payload.items() if k not in _DROP_KEYS}
    return redact(cleaned)


async def record(
    session: AsyncSession,
    ctx: RequestContext | None,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    resource_name: str | None = None,
    result: AuditResult = AuditResult.SUCCESS,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
) -> AuditEvent:
    event = AuditEvent(
        workspace_id=workspace_id or (ctx.workspace_id if ctx else None),
        actor_type=actor_type or (ActorType.USER if ctx and ctx.user_id else ActorType.SYSTEM),
        actor_id=ctx.user_id if ctx else None,
        actor_label=(ctx.email or ctx.full_name) if ctx else "system",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        result=result,
        before_summary=sanitize(before),
        after_summary=sanitize(after),
        ip_address=ctx.ip_address if ctx else None,
        user_agent=(ctx.user_agent or "")[:400] if ctx and ctx.user_agent else None,
        trace_id=ctx.trace_id if ctx else None,
        created_at=utcnow(),
    )
    session.add(event)
    await session.flush()
    return event
