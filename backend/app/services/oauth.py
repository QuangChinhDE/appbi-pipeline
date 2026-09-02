"""Authorisation-code OAuth for connectors that support it.

Why this exists alongside service accounts
------------------------------------------

A service account is the right credential for a warehouse: it belongs to the
organisation, it survives people leaving, and nobody has to click anything.
That is why BigQuery is service-account only here -- and why Airbyte's BigQuery
connectors offer no OAuth branch at all.

It is the wrong credential for somebody's own spreadsheet. A service account
can only read a sheet that has been explicitly shared with an address like
`appbi-685@base-testlab-01.iam.gserviceaccount.com`, which means every user has
to be told to do that, and every failure looks like a broken connector rather
than a missing share. OAuth lets a person grant access to their own files by
signing in as themselves.

So both are offered, per connector, according to what the connector's own spec
declares -- not according to a list maintained here.

Where the refresh token lives
-----------------------------

Never in the browser. The callback lands on the API, exchanges the code
server-side, and stores the resulting credential in the same envelope-encrypted
secret store every other credential uses. The browser is handed a **grant id**:
an opaque, single-use, workspace-scoped, short-lived handle. When the wizard
saves, it sends that handle and the server resolves it.

The alternative -- returning the refresh token to the page so the form can post
it back -- puts a long-lived credential into browser memory, the URL bar, and
any error reporter the page happens to load. A refresh token is not a session;
it does not expire when the tab closes.
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import ValidationError
from app.core.logging import log_event
from app.core.secrets import secret_store
from app.models.oauth import OAuthGrant

logger = logging.getLogger(__name__)

#: How long an unconsumed grant stays usable. Long enough to finish a wizard,
#: short enough that an abandoned one is not a standing credential.
GRANT_TTL_MINUTES = 30


@dataclass(frozen=True)
class Provider:
    """One identity provider, and what a connector needs back from it."""

    key: str
    label: str
    authorize_url: str
    token_url: str
    #: Scopes per connector. Asking for every scope the provider offers is how
    #: a consent screen becomes something users decline, so each connector asks
    #: only for what it reads.
    scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Extra authorize-URL parameters. Google will not issue a refresh token
    #: without `access_type=offline`, and will not re-issue one on a second
    #: consent without `prompt=consent` -- so a user who has authorised before
    #: gets an access token, no refresh token, and a connector that works until
    #: the hour is out.
    authorize_params: dict[str, str] = field(default_factory=dict)
    #: Fields the connector's OAuth branch expects, beyond the token itself.
    extra_config: tuple[str, ...] = ()


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        key="google",
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes={
            # Read-only: this product copies data out, it never writes to the
            # user's spreadsheet, and a consent screen that asks for write
            # access to all your files is one people are right to refuse.
            # Transform runs dbt against BigQuery as the person who consented.
            # `bigquery` covers reading and writing datasets; the read-only
            # cloud-platform scope is what lets the project list be shown, and
            # asking for the writable one would be asking for far more than a
            # warehouse connection needs.
            "destination-bigquery": (
                "https://www.googleapis.com/auth/bigquery",
                "https://www.googleapis.com/auth/cloud-platform.read-only",
            ),
            "source-google-sheets": (
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ),
        },
        authorize_params={"access_type": "offline", "prompt": "consent",
                          "include_granted_scopes": "true"},
    ),
    "microsoft": Provider(
        key="microsoft",
        label="Microsoft",
        # `common` lets both work and personal accounts authorise. A deployment
        # locked to one tenant overrides this with its tenant id.
        authorize_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        scopes={
            "source-microsoft-onedrive": (
                "offline_access", "Files.Read.All", "User.Read",
            ),
            "source-microsoft-sharepoint": (
                "offline_access", "Sites.Read.All", "Files.Read.All",
            ),
        },
        extra_config=("tenant_id",),
    ),
}


def provider_for(connector_key: str) -> Provider | None:
    """Which provider, if any, this connector authorises against."""
    for provider in PROVIDERS.values():
        if connector_key in provider.scopes:
            return provider
    return None


def credentials_of(provider: Provider) -> tuple[str, str, str]:
    """The deployment's registered OAuth application for this provider.

    Configured per deployment, not per user: the client id and secret identify
    *this installation of AppBI* to Google or Microsoft, and the consent screen
    shows its name. There is no sensible default, so an unconfigured provider
    is simply not offered rather than half-offered.
    """
    if provider.key == "google":
        return (settings.google_oauth_client_id,
                settings.google_oauth_client_secret,
                settings.oauth_redirect_uri)
    return (settings.microsoft_oauth_client_id,
            settings.microsoft_oauth_client_secret,
            settings.oauth_redirect_uri)


def configured(provider: Provider) -> bool:
    client_id, client_secret, redirect = credentials_of(provider)
    return bool(client_id and client_secret and redirect)


def _tenant(provider: Provider) -> str:
    if provider.key != "microsoft":
        return ""
    return settings.microsoft_oauth_tenant_id or "common"


def authorize_url(provider: Provider, connector_key: str, state: str) -> str:
    client_id, _, redirect = credentials_of(provider)
    scopes = provider.scopes.get(connector_key, ())
    query = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        **provider.authorize_params,
    }
    base = provider.authorize_url.format(tenant=_tenant(provider))
    return f"{base}?{urllib.parse.urlencode(query)}"


def exchange_code(provider: Provider, code: str) -> dict[str, Any]:
    """Trade the one-time code for tokens. Server side, always."""
    client_id, client_secret, redirect = credentials_of(provider)
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(
        provider.token_url.format(tenant=_tenant(provider)), data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:                               # noqa: BLE001
        # Deliberately not echoing the response: a token endpoint's error body
        # can carry the code, and this path is reached from a browser redirect.
        log_event(logger, logging.WARNING, "oauth.exchange_failed",
                  provider=provider.key, error=type(exc).__name__)
        raise ValidationError(
            "Không đổi được mã uỷ quyền thành token. Hãy thử kết nối lại.")

    if not payload.get("refresh_token"):
        # An access token alone expires within the hour, and a sync scheduled
        # for tonight would fail with something that reads like a permission
        # problem. Better to refuse now and say why.
        raise ValidationError(
            "Nhà cung cấp không trả refresh token. Hãy gỡ quyền truy cập đã "
            "cấp trước đó cho ứng dụng này rồi kết nối lại.")
    return payload


def connector_credentials(provider: Provider, tokens: dict[str, Any],
                          extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shape the tokens the way the connector's OAuth branch expects.

    The branch is `credentials` with an `auth_type` discriminator, so what goes
    into the connector config is a nested object -- which is precisely the
    shape the recursive secret splitter now handles at any depth.
    """
    client_id, client_secret, _ = credentials_of(provider)
    credentials: dict[str, Any] = {
        "auth_type": "Client" if provider.key == "google" else "Client",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
    }
    for name in provider.extra_config:
        value = (extra or {}).get(name) or (
            _tenant(provider) if name == "tenant_id" else "")
        if value:
            credentials[name] = value
    return {"credentials": credentials}


