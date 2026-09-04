"""Invariants of the permission matrix.

The matrix is a literal, and a literal is edited by hand. These are the
properties that hold across every role and would be silently lost by a careless
edit -- the kind that leaves a role able to rewind a warehouse but not see the
pipeline it belongs to, or a module quietly absent from a role so that every
check against it denies for a reason nobody wrote down.

Pure: no database, no server, no fixtures. `app.core.permissions` imports only
the standard library, so this runs anywhere Python does.
"""

from __future__ import annotations

import pytest

from app.core.errors import ForbiddenError
from app.core.permissions import (
    ASSIGNABLE_ROLES,
    MATRIX,
    ORG_MATRIX,
    ORG_ROLES_WITH_WORKSPACE_ACCESS,
    Action,
    Module,
    OrgRole,
    Role,
    allowed,
    org_allowed,
    org_permissions,
    org_require,
    permission_map,
    require,
)

#: Actions that only make sense alongside being able to see the thing.
DERIVED_ACTIONS = [
    Action.VIEW_DATA, Action.CREATE, Action.EDIT, Action.OPERATE,
    Action.RESET, Action.MANAGE_CREDENTIALS, Action.DELETE,
]


@pytest.mark.parametrize("role", list(Role))
def test_every_role_covers_every_module(role: Role) -> None:
    """A missing module denies everything, which reads like a deliberate rule
    and is nearly always an omission. Make the omission fail here instead."""
    missing = [m.value for m in Module if m not in MATRIX[role]]
    assert not missing, f"{role.value} has no entry for: {', '.join(missing)}"


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", DERIVED_ACTIONS)
def test_no_authority_without_visibility(role: Role, action: Action) -> None:
    """Every action implies being able to see what it acts on.

    A role that may rewind a pipeline's cursor but not read the pipeline can
    perform the most destructive operation in the product against something it
    cannot inspect first.
    """
    for module in Module:
        if allowed(role, module, action):
            assert allowed(role, module, Action.VIEW), (
                f"{role.value} has {action.value} on {module.value} without view"
            )


def test_admin_implies_everything() -> None:
    """`admin` is the catch-all, so a role holding it on a module must hold the
    rest -- otherwise two checks against the same module disagree."""
    for role in Role:
        for module in Module:
            if allowed(role, module, Action.ADMIN):
                for action in Action:
                    assert allowed(role, module, action), (
                        f"{role.value} has admin on {module.value} but not {action.value}"
                    )


def test_reset_is_owner_only() -> None:
    """The reason `reset` was split out of `operate`.

    Rewinding a cursor, `dbt --full-refresh` and dropping streams re-deliver or
    discard data already in somebody's warehouse. If a role short of owner
    acquires this, the split has been undone by accident.
    """
    holders = {
        role.value for role in Role
        if any(allowed(role, module, Action.RESET) for module in Module)
    }
    assert holders == {Role.OWNER.value, Role.PLATFORM_ADMIN.value}, holders


def test_auditor_never_sees_data() -> None:
    """An auditor reviews what the platform was configured to do and who
    changed it -- not the records it carried."""
    for module in Module:
        assert not allowed(Role.AUDITOR, module, Action.VIEW_DATA), module.value
        for action in DERIVED_ACTIONS:
            if action is Action.VIEW_DATA:
                continue
            assert not allowed(Role.AUDITOR, module, action), (
                f"auditor may {action.value} on {module.value}; the role is read-only"
            )


def test_creating_an_actor_implies_managing_its_credential() -> None:
    """A source or destination cannot be created without writing a secret, so a
    role that may create one and not manage credentials cannot finish the job it
    is allowed to start."""
    for role in Role:
        for module in (Module.SOURCES, Module.DESTINATIONS, Module.CONNECTORS):
            if allowed(role, module, Action.CREATE):
                assert allowed(role, module, Action.MANAGE_CREDENTIALS), (
                    f"{role.value} may create on {module.value} but not set its credential"
                )


def test_connector_dev_touches_no_pipeline() -> None:
    """The role exists so somebody can write connectors without gaining
    authority over production pipelines."""
    for module in (Module.PIPELINES, Module.SOURCES, Module.DESTINATIONS,
                   Module.TRANSFORMS, Module.MEMBERS):
        for action in DERIVED_ACTIONS + [Action.ADMIN]:
            assert not allowed(Role.CONNECTOR_DEV, module, action), (
                f"connector dev may {action.value} on {module.value}"
            )
    assert allowed(Role.CONNECTOR_DEV, Module.CONNECTORS, Action.CREATE)


def test_assignable_roles_exclude_platform_admin() -> None:
    """`is_platform_admin` is a property of the account, not a membership.
    Offering it in a role picker writes a row the account never honours."""
    assert Role.PLATFORM_ADMIN not in ASSIGNABLE_ROLES
    assert set(ASSIGNABLE_ROLES) == set(Role) - {Role.PLATFORM_ADMIN}


def test_permission_map_names_every_module() -> None:
    """The FE hides controls by looking modules up in this map. A missing key
    is indistinguishable from an empty one, so the map must be total."""
    for role in Role:
        mapped = permission_map(role)
        assert set(mapped) == {m.value for m in Module}


def test_require_refuses_and_says_why() -> None:
    with pytest.raises(ForbiddenError) as caught:
        require(Role.ANALYST, Module.PIPELINES, Action.DELETE)
    details = caught.value.details
    assert details["module"] == "pipelines"
    assert details["action"] == "delete"
    assert details["role"] == "ANALYST"


# ── organisation axis ──────────────────────────────────────────────────────

def test_org_matrix_is_a_ladder() -> None:
    """Each organisation role holds strictly more than the one below it."""
    member = ORG_MATRIX[OrgRole.ORG_MEMBER]
    admin = ORG_MATRIX[OrgRole.ORG_ADMIN]
    owner = ORG_MATRIX[OrgRole.ORG_OWNER]
    assert member < admin < owner


def test_only_org_owner_may_delete() -> None:
    """`delete` is what separates owning the organisation from running it: it
    gates every operation on an ORG_OWNER."""
    assert org_allowed(OrgRole.ORG_OWNER, Action.DELETE)
    assert not org_allowed(OrgRole.ORG_ADMIN, Action.DELETE)
    assert not org_allowed(OrgRole.ORG_MEMBER, Action.DELETE)


def test_plain_org_member_reaches_no_workspace_implicitly() -> None:
    """Belonging to the organisation must not, by itself, open its workspaces:
    that is what the membership table is for."""
    assert OrgRole.ORG_MEMBER not in ORG_ROLES_WITH_WORKSPACE_ACCESS
    assert ORG_ROLES_WITH_WORKSPACE_ACCESS == frozenset(
        {OrgRole.ORG_OWNER, OrgRole.ORG_ADMIN}
    )


def test_no_org_role_means_no_org_authority() -> None:
    """A user outside every organisation must not inherit one's authority."""
    assert org_permissions(None) == []
    for action in Action:
        assert not org_allowed(None, action)
    with pytest.raises(ForbiddenError):
        org_require(None, Action.VIEW)


def test_org_member_may_look_but_not_act() -> None:
    assert org_allowed(OrgRole.ORG_MEMBER, Action.VIEW)
    for action in (Action.CREATE, Action.EDIT, Action.DELETE, Action.ADMIN):
        assert not org_allowed(OrgRole.ORG_MEMBER, action), action.value
