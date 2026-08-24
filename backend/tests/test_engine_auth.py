"""The client-credentials flow, executed rather than inspected.

PM v12 P1-AUTH-001: the existing auth tests checked classes, tuples and source
text. None of them sent a request. So they would have passed against an
implementation that built the wrong body, put the token in the wrong header, or
retried a bad credential forever.

These drive `httpx.MockTransport`, so the whole path runs: the token POST, the
bearer header on the real call, one refresh on 401, and the retry cap. No
network and no Airbyte required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.airbyte_api.adapter import _ClientCredentialsAuth
from app.core.errors import EngineOperationError

TOKEN_PATH = "/api/v1/applications/token"

# Captured before any patching: the replacement constructor must build a real
# client, and referring to `httpx.Client` inside it would call the replacement.
_REAL_CLIENT = httpx.Client


def _mock_client(handler):
    """A stand-in for httpx.Client that routes everything through `handler`."""
    def build(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    return build


def _auth() -> _ClientCredentialsAuth:
    return _ClientCredentialsAuth("http://engine.test", "the-id", "the-secret")


def test_the_token_request_is_what_airbyte_expects(monkeypatch) -> None:
    """A POST of client_id/client_secret as JSON, to the token route.

    Asserted on the wire because the shape is Airbyte's, not ours: form-encoding
    it, or sending it to the public API path, fails at runtime with a 400 that
    reads like bad credentials.
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content or b"{}")
        seen["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(200, json={"access_token": "tok-1"})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    assert _auth()._fetch() == "tok-1"
    assert seen["method"] == "POST"
    assert seen["url"] == f"http://engine.test{TOKEN_PATH}"
    assert seen["body"] == {"client_id": "the-id", "client_secret": "the-secret"}
    assert seen["content_type"] == "application/json"


def test_the_token_is_sent_as_a_bearer_header_and_reused() -> None:
    """One token fetch, then that token on every call.

    Fetching per request triples the traffic against the engine's auth
    endpoint, which is the component least able to absorb it.
    """
    calls = {"token": 0, "api": 0}
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": f"tok-{calls['token']}"})
        calls["api"] += 1
        headers.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, auth=_patched_auth(handler)) as client:
        client.get("http://engine.test/api/v1/health")
        client.get("http://engine.test/api/v1/health")

    assert headers == ["Bearer tok-1", "Bearer tok-1"], headers
    assert calls["token"] == 1, "the token must be reused, not re-fetched per call"
    assert calls["api"] == 2


def _patched_auth(handler):
    """An auth object whose token fetch goes through the same mock transport."""
    auth = _auth()
    auth._fetch = lambda: _fetch_via(handler, auth)  # type: ignore[method-assign]
    return auth


def _fetch_via(handler, auth) -> str:
    with _REAL_CLIENT(transport=httpx.MockTransport(handler)) as client:
        response = client.post(
            f"{auth._base_url}{TOKEN_PATH}",
            json={"client_id": auth._client_id, "client_secret": auth._client_secret})
    return response.json()["access_token"]


def test_a_401_refreshes_the_token_exactly_once() -> None:
    """An expired token must not end a long sync.

    The reconciler polls for the life of a run; a token that expires mid-run
    would otherwise blind it. One refresh, and only one -- retrying a genuinely
    wrong credential in a loop melts the engine's auth endpoint, which is a
    worse outage than the 401 that started it.
    """
    calls = {"token": 0, "api": 0}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": f"tok-{calls['token']}"})
        calls["api"] += 1
        seen.append(request.headers.get("Authorization", ""))
        # Always 401: the point is that the client gives up, not that it wins.
        return httpx.Response(401, json={"message": "Unauthorized"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, auth=_patched_auth(handler)) as client:
        response = client.get("http://engine.test/api/v1/health")

    assert response.status_code == 401
    assert seen == ["Bearer tok-1", "Bearer tok-2"], seen
    assert calls["api"] == 2, "exactly one retry, then the 401 stands"
    assert calls["token"] == 2


def test_a_recovered_token_makes_the_retry_succeed() -> None:
    """The case the refresh exists for: expiry, then a working token."""
    state = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            state["token"] += 1
            return httpx.Response(200, json={"access_token": f"tok-{state['token']}"})
        if request.headers.get("Authorization") == "Bearer tok-1":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, auth=_patched_auth(handler)) as client:
        response = client.get("http://engine.test/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_a_rejected_credential_says_what_to_fix(monkeypatch) -> None:
    """The 400 Airbyte returns for an unregistered client is not obvious.

    "Invalid client id or token" reads like a typo, and the real cause is that
    the credentials belong to no Application. The message has to say so, or the
    operator changes the password and tries again.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Invalid client id or token"})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    with pytest.raises(EngineOperationError) as raised:
        _auth()._fetch()
    detail = str(raised.value.technical_message)
    assert "400" in detail
    assert "Application" in detail, (
        "the message must point at Application credentials, not at the password")
    assert raised.value.code == "ENGINE_AUTH_FAILED"


def test_a_token_response_without_a_token_is_an_error(monkeypatch) -> None:
    """A 200 with no `access_token` would otherwise send `Bearer None`."""
    monkeypatch.setattr(
        httpx, "Client",
        _mock_client(lambda request: httpx.Response(200, json={})))

    with pytest.raises(EngineOperationError):
        _auth()._fetch()
