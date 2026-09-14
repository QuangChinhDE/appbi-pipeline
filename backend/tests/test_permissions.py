"""Invariants of the permission system.

Presets are literals, and a literal is edited by hand. These are the properties
that hold across every role and would be silently lost by a careless edit -- the
kind that leaves a role able to rewind a warehouse but not see the pipeline it
belongs to, or a module quietly absent so that every check against it denies for
a reason nobody wrote down.

Pure: no database, no server, no fixtures. `app.core.permissions` imports only
the standard library, so this runs anywhere Python does.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core.errors import ForbiddenError
from app.core.permissions import (
    ASSIGNABLE_ROLES,
    LEVELS,
    MODULE_ACTIONS,
    ORG_MATRIX,
    ORG_ROLES_WITH_WORKSPACE_ACCESS,
    PRESETS,
    Action,
    Module,
    OrgRole,
    Role,
    ValidationErrorLike,
    allowed,
    catalogue,
    effective,
    level_actions,
    level_of,
    levels_for,
    org_allowed,
    org_permissions,
    org_require,
    parse_overrides,
    require,
    serialise,
)

#: Actions that only make sense alongside being able to see the thing.
DERIVED_ACTIONS = [
    Action.VIEW_DATA, Action.CREATE, Action.EDIT, Action.OPERATE,
    Action.RESET, Action.MANAGE_CREDENTIALS, Action.DELETE,
]


def perms(role: Role, overrides: dict | None = None) -> dict:
    return effective(role, overrides)


# ── the vocabulary ─────────────────────────────────────────────────────────

def test_every_gated_pair_can_actually_be_granted() -> None:
    """A gate on a pair missing from `MODULE_ACTIONS` is a door with no key.

    The permission editor offers exactly the actions in that table. If a route
    demands one that is not there, nobody can be given it -- not by preset, not
    by hand -- and the endpoint is unreachable for everyone but a platform
    administrator, silently, with a 403 that reads like a policy decision.

    So the source is read and every literal pair it checks is required to exist.
    Dynamic modules (`kind.module`, which is SOURCES or DESTINATIONS) are
    checked against both.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    literal = re.compile(
        r"(?:require|can)\(\s*Module\.([A-Z_]+)\s*,\s*Action\.([A-Z_]+)", re.S
    )
    dynamic = re.compile(r"(?:require|can)\(\s*kind\.module\s*,\s*Action\.([A-Z_]+)")

    missing: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        pairs = [
            (Module[m], Action[a]) for m, a in literal.findall(text)
        ]
        for a in dynamic.findall(text):
            pairs.append((Module.SOURCES, Action[a]))
            pairs.append((Module.DESTINATIONS, Action[a]))
        for module, action in pairs:
            if action not in MODULE_ACTIONS[module]:
                missing.append(f"{path.name}: {module.value} cannot be asked to {action.value}")

    assert not missing, "gates nobody can satisfy:\n  " + "\n  ".join(sorted(set(missing)))


def test_admin_is_never_grantable_on_a_module() -> None:
    """`admin` belongs to the organisation axis.

    The old matrix handed it to OWNER on all ten modules, where nothing ever
    checked it -- so granting it said nothing while reading as the broadest
    permission in the product.
    """
    for module, actions in MODULE_ACTIONS.items():
        assert Action.ADMIN not in actions, module.value


def test_view_is_in_every_module_vocabulary() -> None:
    """A module nobody can be allowed to see is a module nobody can use."""
    for module, actions in MODULE_ACTIONS.items():
        assert Action.VIEW in actions, module.value


# ── levels ─────────────────────────────────────────────────────────────────

def test_levels_are_a_ladder() -> None:
    """Each level grants everything the level below it grants.

    A dropdown whose third entry withholds something its second entry gave is
    not a ladder, and an administrator moving somebody "up" would quietly take
    something away.
    """
    for module in Module:
        offered = levels_for(module)
        for lower, higher in zip(offered, offered[1:]):
            assert level_actions(module, lower) <= level_actions(module, higher), (
                f"{module.value}: {higher} does not contain {lower}"
            )


def test_a_level_that_adds_nothing_is_not_offered() -> None:
    """Audit has one verb. Offering five levels that all mean the same thing
    asks somebody to make a choice that does not exist."""
    assert levels_for(Module.AUDIT) == ["none", "view"]
    assert levels_for(Module.SETTINGS) == ["none", "view", "edit"]
    assert levels_for(Module.PIPELINES) == list(LEVELS)


def test_operate_sits_between_view_and_edit() -> None:
    """The rung a four-level scheme cannot express, and the reason for this one.

    Running a pipeline and editing it are different jobs. Without this rung an
    operator has to be given `edit` -- which also renames, reconfigures and
    deletes.
    """
    operate = level_actions(Module.PIPELINES, "operate")
    assert operate == {Action.VIEW, Action.OPERATE}
    assert Action.EDIT not in operate and Action.DELETE not in operate


