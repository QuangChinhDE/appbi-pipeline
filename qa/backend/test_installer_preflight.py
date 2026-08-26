"""The installer's preflight, driven rather than grepped.

PM v15 asked for behavioural coverage here specifically, and the reason is in
the defect it is covering: `verify_engine()` looked correct in source and was
guaranteed to fail. Production credentials are `secret://` references, which
`resolve_secret()` refuses to read on purpose, so the probe sent nothing to an
auth-enabled Airbyte, got 401, and made that fatal — before the correct
post-deploy check in the Pod could run. A test asserting "verify_engine exists
and mentions auth" would have passed throughout.

These stub only the HTTP boundary and let the real decision logic run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

repo_only = pytest.mark.skipif(
    not (ROOT / "scripts" / "production.py").exists(),
    reason="needs the repository layout")

pytestmark = [repo_only]


def _module():
    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(**auth) -> dict:
    """A production engine block, with whatever auth the case needs."""
    return {
        "profile": "external-airbyte-k8s",
        "product": {"namespace": "appbi"},
        "engine": {
            "url": "https://airbyte.internal.example",
            "platform_version": "1.8.5",
            "auth": auth or {
                "mode": "client_credentials",
                # The shape production actually uses, and the one that made the
                # old preflight impossible to pass.
                "client_id_ref": "secret://appbi-secrets/AIRBYTE_CLIENT_ID",
                "client_secret_ref": "secret://appbi-secrets/AIRBYTE_CLIENT_SECRET",
            },
        },
    }


def _with_response(module, status: int, body: str = "{}"):
    """Replace only the HTTP call, so the decision logic is the thing tested."""
    seen: dict = {}

    def fake_http(url, *, timeout=10, auth=None, bearer=""):
        seen["url"] = url
        seen["auth"] = auth
        seen["bearer"] = bearer
        return status, body

    module.http = fake_http
    return seen


def test_a_401_from_an_auth_enabled_engine_is_not_fatal_in_preflight() -> None:
    """The exact case that blocked every production install.

    Credentials are `secret://`, so the installer has nothing to send. A 401
    proves DNS, the port and TLS all work — which is everything preflight can
    honestly decide. Identity is checked from the Pod afterwards.
    """
    module = _module()
    seen = _with_response(module, 401, '{"message":"Unauthorized"}')

    module.verify_engine(_config())      # must not raise

    assert seen["url"].endswith("/api/v1/instance_configuration")
    # And it genuinely had no credential to send -- this is not a case of the
    # test accidentally supplying one.
    assert not seen["bearer"] and not seen["auth"]


def test_a_403_is_treated_the_same_way() -> None:
    module = _module()
    _with_response(module, 403, '{"message":"Forbidden"}')
    module.verify_engine(_config())


def test_credentials_that_are_readable_and_rejected_are_fatal() -> None:
    """If the installer *can* send a credential and it is refused, that is real.

    `env://` is readable here, unlike `secret://`. A 401 then means the
    configured credential is wrong, which is worth stopping for.
    """
    import os

    module = _module()
    os.environ["TEST_ENGINE_USER"] = "ops"
    os.environ["TEST_ENGINE_PASS"] = "wrong"
    try:
        _with_response(module, 401, '{"message":"Unauthorized"}')
        config = _config(mode="basic",
                         username_ref="env://TEST_ENGINE_USER",
                         password_ref="env://TEST_ENGINE_PASS")
        with pytest.raises(SystemExit):
            module.verify_engine(config)
    finally:
        os.environ.pop("TEST_ENGINE_USER", None)
        os.environ.pop("TEST_ENGINE_PASS", None)


def test_an_unreachable_engine_is_fatal() -> None:
    """Status 0 is the transport failing: DNS, refused, timed out."""
    module = _module()
    _with_response(module, 0, "connection refused")
    with pytest.raises(SystemExit):
        module.verify_engine(_config())


def test_a_server_error_is_fatal() -> None:
    module = _module()
    _with_response(module, 503, "upstream unavailable")
    with pytest.raises(SystemExit):
        module.verify_engine(_config())


def test_a_reachable_engine_on_the_wrong_version_is_fatal() -> None:
    """When the version *is* legible, a mismatch still stops the deploy.

    An engine that upgraded itself since certification runs connector versions
    nobody tested.
    """
    module = _module()
    _with_response(module, 200, '{"version":"2.0.1"}')
    with pytest.raises(SystemExit):
        module.verify_engine(_config())


def test_a_reachable_engine_on_the_pinned_version_passes() -> None:
    module = _module()
    _with_response(module, 200, '{"version":"1.8.5"}')
    module.verify_engine(_config())


def test_secret_references_are_never_read_by_the_installer() -> None:
    """The property the whole design rests on.

    If this ever starts returning a value, the installer has become something
    that can read every production secret.
    """
    module = _module()
    assert module.resolve_secret("secret://appbi-secrets/JWT_SECRET") == ""
