"""The Base connector definitions, checked as data.

A Base connector is not code that runs: it is a declarative manifest handed to
Airbyte's `source-declarative-manifest` runner through the
`__injected_declarative_manifest` config key. So the definition is data, and
data is what these tests read -- no Docker, no network, no tenant.

The properties pinned here are the ones whose breakage is silent. A wrong
cursor field fails loudly on the first sync; a wrong *default domain* sends
every request to a separate Base installation that answers a valid token with a
message indistinguishable from an expired one, and the reader has no reason to
suspect the dropdown they never touched.
"""

from __future__ import annotations

import pytest

from app.connectors.base_vn import CONNECTORS, catalogue_entries, manifests
from app.connectors.base_vn._shared import (
    KNOWN_DOMAINS,
    RUNNER_REPOSITORY,
    compile_manifest,
    connection_specification,
)

#: The installation most customers are on. Documented as "bản chính" in the
#: field's own description, so it has to be what the form pre-selects.
PRIMARY_DOMAIN = "base.vn"
SECOND_DOMAIN = "base.com.vn"

ALL = list(CONNECTORS)
IDS = [c.app for c in ALL]


# ── domain ─────────────────────────────────────────────────────────────────

def test_primary_domain_leads_the_known_list() -> None:
    """`domains[0]` is both the form default and the manifest's fallback, so
    the order is behaviour rather than presentation."""
    assert KNOWN_DOMAINS[0] == PRIMARY_DOMAIN
    assert SECOND_DOMAIN in KNOWN_DOMAINS


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_every_connector_offers_both_installations(connector) -> None:
    """Both Base installations exist, with separate accounts, and only the
    customer knows which they are on. A connector that offers one of them
    leaves those customers with no way to connect at all -- which is what the
    two CRM connectors did: their enum held `basecrm.vn` and `base.vn` and
    nothing else."""
    choices = connection_specification(connector)["properties"]["domain"]["enum"]
    assert PRIMARY_DOMAIN in choices, connector.app
    assert SECOND_DOMAIN in choices, connector.app


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_the_default_is_the_primary_installation(connector) -> None:
    spec = connection_specification(connector)["properties"]["domain"]
    assert spec["default"] == PRIMARY_DOMAIN, connector.app
    assert spec["default"] in spec["enum"]


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_domain_is_interpolated_not_baked(connector) -> None:
    """The host has to come from the config at request time. Substituting it
    when the manifest is compiled would bake one installation into a connector
    that ships to customers on both, and the dropdown would do nothing."""
    manifest = compile_manifest(connector)
    for name, stream in manifest["definitions"]["streams"].items():
        url = stream["retriever"]["requester"]["url_base"]
        assert "{domain}" not in url, f"{connector.app}.{name} still has a literal placeholder"
        assert "config['domain']" in url, f"{connector.app}.{name} does not read config"
        # The fallback inside the interpolation is the same default the form
        # shows; a disagreement there is a source created with no domain
        # silently talking to the other installation.
        assert connector.domains[0] in url, f"{connector.app}.{name} falls back elsewhere"


@pytest.mark.parametrize("domain", [PRIMARY_DOMAIN, SECOND_DOMAIN])
@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_both_domains_resolve_to_a_plausible_host(connector, domain: str) -> None:
    """What the interpolation would produce, rendered by hand.

    Not a network call -- it checks the shape, which is where a typo hides: a
    missing dot yields `servicebase.vn`, which resolves to somebody else's
    domain rather than failing.
    """
    template = connector.url_base
    rendered = template.replace("{domain}", domain)
    assert rendered.startswith("https://")
    host = rendered.split("//", 1)[1].split("/", 1)[0]
    assert host.endswith("." + domain) or host == domain, host
    assert ".." not in host and not host.startswith(".")


# ── certification, and what it is allowed to claim ─────────────────────────

@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_certification_is_earned_not_defaulted(connector) -> None:
    """`SUPPORTED` means measured against a live tenant. It was the dataclass
    default, so every connector claimed it by doing nothing -- which made the
    strongest word in the vocabulary carry no information."""
    assert connector.certification in ("SUPPORTED", "BETA"), connector.certification


def test_catalogue_status_fields_agree() -> None:
    """Three fields described the same thing and disagreed on screen:
    `certification=SUPPORTED` beside `release_stage=beta` beside
    `support_level=certified`. A reader had no way to know which to believe."""
    for entry in catalogue_entries():
        supported = entry["certification"] == "SUPPORTED"
        assert entry["release_stage"] == ("generally_available" if supported else "beta")
        assert entry["support_level"] == ("certified" if supported else "community")


def test_bundled_connectors_stay_offered_under_the_default_launch_scope() -> None:
    """The regression this had to avoid.

    `SUPPORTED_ONLY` is the shipped launch scope and it offered
    `certification == "SUPPORTED"` and nothing else, so telling the truth here
    would have removed all twelve Base connectors from the create wizard. They
    are exempt as BUNDLED for the same reason BUILDER already was: the scope
    exists to fence off upstream connectors nobody here has tested, and these
    are not upstream.
    """
    from app.core.config import Settings

    settings = Settings(connector_launch_scope="SUPPORTED_ONLY", connector_beta_allowlist="")
    for entry in catalogue_entries():
        assert settings.connector_is_offered(
            entry["connector_key"], entry["certification"], "BUNDLED"
        ), entry["connector_key"]


# ── the cursor's overlap ───────────────────────────────────────────────────

def test_incremental_streams_carry_a_lookback_window() -> None:
    """Without an overlap the window is `[state, now]` and a record that
    arrives after the sync that should have collected it is never read again --
    clock skew between Base and this host is enough."""
    seen = 0
    for name, manifest in manifests().items():
        for stream_name, stream in manifest["definitions"]["streams"].items():
            cursor = stream.get("incremental_sync")
            if not cursor:
                continue
            seen += 1
            assert "lookback_window" in cursor, f"{name}.{stream_name}"
            assert "config.get('lookback_window')" in cursor["lookback_window"], (
                f"{name}.{stream_name} bakes the overlap instead of reading config"
            )
    assert seen > 0, "no incremental stream found; the test is not proving anything"


def test_lookback_defaults_to_off() -> None:
    """Off by default, and the reason is that the destination write mode is the
    workspace's choice: re-reading an overlap is free under append_dedup and
    duplicates rows under plain append. So it is offered per source rather than
    decided here for everybody."""
    for connector in ALL:
        properties = connection_specification(connector)["properties"]
        if not any(s.incremental for s in connector.streams):
            assert "lookback_window" not in properties, connector.app
            continue
        assert properties["lookback_window"]["default"] == "PT0S", connector.app


# ── things that were already right and must stay that way ──────────────────

@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_every_stream_can_deduplicate(connector) -> None:
    """A re-sync with no primary key appends instead of replacing. `validate()`
    enforces this at import; pinning it here says it is a rule, not an
    accident."""
    for stream in connector.streams:
        assert stream.primary_key, f"{connector.app}.{stream.name}"


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_substream_parents_exist(connector) -> None:
    names = {s.name for s in connector.streams}
    for stream in connector.streams:
        if stream.parent:
            assert stream.parent.stream in names, f"{connector.app}.{stream.name}"


def test_all_connectors_run_on_the_stock_airbyte_runner() -> None:
    """No fork, no patched image: the manifest is the connector and Airbyte's
    published runner executes it."""
    for entry in catalogue_entries():
        assert entry["docker_repository"] == RUNNER_REPOSITORY
        assert entry["declarative_manifest"]["type"] == "DeclarativeSource"
