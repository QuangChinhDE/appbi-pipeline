"""Compiling a builder project into a declarative connector.

Split from `builder.py` on purpose: everything here is a pure function of the
editor state, with no database and no engine. That means the validation which
enforces our egress policy, and the compiler that decides what the engine will
run, can both be tested from a bare checkout — a security rule you can only
exercise by first installing a Postgres driver is a rule that gets skipped.

`builder.py` owns the parts that need a session: projects, test runs, publishing.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from typing import Any

import yaml

from app.adapters.dto import ConnectorDescriptor
from app.core import egress
from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

# The generic runner that executes a declarative manifest. Pinned like every
# other connector image (section 60): a runner upgrade changes behaviour for
# every built connector at once, so it is a deliberate act, not a floating tag.
#
# This module is openly engine-coupled and says so. The Connector Builder
# compiles to the Airbyte low-code CDK manifest format, and there is no neutral
# equivalent to compile to — an engine that does not speak that format cannot
# run what this produces, and the honest answer is that the Builder is an
# Airbyte-CDK feature rather than a pretend-portable one.
#
# What is *not* acceptable is the rest of the product inheriting that coupling.
# An adapter declares its own runner via `declarative_runner()`; these values
# are the Airbyte answer and the fallback when an adapter offers none. Nothing
# outside this module and the adapters should name an image.
RUNNER_REPOSITORY = "airbyte/source-declarative-manifest"
RUNNER_VERSION = "7.28.2"

# The manifest schema version the compiler targets.
MANIFEST_VERSION = "6.0.0"

# A test read must stay a test: it is triggered from an editor, on a connector
# nobody has reviewed, against an API we know nothing about.
TEST_RECORD_LIMIT = 25
TEST_PAGE_LIMIT = 2

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_STREAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

AUTH_METHODS = {"none", "api_key", "bearer", "basic", "oauth2", "jwt", "session_token"}
PAGINATION_MODES = {"none", "page", "offset", "cursor", "link_header"}

_CONFIG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "connector"


def connector_key_for(name: str, project_id: uuid.UUID) -> str:
    """Stable, unique, and recognisable as user-built.

    The id suffix keeps two projects called "Orders" in different workspaces
    from colliding in a catalogue that is shared across the deployment.
    """
    return f"custom-{slugify(name)[:60]}-{str(project_id)[:8]}"


# ── validation ─────────────────────────────────────────────────────────────

def validate(definition: dict[str, Any]) -> dict[str, Any]:
    """Check the editor state well enough that a test read is worth attempting.

    Errors are raised one at a time against a named field so the UI can put the
    message next to the input that caused it, rather than showing a stack of
    everything that is wrong.
    """
    if not isinstance(definition, dict):
        raise ValidationError("Cấu hình connector không hợp lệ.", code="INVALID_DEFINITION")

    base_url = (definition.get("base_url") or "").strip()
    if not base_url:
        raise ValidationError(
            "Base URL là bắt buộc.", code="BUILDER_BASE_URL_REQUIRED",
            details={"field": "base_url"},
        )
    if not base_url.startswith(("http://", "https://")):
        raise ValidationError(
            "Base URL phải bắt đầu bằng http:// hoặc https://.",
            code="BUILDER_BASE_URL_INVALID", details={"field": "base_url"},
        )
    # Where a connector may point is a policy decision, not a free choice. Only
    # the syntactic half runs here: saving a draft must not require the host to
    # resolve. The authoritative check happens before traffic actually leaves.
    egress.check_url_syntax(base_url, field="base_url")

    auth = definition.get("auth") or {}
    method = (auth.get("method") or "none").lower()
    if method not in AUTH_METHODS:
        raise ValidationError(
            f"Phương thức xác thực '{method}' không được hỗ trợ.",
            code="BUILDER_AUTH_UNSUPPORTED",
            details={"field": "auth.method", "allowed": sorted(AUTH_METHODS)},
        )
    if method == "api_key" and not (auth.get("header") or "").strip():
        raise ValidationError(
            "Cần tên header cho API key.", code="BUILDER_AUTH_HEADER_REQUIRED",
            details={"field": "auth.header"},
        )
    if method == "oauth2":
        token_url = ((auth.get("oauth") or {}).get("token_url") or "").strip()
        if not token_url.startswith(("http://", "https://")):
            raise ValidationError(
                "OAuth cần token refresh endpoint là URL đầy đủ.",
                code="BUILDER_OAUTH_TOKEN_URL_REQUIRED",
                details={"field": "auth.oauth.token_url"},
            )
        # The token endpoint is an outbound request like any other, and it is
        # the one that carries the client secret. Checking only the scheme let
        # it point anywhere the base URL could not.
        egress.check_url_syntax(token_url, field="auth.oauth.token_url")

    # A declared input the connector cannot reference is dead weight, and a key
    # that collides with a built-in one silently shadows it.
    reserved = {"base_url", "api_key", "username", "password", "start_date",
                "client_id", "client_secret", "refresh_token", "jwt_secret"}
    seen_inputs: set[str] = set()
    for index, field in enumerate(definition.get("user_inputs") or []):
        key = (field.get("key") or "").strip()
        if not _CONFIG_KEY_RE.match(key):
            raise ValidationError(
                "Khóa tham số chỉ gồm chữ thường, số và dấu gạch dưới.",
                code="BUILDER_INPUT_KEY_INVALID",
                details={"field": f"user_inputs[{index}].key", "value": key},
            )
        if key in reserved:
            raise ValidationError(
                f"'{key}' là tham số hệ thống, hãy chọn tên khác.",
                code="BUILDER_INPUT_KEY_RESERVED",
                details={"field": f"user_inputs[{index}].key",
                         "reserved": sorted(reserved)},
            )
        if key in seen_inputs:
            raise ValidationError(
                f"Tham số '{key}' bị trùng.", code="BUILDER_INPUT_DUPLICATE",
                details={"field": f"user_inputs[{index}].key"},
            )
        seen_inputs.add(key)

    streams = definition.get("streams") or []
    if not streams:
        raise ValidationError(
            "Cần ít nhất một stream.", code="BUILDER_NO_STREAM",
            details={"field": "streams"},
        )

    seen: set[str] = set()
    for index, stream in enumerate(streams):
        name = (stream.get("name") or "").strip()
        if not _STREAM_NAME_RE.match(name):
            raise ValidationError(
                "Tên stream chỉ gồm chữ, số và dấu gạch dưới, bắt đầu bằng chữ.",
                code="BUILDER_STREAM_NAME_INVALID",
                details={"field": f"streams[{index}].name", "value": name},
            )
        if name in seen:
            raise ValidationError(
                f"Stream '{name}' bị trùng tên.", code="BUILDER_STREAM_DUPLICATE",
                details={"field": f"streams[{index}].name"},
            )
        seen.add(name)

        if not (stream.get("path") or "").strip():
            raise ValidationError(
                f"Stream '{name}' cần đường dẫn (path).",
                code="BUILDER_STREAM_PATH_REQUIRED",
                details={"field": f"streams[{index}].path"},
            )

        cursor = (stream.get("cursor_field") or "").strip()
        if stream.get("incremental") and not cursor:
            raise ValidationError(
                f"Stream '{name}' bật incremental thì phải chọn cursor field.",
                code="BUILDER_CURSOR_REQUIRED",
                details={"field": f"streams[{index}].cursor_field"},
            )

        pagination = stream.get("pagination") or {}
        mode = (pagination.get("mode") or "none").lower()
        if mode not in PAGINATION_MODES:
            raise ValidationError(
                f"Kiểu phân trang '{mode}' không được hỗ trợ.",
                code="BUILDER_PAGINATION_UNSUPPORTED",
                details={"field": f"streams[{index}].pagination.mode",
                         "allowed": sorted(PAGINATION_MODES)},
            )
        if mode == "cursor" and not (pagination.get("cursor_path") or "").strip():
            raise ValidationError(
                f"Stream '{name}' dùng cursor pagination thì phải chỉ ra vị trí "
                "cursor trong phản hồi.",
                code="BUILDER_CURSOR_PATH_REQUIRED",
                details={"field": f"streams[{index}].pagination.cursor_path"},
            )

        partition = stream.get("partition") or {}
        partition_mode = (partition.get("mode") or "none").lower()
        if partition_mode == "list" and not (partition.get("values") or "").strip():
            raise ValidationError(
                f"Stream '{name}' phân mảnh theo danh sách thì cần ít nhất một giá trị.",
                code="BUILDER_PARTITION_VALUES_REQUIRED",
                details={"field": f"streams[{index}].partition.values"},
            )
        if partition_mode == "parent":
            parent = partition.get("parent_stream")
            if not parent:
                raise ValidationError(
                    f"Stream '{name}' phân mảnh theo stream cha thì phải chọn stream cha.",
                    code="BUILDER_PARENT_STREAM_REQUIRED",
                    details={"field": f"streams[{index}].partition.parent_stream"},
                )
            if parent == name:
                raise ValidationError(
                    f"Stream '{name}' không thể là cha của chính nó.",
                    code="BUILDER_PARENT_STREAM_SELF",
                    details={"field": f"streams[{index}].partition.parent_stream"},
                )
            known = {(s.get("name") or "").strip() for s in streams}
            if parent not in known:
                raise ValidationError(
                    f"Không tìm thấy stream cha '{parent}'.",
                    code="BUILDER_PARENT_STREAM_UNKNOWN",
                    details={"field": f"streams[{index}].partition.parent_stream",
                             "allowed": sorted(known - {name})},
                )

    return definition


# ── compilation ────────────────────────────────────────────────────────────
#
# What follows mirrors the surface Airbyte's own Connector Builder exposes over
# the low-code CDK. Each helper turns one editor choice into one CDK component,
# and anything the editor does not offer is simply absent from the manifest
# rather than emitted empty — a manifest with hollow components is harder to
# read and can change behaviour.


def _authenticator(auth: dict[str, Any]) -> dict[str, Any] | None:
    """Translate the editor's auth choice into the manifest's authenticator.

    Every credential is referenced as `{{ config[...] }}` rather than inlined:
    the manifest is stored in the product database and returned to the browser,
    so a secret must never be part of it (§21).
    """
    method = (auth.get("method") or "none").lower()

    if method == "none":
        return None

    if method == "api_key":
        return {
            "type": "ApiKeyAuthenticator",
            "api_token": "{{ config['api_key'] }}",
            "inject_into": {
                "type": "RequestOption",
                "field_name": auth.get("header") or "X-API-Key",
                "inject_into": (auth.get("inject_into") or "header"),
            },
        }

    if method == "bearer":
        return {"type": "BearerAuthenticator", "api_token": "{{ config['api_key'] }}"}

    if method == "basic":
        return {
            "type": "BasicHttpAuthenticator",
            "username": "{{ config['username'] }}",
            "password": "{{ config['password'] }}",
        }

    if method == "oauth2":
        oauth = auth.get("oauth") or {}
        compiled: dict[str, Any] = {
            "type": "OAuthAuthenticator",
            "token_refresh_endpoint": oauth.get("token_url") or "",
            "client_id": "{{ config['client_id'] }}",
            "client_secret": "{{ config['client_secret'] }}",
            "refresh_token": "{{ config['refresh_token'] }}",
            "grant_type": oauth.get("grant_type") or "refresh_token",
        }
        scopes = [s.strip() for s in (oauth.get("scopes") or "").split(",") if s.strip()]
        if scopes:
            compiled["scopes"] = scopes
        if oauth.get("expires_in_name"):
            compiled["expires_in_name"] = oauth["expires_in_name"]
        if oauth.get("access_token_name"):
            compiled["access_token_name"] = oauth["access_token_name"]
        return compiled

    if method == "jwt":
        jwt = auth.get("jwt") or {}
        return {
            "type": "JwtAuthenticator",
            "secret_key": "{{ config['jwt_secret'] }}",
            "algorithm": jwt.get("algorithm") or "HS256",
            "token_duration": int(jwt.get("token_duration") or 1200),
            "jwt_headers": {"typ": "JWT"},
            "jwt_payload": {k: v for k, v in (jwt.get("payload") or {}).items()},
        }

    if method == "session_token":
        session = auth.get("session") or {}
        return {
            "type": "SessionTokenAuthenticator",
            "login_requester": {
                "type": "HttpRequester",
                "url_base": "{{ config['base_url'] }}",
                "path": session.get("login_path") or "/login",
                "http_method": "POST",
                "authenticator": {
                    "type": "BasicHttpAuthenticator",
                    "username": "{{ config['username'] }}",
                    "password": "{{ config['password'] }}",
                },
            },
            "session_token_path": _path_parts(session.get("token_path") or "token"),
            "expiration_duration": session.get("expiration") or "PT1H",
            "request_authentication": {
                "type": "ApiKey",
                "inject_into": {
                    "type": "RequestOption",
                    "field_name": session.get("header") or "X-Session-Token",
                    "inject_into": "header",
                },
            },
        }

    return None


def _path_parts(value: str | None) -> list[str]:
    """`data.next.url` -> ["data", "next", "url"]."""
    cleaned = (value or "").strip().strip(".")
    return [part for part in cleaned.split(".") if part] if cleaned else []


def _paginator(pagination: dict[str, Any]) -> dict[str, Any]:
    """Every pagination shape the editor offers.

    `NoPagination` is a real choice, not a fallback: a single page of results is
    the common case for small APIs, and pretending otherwise makes the request
    carry parameters the API never asked for.
    """
    mode = (pagination.get("mode") or "none").lower()
    page_size = int(pagination.get("page_size") or 50)
    inject = pagination.get("inject_into") or "request_parameter"

    if mode == "none":
        return {"type": "NoPagination"}

    if mode == "cursor":
        # The API hands back the next page; we only have to find it and know
        # when to stop.
        cursor_path = pagination.get("cursor_path") or "next"
        compiled: dict[str, Any] = {
            "type": "DefaultPaginator",
            "pagination_strategy": {
                "type": "CursorPagination",
                "page_size": page_size,
                "cursor_value": "{{ response." + cursor_path + " }}",
                "stop_condition": pagination.get("stop_condition")
                or ("{{ not response." + cursor_path + " }}"),
            },
            "page_token_option": {
                "type": "RequestOption",
                "inject_into": inject,
                "field_name": pagination.get("page_param") or "cursor",
            },
        }
        if pagination.get("size_param"):
            compiled["page_size_option"] = {
                "type": "RequestOption", "inject_into": "request_parameter",
                "field_name": pagination["size_param"],
            }
        return compiled

    if mode == "link_header":
        # RFC 5988: the next URL arrives in the Link header, so the token is a
        # whole URL and replaces the path rather than being a parameter.
        return {
            "type": "DefaultPaginator",
            "pagination_strategy": {
                "type": "CursorPagination",
                "page_size": page_size,
                "cursor_value": "{{ headers.link.next.url }}",
                "stop_condition": "{{ 'next' not in headers.link }}",
            },
            "page_token_option": {"type": "RequestPath"},
        }

    strategy = ({"type": "PageIncrement", "page_size": page_size,
                 "start_from_page": int(pagination.get("start_from") or 1)}
                if mode == "page"
                else {"type": "OffsetIncrement", "page_size": page_size})

    compiled = {
        "type": "DefaultPaginator",
        "pagination_strategy": strategy,
        "page_token_option": {
            "type": "RequestOption", "inject_into": inject,
            "field_name": pagination.get("page_param")
            or ("page" if mode == "page" else "offset"),
        },
    }
    size_param = pagination.get("size_param") or ("per_page" if mode == "page" else "limit")
    compiled["page_size_option"] = {
        "type": "RequestOption", "inject_into": "request_parameter",
        "field_name": size_param,
    }
    return compiled


def _record_selector(stream: dict[str, Any]) -> dict[str, Any]:
    selector: dict[str, Any] = {
        "type": "RecordSelector",
        "extractor": {
            "type": "DpathExtractor",
            "field_path": _path_parts(stream.get("record_selector")),
        },
    }
    condition = (stream.get("record_filter") or "").strip()
    if condition:
        # Filtering at the source saves the destination from storing rows the
        # user already said they do not want.
        selector["record_filter"] = {"type": "RecordFilter", "condition": condition}
    return selector


def _transformations(stream: dict[str, Any]) -> list[dict[str, Any]]:
    compiled: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    removals: list[list[str]] = []

    for item in stream.get("transformations") or []:
        kind = (item.get("type") or "").lower()
        path = _path_parts(item.get("path"))
        if not path:
            continue
        if kind == "add" and item.get("value") is not None:
            additions.append({"type": "AddedFieldDefinition",
                              "path": path, "value": str(item["value"])})
        elif kind == "remove":
            removals.append(path)

    # One AddFields with every addition, matching how the CDK expects them
    # grouped; separate components would run in an order the user never chose.
    if additions:
        compiled.append({"type": "AddFields", "fields": additions})
    if removals:
        compiled.append({"type": "RemoveFields", "field_pointers": removals})
    return compiled


def _error_handler(config: dict[str, Any]) -> dict[str, Any] | None:
    """Retry policy. Absent means the CDK's default, which is a sane one."""
    if not config:
        return None

    filters = []
    for item in config.get("filters") or []:
        codes = [int(c) for c in (item.get("http_codes") or []) if str(c).strip()]
        if not codes:
            continue
        entry: dict[str, Any] = {
            "type": "HttpResponseFilter",
            "http_codes": codes,
            "action": (item.get("action") or "RETRY").upper(),
        }
        if item.get("message"):
            entry["error_message"] = item["message"]
        filters.append(entry)

    backoff_mode = ((config.get("backoff") or {}).get("mode") or "none").lower()
    backoff = config.get("backoff") or {}
    strategies: list[dict[str, Any]] = []
    if backoff_mode == "constant":
        strategies.append({"type": "ConstantBackoffStrategy",
                           "backoff_time_in_seconds": int(backoff.get("seconds") or 5)})
    elif backoff_mode == "exponential":
        strategies.append({"type": "ExponentialBackoffStrategy",
                           "factor": int(backoff.get("factor") or 5)})
    elif backoff_mode == "header":
        # Honouring the API's own Retry-After is strictly better than guessing.
        strategies.append({"type": "WaitTimeFromHeader",
                           "header": backoff.get("header") or "Retry-After"})

    if not filters and not strategies and not config.get("max_retries"):
        return None

    handler: dict[str, Any] = {"type": "DefaultErrorHandler"}
    if config.get("max_retries") is not None:
        handler["max_retries"] = int(config["max_retries"])
    if filters:
        handler["response_filters"] = filters
    if strategies:
        handler["backoff_strategies"] = strategies
    return handler


