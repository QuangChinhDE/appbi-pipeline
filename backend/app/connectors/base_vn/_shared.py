"""The Base.vn API, expressed once.

Every Base application — Account, HRM, Workflow, Hiring, Income and the rest —
speaks the same dialect. Encoding that dialect here, rather than repeating it in
ten YAML files, is the whole point of this package: when Base changes something
about how their APIs behave, it changes in one place and every connector and
every workspace gets it.

What the dialect is
-------------------

* `POST`, form-encoded, to `https://<app>.base.vn/extapi/v1/<path>`
* the credential travels in the body as `access_token_v2`
* the payload is `{"<collection>": [...]}`, or `{"code":…, "data": {…}}` for the
  newer applications
* pagination is zero-based with a `limit`; most apps call the page field
  `page`, while Workflow calls it `page_id`
* incremental filtering is `updated_from`, epoch seconds, in the query string

Two things this fixes about the previous YAML
--------------------------------------------

**Failure looks like success.** Base answers `HTTP 200` with `{"code": 0,
"message": "access_token_v2_invalid_3"}` when it rejects a token. Nothing in the
old manifests looked at `code`, so an expired token produced a sync that
completed, wrote zero rows, and reported success — silently replacing a
customer's table with nothing. Every stream built here fails on `code: 0`.

**The credential had the wrong name.** The old manifests send `access_token`.
Base's own error taxonomy distinguishes them: `access_token_invalid_2` for that
name, `access_token_v2_invalid_1` for a malformed v2 token, and
`access_token_v2_invalid_3` for one it will not accept. The current tokens are
v2, so those manifests could not have authenticated at all.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

#: The runner image. A Base connector is a manifest, not an image of its own —
#: which is what makes "update the code once and every workspace has it" true.
RUNNER_REPOSITORY = "airbyte/source-declarative-manifest"
RUNNER_VERSION = "7.28.2"
MANIFEST_VERSION = "6.0.2"

#: The credential field. Named here once so no connector can spell it wrong.
TOKEN_FIELD = "access_token_v2"

#: Base caps a page well below this, but asking for more than the server allows
#: is answered with the server's own maximum rather than an error.
DEFAULT_PAGE_SIZE = 500

#: A browser-ish agent. Some Base endpoints return an HTML error page to
#: unrecognised clients, which is indistinguishable from a broken endpoint.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

JsonSchema = dict[str, Any]

_SCHEMA_REGISTRY: dict[str, dict[str, JsonSchema]] = json.loads(
    Path(__file__).with_name("schemas.json").read_text(encoding="utf-8")
)


@dataclass(frozen=True)
class Parent:
    """This stream is read once per record of another stream.

    Base has no "list all stages" endpoint; there is "list the stages of this
    workflow". So the child is driven by the parent's ids.

    `inject` says how the parent id reaches the request. Base is not consistent
    about the field name — `id` for workflow stages, `cycle_id` for payroll
    records — so it is named per stream rather than assumed.
    """

    stream: str
    inject: str = "id"
    parent_key: str = "id"


@dataclass(frozen=True)
class Incremental:
    """Filter server-side on a timestamp, and remember where we got to.

    `field` is the record's own cursor field; `param` is the request field Base
    filters on. They are rarely the same word: records carry `last_update`
    while the filter is `updated_from`.
    """

    field: str = "last_update"
    param: str = "updated_from"
    #: Epoch seconds is what Base emits and accepts. Kept configurable because
    #: two of the newer applications use ISO-8601.
    fmt: str = "%s"
    #: The closing bound, when the application insists on a closed range.
    #: `publicapi/v2` treats the filter as open-ended and needs no end; Income
    #: is on the older `extapi/v1`, which answers `Updated to param is
    #: required` until it gets one.
    end_param: str | None = None
    #: Where the bounds go. Every `publicapi/v2` application reads them from
    #: the query string. Income does not: with `?updated_from=0` it answers
    #: `Updated from param is required`, and the same value in the form body
    #: is accepted. The parameter was being sent all along -- to a place that
    #: application does not read.
    inject_into: str = "request_parameter"


@dataclass(frozen=True)
class Stream:
    """One collection of records, and how to get all of them exactly once."""

    name: str
    path: str
    #: Where the records live in the response. `("data", "incomes")` for the
    #: applications that wrap everything in `data`.
    collection: tuple[str, ...]
    primary_key: tuple[str, ...] = ("id",)
    incremental: Incremental | None = None
    parent: Parent | None = None
    paginate: bool = True
    page_size: int = DEFAULT_PAGE_SIZE
    #: Base's APIs do not agree on the page parameter. Most use `page`; the
    #: Workflow API uses `page_id`. Treating this as a platform-wide constant
    #: repeats page zero forever as soon as a collection exceeds one page.
    page_field: str = "page"
    #: The page-size parameter is just as inconsistent as the page parameter.
    #: Hiring calls it `num_per_page`; WeWork uses three different names.
    #: Sending the generic `limit` can be ignored silently, which makes the
    #: paginator stop after the server's first short default page.
    page_size_field: str = "limit"
    #: Extra body fields this endpoint requires.
    body: dict[str, str] = field(default_factory=dict)
    #: A one-line note on why this stream exists, shown in the catalogue and in
    #: the handover document.
    note: str = ""
    #: Field types worth pinning. Everything else is left open, because Base
    #: adds fields without warning and a closed schema would drop them.
    fields: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A substream *may* be incremental: Airbyte keeps cursor state per
        # partition, so "tickets of service 7 changed since X" is a coherent
        # question and each service desk advances independently. It is only
        # worth it where the child is large and the endpoint really accepts the
        # filter, so it stays opt-in per stream rather than on by default.
        if not self.collection:
            raise ValueError(f"{self.name}: no collection path")


@dataclass(frozen=True)
class ConfigField:
    """A per-workspace setting beyond the token."""

    name: str
    title: str
    description: str = ""
    required: bool = False
    kind: Literal["string", "integer"] = "string"
    secret: bool = False
    default: Any = None


#: Where Base is hosted. Not a cosmetic setting: `base.vn` and `base.com.vn`
#: are separate installations with separate accounts, and a token issued on one
#: is refused by the other with `access_token_v2_invalid_3` — which reads like
#: an expired token and is not.
#:
#: This was briefly removed on the grounds that the host belongs to the product.
#: That was wrong. The *path* belongs to the product; the host is which Base a
#: customer is on, and only they know it.
DEFAULT_DOMAIN = "base.com.vn"
KNOWN_DOMAINS = ("base.com.vn", "base.vn")


@dataclass(frozen=True)
class BaseConnector:
    """One Base application, as a connector this product ships."""

    app: str
    title: str
    #: The path after the host, with its leading subdomain. Most applications
    #: are `<app>.{domain}/extapi/v1/`, but Hiring is `/publicapi/v2/` and
    #: WeWork is `/extapi/v3/`, so it is stated rather than derived.
    #: `{domain}` is substituted from config at request time.
    url_base: str
    streams: tuple[Stream, ...]
    summary: str = ""
    docs_url: str = ""
    config: tuple[ConfigField, ...] = ()

    @property
    def connector_key(self) -> str:
        return f"source-base-{self.app}"

    def stream(self, name: str) -> Stream:
        for candidate in self.streams:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.app}: no stream {name!r}")

    def validate(self) -> None:
        """Structural checks, run at import time by the package __init__.

        A connector that ships broken is worse than one that fails to load: the
        first shows up in the catalogue and fails at sync time for a customer.
        """
        names = [s.name for s in self.streams]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"{self.app}: duplicate stream names {sorted(duplicates)}")
        for candidate in self.streams:
            if candidate.parent and candidate.parent.stream not in names:
                raise ValueError(
                    f"{self.app}.{candidate.name}: parent "
                    f"{candidate.parent.stream!r} is not a stream here")
            if not candidate.primary_key:
                raise ValueError(
                    f"{self.app}.{candidate.name}: no primary key, so a "
                    "re-sync cannot deduplicate")


# ── manifest compilation ─────────────────────────────────────────────────────

# Refusals that are about one resource rather than about the request.
#
# Base answers 200 with `code: 0` for both "your token is not accepted" and
# "you may not see this particular opening", and only the message tells them
# apart. Failing on the second kills an entire sync over a single record that
# the workspace was never allowed to read: observed on Hiring, where
# `stage/list` is partitioned by `opening_id` and opening 386 is private. The
# stream failed, the sync failed, and every retry failed identically.
#
# Deliberately a narrow allowlist, matched on a lowercased message. Anything
# not named here still fails, so a refusal nobody has seen before cannot
# quietly turn into an empty table -- which is the failure the FAIL rule below
# exists to prevent, and is worse than a loud stop.
PARTITION_REFUSALS = (
    "is private",
)


def _error_handler() -> dict[str, Any]:
    """Turn Base's 200-with-`code:0` into a failure, except where it is not one.

    This is the single most important thing in this file. Base signals refusal
    in the body, not the status line, so the default "2xx is success" rule
    reads a rejected token as an empty collection. A full-refresh sync then
    replaces the customer's table with nothing and reports success.

    `response_filters` inspect the body, so the failure surfaces as a failure.
    The one exception is a refusal naming a single inaccessible resource; see
    `PARTITION_REFUSALS`. Order matters -- IGNORE has to be evaluated before
    the catch-all FAIL.
    """
    private = " or ".join(
        f"{phrase!r} in message" for phrase in PARTITION_REFUSALS)
    return {
        "type": "DefaultErrorHandler",
        "response_filters": [
            {
                "type": "HttpResponseFilter",
                "action": "IGNORE",
                "predicate": (
                    "{% set message = (response.get('message') or '')|lower %}"
                    "{{ response.get('code') == 0 and (" + private + ") }}"
                ),
                "error_message": (
                    "Base will not show this resource to the current token "
                    "({{ response.get('message', 'unknown') }}); skipping it "
                    "and continuing the sync."
                ),
            },
            {
                "type": "HttpResponseFilter",
                "action": "FAIL",
                "predicate": "{{ 'message' in response and response.get('code') == 0 }}",
                "error_message": (
                    "Base rejected this request: "
                    "{{ response.get('message', 'unknown') }}. "
                    "An `access_token_v2_invalid` message means the token is "
                    "not accepted for this application — issue a new one in "
                    "the Base admin console."
                ),
            },
            # Rate limiting, when Base signals it at all, is a 429.
            {
                "type": "HttpResponseFilter",
                "action": "RATE_LIMITED",
                "http_codes": [429],
            },
        ],
        "backoff_strategies": [
            {"type": "ExponentialBackoffStrategy", "factor": 2},
        ],
    }


def _paginator(stream: Stream) -> dict[str, Any]:
    if not stream.paginate:
        return {"type": "NoPagination"}
    return {
        "type": "DefaultPaginator",
        "page_size_option": {
            "type": "RequestOption",
            "field_name": stream.page_size_field,
            "inject_into": "body_data",
        },
        "page_token_option": {
            "type": "RequestOption",
            "field_name": stream.page_field,
            "inject_into": "body_data",
        },
        "pagination_strategy": {
            "type": "PageIncrement",
            "page_size": stream.page_size,
            "start_from_page": 0,
        },
    }


def _incremental(inc: Incremental) -> dict[str, Any]:
    """A cursor Base can actually filter on.

    `step: P1000Y` is not a mistake. Base takes a single "changed since" value
    rather than a range, so slicing the window would issue N identical requests
    and read the same records N times. One slice, one request, one high-water
    mark.
    """
    return {
        "type": "DatetimeBasedCursor",
        "cursor_field": inc.field,
        "datetime_format": inc.fmt,
        "cursor_datetime_formats": [inc.fmt, "%s", "%Y-%m-%dT%H:%M:%SZ"],
        "cursor_granularity": "PT1S",
        "step": "P1000Y",
        "start_datetime": {
            "type": "MinMaxDatetime",
            "datetime": "{{ config.get('updated_from') or 0 }}",
            "datetime_format": "%s",
        },
        "end_datetime": {
            "type": "MinMaxDatetime",
            "datetime": "{{ now_utc().strftime('%s') }}",
            "datetime_format": "%s",
        },
        "start_time_option": {
            "type": "RequestOption",
            "field_name": inc.param,
            "inject_into": inc.inject_into,
        },
        **({
            "end_time_option": {
                "type": "RequestOption",
                "field_name": inc.end_param,
                "inject_into": inc.inject_into,
            },
        } if inc.end_param else {}),
    }


def _schema(connector: BaseConnector, stream: Stream) -> JsonSchema:
    """Open by default, typed where it matters.

    Base adds response fields without announcing it, and a closed schema turns
    each addition into dropped data. The primary key and cursor are pinned
    because a wrong type there breaks deduplication and state rather than just
    losing a column.
    """
    schema = copy.deepcopy(
        _SCHEMA_REGISTRY.get(connector.app, {}).get(stream.name, {})
    )
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["type"] = "object"
    schema["additionalProperties"] = True
    properties: dict[str, Any] = schema.setdefault("properties", {})
    for key in stream.primary_key:
        properties.setdefault(key, {"type": ["null", "string", "integer"]})
    if stream.incremental:
        properties.setdefault(
            stream.incremental.field,
            {"type": ["null", "string", "integer"]},
        )
    for name, kind in stream.fields.items():
        properties.setdefault(name, {"type": ["null", kind]})
    return schema


def _stream_manifest(connector: BaseConnector, stream: Stream) -> dict[str, Any]:
    body: dict[str, str] = {TOKEN_FIELD: "{{ config['" + TOKEN_FIELD + "'] }}"}
    body.update(stream.body)

    retriever: dict[str, Any] = {
        "type": "SimpleRetriever",
        "requester": {
            "type": "HttpRequester",
            "url_base": connector.url_base.replace(
                "{domain}", "{{ config['domain'] or '" + DEFAULT_DOMAIN + "' }}"),
            "path": stream.path,
            "http_method": "POST",
            "request_headers": {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            "request_body_data": body,
            "error_handler": _error_handler(),
        },
        "record_selector": {
            "type": "RecordSelector",
            "extractor": {
                "type": "DpathExtractor",
                "field_path": list(stream.collection),
            },
        },
        "paginator": _paginator(stream),
    }

    if stream.parent:
        body[stream.parent.inject] = "{{ stream_partition.parent_id }}"
        retriever["partition_router"] = {
            "type": "SubstreamPartitionRouter",
            "parent_stream_configs": [{
                "type": "ParentStreamConfig",
                "stream": {"$ref": f"#/definitions/streams/{stream.parent.stream}"},
                "parent_key": stream.parent.parent_key,
                "partition_field": "parent_id",
            }],
        }

    manifest: dict[str, Any] = {
        "type": "DeclarativeStream",
        "name": stream.name,
        "primary_key": list(stream.primary_key),
        "retriever": retriever,
        "schema_loader": {
            "type": "InlineSchemaLoader",
            "schema": _schema(connector, stream),
        },
    }
    if stream.incremental:
        manifest["incremental_sync"] = _incremental(stream.incremental)
    return manifest


def connection_specification(connector: BaseConnector) -> JsonSchema:
    """What a workspace has to supply. The token, and almost never anything else.

    Deliberately minimal. The previous HRM manifest required `domain` and
    `version` and templated them into the URL, so every workspace had to know
    Base's hosting layout to connect to it — and a typo produced a connector
    that pointed somewhere else entirely. The host belongs to the connector,
    which ships with the product; the token belongs to the workspace.
    """
    # Vietnamese, because the product is. English help beside Vietnamese
    # labels was the single most jarring thing about the first version of this
    # form — the reader has to switch language mid-field to find out what to
    # type. No backticks either: the form renders help as plain text, so
    # markdown arrives on screen as punctuation.
    properties: dict[str, Any] = {
        TOKEN_FIELD: {
            "type": "string",
            "title": "Access token",
            "description": (
                f"Token API của {connector.title}, lấy trong phần quản trị "
                "của Base. Mỗi ứng dụng một token riêng: token của Workflow "
                "không đọc được HRM."
            ),
            "airbyte_secret": True,
            "order": 0,
        },
    }
    required = [TOKEN_FIELD]

    properties["domain"] = {
        "type": "string",
        "title": "Tên miền Base",
        # An enum, so the form renders a dropdown rather than a free-text box.
        # These are two separate installations with separate accounts, and a
        # token from one is refused by the other with a message that reads
        # exactly like an expired token -- which cost most of a debugging
        # session to work out. Two choices instead of a text field removes the
        # typo and names the alternative.
        "enum": list(KNOWN_DOMAINS),
        "default": DEFAULT_DOMAIN,
        "description": (
            "Tài khoản này nằm trên bản Base nào. base.vn là bản chính; "
            "base.com.vn là một bản cài riêng, tài khoản tách biệt. Token của "
            "bản này bị bản kia từ chối, và báo lỗi trông y hệt token hết hạn "
            "— nên nếu token đúng mà vẫn bị từ chối, kiểm tra mục này trước."
        ),
        "order": 1,
    }

    if any(s.incremental for s in connector.streams):
        properties["updated_from"] = {
            "type": "string",
            "title": "Chỉ lấy bản ghi thay đổi từ",
            "description": (
                "Thời điểm dạng epoch seconds. Để 0 để lần đồng bộ đầu lấy "
                "toàn bộ; các lần sau tự tiếp tục từ chỗ lần trước dừng."
            ),
            "default": "0",
            "examples": ["0"],
            "order": 2,
        }

    for index, extra in enumerate(connector.config, start=3):
        properties[extra.name] = {
            "type": extra.kind,
            "title": extra.title,
            "description": extra.description,
            "order": index,
        }
        if extra.secret:
            properties[extra.name]["airbyte_secret"] = True
        if extra.default is not None:
            properties[extra.name]["default"] = extra.default
        if extra.required:
            required.append(extra.name)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{connector.title} source",
        "required": required,
        "properties": properties,
        "additionalProperties": True,
    }


def compile_manifest(connector: BaseConnector) -> dict[str, Any]:
    """The Airbyte declarative manifest for one Base application.

    Compiled from the Python definition rather than stored as YAML, so the
    definition can be reviewed as code, checked at import time, and shipped
    inside the product with nothing for an operator to copy.
    """
    connector.validate()
    streams = {s.name: _stream_manifest(connector, s) for s in connector.streams}

    # The cheapest stream with no parent — `check` should confirm the token, not
    # crawl the largest table in the account.
    checkable = next(
        (s.name for s in connector.streams if not s.parent and not s.paginate),
        next((s.name for s in connector.streams if not s.parent),
             connector.streams[0].name),
    )

    return {
        "version": MANIFEST_VERSION,
        "type": "DeclarativeSource",
        "check": {"type": "CheckStream", "stream_names": [checkable]},
        "definitions": {"streams": streams},
        "streams": [{"$ref": f"#/definitions/streams/{name}"} for name in streams],
        "spec": {
            "type": "Spec",
            "connection_specification": connection_specification(connector),
            "documentation_url": connector.docs_url or "https://base.vn",
        },
    }