def test_view_data_has_no_rung() -> None:
    """Reading the rows is not a quantity of authority.

    An auditor outranks an analyst on configuration and must still not see the
    data, so `view_data` cannot sit on the ladder -- it is a box on its own.
    """
    for module in Module:
        for level in levels_for(module):
            if level == "full":
                continue
            assert Action.VIEW_DATA not in level_actions(module, level), (
                f"{module.value}:{level} grants view_data"
            )


def test_level_of_names_what_it_can_and_admits_what_it_cannot() -> None:
    assert level_of(Module.PIPELINES, {Action.VIEW}) == "view"
    assert level_of(Module.PIPELINES, set(MODULE_ACTIONS[Module.PIPELINES])) == "full"
    # Analyst's transforms grant -- view plus the data box -- is no level.
    assert level_of(Module.TRANSFORMS, {Action.VIEW, Action.VIEW_DATA}) == "custom"


# ── presets ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", list(Role))
def test_every_preset_covers_every_module(role: Role) -> None:
    """A missing module denies everything, which reads like a deliberate rule
    and is nearly always an omission. Make the omission fail here instead."""
    missing = [m.value for m in Module if m not in PRESETS[role]]
    assert not missing, f"{role.value} has no entry for: {', '.join(missing)}"


@pytest.mark.parametrize("role", list(Role))
def test_presets_only_mention_actions_the_module_has(role: Role) -> None:
    for module, actions in PRESETS[role].items():
        stray = actions - set(MODULE_ACTIONS[module])
        assert not stray, (
            f"{role.value} grants {[a.value for a in stray]} on {module.value}, "
            "which cannot be asked to do that"
        )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", DERIVED_ACTIONS)
def test_no_authority_without_visibility(role: Role, action: Action) -> None:
    """Every action implies being able to see what it acts on.

    A role that may rewind a pipeline's cursor but not read the pipeline can
    perform the most destructive operation in the product against something it
    cannot inspect first.
    """
    resolved = perms(role)
    for module in Module:
        if allowed(resolved, module, action):
            assert allowed(resolved, module, Action.VIEW), (
                f"{role.value} has {action.value} on {module.value} without view"
            )


def test_reset_is_owner_only() -> None:
    """The reason `reset` was split out of `operate`.

    Rewinding a cursor, `dbt --full-refresh` and dropping streams re-deliver or
    discard data already in somebody's warehouse. If a preset short of owner
    acquires this, the split has been undone by accident.
    """
    holders = {
        role.value for role in Role
        if any(allowed(perms(role), module, Action.RESET) for module in Module)
    }
    assert holders == {Role.OWNER.value, Role.PLATFORM_ADMIN.value}, holders


def test_auditor_never_sees_data() -> None:
    """An auditor reviews what the platform was configured to do and who
    changed it -- not the records it carried."""
    resolved = perms(Role.AUDITOR)
    for module in Module:
        assert not allowed(resolved, module, Action.VIEW_DATA), module.value
        for action in DERIVED_ACTIONS:
            if action is Action.VIEW_DATA:
                continue
            assert not allowed(resolved, module, action), (
                f"auditor may {action.value} on {module.value}; the role is read-only"
            )


def test_creating_an_actor_implies_managing_its_credential() -> None:
    """A source or destination cannot be created without writing a secret, so a
    role that may create one and not manage credentials cannot finish the job it
    is allowed to start."""
    for role in Role:
        resolved = perms(role)
        for module in (Module.SOURCES, Module.DESTINATIONS, Module.CONNECTORS):
            if allowed(resolved, module, Action.CREATE):
                assert allowed(resolved, module, Action.MANAGE_CREDENTIALS), (
                    f"{role.value} may create on {module.value} but not set its credential"
                )


def test_connector_dev_touches_no_pipeline() -> None:
    """The role exists so somebody can write connectors without gaining
    authority over production pipelines."""
    resolved = perms(Role.CONNECTOR_DEV)
    for module in (Module.PIPELINES, Module.SOURCES, Module.DESTINATIONS,
                   Module.TRANSFORMS, Module.MEMBERS):
        for action in DERIVED_ACTIONS:
            assert not allowed(resolved, module, action), (
                f"connector dev may {action.value} on {module.value}"
            )
    assert allowed(resolved, Module.CONNECTORS, Action.CREATE)


def test_assignable_roles_exclude_platform_admin() -> None:
    """`is_platform_admin` is a property of the account, not a membership.
    Offering it in a role picker writes a row the account never honours."""
    assert Role.PLATFORM_ADMIN not in ASSIGNABLE_ROLES
    assert set(ASSIGNABLE_ROLES) == set(Role) - {Role.PLATFORM_ADMIN}