def _partition_router(partition: dict[str, Any]) -> dict[str, Any] | None:
    """Repeat a stream once per value, or once per record of a parent stream."""
    mode = (partition.get("mode") or "none").lower()

    if mode == "list":
        values = [v.strip() for v in (partition.get("values") or "").split(",") if v.strip()]
        if not values:
            return None
        router: dict[str, Any] = {
            "type": "ListPartitionRouter",
            "values": values,
            "cursor_field": partition.get("cursor_field") or "partition",
        }
        if partition.get("param"):
            router["request_option"] = {
                "type": "RequestOption", "inject_into": "request_parameter",
                "field_name": partition["param"],
            }
        return router

    if mode == "parent":
        parent = partition.get("parent_stream")
        if not parent:
            return None
        return {
            "type": "SubstreamPartitionRouter",
            "parent_stream_configs": [{
                "type": "ParentStreamConfig",
                "stream": "#/definitions/streams/" + parent,
                "parent_key": partition.get("parent_key") or "id",
                "partition_field": partition.get("partition_field") or "parent_id",
            }],
        }

    return None


def _incremental(stream: dict[str, Any]) -> dict[str, Any]:
    cursor = stream["cursor_field"].strip()
    compiled: dict[str, Any] = {
        "type": "DatetimeBasedCursor",
        "cursor_field": cursor,
        "datetime_format": stream.get("cursor_format") or "%Y-%m-%dT%H:%M:%SZ",
        "start_datetime": {
            "type": "MinMaxDatetime",
            "datetime": "{{ config['start_date'] }}",
            "datetime_format": "%Y-%m-%dT%H:%M:%SZ",
        },
        "start_time_option": {
            "type": "RequestOption",
            "inject_into": "request_parameter",
            "field_name": stream.get("cursor_param") or cursor,
        },
    }
    if stream.get("cursor_end_param"):
        compiled["end_datetime"] = {
            "type": "MinMaxDatetime",
            "datetime": "{{ now_utc().strftime('%Y-%m-%dT%H:%M:%SZ') }}",
            "datetime_format": "%Y-%m-%dT%H:%M:%SZ",
        }
        compiled["end_time_option"] = {
            "type": "RequestOption",
            "inject_into": "request_parameter",
            "field_name": stream["cursor_end_param"],
        }
    if stream.get("step"):
        # Slicing the window keeps one sync from asking for years of data in a
        # single request.
        compiled["step"] = stream["step"]
        compiled["cursor_granularity"] = stream.get("cursor_granularity") or "PT1S"
    if stream.get("lookback"):
        compiled["lookback_window"] = stream["lookback"]
    return compiled


