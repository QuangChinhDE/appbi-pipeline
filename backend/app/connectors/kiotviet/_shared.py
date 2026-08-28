"""Compile a KiotViet connector from a Python definition.

Deliberately not `base_vn/_shared.py`. That compiler speaks Base's dialect all
the way down -- POST with a form body, the token as a body field, and refusal
signalled by `code: 0` inside a 200. KiotViet shares none of it:

    Base.vn                          KiotViet
    POST, form body                  GET, query string
    token in the body                Bearer, refreshed by OAuth2
    per-app host                     one host + a `Retailer` header
    page number                      `currentItem` offset
    `{"code": 0}` on refusal         ordinary HTTP status codes

Bending one compiler across both would leave every field conditional on which
vendor it was for, which is two compilers wearing one name. The *pattern* is
shared -- Python definition, compiled to a declarative manifest at import,
registered through the same catalogue path -- and that is the part worth reusing.

Two facts drive most of the shape below, and both are measured rather than read:

* **The token is fetched, not supplied.** The published document covers every
  GET endpoint and explicitly excludes the token flow. `client_credentials`
  against `id.kiotviet.vn/connect/token` with `scopes=PublicApi.Access` works,
  and `OAuthAuthenticator` performs the exchange inside the sync, so nothing
  above the connector ever handles a bearer token.
* **Every collection is `data`.** Nineteen of the twenty-four documented GET
  endpoints answer with the same envelope, `{total, pageSize, data, timestamp}`.
  The five that do not are dealt with individually in `catalog.py`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = "6.0.0"

#: One host for every endpoint; the shop is chosen by a header, not a subdomain.
API_BASE = "https://public.kiotapi.com/"
TOKEN_ENDPOINT = "https://id.kiotviet.vn/connect/token"

#: The scope KiotViet issues Public API tokens under. Sent as a literal body
#: field rather than through the CDK's `scopes` list, because the parameter is
#: named `scopes` (plural) with a single value -- and the CDK's own list is
#: serialised under whatever name it prefers, which is not something to guess
#: at when one string settles it.
TOKEN_SCOPE = "PublicApi.Access"

#: Documented maximum, and what the connector asks for.
#:
#: The server actually honoured `pageSize=200` -- measured, 200 records came
#: back. The document says 100. Asking for more than the published contract
#: allows buys one fewer request per hundred records and pays for it the first
#: time KiotViet enforces what it wrote down, so the documented number wins.
PAGE_SIZE = 100

#: 5,000 GET requests an hour, from the document's own introduction.
RATE_LIMIT = (5000, "PT1H")


@dataclass(frozen=True)
class Incremental:
    """Filter on the server, and remember where we got to.

    `param` is always `lastModifiedFrom` -- KiotViet uses one name everywhere it
    offers the filter at all. `field` is the record's own timestamp and is *not*
    uniform: products carry `modifiedDate`, branches carry only `createdDate`.
    """

    field: str = "modifiedDate"
    param: str = "lastModifiedFrom"
    #: What KiotViet accepts on the way in and emits on the way out.
    fmt: str = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True)
class Stream:
    """One collection, and how to read all of it exactly once."""

    name: str
    path: str
    primary_key: tuple[str, ...] = ("id",)
    #: Where the records live. `("data",)` for the paged envelope; empty means
    #: the response *is* the record, which `settings` needs.
    collection: tuple[str, ...] = ("data",)
    incremental: Incremental | None = None
    paginate: bool = True
    #: Extra query parameters this endpoint requires.
    params: dict[str, str] = field(default_factory=dict)
    #: Field types worth pinning. Everything else stays open: KiotViet adds
    #: response fields without announcing it and a closed schema drops them.
    fields: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.path:
            raise ValueError(f"{self.name or '?'}: name and path are both required")
        if self.incremental and not self.paginate:
            # A filtered collection that cannot page truncates at the first page
            # and calls it a sync.
            raise ValueError(f"{self.name}: incremental without pagination")


@dataclass(frozen=True)
class KiotVietConnector:
    app: str
    title: str
    streams: tuple[Stream, ...]
    summary: str = ""
    docs_url: str = ""

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


# ── compilation ─────────────────────────────────────────────────────────────

def _authenticator() -> dict[str, Any]:
    """Exchange the client credentials for a bearer token, inside the sync.

    KiotViet's tokens last a day. Handing the product a token to store would
    make every connection break silently once a day and read as an expired
    credential; the client id and secret do not expire, so those are what the
    workspace supplies.
    """
    return {
        "type": "OAuthAuthenticator",
        "token_refresh_endpoint": TOKEN_ENDPOINT,
        "grant_type": "client_credentials",
        "client_id": "{{ config['client_id'] }}",
        "client_secret": "{{ config['client_secret'] }}",
        "refresh_request_body": {"scopes": TOKEN_SCOPE},
    }


def _error_handler() -> dict[str, Any]:
    """Which refusals stop a sync, and which are just this shop's settings.

    HTTP 420 is KiotViet's "that module is switched off for this retailer" --
    observed on `ordersuppliers`: *Thiết lập "Đặt hàng nhập" đang không được
    bật*. Failing the sync over it would mean a shop that does not use purchase
    ordering could never sync anything else, so the stream is skipped and the
    rest of the connector continues.
    """
    return {
        "type": "DefaultErrorHandler",
        "response_filters": [
            {
                "type": "HttpResponseFilter",
                "action": "IGNORE",
                "http_codes": [420],
                "error_message": (
                    "KiotViet reports this feature is not enabled for the shop; "
                    "skipping the stream and continuing the sync."
                ),
            },
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
            "field_name": "pageSize",
            "inject_into": "request_parameter",
        },
        "page_token_option": {
            "type": "RequestOption",
            "field_name": "currentItem",
            "inject_into": "request_parameter",
        },
        # An offset, not a page number: `currentItem=100` means "start at the
        # hundred-and-first record". Measured on `locations`, three pages of a
        # 754-record collection with no overlap.
        "pagination_strategy": {"type": "OffsetIncrement", "page_size": PAGE_SIZE},
    }


def _incremental(inc: Incremental) -> dict[str, Any]:
    """One window, not many.

    KiotViet takes a single "modified since" value and no upper bound, so
    slicing the range would issue N requests for the same records. `step` is
    left off for that reason; the cursor is a high-water mark, nothing more.
    """
    return {
        "type": "DatetimeBasedCursor",
        "cursor_field": inc.field,
        "datetime_format": inc.fmt,
        # KiotViet emits fractional seconds on some records and not others.
        "cursor_datetime_formats": [inc.fmt, "%Y-%m-%dT%H:%M:%S.%f",
                                    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"],
        "start_datetime": {
            "type": "MinMaxDatetime",
            "datetime": "{{ config.get('modified_from') or '2000-01-01T00:00:00' }}",
            "datetime_format": inc.fmt,
        },
        "start_time_option": {
            "type": "RequestOption",
            "field_name": inc.param,
            "inject_into": "request_parameter",
        },
    }


def _schema(stream: Stream) -> dict[str, Any]:
    """Open by default, typed where a wrong type would break something."""
    properties: dict[str, Any] = {}
    for key in stream.primary_key:
        properties[key] = {"type": ["null", "string", "integer"]}
    if stream.incremental:
        properties[stream.incremental.field] = {"type": ["null", "string"]}
    for name, kind in stream.fields.items():
        properties.setdefault(name, {"type": ["null", kind]})
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
    }


def _stream_manifest(stream: Stream) -> dict[str, Any]:
    requester: dict[str, Any] = {
        "type": "HttpRequester",
        "url_base": API_BASE,
        "path": stream.path,
        "http_method": "GET",
        # The shop is chosen here. One host serves every retailer, so a missing
        # or wrong `Retailer` reads somebody else's catalogue or nothing at all.
        "request_headers": {"Retailer": "{{ config['retailer'] }}"},
        "authenticator": _authenticator(),
        "error_handler": _error_handler(),
    }
    if stream.params:
        requester["request_parameters"] = dict(stream.params)

    compiled: dict[str, Any] = {
        "type": "DeclarativeStream",
        "name": stream.name,
        "primary_key": list(stream.primary_key),
        "retriever": {
            "type": "SimpleRetriever",
            "requester": requester,
            "record_selector": {
                "type": "RecordSelector",
                "extractor": {"type": "DpathExtractor",
                              "field_path": list(stream.collection)},
            },
            "paginator": _paginator(stream),
        },
        "schema_loader": {"type": "InlineSchemaLoader", "schema": _schema(stream)},
    }
    if stream.incremental:
        compiled["incremental_sync"] = _incremental(stream.incremental)
    return compiled


def connection_specification(connector: KiotVietConnector) -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{connector.title} Spec",
        "required": ["retailer", "client_id", "client_secret"],
        "additionalProperties": True,
        "properties": {
            "retailer": {
                "type": "string",
                "title": "Tên gian hàng",
                "description": (
                    "Phần đứng trước .kiotviet.vn trong địa chỉ cửa hàng. "
                    "Ví dụ với https://taphoaxyz.kiotviet.vn thì điền "
                    "taphoaxyz. Đây là thứ quyết định connector đọc dữ liệu "
                    "của cửa hàng nào."
                ),
                "order": 0,
            },
            "client_id": {
                "type": "string",
                "title": "Client ID",
                "description": (
                    "Lấy trong KiotViet ở mục Thiết lập cửa hàng, phần Kết nối "
                    "API. Không phải tên đăng nhập."
                ),
                "airbyte_secret": True,
                "order": 1,
            },
            "client_secret": {
                "type": "string",
                "title": "Client Secret",
                "description": (
                    "Mã bảo mật đi cùng Client ID. Connector tự đổi hai giá trị "
                    "này lấy access token ở mỗi lần chạy, nên không cần dán "
                    "token vào đây."
                ),
                "airbyte_secret": True,
                "order": 2,
            },
            "modified_from": {
                "type": "string",
                "title": "Chỉ lấy dữ liệu thay đổi từ",
                "description": (
                    "Mốc bắt đầu cho lần chạy đầu tiên, dạng "
                    "2024-01-01T00:00:00. Bỏ trống để lấy toàn bộ lịch sử."
                ),
                "pattern": r"^$|^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
                "order": 3,
            },
        },
    }


def compile_manifest(connector: KiotVietConnector) -> dict[str, Any]:
    """The declarative manifest for one KiotViet connector."""
    connector.validate()
    streams = {s.name: _stream_manifest(s) for s in connector.streams}

    # `check` should confirm the credentials, not read the largest table in the
    # shop. `branches` is the cheapest collection every retailer has.
    checkable = "branch" if "branch" in streams else next(iter(streams))

    return {
        "version": MANIFEST_VERSION,
        "type": "DeclarativeSource",
        "check": {"type": "CheckStream", "stream_names": [checkable]},
        "definitions": {"streams": copy.deepcopy(streams)},
        "streams": [{"$ref": f"#/definitions/streams/{name}"} for name in streams],
        "spec": {
            "type": "Spec",
            "connection_specification": connection_specification(connector),
        },
        # 5,000 GET requests an hour, per the document. Declared so the CDK
        # paces itself rather than learning the cap from a refusal.
        "api_budget": {
            "type": "HTTPAPIBudget",
            "policies": [{
                "type": "MovingWindowCallRatePolicy",
                "rates": [{"type": "Rate", "limit": RATE_LIMIT[0],
                           "interval": RATE_LIMIT[1]}],
                "matchers": [],
            }],
        },
    }
