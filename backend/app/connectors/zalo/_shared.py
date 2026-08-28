"""Compile a Zalo connector from a Python definition.

Small on purpose. Zalo Ads exposes one collection today, so this is the least
machinery that still produces a manifest the runner accepts and that the rest
of the product can seed, permission and sync like any other connector.

The dialect, measured where it could be:

* **OAuth2 client credentials**, `ads.zalo.me/open-api/oauth/token`. Probed with
  a deliberately wrong client id and it answers `No application registered for
  client_id`, so the endpoint is real and speaks the flow the contract claims.
* **GET with query parameters**, one host, no per-account subdomain.
* **The account is a phone number**, passed as a query parameter rather than
  derived from the token. That is unusual and is why `phone` is a required
  input rather than something the connector discovers.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = "6.0.0"

API_BASE = "https://ads.zalo.me/open-api/"
TOKEN_ENDPOINT = "https://ads.zalo.me/open-api/oauth/token"

#: Zalo's ad console is a browser product and the open API sits behind the same
#: edge. The reviewed contract sends a browser user agent; kept because dropping
#: it is a change nobody here can test, and the Base connectors carry the same
#: header for the same reason.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


@dataclass(frozen=True)
class Stream:
    """One collection."""

    name: str
    path: str
    primary_key: tuple[str, ...] = ("id",)
    #: Where the records live. Empty means the response is the array itself.
    collection: tuple[str, ...] = ()
    #: Query parameters, as Jinja against `config`.
    params: dict[str, str] = field(default_factory=dict)
    #: Constant fields added to every record, as Jinja against `config`.
    stamp: dict[str, str] = field(default_factory=dict)
    #: Field types worth pinning; everything else stays open.
    fields: dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class ZaloConnector:
    app: str
    title: str
    streams: tuple[Stream, ...]
    summary: str = ""
    docs_url: str = ""
    #: Extra properties on the connection spec, beyond the OAuth pair.
    config: tuple[dict[str, Any], ...] = ()

    @property
    def connector_key(self) -> str:
        return f"source-{self.app}"

    def stream(self, name: str) -> Stream:
        for candidate in self.streams:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.app}: no stream {name!r}")

    def validate(self) -> None:
        seen: set[str] = set()
        for stream in self.streams:
            if stream.name in seen:
                raise ValueError(f"{self.app}: stream {stream.name!r} declared twice")
            seen.add(stream.name)
            if not stream.primary_key:
                raise ValueError(
                    f"{self.app}.{stream.name}: no primary key, so a re-sync "
                    "cannot deduplicate")


def _authenticator() -> dict[str, Any]:
    return {
        "type": "OAuthAuthenticator",
        "token_refresh_endpoint": TOKEN_ENDPOINT,
        "grant_type": "client_credentials",
        "client_id": "{{ config['client_id'] }}",
        "client_secret": "{{ config['client_secret'] }}",
        "scopes": [],
        "refresh_request_body": {},
    }


def _schema(stream: Stream) -> dict[str, Any]:
    properties: dict[str, Any] = {
        key: {"type": ["null", "string", "integer"]} for key in stream.primary_key
    }
    for name, kind in stream.fields.items():
        properties.setdefault(name, {"type": ["null", kind]})
    for name in stream.stamp:
        properties.setdefault(name, {"type": ["null", "string"]})
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
    }


def _stream_manifest(stream: Stream) -> dict[str, Any]:
    compiled: dict[str, Any] = {
        "type": "DeclarativeStream",
        "name": stream.name,
        "primary_key": list(stream.primary_key),
        "retriever": {
            "type": "SimpleRetriever",
            "requester": {
                "type": "HttpRequester",
                "url_base": API_BASE,
                "path": stream.path,
                "http_method": "GET",
                "request_headers": {"User-Agent": USER_AGENT},
                "request_parameters": dict(stream.params),
                "authenticator": _authenticator(),
            },
            "record_selector": {
                "type": "RecordSelector",
                "extractor": {"type": "DpathExtractor",
                              "field_path": list(stream.collection)},
            },
            # No paginator. The reviewed contract declares none and the API was
            # not reachable to find one, so claiming an offset or a page number
            # would be an invention that silently truncates at one page.
            "paginator": {"type": "NoPagination"},
        },
        "schema_loader": {"type": "InlineSchemaLoader", "schema": _schema(stream)},
    }
    if stream.stamp:
        compiled["transformations"] = [{
            "type": "AddFields",
            "fields": [{"path": [name], "value": value}
                       for name, value in stream.stamp.items()],
        }]
    return compiled


def connection_specification(connector: ZaloConnector) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "client_id": {
            "type": "string",
            "title": "Client ID",
            "description": (
                "Lấy trong Zalo Ads ở phần ứng dụng đã đăng ký. Connector tự "
                "đổi Client ID và Client Secret lấy access token ở mỗi lần "
                "chạy, nên không cần dán token vào đây."
            ),
            "airbyte_secret": True,
            "order": 0,
        },
        "client_secret": {
            "type": "string",
            "title": "Client Secret",
            "description": "Mã bí mật đi cùng Client ID của ứng dụng Zalo Ads.",
            "airbyte_secret": True,
            "order": 1,
        },
    }
    required = ["client_id", "client_secret"]
    for index, extra in enumerate(connector.config, start=2):
        name = extra["name"]
        properties[name] = {
            "type": extra.get("type", "string"),
            "title": extra["title"],
            "description": extra["description"],
            "order": index,
        }
        if extra.get("secret"):
            properties[name]["airbyte_secret"] = True
        if extra.get("required", True):
            required.append(name)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{connector.title} Spec",
        "required": required,
        "additionalProperties": True,
        "properties": properties,
    }


def compile_manifest(connector: ZaloConnector) -> dict[str, Any]:
    connector.validate()
    streams = {s.name: _stream_manifest(s) for s in connector.streams}
    return {
        "version": MANIFEST_VERSION,
        "type": "DeclarativeSource",
        "check": {"type": "CheckStream", "stream_names": [next(iter(streams))]},
        "definitions": {"streams": copy.deepcopy(streams)},
        "streams": [{"$ref": f"#/definitions/streams/{name}"} for name in streams],
        "spec": {
            "type": "Spec",
            "connection_specification": connection_specification(connector),
        },
    }