def _key_fields(stream: dict[str, Any]) -> list[str]:
    """Field names the stream configuration claims exist.

    A primary key and a cursor are assertions about the records: the user is
    saying "every record has this". They therefore have to appear in the
    schema, whether or not the user filled a schema in.
    """
    names: list[str] = []
    primary_key = (stream.get("primary_key") or "").strip()
    names += [part.strip() for part in primary_key.split(",") if part.strip()]

    # `incremental` is a flag; the cursor sits beside it, the same shape
    # `_incremental` reads.
    if stream.get("incremental"):
        cursor = (stream.get("cursor_field") or "").strip()
        if cursor:
            names.append(cursor)

    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _stream_schema(stream: dict[str, Any]) -> dict[str, Any]:
    """A declared schema if the user gave one, otherwise an open object.

    Guessing types from a sample would bake one response's shape into the
    connector; an open schema lets the destination take whatever arrives.

    Open, but not empty. Airbyte rejects a catalog whose source-defined primary
    key names a field the schema does not declare — "key: 'id' of path: '[id]'
    not found in schema" — and it is right to: a key that is not in the schema
    cannot be deduplicated on. The fields the user named are therefore declared
    here, with no type constraint, because naming them as the key is already a
    claim that they exist. Nothing else is invented.
    """
    declared = stream.get("schema")
    if isinstance(declared, dict) and declared.get("properties"):
        schema = dict(declared)
        properties = dict(schema.get("properties") or {})
        for name in _key_fields(stream):
            properties.setdefault(name, {})
        schema["properties"] = properties
        return schema

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": True,
        # `{}` means "any type": the field must be present, and we do not
        # pretend to know what it holds.
        "properties": {name: {} for name in _key_fields(stream)},
    }


