"""Who may do what, and where that answer comes from (section 4.2).

The backend is the only authority. FE gating exists purely so the UI is not
littered with buttons that would 403 -- every endpoint re-checks here.

WHAT CHANGED, AND WHY IT HAD TO.

A role used to *be* the permission system: `MATRIX[role][module]` was consulted
on every request, so the only way to let one person manage alerts without
handing them monitoring was to invent a role in Python and redeploy. Six roles
is not a permission model, it is six opinions about how teams are shaped.

Now a role is a **preset** -- a named starting point that an administrator
copies into a membership and then edits. The membership is the answer from that
point on. Nothing migrates: a membership with no stored map still resolves
through its preset, which is what every existing row does.

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
    #: The organisation axis only. Deliberately absent from every entry in
    #: `MODULE_ACTIONS`, so it can never be granted on a module -- which is
    #: what it always meant, and what the old matrix obscured by handing it to
    #: OWNER on all ten.
    ADMIN = "admin"


#: WHAT EACH MODULE CAN ACTUALLY BE ASKED TO DO.
#:
#: The matrix used to hand every role the whole `Action` enum on every module,
#: which read as generosity and was really an absence of thought: RESET on the
#: audit log, MANAGE_CREDENTIALS on monitoring, ADMIN on sources. None of those
#: are checked anywhere, so granting them said nothing -- but an admin choosing
#: them would believe they were choosing something.
#:
#: This is the vocabulary. The permission editor offers exactly these boxes, a
#: preset may only mention these, and `test_permissions_vocabulary` proves every
#: pair the code actually gates on appears here -- because a gate on a pair
#: nobody can grant is a door with no key.
MODULE_ACTIONS: dict[Module, tuple[Action, ...]] = {
    Module.SOURCES: (
        Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE,
        Action.MANAGE_CREDENTIALS, Action.DELETE,
    ),
    Module.DESTINATIONS: (
        Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE,
        Action.MANAGE_CREDENTIALS, Action.DELETE,
    ),
    Module.PIPELINES: (
        Action.VIEW, Action.VIEW_DATA, Action.CREATE, Action.EDIT,
        Action.OPERATE, Action.RESET, Action.DELETE,
    ),
    Module.TRANSFORMS: (
        Action.VIEW, Action.VIEW_DATA, Action.CREATE, Action.EDIT,
        Action.OPERATE, Action.RESET, Action.DELETE,
    ),
    #: Cancelling and retrying a run is operating it; reading a log that quotes
    #: the rows that failed is reading the data.
    Module.MONITORING: (Action.VIEW, Action.VIEW_DATA, Action.OPERATE),
    #: OPERATE is muting and acknowledging. Changing what a rule says is EDIT.
    Module.ALERTS: (
        Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE, Action.DELETE,
    ),
    #: Read-only by nature. An audit log somebody can edit is not one.
    Module.AUDIT: (Action.VIEW,),
    Module.MEMBERS: (Action.VIEW, Action.CREATE, Action.EDIT, Action.DELETE),
    Module.SETTINGS: (Action.VIEW, Action.EDIT),
    Module.CONNECTORS: (
        Action.VIEW, Action.CREATE, Action.EDIT, Action.OPERATE,
        Action.MANAGE_CREDENTIALS, Action.DELETE,
    ),
}


#: The rungs an administrator picks from, in order.
#:
#: A level is shorthand for a set of actions, not a replacement for one. The
#: rung that matters is in the middle: `operate` exists because running a
#: pipeline and editing it are different jobs, and a four-level scheme has
#: nothing between `view` and `edit` that can say so.
#:
#: `view_data` deliberately has no rung of its own. Whether somebody may read
#: the rows is not a question of how much authority they hold -- an auditor
#: outranks an analyst on configuration and still must not see the data -- so it
#: is a box on its own, and any level that includes it is only a starting point.
LEVELS: tuple[str, ...] = ("none", "view", "operate", "edit", "full")

_LADDER: dict[str, set[Action]] = {
    "none": set(),
    "view": {Action.VIEW},
    "operate": {Action.VIEW, Action.OPERATE},
    "edit": {Action.VIEW, Action.OPERATE, Action.CREATE, Action.EDIT},
}


def level_actions(module: Module, level: str) -> set[Action]:
    """The actions a named level grants on one module.

    Derived from `MODULE_ACTIONS` rather than written out per module, so a
    module that gains an action gains it in `full` on the same line.
    """
    vocabulary = set(MODULE_ACTIONS.get(module, ()))
    if level == "full":
        return vocabulary
    return _LADDER.get(level, set()) & vocabulary


def levels_for(module: Module) -> list[str]:
    """The levels worth offering for one module.

    A rung granting exactly what the rung below it grants is not a choice. Audit
    has one verb, so it offers `none` and `view` and stops -- rather than five
    dropdown entries that all mean the same thing.
    """
    offered: list[str] = []
    seen: list[set[Action]] = []
    for level in LEVELS:
        actions = level_actions(module, level)
        if actions in seen:
            continue
        seen.append(actions)
        offered.append(level)
    return offered


def level_of(module: Module, actions: set[Action]) -> str:
    """Which level names this set exactly, or `custom` when none does."""
    for level in levels_for(module):
        if level_actions(module, level) == actions:
            return level
    return "custom"


def _grant(spec: dict[Module, str | set[Action]]) -> dict[Module, set[Action]]:
    """A preset row: levels where a level says it, an explicit set where not."""
    return {
        module: (level_actions(module, value) if isinstance(value, str) else set(value))
        for module, value in spec.items()
    }


#: Named starting points, not the permission system itself.
#:
#: A preset is copied into a membership the first time an administrator edits
#: that membership, and from then on the membership is the answer. Until then
#: the preset resolves live, so an existing row needs no migration and a preset
#: improved here reaches everybody still following it.
PRESETS: dict[Role, dict[Module, set[Action]]] = {
    Role.OWNER: _grant({m: "full" for m in Module}),
    Role.PLATFORM_ADMIN: _grant({m: "full" for m in Module}),
    Role.DATA_ADMIN: _grant({
        Module.SOURCES: "full",
        Module.DESTINATIONS: "full",
        # No RESET: rewinding a cursor re-delivers history into somebody's
        # warehouse, which is an owner's call rather than a daily one.
        Module.PIPELINES: {Action.VIEW, Action.VIEW_DATA, Action.CREATE,
                           Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.TRANSFORMS: {Action.VIEW, Action.VIEW_DATA, Action.CREATE,
                            Action.EDIT, Action.OPERATE, Action.DELETE},
        Module.MONITORING: "full",
        Module.ALERTS: "full",
        Module.AUDIT: "view",
        Module.MEMBERS: "view",
        Module.SETTINGS: "edit",
        Module.CONNECTORS: "view",
    }),
    # Writes connectors in the Builder; has no authority over the pipelines that
    # use them. Before this role existed, letting somebody build a connector
    # meant handing them member management and delete on every pipeline in the
    # workspace.
    Role.CONNECTOR_DEV: _grant({
        Module.SOURCES: "view",
        Module.DESTINATIONS: "view",
        Module.PIPELINES: "view",
        Module.TRANSFORMS: "none",
        Module.MONITORING: "view",
        Module.ALERTS: "none",
        Module.AUDIT: "none",
        Module.MEMBERS: "none",
        Module.SETTINGS: "view",
        Module.CONNECTORS: "full",
    }),
    Role.OPERATOR: _grant({
        Module.SOURCES: "operate",
        Module.DESTINATIONS: "operate",
        Module.PIPELINES: "operate",
        Module.TRANSFORMS: "operate",
        # Reading run logs is the job, and logs quote failing records.
        Module.MONITORING: "full",
        Module.ALERTS: "operate",
        Module.AUDIT: "none",
        Module.MEMBERS: "none",
        Module.SETTINGS: "view",
        Module.CONNECTORS: "view",
    }),
    Role.ANALYST: _grant({
        Module.SOURCES: "view",
        Module.DESTINATIONS: "view",
        Module.PIPELINES: "view",
        # An analyst is the one person who legitimately needs to see the rows a
        # model produces, and nothing else.
        Module.TRANSFORMS: {Action.VIEW, Action.VIEW_DATA},
        Module.MONITORING: "view",
        Module.ALERTS: "view",
        Module.AUDIT: "none",
        Module.MEMBERS: "none",
        Module.SETTINGS: "view",
        Module.CONNECTORS: "view",
    }),
    # Deliberately no VIEW_DATA anywhere: an auditor reviews what the platform
    # was configured to do and who changed it, not the data it carried.
    Role.AUDITOR: _grant({m: "view" for m in Module}),
}

#: The old name, kept while anything still reads it.
MATRIX = PRESETS


# ── The one place a permission question is answered ──────────────────────────

def effective(
    role: Role,
    overrides: dict | None = None,
    *,
    is_platform_admin: bool = False,
) -> dict[Module, set[Action]]:
    """What this membership may do, as every reader must see it.

    THE SINGLE SOURCE OF TRUTH. The route gate, the payload the browser is sent,
    and the matrix an administrator edits all resolve through this function, so
    they cannot answer the same question differently -- the failure where a menu
    item is shown, the page opens, and the endpoint behind it returns 403.

    Resolution, in order:

    1. a platform administrator holds everything, membership or not;
    2. a module stored on the membership wins, including when it stores nothing
       -- an explicit empty list is a revocation and must survive;
    3. anything the membership does not mention falls back to the preset named
       by `role`, so a module added to the product after somebody's permissions
       were last edited arrives with a sensible default rather than silence.
    """
    if is_platform_admin:
        return _grant({m: "full" for m in Module})

    resolved = {m: set(a) for m, a in PRESETS.get(role, {}).items()}
    for module in Module:
        resolved.setdefault(module, set())

    for key, raw in (overrides or {}).items():
        module = _as_module(key)
        if module is None:
            continue
        resolved[module] = _as_actions(module, raw)
    return resolved


def _as_module(key) -> Module | None:
    try:
        return Module(str(key))
    except ValueError:
        return None


def _as_actions(module: Module, raw) -> set[Action]:
    """Actions from stored JSON, keeping only what the module can be asked.

    Fails towards *less*: an action that is not in the vocabulary, or a level
    name that does not exist, is dropped rather than guessed at. A stored map
    that has gone stale therefore narrows access; it never widens it.
    """
    vocabulary = set(MODULE_ACTIONS.get(module, ()))
    if isinstance(raw, str):
        return level_actions(module, raw)
    if not isinstance(raw, (list, tuple, set)):
        return set()
    out: set[Action] = set()
    for item in raw:
        try:
            action = Action(str(item))
        except ValueError:
            continue
        if action in vocabulary:
            out.add(action)
    return out


def parse_overrides(raw: dict | None) -> dict[str, list[str]]:
    """Validate an incoming permission map into its stored shape.

    Raises rather than trimming, because this is what an administrator just
    submitted: silently storing less than they asked for is how a permission
    editor lies about what it saved.
    """
    if not raw:
        return {}
    stored: dict[str, list[str]] = {}
    for key, value in raw.items():
        module = _as_module(key)
        if module is None:
            raise ValidationErrorLike(f"Unknown module: {key}", "MODULE_UNKNOWN")
        vocabulary = set(MODULE_ACTIONS[module])
        actions: set[Action] = set()
        if isinstance(value, str):
            if value not in levels_for(module):
                raise ValidationErrorLike(
                    f"Level {value!r} does not exist for {module.value}.",
                    "LEVEL_UNKNOWN",
                )
            actions = level_actions(module, value)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                try:
                    action = Action(str(item))
                except ValueError:
                    raise ValidationErrorLike(
                        f"Unknown action: {item}", "ACTION_UNKNOWN",
                    ) from None
                if action not in vocabulary:
                    raise ValidationErrorLike(
                        f"{module.value} cannot be asked to {action.value}.",
                        "ACTION_NOT_ON_MODULE",
                    )
                actions.add(action)
        else:
            raise ValidationErrorLike(
                f"Permissions for {module.value} must be a level or a list.",
                "PERMISSION_SHAPE",
            )
        stored[module.value] = sorted(a.value for a in actions)
    return stored


class ValidationErrorLike(Exception):
    """Raised by the pure functions here and turned into an API error upstream.

    This module is imported by the worker and by tests that have no request, so
    it does not reach for the HTTP error types.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def allowed(perms: dict[Module, set[Action]], module: Module, action: Action) -> bool:
    return action in perms.get(module, set())