def dbt_credentials(provider: Provider, tokens: dict[str, Any]) -> dict[str, Any]:
    """Shape the tokens the way a dbt profile expects, not the way Airbyte does.

    Airbyte takes a nested `credentials` object with an `auth_type`; dbt takes
    a flat refresh token beside the application's own client id and secret.
    Same grant, two consumers, so the shaping happens per consumer rather than
    being bent to serve both badly.
    """
    client_id, client_secret, _ = credentials_of(provider)
    return {
        "auth_method": "oauth",
        "refresh_token": tokens["refresh_token"],
        "oauth_client_id": client_id,
        "oauth_client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "oauth_account": tokens.get("account") or "",
    }


async def store_grant(session: AsyncSession, *, workspace_id: uuid.UUID,
                      user_id: uuid.UUID | None, connector_key: str,
                      provider: Provider, credentials: dict[str, Any],
                      account_label: str = "") -> OAuthGrant:
    ref = await secret_store.write(session, workspace_id, credentials)
    grant = OAuthGrant(
        workspace_id=workspace_id, connector_key=connector_key,
        provider=provider.key, secret_ref=ref, account_label=account_label[:255],
        created_by=user_id, created_at=utcnow(),
        expires_at=utcnow() + timedelta(minutes=GRANT_TTL_MINUTES))
    session.add(grant)
    await session.flush()
    return grant


async def consume_grant(session: AsyncSession, grant_id: uuid.UUID, *,
                        workspace_id: uuid.UUID, connector_key: str
                        ) -> dict[str, Any]:
    """Resolve a grant into connector credentials, exactly once.

    Every condition below is a way somebody else's credential could otherwise
    end up attached to a resource: another workspace's grant, a grant issued
    for a different connector, a replayed handle, an abandoned one that has sat
    around for a week.
    """
    grant = await session.get(OAuthGrant, grant_id)
    if grant is None or grant.workspace_id != workspace_id:
        raise ValidationError("Phiên uỷ quyền không tồn tại hoặc đã hết hạn.")
    if grant.connector_key != connector_key:
        raise ValidationError("Phiên uỷ quyền thuộc về một connector khác.")
    if grant.consumed_at is not None:
        raise ValidationError("Phiên uỷ quyền đã được dùng. Hãy kết nối lại.")
    if grant.expires_at < utcnow():
        raise ValidationError("Phiên uỷ quyền đã hết hạn. Hãy kết nối lại.")

    credentials = await secret_store.read(session, grant.secret_ref)
    grant.consumed_at = utcnow()
    await session.flush()
    return credentials


async def purge_expired(session: AsyncSession) -> int:
    """Drop grants nobody finished with. Each one holds a live refresh token."""
    stale = list((await session.scalars(
        select(OAuthGrant).where(OAuthGrant.expires_at < utcnow())
    )).all())
    for grant in stale:
        try:
            await secret_store.delete(session, grant.secret_ref)
        except Exception:                                  # noqa: BLE001
            pass
        await session.delete(grant)
    return len(stale)


#: The `state` parameter is a signed token of its own, not a session token.
#:
#: Reusing a session token here would put a live credential into a URL that
#: travels to Google, sits in browser history and lands in the provider's
#: logs. This one carries only who started the flow and for which connector,
#: expires in minutes, and is rejected everywhere a session is expected
#: because its `typ` does not match.
STATE_TYPE = "oauth-state"
STATE_TTL_SECONDS = 15 * 60


def issue_state(user_id: uuid.UUID, workspace_id: uuid.UUID,
                connector_key: str) -> str:
    import time

    import jwt

    now = int(time.time())
    return jwt.encode({
        "typ": STATE_TYPE,
        "sub": str(user_id),
        "ws": str(workspace_id),
        "connector": connector_key,
        # Random even though the rest is deterministic, so two flows started in
        # the same second are still distinct values.
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_state(token: str) -> dict[str, Any]:
    """Returns the claims, or an empty dict. Never raises at a redirect."""
    import jwt
    from jwt import PyJWTError

    try:
        claims = jwt.decode(token, settings.jwt_secret,
                            algorithms=[settings.jwt_algorithm])
    except PyJWTError:
        return {}
    if claims.get("typ") != STATE_TYPE:
        # A session token presented as state, or anything else signed by this
        # deployment. Signature alone is not authorisation for this flow.
        return {}
    return claims