def _requester(stream: dict[str, Any], authenticator: dict[str, Any] | None) -> dict[str, Any]:
    requester: dict[str, Any] = {
        "type": "HttpRequester",
        "url_base": "{{ config['base_url'] }}",
        "path": stream["path"].strip(),
        "http_method": (stream.get("http_method") or "GET").upper(),
    }
    if authenticator:
        requester["authenticator"] = authenticator

    params = {p["key"]: p["value"] for p in (stream.get("query_params") or [])
              if (p or {}).get("key")}
    if params:
        requester["request_parameters"] = params

    headers = {h["key"]: h["value"] for h in (stream.get("headers") or [])
               if (h or {}).get("key")}
    if headers:
        requester["request_headers"] = headers

    body = stream.get("request_body") or {}
    entries = {e["key"]: e["value"] for e in (body.get("entries") or [])
               if (e or {}).get("key")}
    if entries:
        mode = (body.get("mode") or "json").lower()
        requester["request_body_json" if mode == "json" else "request_body_data"] = entries

    handler = _error_handler(stream.get("error_handler") or {})
    if handler:
        requester["error_handler"] = handler

    return requester


def _stream(stream: dict[str, Any], authenticator: dict[str, Any] | None) -> dict[str, Any]:
    retriever: dict[str, Any] = {
        "type": "SimpleRetriever",
        "requester": _requester(stream, authenticator),
        "record_selector": _record_selector(stream),
        "paginator": _paginator(stream.get("pagination") or {}),
    }

    router = _partition_router(stream.get("partition") or {})
    if router:
        retriever["partition_router"] = router

    compiled: dict[str, Any] = {
        "type": "DeclarativeStream",
        "name": stream["name"].strip(),
        "retriever": retriever,
        "schema_loader": {"type": "InlineSchemaLoader", "schema": _stream_schema(stream)},
    }

    primary_key = (stream.get("primary_key") or "").strip()
    if primary_key:
        # A comma makes it composite; the CDK reads a list as one compound key.
        parts = [p.strip() for p in primary_key.split(",") if p.strip()]
        compiled["primary_key"] = parts if len(parts) > 1 else parts[0]

    if stream.get("incremental"):
        compiled["incremental_sync"] = _incremental(stream)

    transformations = _transformations(stream)
    if transformations:
        compiled["transformations"] = transformations

    return compiled


