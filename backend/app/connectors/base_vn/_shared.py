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
    Why a substream reads every parent id, every sync
    -------------------------------------------------

    An incremental parent narrows what it *emits*, not what the partition
    router *iterates*: the child still issues one request per parent record.
    Measured on Base CRM, whose 270 sales pipelines make the gap obvious:

        run 1, no state    5,582 deals emitted, 270 child requests
        run 2, with state    263 deals emitted, 270 child requests

    The CDK does offer `incremental_dependency` on `ParentStreamConfig` for
    exactly this, and it is deliberately not set. Two reasons, both measured
    rather than argued:

    * **It does not help on these APIs.** Turning it on for CRM changed
      nothing -- 263 records and 270 child requests either way -- because
      `pipeline/all` ignores `last_update_stime` and returns all 270 records
      regardless, so there is nothing narrower for the router to iterate.
    * **It would lose data where it did work.** It is only safe when a parent
      is touched whenever one of its children changes, and Base does not do
      that. Of 234 CRM pipelines holding deals, 234 have a deal newer than the
      pipeline itself, some by over a year; WeWork is the same, 41 of 42
      projects. With the flag on, those parents would drop out of the
      partition list after the first sync and their children would silently
      stop arriving, while every run still reported success.

    `workflow.workflow -> workflow.stage` is the one pair whose parent is
    incremental, and it is safe for the opposite reason: 20 of 20 workflows
    are at least as new as their newest stage. It still reads all 20
    partitions each sync, and that is the behaviour to keep.
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
    #: Whether to put the bounds on the request at all.
    #:
    #: Off means "track a cursor, filter on the way out, send nothing". That is
    #: the honest shape for an endpoint whose documented parameters do not
    #: include a time filter -- `deal/get.activities` takes an id and nothing
    #: else. Inventing `last_update_stime` there is noise today and a hazard
    #: later: if Base ever implements that name with different semantics, the
    #: connector changes behaviour without anybody editing it.
    #:
    #: Distinct from an endpoint that documents the filter and applies it --
    #: `pipeline/deals`, `account/list` and `contact/list` all do, provided they
    #: are sent the *closed pair*. A lone `last_update_stime` is ignored there,
    #: which is what made them look like they filtered nothing.
    send_request_options: bool = True
    #: Also drop records older than the cursor before emitting them.
    #:
    #: For an endpoint that returns everything no matter what it is sent, the
    #: cursor can still earn its keep: the CDK compares each record against the
    #: high-water mark and only the changed ones reach the destination. It saves
    #: warehouse writes, never API calls -- every record is still fetched.
    #:
    #: Leave it off where the server filters. Declaring it there would re-check
    #: on the client what the server already applied, and any disagreement
    #: between the two -- a clock skew, an inclusive versus exclusive bound --
    #: would silently drop rows the server was willing to give us.
    client_side: bool = False


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
    #:
    #: `None` means the endpoint pages but takes no size at all -- Base CRM
    #: Leads documents `page` and nothing else. Sending an invented `limit`
    #: there is the same hazard as inventing a time filter: noise today, and a
    #: silent change of behaviour the day Base implements that name.
    page_size_field: str | None = "limit"
    #: Where page numbering starts. Base's older APIs count from 0; the CRM
    #: (`apis.base.vn/sales`) counts from 1, and starting at 0 there returns
    #: the first page twice before the paginator believes it is done.
    first_page: int = 0
    #: Whether the page number rides on the first request too. Off by default,
    #: because most Base endpoints treat an explicit page on the first call as
    #: a filter rather than an offset.
    page_on_first_request: bool = False
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
    #: Send this field in the body of every request, not merely collect it.
    #:
    #: Most Base applications authenticate with a token alone. Base CRM wants a
    #: token *and* an account password on each call, and a credential that is
    #: asked for but never sent is the worst of both: the form demands it and
    #: every request is still refused.
    send_in_body: bool = False


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
    #: The request field the token travels in. Every application on
    #: `publicapi/v2` and `extapi/v1` reads `access_token_v2`; Base CRM reads
    #: plain `access_token` and refuses the other name.
    token_field: str = TOKEN_FIELD
    #: Hosts this application is reachable on. The CRM lives on
    #: `apis.base.vn` / `apis.basecrm.vn`, which are the same backend under two
    #: names, and not on the per-app subdomains the others use.
    domains: tuple[str, ...] = KNOWN_DOMAINS
    #: Requests per interval this application allows, as (limit, ISO-8601).
    #:
    #: Declared so the CDK paces itself with a moving window, instead of running
    #: flat out and learning the cap from a refusal. `lead_feed` issues one
    #: request per lead -- 546 of them here -- and without this it exceeded Base
    #: CRM Leads' 100/minute after about a hundred, failed the stream, and then
    #: took the whole job down through an Airbyte bug (see the module docstring
    #: in `crm_leads.py`).
    #:
    #: Left unset elsewhere on purpose: the Sales API sustained 5,582 requests
    #: in one sync at roughly 220/minute with no refusal, so the two
    #: applications do not share a limit and guessing one for the others would
    #: slow every sync to protect against a cap nobody has observed.
    rate_limit: tuple[int, str] | None = None
    #: What the catalogue claims about this connector.
    #:
    #: `SUPPORTED` means "this product wrote it, tested it against the live API
    #: and stands behind it". A connector built from documentation but not yet
    #: run against a real tenant is `BETA` -- it is honest to ship the shape and
    #: dishonest to call it certified. Promote it when the measurements exist,
    #: not when the code is finished.
    certification: str = "SUPPORTED"
    #: Key to look this application's field contracts up under in
    #: `schemas.json`. Defaults to `app`; set it where the reviewed YAML is
    #: filed under a different name -- `base_crm_sale.yaml` yields `crm_sale`
    #: while the connector ships as `crm`. Renaming either to match the other
    #: would mean either an awkward `source-base-crm_sale` in the catalogue or
    #: a source document that no longer matches what Base calls it.
    schema_app: str | None = None

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

