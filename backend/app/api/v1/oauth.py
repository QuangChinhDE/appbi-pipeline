"""Connecting a connector by signing in, rather than by pasting a key.

Three endpoints and a redirect:

    GET  /oauth/providers            what this deployment can offer
    POST /oauth/{connector_key}/start   -> consent URL + state
    GET  /oauth/callback             <- the provider redirects here
    GET  /oauth/grant/{grant_id}     did it work, and whose account was it

The refresh token never reaches the browser. The callback exchanges the code on
the server, writes the credential to the encrypted store, and redirects the
user back to the wizard with nothing but an opaque grant id.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.deps import CtxDep, SessionDep
from app.core.config import settings
from app.core.errors import ValidationError
from app.core.permissions import Action, Module
from app.services import oauth as oauth_service

router = APIRouter(tags=["oauth"])


class ProviderView(BaseModel):
    connector_key: str
    provider: str
    label: str
    scopes: list[str]


class StartResponse(BaseModel):
    authorize_url: str
    state: str


class GrantView(BaseModel):
    id: uuid.UUID
    connector_key: str
    provider: str
    account_label: str
    consumed: bool


@router.get("/oauth/providers", response_model=list[ProviderView])
async def providers(ctx: CtxDep) -> list[ProviderView]:
    """Which connectors this deployment can authorise, and for what.

    A provider with no client id configured is omitted rather than listed as
    unavailable: the wizard should offer the service-account path cleanly
    instead of showing a button that cannot work.
    """
    ctx.require(Module.CONNECTORS, Action.VIEW)
    out: list[ProviderView] = []
    for provider in oauth_service.PROVIDERS.values():
        if not oauth_service.configured(provider):
            continue
        for connector_key, scopes in provider.scopes.items():
            out.append(ProviderView(
                connector_key=connector_key, provider=provider.key,
                label=provider.label, scopes=list(scopes)))
    return out


@router.post("/oauth/{connector_key}/start", response_model=StartResponse)
async def start(connector_key: str, ctx: CtxDep) -> StartResponse:
    # A destination connector is authorised to build a Transform connection, a
    # source connector to build a Source. Same flow, different permission --
    # requiring the Source one for both would let a Transform author be refused
    # for a module they are not using.
    ctx.require(
        Module.TRANSFORMS if connector_key.startswith("destination-") else Module.SOURCES,
        Action.CREATE,
    )
    provider = oauth_service.provider_for(connector_key)
    if provider is None:
        raise ValidationError(
            f"Connector '{connector_key}' không hỗ trợ đăng nhập uỷ quyền. "
            "Hãy dùng service account.")
    if not oauth_service.configured(provider):
        raise ValidationError(
            f"Deployment này chưa cấu hình ứng dụng OAuth cho {provider.label}.")

    # The state carries who is asking, in a token signed for this purpose
    # only. The provider's redirect arrives as a fresh top-level navigation
    # from an external origin, so a cookie cannot be relied on -- but a session
    # token would then be travelling in a URL to Google, into browser history
    # and into their logs, which is worse than the problem it solves.
    state = oauth_service.issue_state(ctx.user_id, ctx.workspace_id, connector_key)
    return StartResponse(
        authorize_url=oauth_service.authorize_url(provider, connector_key, state),
        state=state)


@router.get("/oauth/callback", include_in_schema=False)
async def callback(
    request: Request,
    session: SessionDep,
    state: Annotated[str, Query()] = "",
    code: Annotated[str, Query()] = "",
    error: Annotated[str, Query()] = "",
) -> RedirectResponse:
    """Where the provider sends the browser back.

    Deliberately not `CtxDep`: this is a top-level navigation from an external
    origin, so the caller is identified by the signed `state` rather than by a
    cookie that may not be attached.
    """
    base = settings.frontend_base_url.rstrip("/")

    if error:
        # The user declined, or the provider refused. Not an exception -- a
        # cancelled consent is an ordinary outcome.
        return RedirectResponse(f"{base}/sources/new?oauth=denied", status_code=303)

    claims = oauth_service.decode_state(state) if state else {}
    connector_key = str(claims.get("connector") or "")
    if not claims or not connector_key or not code:
        return RedirectResponse(f"{base}/sources/new?oauth=invalid", status_code=303)

    provider = oauth_service.provider_for(connector_key)
    if provider is None or not oauth_service.configured(provider):
        return RedirectResponse(f"{base}/sources/new?oauth=invalid", status_code=303)

    try:
        tokens = oauth_service.exchange_code(provider, code)
    except ValidationError:
        return RedirectResponse(f"{base}/sources/new?oauth=failed", status_code=303)

    # A warehouse grant is consumed by a dbt profile, a source grant by the
    # connector engine, and the two want different shapes of the same tokens.
    for_transform = connector_key.startswith("destination-")
    credentials = (
        oauth_service.dbt_credentials(provider, tokens) if for_transform
        else oauth_service.connector_credentials(provider, tokens)
    )
    grant = await oauth_service.store_grant(
        session,
        workspace_id=uuid.UUID(str(claims["ws"])),
        user_id=uuid.UUID(str(claims["sub"])),
        connector_key=connector_key,
        provider=provider,
        credentials=credentials,
        account_label=str(tokens.get("account") or ""),
    )
    await session.commit()

    # Only the handle travels back through the browser.
    landing = "/transforms/new" if for_transform else "/sources/new"
    return RedirectResponse(
        f"{base}{landing}?connector={connector_key}&oauth_grant={grant.id}",
        status_code=303)


@router.get("/oauth/grant/{grant_id}", response_model=GrantView)
async def grant(grant_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> GrantView:
    """What the wizard shows after the redirect: connected, and as whom."""
    from app.models.oauth import OAuthGrant

    row = await session.get(OAuthGrant, grant_id)
    if row is None or row.workspace_id != ctx.workspace_id:
        raise ValidationError("Phiên uỷ quyền không tồn tại hoặc đã hết hạn.")
    ctx.require(
        Module.TRANSFORMS if row.connector_key.startswith("destination-")
        else Module.SOURCES,
        Action.CREATE,
    )
    return GrantView(
        id=row.id, connector_key=row.connector_key, provider=row.provider,
        account_label=row.account_label, consumed=row.consumed_at is not None)
