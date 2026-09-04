"""RBAC matrix (section 4.2).

The backend is the only authority. FE gating exists purely so the UI is not
littered with buttons that would 403 -- every endpoint re-checks here.

Three of the actions below exist because the original six collapsed decisions
that are not the same decision:

* `OPERATE` used to cover both "press Run" and "rewind the replication cursor".
  The first is idempotent; the second re-reads history and rewrites rows in
  somebody's warehouse. `RESET` carries the second so an Operator can keep a
  pipeline running without being able to re-materialise its history.
* `EDIT` used to cover both "rename this source" and "replace the password it
  authenticates with". `MANAGE_CREDENTIALS` carries the second.
* `VIEW` used to cover both "see that this pipeline moved 2,000 rows" and "look
  at the rows". `VIEW_DATA` carries the second, which is what lets an Auditor
  review the configuration of a pipeline carrying data they may not read.
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import ForbiddenError


class Role(str, Enum):
    OWNER = "OWNER"
    DATA_ADMIN = "DATA_ADMIN"
    CONNECTOR_DEV = "CONNECTOR_DEV"
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
    #: Read the records themselves, not just the configuration and counts:
    #: dbt previews, run logs that quote rows, sampled records.
    VIEW_DATA = "view_data"
    CREATE = "create"
    EDIT = "edit"
    #: Idempotent operations that move no history: run now, pause, resume,
    #: cancel, retry, test a saved connection.
    OPERATE = "operate"
    #: Operations that re-read or overwrite data already delivered: editing the
    #: replication cursor, `dbt --full-refresh`, accepting a schema change that
    #: drops streams.
    RESET = "reset"
    #: Writing or rotating the secret a connection authenticates with.
    MANAGE_CREDENTIALS = "manage_credentials"
    DELETE = "delete"
    ADMIN = "admin"


_ALL = {
    Action.VIEW, Action.VIEW_DATA, Action.CREATE, Action.EDIT, Action.OPERATE,
    Action.RESET, Action.MANAGE_CREDENTIALS, Action.DELETE, Action.ADMIN,
}
_RO = {Action.VIEW}
#: Everything a role needs to own a source or destination end to end.
_ACTOR_FULL = {
    Action.VIEW, Action.VIEW_DATA, Action.CREATE, Action.EDIT, Action.OPERATE,
    Action.MANAGE_CREDENTIALS, Action.DELETE,
}

# role -> module -> allowed actions
MATRIX: dict[Role, dict[Module, set[Action]]] = {
    Role.OWNER: {m: set(_ALL) for m in Module},
    Role.PLATFORM_ADMIN: {m: set(_ALL) for m in Module},
    Role.DATA_ADMIN: {
        Module.SOURCES: set(_ACTOR_FULL),
        Module.DESTINATIONS: set(_ACTOR_FULL),
        # No RESET: rewinding a cursor re-delivers history into the warehouse,
        # which is an owner's call rather than a daily one.
        Module.PIPELINES: {Action.VIEW, Action.VIEW_DATA, Action.CREATE,
                           Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.TRANSFORMS: {Action.VIEW, Action.VIEW_DATA, Action.CREATE,
                            Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.MONITORING: {Action.VIEW, Action.VIEW_DATA, Action.OPERATE},
        Module.ALERTS: {Action.VIEW, Action.CREATE, Action.EDIT,
                        Action.OPERATE, Action.DELETE},
        Module.AUDIT: _RO,
        Module.MEMBERS: _RO,
        Module.SETTINGS: {Action.VIEW, Action.EDIT},
        Module.CONNECTORS: _RO,
    },
    # Writes connectors in the Builder; has no authority over the pipelines
    # that use them. Before this role existed, letting somebody build a
    # connector meant handing them member management and delete on every
    # pipeline in the workspace.
    Role.CONNECTOR_DEV: {
        Module.SOURCES: _RO,
        Module.DESTINATIONS: _RO,
        Module.PIPELINES: _RO,
        Module.TRANSFORMS: set(),
        Module.MONITORING: _RO,
        Module.ALERTS: set(),
        Module.AUDIT: set(),
        Module.MEMBERS: set(),
        Module.SETTINGS: _RO,
        Module.CONNECTORS: {Action.VIEW, Action.CREATE, Action.EDIT,
                            Action.OPERATE, Action.MANAGE_CREDENTIALS,
                            Action.DELETE},
    },
    Role.OPERATOR: {
        Module.SOURCES: {Action.VIEW, Action.OPERATE},
        Module.DESTINATIONS: {Action.VIEW, Action.OPERATE},
        Module.PIPELINES: {Action.VIEW, Action.OPERATE},
        Module.TRANSFORMS: {Action.VIEW, Action.OPERATE},
        # Reading run logs is the job; logs quote failing records.
        Module.MONITORING: {Action.VIEW, Action.VIEW_DATA, Action.OPERATE},
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
        # An analyst is the one person who legitimately needs to see the rows a
        # model produces, and nothing else.
        Module.TRANSFORMS: {Action.VIEW, Action.VIEW_DATA},
        Module.MONITORING: _RO,
        Module.ALERTS: _RO,
        Module.AUDIT: set(),
        Module.MEMBERS: set(),
        Module.SETTINGS: _RO,
        Module.CONNECTORS: _RO,
    },
    # Deliberately no VIEW_DATA anywhere: an auditor reviews what the platform
    # was configured to do and who changed it, not the data it carried.
    Role.AUDITOR: {
        Module.SOURCES: _RO,
        Module.DESTINATIONS: _RO,
        Module.PIPELINES: _RO,
        Module.TRANSFORMS: _RO,
        Module.MONITORING: _RO,
        Module.ALERTS: _RO,
        Module.AUDIT: _RO,
        Module.MEMBERS: _RO,
        Module.SETTINGS: _RO,
        Module.CONNECTORS: _RO,
    },
}


class OrgRole(str, Enum):
    """Authority over an organisation, which owns workspaces.

    Deliberately a separate axis from `Role`. A workspace role answers "what may
    this person do inside this workspace"; an organisation role answers "which
    workspaces exist, who may open them, and who pays". Collapsing the two would
    mean every new workspace needed a membership row for every administrator
    before anyone could see it -- the failure mode where a tenant creates a
    workspace and immediately cannot administer it.
    """

    ORG_OWNER = "ORG_OWNER"
    ORG_ADMIN = "ORG_ADMIN"
    ORG_MEMBER = "ORG_MEMBER"


#: Organisation-level authority. `CREATE` makes workspaces and `ADMIN` manages
#: organisation members.
#:
#: `DELETE` is what separates owning the organisation from running it: it gates
#: every operation on an ORG_OWNER, so an ORG_ADMIN can neither mint one nor
#: demote one. Deleting a workspace is not implemented yet -- it destroys
#: pipelines and run history, so it wants a confirmation flow rather than an
#: endpoint added in passing -- and when it lands this is the permission it
#: belongs behind.
ORG_MATRIX: dict[OrgRole, set[Action]] = {
    OrgRole.ORG_OWNER: {Action.VIEW, Action.CREATE, Action.EDIT, Action.DELETE, Action.ADMIN},
    # Runs the organisation day to day but cannot dissolve it or delete a
    # workspace, which destroys pipelines and history that are not theirs.
    OrgRole.ORG_ADMIN: {Action.VIEW, Action.CREATE, Action.EDIT, Action.ADMIN},
    # Belongs to the organisation and nothing more: reaches only the workspaces
    # they were explicitly added to.
    OrgRole.ORG_MEMBER: {Action.VIEW},
}

#: Organisation roles that carry implicit OWNER inside every workspace the
#: organisation holds. This is the whole point of the layer, and the one place
#: that decides it.
ORG_ROLES_WITH_WORKSPACE_ACCESS: frozenset[OrgRole] = frozenset(
    {OrgRole.ORG_OWNER, OrgRole.ORG_ADMIN}
)


def org_allowed(role: OrgRole | None, action: Action) -> bool:
    if role is None:
        return False
    return action in ORG_MATRIX.get(role, set())


def org_require(role: OrgRole | None, action: Action) -> None:
    if not org_allowed(role, action):
        raise ForbiddenError(
            f"Vai trò tổ chức {role.value if role else 'không có'} "
            f"không có quyền {action.value} trên tổ chức.",
            details={"scope": "organization", "action": action.value,
                     "role": role.value if role else None},
        )


def org_permissions(role: OrgRole | None) -> list[str]:
    """Serialised for the FE beside the workspace map, never merged into it:
    the two answer different questions and merging them hid that."""
    return sorted(a.value for a in ORG_MATRIX.get(role, set())) if role else []


#: Roles a workspace can hand out. PLATFORM_ADMIN is a property of the account
#: (`users.is_platform_admin`), not a membership, so offering it in a role
#: picker would be a control that silently does nothing.
ASSIGNABLE_ROLES: tuple[Role, ...] = (
    Role.OWNER, Role.DATA_ADMIN, Role.CONNECTOR_DEV,
    Role.OPERATOR, Role.ANALYST, Role.AUDITOR,
)


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