def require(
    perms: dict[Module, set[Action]], module: Module, action: Action, role: Role,
) -> None:
    if not allowed(perms, module, action):
        raise ForbiddenError(
            f"This account does not carry {action.value} on {module.value}.",
            code="MODULE_ACTION_DENIED",
            details={"module": module.value, "action": action.value,
                     "role": role.value},
        )


def serialise(perms: dict[Module, set[Action]]) -> dict[str, list[str]]:
    """Serialised for the FE so it can hide (not enforce) unavailable actions."""
    return {
        module.value: sorted(a.value for a in perms.get(module, set()))
        for module in Module
    }


def catalogue() -> dict:
    """Everything a permission editor needs, so the browser hardcodes nothing.

    The admin screen used to carry its own copy of the module list and its own
    idea of which actions each one had. Two hand-maintained lists of the same
    thing is how a module comes to be editable in the UI while the backend has
    never heard of it.
    """
    return {
        "modules": [
            {
                "module": module.value,
                "actions": [a.value for a in MODULE_ACTIONS[module]],
                "levels": levels_for(module),
                "level_actions": {
                    level: sorted(a.value for a in level_actions(module, level))
                    for level in levels_for(module)
                },
            }
            for module in Module
        ],
        "presets": {
            role.value: serialise(PRESETS[role])
            for role in ASSIGNABLE_ROLES
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
            f"The organization role {role.value if role else 'none'} does "
            f"not carry {action.value} on the organization.",
            code="ORG_ACTION_DENIED",
            details={"scope": "organization", "action": action.value,
                     "role": role.value if role else "none"},
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
