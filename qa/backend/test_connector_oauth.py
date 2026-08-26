"""Signing in to a connector, instead of pasting a key.

Both credential styles are offered where the connector supports both, because
they suit different situations. A service account belongs to the organisation
and survives people leaving, which is what a warehouse wants — and it is the
only option Airbyte's BigQuery connectors expose at all. OAuth lets somebody
grant access to their own spreadsheet without first sharing it with an address
like `appbi-685@base-testlab-01.iam.gserviceaccount.com`.

The properties worth testing here are the ones that go quietly wrong:

  * the refresh token must never reach the browser
  * `state` must not be a session token, or a live credential travels in a URL
    to Google and into their logs
  * a grant must be single-use, workspace-scoped and connector-scoped
  * the credentials must land in the shape the connector's own spec declares,
    and be treated as secret at every level of that shape
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid

import jwt
import pytest

from app.core.config import settings
from app.services import oauth

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "oauth_redirect_uri",
                        "https://appbi.example/api/v1/oauth/callback")


# ── which connectors, and on whose say-so ────────────────────────────────────

def test_oauth_is_offered_only_where_the_connector_declares_it() -> None:
    """Driven by the shipped spec, not by an opinion held here.

    Airbyte's BigQuery connectors have no OAuth branch: the only credential
    they accept is a service-account JSON. Offering a "Sign in with Google"
    button for them would produce a token the connector cannot use.
    """
    registry = json.loads(
        (ROOT / "backend" / "app" / "resources" / "connector_registry.json")
        .read_text(encoding="utf-8"))
    specs = {c["connector_key"]: (c.get("spec_schema") or {})
             for c in registry["connectors"]}

    def has_oauth_branch(connector_key: str) -> bool:
        properties = (specs.get(connector_key) or {}).get("properties") or {}
        for value in properties.values():
            for branch in value.get("oneOf") or []:
                if "refresh_token" in (branch.get("properties") or {}):
                    return True
        return False

    # Every connector this build ships that OAuth is offered for must actually
    # have an OAuth branch in its spec. Providers may know about connectors the
    # launch scope has since dropped -- `source-microsoft-onedrive` is one --
    # and that is harmless: nothing can select a connector that is not shipped.
    shipped = set(specs)
    offered = {key for provider in oauth.PROVIDERS.values() for key in provider.scopes}
    assert offered & shipped, "no shipped connector is offered OAuth at all"

    for connector_key in sorted(offered & shipped):
        assert has_oauth_branch(connector_key), (
            f"{connector_key} is offered OAuth but its spec has no branch for it")
    assert "source-google-sheets" in offered & shipped

    for connector_key in ("source-bigquery", "destination-bigquery",
                          "source-postgres", "destination-postgres"):
        assert oauth.provider_for(connector_key) is None, (
            f"{connector_key} would be offered a token it cannot use")
        assert not has_oauth_branch(connector_key), connector_key


def test_a_provider_with_no_registered_application_is_not_offered(monkeypatch) -> None:
    """Half-offering it is worse than not offering it.

    Without a client id there is no consent screen to send anyone to, so the
    wizard should show the service-account path cleanly rather than a button
    that fails after a redirect.
    """
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    assert oauth.configured(oauth.PROVIDERS["google"]) is False

    _configure(monkeypatch)
    assert oauth.configured(oauth.PROVIDERS["google"]) is True


# ── the consent URL ──────────────────────────────────────────────────────────

def test_the_consent_url_asks_for_a_refresh_token(monkeypatch) -> None:
    """`access_type=offline` and `prompt=consent`, or there is no refresh token.

    Without the first, Google returns an access token that expires within the
    hour. Without the second, a user who has authorised before gets no refresh
    token at all on a re-consent -- and the failure surfaces as a scheduled
    sync failing overnight with what looks like a permissions error.
    """
    import urllib.parse

    _configure(monkeypatch)
    url = oauth.authorize_url(
        oauth.PROVIDERS["google"], "source-google-sheets", "the-state")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["the-state"]
    assert query["redirect_uri"] == [settings.oauth_redirect_uri]


def test_only_read_scopes_are_requested(monkeypatch) -> None:
    """This product copies data out; it never writes to the user's documents.

    A consent screen asking for write access to all your files is one people
    are right to refuse, and it would be asking for something never used.
    """
    _configure(monkeypatch)
    for scope in oauth.PROVIDERS["google"].scopes["source-google-sheets"]:
        assert scope.endswith(".readonly"), scope


# ── the state parameter ──────────────────────────────────────────────────────

def test_state_is_not_a_session_token() -> None:
    """The state travels in a URL to Google and lands in their logs.

    Putting a session token there would hand out a live credential to reach
    that. This token carries only who started the flow, expires in minutes, and
    is refused anywhere a session is expected.
    """
    user_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    state = oauth.issue_state(user_id, workspace_id, "source-google-sheets")

    claims = oauth.decode_state(state)
    assert claims["sub"] == str(user_id)
    assert claims["ws"] == str(workspace_id)
    assert claims["connector"] == "source-google-sheets"
    assert claims["typ"] == oauth.STATE_TYPE
    # Short-lived, unlike a session.
    assert claims["exp"] - claims["iat"] <= oauth.STATE_TTL_SECONDS

    # And a session token must not be accepted as state, even though this
    # deployment signed it.
    from app.core.security import issue_session_token

    assert oauth.decode_state(issue_session_token(user_id, workspace_id, 0)) == {}


def test_a_tampered_or_expired_state_is_refused() -> None:
    user_id, workspace_id = uuid.uuid4(), uuid.uuid4()

    assert oauth.decode_state("not-a-token") == {}
    assert oauth.decode_state("") == {}

    # Signed by somebody else.
    forged = jwt.encode({"typ": oauth.STATE_TYPE, "sub": str(user_id),
                         "ws": str(workspace_id), "connector": "source-google-sheets",
                         "exp": int(time.time()) + 600},
                        "a-different-secret", algorithm=settings.jwt_algorithm)
    assert oauth.decode_state(forged) == {}

    expired = jwt.encode({"typ": oauth.STATE_TYPE, "sub": str(user_id),
                          "ws": str(workspace_id), "connector": "source-google-sheets",
                          "iat": int(time.time()) - 7200,
                          "exp": int(time.time()) - 3600},
                         settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert oauth.decode_state(expired) == {}


def test_two_flows_started_together_get_different_state() -> None:
    user_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    first = oauth.issue_state(user_id, workspace_id, "source-google-sheets")
    second = oauth.issue_state(user_id, workspace_id, "source-google-sheets")
    assert first != second


# ── the exchanged credentials ────────────────────────────────────────────────

def test_an_access_token_without_a_refresh_token_is_refused(monkeypatch) -> None:
    """It would work for an hour, then fail as a scheduled job overnight.

    Refusing at consent time, while somebody is looking at the screen, is the
    only moment this is cheap to fix.
    """
    from app.core.errors import ValidationError

    _configure(monkeypatch)

    class _Response:
        def read(self):
            return json.dumps({"access_token": "short-lived", "expires_in": 3600}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(oauth.urllib.request, "urlopen",
                        lambda *a, **kw: _Response())
    with pytest.raises(ValidationError):
        oauth.exchange_code(oauth.PROVIDERS["google"], "the-code")


def test_the_credentials_match_the_connector_branch_and_are_all_secret(
        monkeypatch) -> None:
    """The shape has to be what the spec declares, and secret all the way down.

    `credentials.refresh_token` is two levels deep. Under the old one-level
    splitter it would have been written to plain configuration -- which is the
    same defect as the BigQuery HMAC key, reached from a new direction.
    """
    from app.services.catalog import merge_configuration, split_configuration

    _configure(monkeypatch)
    built = oauth.connector_credentials(
        oauth.PROVIDERS["google"], {"refresh_token": "SENTINEL-REFRESH-TOKEN"})

    assert set(built) == {"credentials"}
    assert built["credentials"]["auth_type"] == "Client"
    assert built["credentials"]["client_id"] == "test-client-id"
    assert built["credentials"]["refresh_token"] == "SENTINEL-REFRESH-TOKEN"

    registry = json.loads(
        (ROOT / "backend" / "app" / "resources" / "connector_registry.json")
        .read_text(encoding="utf-8"))
    spec = next(c for c in registry["connectors"]
                if c["connector_key"] == "source-google-sheets")["spec_schema"]

    config, secrets = split_configuration(spec, built)
    assert "SENTINEL-REFRESH-TOKEN" not in json.dumps(config)
    assert "test-client-secret" not in json.dumps(config)
    assert "SENTINEL-REFRESH-TOKEN" in json.dumps(secrets)
    assert merge_configuration(config, secrets) == built


def test_a_failed_exchange_never_echoes_the_response(monkeypatch, capsys) -> None:
    """This path is reached from a browser redirect, and codes are credentials."""
    from app.core.errors import ValidationError

    _configure(monkeypatch)

    def explode(*a, **kw):
        raise RuntimeError("400: {'error':'invalid_grant','code':'SECRET-CODE'}")

    monkeypatch.setattr(oauth.urllib.request, "urlopen", explode)
    with pytest.raises(ValidationError) as caught:
        oauth.exchange_code(oauth.PROVIDERS["google"], "SECRET-CODE")

    printed = capsys.readouterr()
    assert "SECRET-CODE" not in str(caught.value)
    assert "SECRET-CODE" not in printed.out + printed.err