_USER_INPUT_TYPES = {"string": "string", "integer": "integer",
                     "number": "number", "boolean": "boolean"}


def _connection_spec(definition: dict[str, Any]) -> dict[str, Any]:
    """The config form a user fills in when creating a source from this connector.

    Base URL is part of it — a built connector is a template, and the same shape
    of API often lives at several hosts.
    """
    properties: dict[str, Any] = {
        "base_url": {
            "type": "string",
            "title": "Base URL",
            "description": "Địa chỉ gốc của API.",
            "default": definition.get("base_url"),
            "order": 0,
        },
    }
    required = ["base_url"]

    method = ((definition.get("auth") or {}).get("method") or "none").lower()
    if method in {"api_key", "bearer"}:
        properties["api_key"] = {
            "type": "string", "title": "API key", "airbyte_secret": True, "order": 1,
        }
        required.append("api_key")
    elif method in {"basic", "session_token"}:
        properties["username"] = {"type": "string", "title": "Username", "order": 1}
        properties["password"] = {
            "type": "string", "title": "Password", "airbyte_secret": True, "order": 2,
        }
        required += ["username", "password"]
    elif method == "oauth2":
        properties["client_id"] = {
            "type": "string", "title": "Client ID", "airbyte_secret": True, "order": 1,
        }
        properties["client_secret"] = {
            "type": "string", "title": "Client secret", "airbyte_secret": True, "order": 2,
        }
        properties["refresh_token"] = {
            "type": "string", "title": "Refresh token", "airbyte_secret": True, "order": 3,
        }
        required += ["client_id", "client_secret", "refresh_token"]
    elif method == "jwt":
        properties["jwt_secret"] = {
            "type": "string", "title": "JWT secret", "airbyte_secret": True, "order": 1,
        }
        required.append("jwt_secret")

    if any(s.get("incremental") for s in definition.get("streams") or []):
        properties["start_date"] = {
            "type": "string",
            "title": "Start date",
            "description": "Mốc thời gian bắt đầu cho đồng bộ incremental.",
            "default": "1970-01-01T00:00:00Z",
            "order": 9,
        }
        required.append("start_date")

    # Fields the builder declared for itself, so a connector can be a template
    # over an account id, a region, a tenant slug.
    for index, field in enumerate(definition.get("user_inputs") or []):
        key = (field.get("key") or "").strip()
        if not key or key in properties:
            continue
        entry: dict[str, Any] = {
            "type": _USER_INPUT_TYPES.get((field.get("type") or "string").lower(), "string"),
            "title": field.get("title") or key,
            "order": 20 + index,
        }
        if field.get("description"):
            entry["description"] = field["description"]
        if field.get("default") not in (None, ""):
            entry["default"] = field["default"]
        if field.get("secret"):
            entry["airbyte_secret"] = True
        properties[key] = entry
        if field.get("required"):
            required.append(key)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{definition.get('name') or 'Custom'} Spec",
        "required": required,
        "properties": properties,
        "additionalProperties": True,
    }


