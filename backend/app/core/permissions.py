"""RBAC matrix (section 4.2).

The backend is the only authority. FE gating exists purely so the UI is not
littered with buttons that would 403 -- every endpoint re-checks here.
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import ForbiddenError


class Role(str, Enum):
    OWNER = "OWNER"
    DATA_ADMIN = "DATA_ADMIN"
    OPERATOR = "OPERATOR"
    ANALYST = "ANALYST"
    AUDITOR = "AUDITOR"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"


class Module(str, Enum):
    SOURCES = "sources"
    DESTINATIONS = "destinations"
    PIPELINES = "pipelines"
    TRANSFORMS = "transforms"
    MONITORING = "monitoring"
    ALERTS = "alerts"
    AUDIT = "audit"
    MEMBERS = "members"
    SETTINGS = "settings"
    CONNECTORS = "connectors"


class Action(str, Enum):
    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    OPERATE = "operate"
    DELETE = "delete"
    ADMIN = "admin"


_ALL = {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE, Action.ADMIN}
_RO = {Action.VIEW}

# role -> module -> allowed actions
MATRIX: dict[Role, dict[Module, set[Action]]] = {
    Role.OWNER: {m: set(_ALL) for m in Module},
    Role.PLATFORM_ADMIN: {m: set(_ALL) for m in Module},
    Role.DATA_ADMIN: {
        Module.SOURCES: {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.DESTINATIONS: {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.PIPELINES: {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.TRANSFORMS: {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.MONITORING: {Action.VIEW, Action.OPERATE},
        Module.ALERTS: {Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.AUDIT: _RO,
        Module.MEMBERS: _RO,
        Module.SETTINGS: {Action.VIEW, Action.EDIT},
        Module.CONNECTORS: {Action.VIEW},
    },
    Role.OPERATOR: {
        Module.SOURCES: {Action.VIEW, Action.OPERATE},
        Module.DESTINATIONS: {Action.VIEW, Action.OPERATE},
        Module.PIPELINES: {Action.VIEW, Action.OPERATE},
        Module.TRANSFORMS: {Action.VIEW, Action.OPERATE},
        Module.MONITORING: {Action.VIEW, Action.OPERATE},
        Module.ALERTS: {Action.VIEW, Action.OPERATE},
        Module.AUDIT: set(),
        Module.MEMBERS: set(),
        Module.SETTINGS: _RO,
        Module.CONNECTORS: _RO,
    },
    Role.ANALYST: {
        Module.SOURCES: _RO,
        Module.DESTINATIONS: _RO,
        Module.PIPELINES: _RO,
        Module.TRANSFORMS: _RO,
        Module.MONITORING: _RO,
        Module.ALERTS: _RO,
        Module.AUDIT: set(),
        Module.MEMBERS: set(),
        Module.SETTINGS: _RO,
        Module.CONNECTORS: _RO,
    },
    Role.AUDITOR: {
        Module.SOURCES: _RO,
        Module.DESTINATIONS: _RO,
        Module.PIPELINES: _RO,
        Module.TRANSFORMS: _RO,
        Module.MONITORING: _RO,
        Module.ALERTS: _RO,
        Module.AUDIT: {Action.VIEW, Action.ADMIN},
        Module.MEMBERS: _RO,
        Module.SETTINGS: _RO,
        Module.CONNECTORS: _RO,
    },
}


def allowed(role: Role, module: Module, action: Action) -> bool:
    return action in MATRIX.get(role, {}).get(module, set())


def require(role: Role, module: Module, action: Action) -> None:
    if not allowed(role, module, action):
        raise ForbiddenError(
            f"Vai trò {role.value} không có quyền {action.value} trên {module.value}.",
            details={"module": module.value, "action": action.value, "role": role.value},
        )


def permission_map(role: Role) -> dict[str, list[str]]:
    """Serialised for the FE so it can hide (not enforce) unavailable actions."""
    return {
        module.value: sorted(a.value for a in MATRIX.get(role, {}).get(module, set()))
        for module in Module
    }