# ── resolution: preset, then what the membership stores ────────────────────

def test_no_overrides_is_exactly_the_preset() -> None:
    """The migration that was not needed. Every membership predating the
    permissions column stores nothing, and must keep behaving identically."""
    for role in Role:
        assert effective(role, None) == PRESETS[role]
        assert effective(role, {}) == PRESETS[role]


def test_an_empty_list_revokes_rather_than_falling_back() -> None:
    """The difference between "nothing stored" and "stored nothing".

    Falling back to the preset for an explicitly emptied module would make
    revoking one module impossible -- the editor would save, report success,
    and the permission would still be there.
    """
    resolved = effective(Role.OWNER, {"audit": []})
    assert resolved[Module.AUDIT] == set()
    assert resolved[Module.PIPELINES] == PRESETS[Role.OWNER][Module.PIPELINES]


def test_a_module_absent_from_the_map_keeps_its_preset() -> None:
    """A module added to the product after somebody's permissions were last
    edited must arrive with a sensible default, not silently denied."""
    resolved = effective(Role.OPERATOR, {"alerts": ["view"]})
    assert resolved[Module.ALERTS] == {Action.VIEW}
    assert resolved[Module.MONITORING] == PRESETS[Role.OPERATOR][Module.MONITORING]


def test_a_stale_stored_action_narrows_and_never_widens() -> None:
    """A map written before a module lost an action must not grant it back."""
    resolved = effective(Role.ANALYST, {"audit": ["view", "delete", "not_a_verb"]})
    assert resolved[Module.AUDIT] == {Action.VIEW}


def test_a_stored_level_name_resolves_like_the_dropdown() -> None:
    assert effective(Role.AUDITOR, {"pipelines": "operate"})[Module.PIPELINES] == {
        Action.VIEW, Action.OPERATE,
    }


def test_platform_admin_ignores_every_override() -> None:
    """Administering the deployment cannot depend on never having been given a
    restricted seat in one of its workspaces."""
    resolved = effective(Role.ANALYST, {"pipelines": [], "members": []},
                         is_platform_admin=True)
    for module in Module:
        assert resolved[module] == set(MODULE_ACTIONS[module]), module.value


# ── what an administrator is allowed to submit ─────────────────────────────

def test_parse_refuses_an_action_the_module_does_not_have() -> None:
    """Storing less than was asked for is how a permission editor lies about
    what it saved. Refuse instead."""
    with pytest.raises(ValidationErrorLike) as caught:
        parse_overrides({"audit": ["delete"]})
    assert caught.value.code == "ACTION_NOT_ON_MODULE"


def test_parse_refuses_an_unknown_module_and_an_unknown_level() -> None:
    with pytest.raises(ValidationErrorLike) as caught:
        parse_overrides({"dashboards": ["view"]})
    assert caught.value.code == "MODULE_UNKNOWN"
    with pytest.raises(ValidationErrorLike) as caught:
        parse_overrides({"audit": "edit"})
    assert caught.value.code == "LEVEL_UNKNOWN"


def test_parse_accepts_both_spellings_and_stores_one() -> None:
    """A level and its action list are the same grant; storing the expanded
    form means a preset improved later does not silently rewrite what somebody
    chose by hand."""
    assert parse_overrides({"pipelines": "operate"}) == {
        "pipelines": ["operate", "view"],
    }
    assert parse_overrides({"pipelines": ["view", "operate"]}) == {
        "pipelines": ["operate", "view"],
    }


# ── serialisation ──────────────────────────────────────────────────────────

def test_serialise_names_every_module() -> None:
    """The FE hides controls by looking modules up in this map. A missing key
    is indistinguishable from an empty one, so the map must be total."""
    for role in Role:
        assert set(serialise(perms(role))) == {m.value for m in Module}


def test_catalogue_describes_every_module_and_only_real_levels() -> None:
    """The browser renders the editor from this and hardcodes nothing."""
    document = catalogue()
    assert {entry["module"] for entry in document["modules"]} == {m.value for m in Module}
    for entry in document["modules"]:
        module = Module(entry["module"])
        assert entry["actions"] == [a.value for a in MODULE_ACTIONS[module]]
        assert entry["levels"] == levels_for(module)
        assert set(entry["level_actions"]) == set(entry["levels"])
    assert set(document["presets"]) == {r.value for r in ASSIGNABLE_ROLES}


def test_require_refuses_and_says_why() -> None:
    with pytest.raises(ForbiddenError) as caught:
        require(perms(Role.ANALYST), Module.PIPELINES, Action.DELETE, Role.ANALYST)
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