def outbound_urls(definition: dict[str, Any]) -> list[tuple[str, str]]:
    """Every URL this connector may call, as (field, url).

    Kept in one function so the egress policy cannot fall behind the compiler:
    a new place that takes a URL has to be added here, and both the save-time
    and send-time checks pick it up at once. The OAuth token endpoint was
    missed exactly because each check named `base_url` by hand.
    """
    urls: list[tuple[str, str]] = []

    base_url = (definition.get("base_url") or "").strip()
    if base_url:
        urls.append(("base_url", base_url))

    auth = definition.get("auth") or {}
    token_url = ((auth.get("oauth") or {}).get("token_url") or "").strip()
    if token_url:
        urls.append(("auth.oauth.token_url", token_url))

    return urls


def compile_manifest(definition: dict[str, Any]) -> dict[str, Any]:
    """Editor state -> declarative connector document."""
    validate(definition)
    authenticator = _authenticator(definition.get("auth") or {})
    compiled = [_stream(s, authenticator) for s in definition["streams"]]

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "type": "DeclarativeSource",
        "check": {"type": "CheckStream", "stream_names": [compiled[0]["name"]]},
        "streams": compiled,
        "spec": {
            "type": "Spec",
            "connection_specification": _connection_spec(definition),
        },
    }

    # A parent-stream router references its parent by pointer, so the parents
    # have to exist somewhere resolvable.
    if any("partition_router" in s["retriever"]
           and s["retriever"]["partition_router"]["type"] == "SubstreamPartitionRouter"
           for s in compiled):
        manifest["definitions"] = {"streams": {s["name"]: s for s in compiled}}

    return manifest


