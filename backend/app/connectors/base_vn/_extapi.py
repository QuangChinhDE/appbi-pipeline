"""The extapis dialect of Base, expressed once.

The newer Base applications -- BPM, Dataset, and the ones that follow -- do not
speak the dialect `_shared.py` compiles. They answer on one gateway host,
`https://extapis.<domain>/<app>/v1/<path>`, and:

* take `POST` with a **JSON** body, not form fields
* read the key from the `X-API-TOKEN` **header** (`X-API-PASSWORD` beside it
  when the key was issued with one), not `access_token_v2` in the body
* answer `{"status": 1, …}` on success and `{"status": 0, "error": …}` on
  refusal -- `code` is not in the vocabulary, so `_shared._error_handler`
  would read every refusal as success
* page, where they page at all, by an opaque keyset cursor object
  (`next_cursor`, `has_more`) rather than by page number

So these connectors live in this package, under the Base.vn heading the
catalogue already groups by, and are compiled here instead of by `_shared.py`:
`ExtapiConnector` / `ExtapiStream` are the definitions, `compile_manifest()`
and `catalogue_entry()` turn them into what the engine and the catalogue read.
Each application is a file that only *declares* its connector (`bpm.py`,
`dataset.py`); none of them builds a manifest by hand.

Incremental, by rule
--------------------

Enforced here so no connector can get it wrong:

* a stream is incremental only when its records carry a real timestamp or
  sequence column; nothing is stamped on a record to manufacture a cursor
* the workspace picks the cursor in the pipeline
  (`allow_catalog_defined_cursor_field`); the manifest names none, so discover
  reports `[""]` and the form pre-fills nothing
* an incremental stream has a primary key, so the overlap the CDK re-reads at
  the cursor boundary can be deduplicated
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ._shared import KNOWN_DOMAINS, MANIFEST_VERSION, USER_AGENT

JsonSchema = dict[str, Any]

#: The cursor the manifest names. Deliberately empty: the workspace picks it.
USER_PICKED_CURSOR = ""

_EPOCH = "%s"
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def nullable(*kinds: str, en: str = "", description_vi: str = "",
             items: JsonSchema | None = None,
             properties: dict[str, JsonSchema] | None = None) -> JsonSchema:
    """A schema property that may be null. Every field here may be.

    `en` / `description_vi` are what the field means, shown in the pipeline's Dictionary
    tab ahead of any AI guess. `items` / `properties` describe what an array
    or object holds, so the dictionary can go below `array` and `object`.
    Nested schemas stay open (`additionalProperties`), for the same reason the
    top level does: Base adds fields without notice.
    """
    prop: JsonSchema = {"type": ["null", *kinds]}
    if en:
        prop["description"] = en
    if description_vi:
        prop["description_vi"] = description_vi
    if items is not None:
        prop["items"] = items
    if properties is not None:
        prop["properties"] = properties
        prop["additionalProperties"] = True
    return prop


def obj(properties: dict[str, JsonSchema]) -> JsonSchema:
    """An array element that is an object with these fields (and maybe more)."""
    return {"type": "object", "properties": properties, "additionalProperties": True}


@dataclass(frozen=True)
class Parent:
    """This stream is read once per record of another stream.

    `partition_field` names the parent id inside `stream_partition`; the
    stream's `body` sends it as `{{ stream_partition.<partition_field> }}`.
    `stamp` writes it onto each record as well, for endpoints whose rows do
    not repeat the parent id -- without it the rows of every parent land in
    one table with no way to tell them apart.
    """

    stream: str
    partition_field: str
    parent_key: str = "id"
    stamp: bool = False


@dataclass(frozen=True)
class Incremental:
    """The stream offers incremental, on a cursor the workspace picks.

    `candidates` are the record's real timestamp or sequence columns the
    cursor may be; all of them must be whole numbers (epoch seconds or ids).

    `request_param` is the server-side filter the stored cursor value is sent
    as, when the endpoint has one. It has to be a *superset* filter for every
    candidate -- the chosen column itself is enforced on the records.

    `global_cursor` keeps one high-water mark for the whole stream instead of
    one per parent. Needed when each partition is a single record (one call
    per id): a per-partition mark compares each record with its own previous
    value and lets it through every time.
    """

    candidates: tuple[str, ...]
    request_param: str | None = None
    #: The closing bound, for endpoints that ignore an open-ended filter.
    #: Sales reads `last_update_stime` only beside `last_update_etime`; sent
    #: alone it is dropped and every record comes back (measured).
    request_end_param: str | None = None
    global_cursor: bool = False


@dataclass(frozen=True)
class PageNumber:
    """Page-number paging: `page` from `start`, `size` records per page.

    `size` should be the endpoint's maximum. Sales offsets pages
    inconsistently: walking 999 deals at 100 a page returned 996 distinct
    ones, at 37 a page 987, while one page of 1,000 returned all 999 (measured
    2026-10-20). Fewer pages is the only mitigation available to a client.
    """

    size: int
    page_param: str = "page"
    size_param: str = "limit"
    start: int = 0


@dataclass(frozen=True)
class Before:
    """Walk back in time: each page asks for records older than the last one.

    For endpoints that page by a timestamp bound rather than a page number or
    an opaque cursor (Sales `pipeline/get.logs`: newest first, `before` = the
    oldest `created_at` returned, at most `size` records a page).

    The bound is sent as the oldest value **plus one**, so the next page
    starts again at the second the last one ended in. `before` excludes that
    second, and records sharing one are common -- 141 of 200 Sales logs did --
    so sending the value as-is drops whatever of that second did not fit on
    the page. Re-reading it costs a few duplicate rows, which the primary key
    removes under Append + Deduped; losing them costs data.

    Stops on a short page, or on a page that is one second wide (sending the
    same bound again would return the same page forever).
    """

    field: str
    param: str
    size: int
    size_param: str = "limit"


@dataclass(frozen=True)
class Keyset:
    """Keyset paging that sends the values of `next_cursor` back as fields.

    Unlike `ExtapiStream.paginate` (BPM `job/list`, which takes the whole
    cursor object back as one `cursor` field), these endpoints want each key
    of `next_cursor` as a body field of the same name: Project's task feeds
    `{"before": …}`, Lead's lead feed `{"before_ts": …, "before_id": …}`.
    `next_cursor` is null or absent on the last page; an empty value on the
    first request is accepted (measured).
    """

    keys: tuple[str, ...]
    size: int
    size_param: str = "limit"


@dataclass(frozen=True)
class Split:
    """Read the stream once per fixed value of one request field.

    For endpoints that filter by default and offer no "all": Sales
    `contact/list` returns active contacts unless asked for `disabled`.
    """

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class ExtapiStream:
    name: str
    path: str
    #: Where the records live in the response, e.g. `("jobs",)`.
    extract: tuple[str, ...]
    primary_key: tuple[str, ...]
    #: Field types worth pinning. The schema stays open for everything else.
    properties: dict[str, JsonSchema]
    #: The JSON body, as Jinja against `config` and `stream_partition`.
    body: dict[str, Any] = field(default_factory=dict)
    parent: Parent | None = None
    split: Split | None = None
    incremental: Incremental | None = None
    #: Keyset paging on `next_cursor` / `has_more`.
    paginate: bool = False
    #: Page-number paging instead.
    pages: PageNumber | None = None
    #: Or paging back in time by a timestamp bound.
    before: Before | None = None
    #: Or keyset paging on one value of `next_cursor`. At most one of the four.
    keyset: Keyset | None = None


@dataclass(frozen=True)
class BodyField:
    """A workspace setting sent in the body of every request.

    For applications that answer *as a person* rather than as the workspace:
    Project refuses every read without `user_id` (`invalid_data`, "this
    endpoint answers as a person") and returns only what that person may see.
    """

    name: str
    title: str
    title_vi: str
    description: str
    description_vi: str
    kind: str = "string"
    required: bool = True


@dataclass(frozen=True)
class ExtapiConnector:
    """One Base application on the extapis gateway, as a connector."""

    #: The gateway path segment, e.g. `bpm` -> `extapis.<domain>/bpm/v1/`.
    app: str
    title: str
    streams: tuple[ExtapiStream, ...]
    summary: str
    summary_vi: str
    docs_url: str
    #: What the key needs, for the refusal message and the form.
    key_help: str
    key_help_vi: str
    scopes: str
    #: Offer `X-API-PASSWORD`, for keys issued with one.
    password: bool = False
    #: Settings sent in every request body; see `BodyField`.
    body_fields: tuple[BodyField, ...] = ()
    certification: str = "BETA"

    @property
    def connector_key(self) -> str:
        return f"source-base-{self.app}"

    @property
    def url_base(self) -> str:
        return f"https://extapis.{{domain}}/{self.app}/v1/"

    def stream(self, name: str) -> ExtapiStream:
        for candidate in self.streams:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.app}: no stream {name!r}")

    def validate(self) -> None:
        """Structural checks, run at import time by the package __init__."""
        names = [s.name for s in self.streams]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"{self.app}: duplicate stream names {sorted(duplicates)}")
        for stream in self.streams:
            if not stream.primary_key:
                raise ValueError(
                    f"{self.app}.{stream.name}: no primary key, so a re-sync "
                    "cannot deduplicate")
            if sum(bool(p) for p in (stream.paginate, stream.pages, stream.before,
                                     stream.keyset)) > 1:
                raise ValueError(
                    f"{self.app}.{stream.name}: only one paging scheme per stream")
            if stream.parent and stream.parent.stream not in names:
                raise ValueError(
                    f"{self.app}.{stream.name}: parent {stream.parent.stream!r} "
                    "is not a stream here")
            inc = stream.incremental
            if inc:
                if not inc.candidates:
                    raise ValueError(
                        f"{self.app}.{stream.name}: incremental with no cursor "
                        "candidates; a stream without a real timestamp or "
                        "sequence column is full refresh")
                missing = [c for c in inc.candidates if c not in stream.properties]
                if missing:
                    raise ValueError(
                        f"{self.app}.{stream.name}: cursor candidates {missing} "
                        "are not columns of the record")


# ── manifest compilation ─────────────────────────────────────────────────────

def _error_handler(connector: ExtapiConnector) -> dict[str, Any]:
    """The refusal is `status: 0` in the body, sometimes on a 200.

    Read as "2xx is success", a refused key becomes an empty collection and a
    full-refresh sync replaces the table with nothing.
    """
    return {
        "type": "DefaultErrorHandler",
        "response_filters": [
            {
                "type": "HttpResponseFilter",
                "action": "RATE_LIMITED",
                "http_codes": [429],
            },
            {
                "type": "HttpResponseFilter",
                "action": "FAIL",
                "predicate": "{{ response.get('status') == 0 }}",
                "error_message": (
                    f"{connector.title} rejected this request: "
                    "{{ response.get('error', 'unknown') }} - "
                    "{{ response.get('message', '') }}"
                    # Field-level reasons ride in `errors`, not `message`:
                    # "No active user with this username in this workspace"
                    # arrived there and the reader saw only "invalid_data".
                    "{% if response.get('errors') %} {{ response.get('errors') }}{% endif %}"
                    "{% if response.get('error') == 'scope_required' %}"
                    f" Re-issue the API key with {connector.scopes}."
                    "{% elif response.get('error') == 'unauthorized' %}"
                    " Check the API key, and that the domain matches the Base "
                    "installation that issued it."
                    "{% endif %}"
                ),
            },
            {
                "type": "HttpResponseFilter",
                "action": "FAIL",
                "http_codes": [401, 403],
                "error_message": (
                    f"{connector.title} refused the API key. Check the key, and "
                    "that the domain matches the Base installation that issued it."
                ),
            },
        ],
        "backoff_strategies": [
            {"type": "ExponentialBackoffStrategy", "factor": 2},
        ],
    }


def _requester(connector: ExtapiConnector, stream: ExtapiStream) -> dict[str, Any]:
    return {
        "type": "HttpRequester",
        "url_base": connector.url_base.replace(
            "{domain}", "{{ config.get('domain') or '" + KNOWN_DOMAINS[0] + "' }}"),
        "path": stream.path,
        "http_method": "POST",
        "request_headers": {
            "X-API-TOKEN": "{{ config['api_token'] }}",
            "X-API-PASSWORD": "{{ config.get('api_password') or '' }}",
            "User-Agent": USER_AGENT,
        },
        "request_body_json": {
            **{f.name: "{{ config['" + f.name + "'] }}" for f in connector.body_fields},
            **stream.body,
            **_keyset_body(stream),
        },
        "error_handler": _error_handler(connector),
    }


def _keyset_body(stream: ExtapiStream) -> dict[str, str]:
    """One body field per key of the previous page's `next_cursor`, empty on
    the first page."""
    if not stream.keyset:
        return {}
    return {
        key: ("{{ (next_page_token.get('next_page_token') or {}).get('" + key + "', '') "
              "if next_page_token else '' }}")
        for key in stream.keyset.keys
    }


def _paginator(stream: ExtapiStream) -> dict[str, Any]:
    if stream.keyset:
        k = stream.keyset
        # The whole cursor object is the page token; `_keyset_body` spreads
        # its keys into the request body.
        return {
            "type": "DefaultPaginator",
            "page_size_option": {
                "type": "RequestOption",
                "field_name": k.size_param,
                "inject_into": "body_json",
            },
            "pagination_strategy": {
                "type": "CursorPagination",
                "page_size": k.size,
                "cursor_value": "{{ response.get('next_cursor') }}",
                "stop_condition": (
                    "{{ not response.get('has_more') or not response.get('next_cursor') }}"
                ),
            },
        }
    if stream.before:
        b = stream.before
        records = "response.get('" + stream.extract[-1] + "') or []"
        return {
            "type": "DefaultPaginator",
            "page_size_option": {
                "type": "RequestOption",
                "field_name": b.size_param,
                "inject_into": "body_json",
            },
            "page_token_option": {
                "type": "RequestOption",
                "field_name": b.param,
                "inject_into": "body_json",
            },
            "pagination_strategy": {
                "type": "CursorPagination",
                "page_size": b.size,
                # See `Before`: one past the oldest, so its second is re-read.
                "cursor_value": "{{ (last_record['" + b.field + "'] | int) + 1 }}",
                "stop_condition": (
                    "{% set rows = " + records + " %}"
                    "{{ rows | length < " + str(b.size) + " or "
                    "rows[0]['" + b.field + "'] == rows[-1]['" + b.field + "'] }}"
                ),
            },
        }
    if stream.pages:
        pages = stream.pages
        return {
            "type": "DefaultPaginator",
            "page_size_option": {
                "type": "RequestOption",
                "field_name": pages.size_param,
                "inject_into": "body_json",
            },
            "page_token_option": {
                "type": "RequestOption",
                "field_name": pages.page_param,
                "inject_into": "body_json",
            },
            "pagination_strategy": {
                "type": "PageIncrement",
                "page_size": pages.size,
                "start_from_page": pages.start,
                "inject_on_first_request": True,
            },
        }
    if not stream.paginate:
        return {"type": "NoPagination"}
    # The cursor is an object, returned and sent back verbatim. Injected as
    # JSON so it stays an object on the wire.
    return {
        "type": "DefaultPaginator",
        "page_token_option": {
            "type": "RequestOption",
            "field_name": "cursor",
            "inject_into": "body_json",
        },
        "pagination_strategy": {
            "type": "CursorPagination",
            "cursor_value": "{{ response.get('next_cursor') }}",
            "stop_condition": (
                "{{ not response.get('has_more') "
                "or not response.get('next_cursor') }}"
            ),
        },
    }


def _incremental(inc: Incremental) -> dict[str, Any]:
    compiled: dict[str, Any] = {
        "type": "DatetimeBasedCursor",
        "cursor_field": USER_PICKED_CURSOR,
        "allow_catalog_defined_cursor_field": True,
        # Every candidate is a whole number -- epoch seconds or a sequence id
        # -- so one numeric format reads them all. A global mark is *written*
        # as ISO: the CDK (7.28.2 `_update_global_cursor`) keeps it by
        # comparing formatted strings, and epoch strings of unequal length
        # sort wrong ("9" over "8045"). ISO strings sort in time order.
        "datetime_format": _ISO if inc.global_cursor else _EPOCH,
        "cursor_datetime_formats": [_EPOCH, _ISO] if inc.global_cursor else [_EPOCH],
        "cursor_granularity": "PT1S",
        # The server filter, if any, is a superset; the chosen column is
        # enforced here, on the records.
        "is_client_side_incremental": True,
        # One slice: the filters are an open "changed since", so slicing would
        # read the same records once per slice.
        "step": "P1000Y",
        "lookback_window": "{{ config.get('lookback_window') or 'PT0S' }}",
        "start_datetime": {
            "type": "MinMaxDatetime",
            "datetime": "{{ config.get('updated_from') or 0 }}",
            "datetime_format": _EPOCH,
        },
        "end_datetime": {
            "type": "MinMaxDatetime",
            # Not `strftime('%s')`: a glibc extension that raises "Invalid
            # format string" anywhere else.
            "datetime": "{{ now_utc().timestamp() | int }}",
            "datetime_format": _EPOCH,
        },
    }
    if inc.global_cursor:
        compiled["global_substream_cursor"] = True
    if inc.request_param:
        compiled["start_time_option"] = {
            "type": "RequestOption",
            "field_name": inc.request_param,
            "inject_into": "body_json",
        }
    if inc.request_end_param:
        compiled["end_time_option"] = {
            "type": "RequestOption",
            "field_name": inc.request_end_param,
            "inject_into": "body_json",
        }
    return compiled


def _schema(stream: ExtapiStream) -> JsonSchema:
    properties = dict(stream.properties)
    if stream.parent and stream.parent.stamp:
        properties.setdefault(stream.parent.partition_field, nullable("string"))
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
    }


def _stream_manifest(connector: ExtapiConnector, stream: ExtapiStream) -> dict[str, Any]:
    retriever: dict[str, Any] = {
        "type": "SimpleRetriever",
        "requester": _requester(connector, stream),
        "record_selector": {
            "type": "RecordSelector",
            "extractor": {"type": "DpathExtractor", "field_path": list(stream.extract)},
        },
        "paginator": _paginator(stream),
    }
    routers: list[dict[str, Any]] = []
    if stream.parent:
        routers.append({
            "type": "SubstreamPartitionRouter",
            "parent_stream_configs": [{
                "type": "ParentStreamConfig",
                "stream": {"$ref": f"#/definitions/streams/{stream.parent.stream}"},
                "parent_key": stream.parent.parent_key,
                "partition_field": stream.parent.partition_field,
            }],
        })
    if stream.split:
        routers.append({
            "type": "ListPartitionRouter",
            "cursor_field": stream.split.field,
            "values": list(stream.split.values),
            "request_option": {
                "type": "RequestOption",
                "field_name": stream.split.field,
                "inject_into": "body_json",
            },
        })
    if routers:
        # One router is set as itself, so a manifest that needs no split
        # compiles exactly as it did before splits existed.
        retriever["partition_router"] = routers[0] if len(routers) == 1 else routers
    compiled: dict[str, Any] = {
        "type": "DeclarativeStream",
        "name": stream.name,
        "primary_key": list(stream.primary_key),
        "retriever": retriever,
        "schema_loader": {"type": "InlineSchemaLoader", "schema": _schema(stream)},
    }
    if stream.parent and stream.parent.stamp:
        field_name = stream.parent.partition_field
        compiled["transformations"] = [{
            "type": "AddFields",
            "fields": [{"path": [field_name],
                        "value": "{{ stream_partition." + field_name + " }}"}],
        }]
    if stream.incremental:
        compiled["incremental_sync"] = _incremental(stream.incremental)
    return compiled


def connection_specification(connector: ExtapiConnector) -> JsonSchema:
    properties: dict[str, Any] = {
        "api_token": {
            "type": "string",
            "title": "API token",
            "title_vi": "API token",
            "description": connector.key_help,
            "description_vi": connector.key_help_vi,
            "airbyte_secret": True,
            "order": 0,
        },
        "domain": {
            "type": "string",
            "title": "Base domain",
            "title_vi": "Tên miền Base",
            "enum": list(KNOWN_DOMAINS),
            "default": KNOWN_DOMAINS[0],
            "description": (
                "Which Base installation issued the key. A key from one is "
                "refused by the other with 401."
            ),
            "description_vi": (
                "Key được cấp trên bản Base nào. Key của bản này bị bản kia "
                "từ chối với lỗi 401."
            ),
            "order": 1,
        },
    }
    order = 2
    if connector.password:
        properties["api_password"] = {
            "type": "string",
            "title": "API password",
            "title_vi": "Mật khẩu API",
            "description": (
                "Only when the key was issued with a password "
                "(X-API-PASSWORD). Leave it empty otherwise."
            ),
            "description_vi": (
                "Chỉ điền khi key được cấp kèm mật khẩu (X-API-PASSWORD). "
                "Không có thì để trống."
            ),
            "airbyte_secret": True,
            "order": order,
        }
        order += 1
    required = ["api_token"]
    for body_field in connector.body_fields:
        properties[body_field.name] = {
            "type": body_field.kind,
            "title": body_field.title,
            "title_vi": body_field.title_vi,
            "description": body_field.description,
            "description_vi": body_field.description_vi,
            "order": order,
        }
        order += 1
        if body_field.required:
            required.append(body_field.name)
    if any(s.incremental for s in connector.streams):
        properties["updated_from"] = {
            "type": "string",
            "title": "Start incremental syncs from",
            "title_vi": "Mốc bắt đầu đồng bộ incremental",
            "description": (
                "The first value of the cursor you pick, as a whole number: "
                "epoch seconds for a time column, or an id. Leave it at 0 to "
                "read everything; later syncs continue from where the last "
                "one stopped."
            ),
            "description_vi": (
                "Giá trị ban đầu của cursor bạn chọn, dạng số nguyên: epoch "
                "seconds với cột thời gian, hoặc id. Để 0 để lấy toàn bộ; các "
                "lần sau tự tiếp tục từ chỗ lần trước dừng."
            ),
            "default": "0",
            "examples": ["0"],
            "order": order,
        }
        properties["lookback_window"] = {
            "type": "string",
            "title": "Re-read this far back each sync",
            "title_vi": "Đọc lùi lại mỗi lần đồng bộ",
            "description": (
                "How far to overlap, as an ISO-8601 duration: PT10M is ten "
                f"minutes. PT0S turns it off. Covers clock skew between "
                f"{connector.title} and this host. Only turn it on where the "
                "destination writes with Append + deduplicate."
            ),
            "description_vi": (
                "Khoảng đọc chồng lại, dạng ISO-8601: PT10M là mười phút. PT0S "
                f"là tắt. Bù lệch giờ giữa máy chủ {connector.title} và máy "
                "chạy đồng bộ. Chỉ bật khi đích ghi theo Append + khử trùng lặp."
            ),
            "default": "PT0S",
            "examples": ["PT0S", "PT10M"],
            "order": order + 1,
        }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{connector.title} source",
        "required": required,
        "additionalProperties": True,
        "properties": properties,
    }


def _routers(stream: dict[str, Any]) -> list[dict[str, Any]]:
    router = stream["retriever"].get("partition_router")
    if router is None:
        return []
    return router if isinstance(router, list) else [router]


def _as_parent(streams: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    """The stream as a *parent* reads it: every record, no cursor.

    A child reads its parent only to learn the ids to iterate, and wants all
    of them. Referenced with its `incremental_sync`, the parent ran a cursor
    whose field is deliberately empty (the workspace picks it, and only for
    the stream it syncs itself), and the CDK warned once per parent record --
    "Could not find cursor field `` in record ... will assume it needs to be
    synced": 86 such lines in one Project run, all harmless, all noise.
    Applied down the chain, so a grandchild's parents are cursor-free too.
    """
    parent = copy.deepcopy(streams[name])
    parent.pop("incremental_sync", None)
    for router in _routers(parent):
        for config in router.get("parent_stream_configs", []):
            config["stream"] = _as_parent(streams, config["stream"]["$ref"].rsplit("/", 1)[-1])
    return parent


def compile_manifest(connector: ExtapiConnector) -> dict[str, Any]:
    connector.validate()
    streams = {s.name: _stream_manifest(connector, s) for s in connector.streams}
    # Children reference cursor-free copies of their parents; see `_as_parent`.
    # Copies are taken from the untouched definitions, before any is rewired.
    original = copy.deepcopy(streams)
    for stream in streams.values():
        for router in _routers(stream):
            for config in router.get("parent_stream_configs", []):
                config["stream"] = _as_parent(original, config["stream"]["$ref"].rsplit("/", 1)[-1])
    # The first stream with no parent and no paging: one call that confirms
    # the key and its scope, not a crawl.
    checkable = next(
        (s.name for s in connector.streams
         if not s.parent and not s.split and not s.paginate and not s.pages
         and not s.before and not s.keyset),
        connector.streams[0].name,
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
            "documentation_url": connector.docs_url,
        },
    }


def catalogue_entry(connector: ExtapiConnector, runner_repository: str,
                    runner_version: str) -> dict[str, Any]:
    """A registry row, in the same shape `base_vn.catalogue_entries` emits."""
    return {
        "connector_key": connector.connector_key,
        "display_name": connector.title,
        "connector_type": "SOURCE",
        "category": "Base.vn",
        "description": connector.summary,
        "description_vi": connector.summary_vi,
        "icon": connector.connector_key,
        "icon_url": "",
        "documentation_url": connector.docs_url,
        "docker_repository": runner_repository,
        "version": runner_version,
        "release_stage": (
            "generally_available" if connector.certification == "SUPPORTED" else "beta"),
        "support_level": (
            "certified" if connector.certification == "SUPPORTED" else "community"),
        "supports_oauth": False,
        "supports_incremental": any(s.incremental for s in connector.streams),
        "supports_cdc": False,
        "supports_namespaces": False,
        "supported_destination_sync_modes": [],
        "spec_schema": connection_specification(connector),
        "declarative_manifest": compile_manifest(connector),
        "certification": connector.certification,
        "stream_count": len(connector.streams),
    }
