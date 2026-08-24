"""Per-request identity + tenant context.

Every service takes one of these. `workspace_id` comes from the authenticated
session, never from the request body -- that is the tenant-isolation guarantee
(guardrail 8, section 30.1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.permissions import Action, Module, Role, require


@dataclass(slots=True)
class RequestContext:
    user_id: uuid.UUID | None
    workspace_id: uuid.UUID
    role: Role
    trace_id: str
    email: str | None = None
    full_name: str | None = None
    is_platform_admin: bool = False
    ip_address: str | None = None
    user_agent: str | None = None
    timezone: str = "Asia/Bangkok"
    workspace_settings: dict = field(default_factory=dict)

    def require(self, module: Module, action: Action) -> None:
        require(self.role, module, action)

    def can(self, module: Module, action: Action) -> bool:
        from app.core.permissions import allowed

        return allowed(self.role, module, action)

    @classmethod
    def system(cls, workspace_id: uuid.UUID, trace_id: str, timezone: str = "Asia/Bangkok") -> "RequestContext":
        """Context for background workers: full rights, no user attribution."""
        return cls(
            user_id=None, workspace_id=workspace_id, role=Role.PLATFORM_ADMIN,
            trace_id=trace_id, full_name="system", timezone=timezone,
        )