#: Refusals that mean "too fast", said in the body rather than the status line.
#:
#: Base CRM Leads caps at 100 requests a minute and says so with HTTP 400 and
#: `{"code": 0, "message": "Quota exceeded: 100 req/min"}` -- no 429, no
#: `Retry-After`. Read by the catch-all FAIL rule below, that is a dead sync:
#: `lead_feed` makes one request per lead, hit the cap after about a hundred, and
#: the stream failed 37 seconds in. Matched before FAIL so it becomes a wait.
#:
#: The client-side budget (`BaseConnector.rate_limit`) is the real control; this
#: is the safety net for when something else is spending the same quota.
RATE_LIMIT_REFUSALS = (
    "quota exceeded",
    "too many request",
)


def _api_budget(connector: BaseConnector) -> dict[str, Any]:
    """A moving-window rate limit over every request this connector makes.

    One policy with no matchers, so it covers the whole connector: the quota
    Base enforces is per token, not per endpoint, and a substream firing one
    request per parent is exactly the stream that would exhaust it.
    """
    limit, interval = connector.rate_limit  # type: ignore[misc]
    return {
        "type": "HTTPAPIBudget",
        "policies": [{
            "type": "MovingWindowCallRatePolicy",
            "rates": [{"type": "Rate", "limit": limit, "interval": interval}],
            "matchers": [],
        }],
    }


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
    throttled = " or ".join(
        f"{phrase!r} in message" for phrase in RATE_LIMIT_REFUSALS)
    return {
        "type": "DefaultErrorHandler",
        "response_filters": [
            # Before FAIL: a quota refusal is a wait, not a broken request.
            {
                "type": "HttpResponseFilter",
                "action": "RATE_LIMITED",
                "predicate": (
                    "{% set message = (response.get('message') or '')|lower %}"
                    "{{ " + throttled + " }}"
                ),
                "error_message": (
                    "Base is rate limiting this connector "
                    "({{ response.get('message', 'unknown') }}); waiting and "
                    "retrying."
                ),
            },
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
        **({"page_size_option": {
            "type": "RequestOption",
            "field_name": stream.page_size_field,
            "inject_into": "body_data",
        }} if stream.page_size_field else {}),
        "page_token_option": {
            "type": "RequestOption",
            "field_name": stream.page_field,
            "inject_into": "body_data",
        },
        "pagination_strategy": {
            "type": "PageIncrement",
            # Declared only when we control it. `page_size` is what the CDK
            # compares a short page against to decide it has reached the end, so
            # asserting a size the server was never told about means guessing:
            # too high and it stops on the server's first default-sized page,
            # too low and it pages past the end forever. With no size declared
            # it stops on an empty page, which is true whatever the server does.
            **({"page_size": stream.page_size} if stream.page_size_field else {}),
            "start_from_page": stream.first_page,
            "inject_on_first_request": stream.page_on_first_request,
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
        **({
            "start_time_option": {
                "type": "RequestOption",
                "field_name": inc.param,
                "inject_into": inc.inject_into,
            },
        } if inc.send_request_options else {}),
        **({
            "end_time_option": {
                "type": "RequestOption",
                "field_name": inc.end_param,
                "inject_into": inc.inject_into,
            },
        } if inc.end_param and inc.send_request_options else {}),
        **({"is_client_side_incremental": True} if inc.client_side else {}),
    }


def _schema(connector: BaseConnector, stream: Stream) -> JsonSchema:
    """Open by default, typed where it matters.

    Base adds response fields without announcing it, and a closed schema turns
    each addition into dropped data. The primary key and cursor are pinned
    because a wrong type there breaks deduplication and state rather than just
    losing a column.
    """
    schema = copy.deepcopy(
        _SCHEMA_REGISTRY.get(connector.schema_app or connector.app, {})
        .get(stream.name, {})
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
    token = connector.token_field
    body: dict[str, str] = {token: "{{ config['" + token + "'] }}"}
    # Credentials that have to accompany every call, not just be collected.
    for extra in connector.config:
        if extra.send_in_body:
            body[extra.name] = "{{ config['" + extra.name + "'] }}"
    body.update(stream.body)

    retriever: dict[str, Any] = {
        "type": "SimpleRetriever",
        "requester": {
            "type": "HttpRequester",
            "url_base": connector.url_base.replace(
                "{domain}",
                "{{ config['domain'] or '" + connector.domains[0] + "' }}"),
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
        connector.token_field: {
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
    required = [connector.token_field]

    properties["domain"] = {
        "type": "string",
        "title": "Tên miền Base",
        # An enum, so the form renders a dropdown rather than a free-text box.
        # These are two separate installations with separate accounts, and a
        # token from one is refused by the other with a message that reads
        # exactly like an expired token -- which cost most of a debugging
        # session to work out. Two choices instead of a text field removes the
        # typo and names the alternative.
        "enum": list(connector.domains),
        "default": connector.domains[0],
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
        # Only where a cap has actually been observed; see `rate_limit`.
        **({"api_budget": _api_budget(connector)} if connector.rate_limit else {}),
        "spec": {
            "type": "Spec",
            "connection_specification": connection_specification(connector),
            "documentation_url": connector.docs_url or "https://base.vn",
        },
    }