def infer_schema(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a JSON schema from sampled records.

    Without this a built connector discovers a stream with no columns, and the
    pipeline wizard has nothing to select. Types are widened rather than
    guessed narrowly: a field seen as both integer and string is a string, and
    a field that was ever null stays nullable, because a 25-record sample is
    not evidence about the other million.
    """
    seen: dict[str, set[str]] = {}
    nullable: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            kinds = seen.setdefault(key, set())
            if value is None:
                nullable.add(key)
            elif isinstance(value, bool):
                kinds.add("boolean")
            elif isinstance(value, int):
                kinds.add("integer")
            elif isinstance(value, float):
                kinds.add("number")
            elif isinstance(value, str):
                kinds.add("string")
            elif isinstance(value, list):
                kinds.add("array")
            elif isinstance(value, dict):
                kinds.add("object")
            else:
                kinds.add("string")

    def resolve(kinds: set[str]) -> str:
        if not kinds:
            return "string"
        if len(kinds) == 1:
            return next(iter(kinds))
        # Mixed numeric evidence is still numeric; anything else falls back to
        # a string, which every destination can store.
        if kinds <= {"integer", "number"}:
            return "number"
        return "string"

    properties: dict[str, Any] = {}
    for key, kinds in seen.items():
        base = resolve(kinds)
        # A field that was null in the sample must not be declared non-null.
        properties[key] = ({"type": [base, "null"]}
                           if key in nullable or not kinds else {"type": base})

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
    }


class _ReadableDumper(yaml.SafeDumper):
    """Quote the way a person would.

    Almost every value in a manifest is a Jinja expression containing single
    quotes — `{{ config['base_url'] }}`. PyYAML's default is a single-quoted
    scalar, which escapes those by doubling them into `config[''base_url'']`:
    correct, round-trippable, and unreadable in the editor this YAML exists for.
    """


def _quote_readably(dumper: yaml.SafeDumper, value: str):
    style = '"' if "'" in value else None
    if chr(10) in value:
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_ReadableDumper.add_representer(str, _quote_readably)


def manifest_yaml(definition: dict[str, Any]) -> str:
    """The compiled manifest as YAML.

    Airbyte's own tooling speaks YAML, so this is what a user copies out to
    review in a PR, hand to support, or paste into a plain CDK project.
    """
    return yaml.dump(
        compile_manifest(definition), Dumper=_ReadableDumper,
        sort_keys=False, allow_unicode=True, default_flow_style=False, width=100,
    )


def definition_from_manifest(document: str) -> dict[str, Any]:
    """Read a declarative manifest back into editor state.

    This is deliberately partial: the CDK has components the editor does not
    render, and silently dropping them on the next save would destroy work the
    user could see in the YAML. So anything unrecognised makes the import fail
    loudly instead.
    """
    try:
        manifest = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise ValidationError(
            "Không đọc được YAML.", code="BUILDER_YAML_INVALID",
            details={"reason": str(exc)[:300]},
        ) from None

    if not isinstance(manifest, dict) or manifest.get("type") != "DeclarativeSource":
        raise ValidationError(
            "Tài liệu này không phải một DeclarativeSource.",
            code="BUILDER_MANIFEST_NOT_SOURCE",
        )

    streams = manifest.get("streams")
    if not isinstance(streams, list) or not streams:
        raise ValidationError(
            "Manifest không có stream nào.", code="BUILDER_MANIFEST_NO_STREAM",
        )

    base_url = ""
    auth: dict[str, Any] = {"method": "none"}
    imported: list[dict[str, Any]] = []

    for raw in streams:
        retriever = (raw or {}).get("retriever") or {}
        requester = retriever.get("requester") or {}
        selector = (retriever.get("record_selector") or {}).get("extractor") or {}
        paginator = retriever.get("paginator") or {}
        strategy = paginator.get("pagination_strategy") or {}

        url_base = str(requester.get("url_base") or "")
        if url_base and "config[" not in url_base:
            base_url = base_url or url_base

        authenticator = requester.get("authenticator") or {}
        kind = authenticator.get("type")
        if kind == "ApiKeyAuthenticator":
            auth = {"method": "api_key",
                    "header": (authenticator.get("inject_into") or {}).get("field_name")}
        elif kind == "BearerAuthenticator":
            auth = {"method": "bearer"}
        elif kind == "BasicHttpAuthenticator":
            auth = {"method": "basic"}
        elif kind == "OAuthAuthenticator":
            auth = {"method": "oauth2",
                    "oauth": {"token_url": authenticator.get("token_refresh_endpoint"),
                              "scopes": ", ".join(authenticator.get("scopes") or [])}}
        elif kind == "JwtAuthenticator":
            auth = {"method": "jwt",
                    "jwt": {"algorithm": authenticator.get("algorithm")}}

        pagination_mode = {
            "PageIncrement": "page",
            "OffsetIncrement": "offset",
            "CursorPagination": "cursor",
        }.get(strategy.get("type"), "none")

        primary_key = raw.get("primary_key")
        if isinstance(primary_key, list):
            primary_key = ", ".join(str(p) for p in primary_key)

        cursor = raw.get("incremental_sync") or {}
        imported.append({
            "name": raw.get("name") or "stream",
            "path": requester.get("path") or "/",
            "http_method": (requester.get("http_method") or "GET").upper(),
            "record_selector": ".".join(selector.get("field_path") or []),
            "record_filter": ((retriever.get("record_selector") or {})
                              .get("record_filter") or {}).get("condition", ""),
            "primary_key": primary_key or "",
            "pagination": {
                "mode": pagination_mode,
                "page_size": strategy.get("page_size") or 50,
                "page_param": (paginator.get("page_token_option") or {}).get("field_name"),
                "size_param": (paginator.get("page_size_option") or {}).get("field_name"),
            },
            "incremental": bool(cursor),
            "cursor_field": cursor.get("cursor_field") or "",
            "cursor_format": cursor.get("datetime_format") or "",
            "cursor_param": (cursor.get("start_time_option") or {}).get("field_name") or "",
            "query_params": [{"key": k, "value": str(v)}
                             for k, v in (requester.get("request_parameters") or {}).items()],
            "headers": [{"key": k, "value": str(v)}
                        for k, v in (requester.get("request_headers") or {}).items()],
            "schema": (raw.get("schema_loader") or {}).get("schema") or {},
            "partition": {"mode": "none"},
            "transformations": [],
            "error_handler": {},
        })

    return {
        "name": ((manifest.get("spec") or {}).get("connection_specification") or {})
        .get("title", "Imported connector").replace(" Spec", ""),
        "base_url": base_url or "https://api.example.com",
        "auth": auth,
        "user_inputs": [],
        "streams": imported,
    }


def descriptor() -> ConnectorDescriptor:
    """The runner image, described the same way as any other connector."""
    return ConnectorDescriptor(
        connector_key="source-declarative-manifest",
        docker_repository=RUNNER_REPOSITORY,
        version=RUNNER_VERSION,
    )


def starter_definition(name: str) -> dict[str, Any]:
    """A project that is already valid enough to test.

    An empty editor makes the user guess what the fields mean; a working example
    they can edit teaches the shape in one read.
    """
    return {
        "name": name,
        "base_url": "https://jsonplaceholder.typicode.com",
        "auth": {"method": "none"},
        "user_inputs": [],
        "streams": [{
            "name": "posts",
            "path": "/posts",
            "http_method": "GET",
            "record_selector": "",
            "record_filter": "",
            "primary_key": "id",
            "pagination": {"mode": "none", "page_size": 50},
            "incremental": False,
            "query_params": [],
            "headers": [],
            "request_body": {"mode": "json", "entries": []},
            "partition": {"mode": "none"},
            "transformations": [],
            "error_handler": {},
        }],
    }


