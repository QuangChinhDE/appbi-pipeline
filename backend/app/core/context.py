"""Per-request identity + tenant context.

Every service takes one of these. `workspace_id` comes from the authenticated
session, never from the request body -- that is the tenant-isolation guarantee
(guardrail 8, section 30.1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.permissions import (
    Action, Module, OrgRole, Role, allowed, effective, org_require, require,
)


@dataclass(slots=True)
class RequestContext:
    user_id: uuid.UUID | None
    workspace_id: uuid.UUID
    role: Role
    trace_id: str
    #: The organisation that owns `workspace_id`, and this user's standing in
    #: it. `org_role` is None for a platform admin reaching a workspace they
    #: administer without belonging to its organisation. Declared here, after
    #: the last field without a default, because a dataclass refuses to order
    #: them the other way round.
    organization_id: uuid.UUID | None = None
    org_role: OrgRole | None = None
    email: str | None = None
    full_name: str | None = None
    is_platform_admin: bool = False
    ip_address: str | None = None
    user_agent: str | None = None
    timezone: str = "Asia/Bangkok"
    workspace_settings: dict = field(default_factory=dict)
    #: What this membership stores on top of its preset, straight from the
    #: database. Resolved once, lazily, into `_perms` -- so a request that never
    #: asks a permission question does no work, and one that asks fifty resolves
    #: the same answer fifty times instead of reading the row again.
    permission_overrides: dict | None = None
    _perms: dict | None = field(default=None, repr=False, compare=False)

    @property
    def permissions(self) -> dict[Module, set[Action]]:
        """This context's answer to every permission question.

        One resolution, shared by the gate, the payload the browser receives and
        the matrix an administrator edits. Keeping three readers on one function
        is the whole point: the alternative is a menu item that appears, a page
        that opens, and an endpoint behind it that returns 403.
        """
        if self._perms is None:
            self._perms = effective(
                self.role, self.permission_overrides,
                is_platform_admin=self.is_platform_admin,
            )
        return self._perms

    def require(self, module: Module, action: Action) -> None:
        require(self.permissions, module, action, self.role)

    def require_org(self, action: Action) -> None:
        """Authority over the organisation itself, not over what is inside a
        workspace. A platform admin passes regardless: they administer the
        deployment the organisation lives in."""
        if self.is_platform_admin:
            return
        org_require(self.org_role, action)

    def can_org(self, action: Action) -> bool:
        from app.core.permissions import org_allowed

        return self.is_platform_admin or org_allowed(self.org_role, action)

    def can(self, module: Module, action: Action) -> bool:
        return allowed(self.permissions, module, action)

    @classmethod
    def system(cls, workspace_id: uuid.UUID, trace_id: str, timezone: str = "Asia/Bangkok") -> "RequestContext":
        """Context for background workers: full rights, no user attribution."""
        return cls(
            user_id=None, workspace_id=workspace_id, role=Role.PLATFORM_ADMIN,
            trace_id=trace_id, full_name="system", timezone=timezone,
            # "Full rights" has to include the organisation axis, or a
            # background job inherits less authority than its docstring claims.
            is_platform_admin=True,
        )
