"""Server-owned tool policy for the Builder assistant.

The model never receives a mutation primitive. Read tools collect sanitized
context; the sole propose tool can create a reviewable ChangeSet after the
server validates it. Apply, reject, undo and publish remain ordinary product
endpoints guarded by RBAC.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ValidationError
from app.models.builder import BuilderAIToolEvent


class AgentPhase(str, Enum):
    DIAGNOSE = "DIAGNOSE"
    PROPOSE = "PROPOSE"


ALLOWED_TOOLS = {
    AgentPhase.DIAGNOSE: {"read_builder_context", "read_test_evidence"},
    AgentPhase.PROPOSE: {"propose_change_set"},
}


async def record_tool(
    session: AsyncSession,
    *,
    ai_session_id: uuid.UUID,
    phase: AgentPhase,
    tool_name: str,
    result_summary: dict[str, Any],
) -> None:
    if tool_name not in ALLOWED_TOOLS[phase]:
        raise ValidationError(
            "AI tool không được phép trong giai đoạn hiện tại.",
            code="AI_TOOL_PHASE_BLOCKED",
            details={"phase": phase.value, "tool": tool_name},
        )
    session.add(BuilderAIToolEvent(
        session_id=ai_session_id, tool_name=tool_name, phase=phase.value,
        result_summary=result_summary, created_at=utcnow(),
    ))
