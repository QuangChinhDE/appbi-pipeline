"""Base CRM - Deals, which speaks a different dialect from every other app.

Ported from the reviewed `base_crm_sale` contract and the published Postman
collection, then checked endpoint by endpoint against the live API. Every
claim below is a measurement, not a reading of the YAML -- which turned out to
be wrong in several places.

Four dialect differences
------------------------

* **The token field is `access_token`,** not `access_token_v2`; the other name
  is refused.
* **A password travels with every request.** Base CRM authenticates with a
  token *and* an account password on each call. A second secret, not a login.
* **The host is shared, not per-app.** Everything else lives at
  `<app>.base.com.vn`; the CRM is `apis.basecrm.vn/sales/v1/`, also answering
  as `apis.base.vn` -- the same backend under two names. `basecrmdemo.base.vn`
  is the web UI and returns an HTML login page.
* **Paging starts at 1 and must be sent on the first request.** Starting at 0
  returns page one twice.

Incremental works server-side, but only on a closed range
---------------------------------------------------------

`last_update_stime` / `last_update_etime` on `pipeline/deals`, `account/list`
and `contact/list` are applied -- **when both bounds arrive together**. A lone
`last_update_stime` set to the year 2030 returns all 65 deals of a pipeline;
the same value paired with an `etime` returns 0. That single-parameter probe is
what made these endpoints look like they ignored the filter, and the mistake
was visible in the runs: the streams reduced 5,583 -> 263, 957 -> 36 and
1,317 -> 27 on the second sync while the connector had no client-side filtering
compiled in at all. Server-side was the only thing that could have done it.

`pipeline/get.logs` is the same shape under different names, `stime`/`etime`.
Its timestamp field is `since`, not `last_update`, and its records carry no
`last_update` at all -- so the cursor field differs from every other stream.

`deal/get.activities` is the one true exception: no time filter under any name,
paired or not. It keeps a cursor, sends nothing, and filters on the way out.
See `_INC_FEED`.

What the YAML got wrong, and what is left out
---------------------------------------------

The source YAML hardcoded one tenant's ids -- `service_id: '248'` for contacts
and `['680', '211']` for accounts -- which fail for anybody else with
`INVALID_CONTACT_SERVICE`. They are not needed: `account/service/all` and
`contact/service/all` list them from the token alone (29 and 22 here), so the
services are a stream and everything under them is a substream.

Three documented endpoints are deliberately not shipped:

* `pipeline/get.stages` returns exactly what `pipeline.cached_stages` already
  carries. Compared field by field across twenty pipelines: the only
  difference is `token`, which is a per-response value and not data. A
  separate stream would be 270 extra requests per sync for rows the reader
  already has.
* `pipeline/get.segments` is the same story against `pipeline.cached_segments`
  -- identical on all fifteen pipelines checked, once `token` is ignored.
* `account/get.activities` and `contact/get.activities` answer with the plain
  string `Function 1 is deprecated`. Not JSON, not an error code: retired.

`deal_activity` replaces the webhook
------------------------------------

The YAML routed deal feeds through `https://n8n.base-datateam.com/webhook/...`
with a hardcoded `user_id`, because the substream could not be expressed. It
can: `deal/get.activities` takes a deal id and returns `feeds`, and `user_id`
turns out to be optional -- the same six records come back with and without
it. So the third-party hop is gone.

It is the expensive stream. One request per deal, measured at 0.35s, and this
tenant has 5,582 deals: roughly half an hour of API calls per sync, every
sync, because a substream iterates every parent id regardless of the parent's
cursor (see `Parent`). Enabling `incremental_dependency` would cut that
twentyfold and is **not safe here**: of 121 deals carrying feeds, 46 have a
feed newer than the deal itself, by up to two days. Those deals would drop out
of the partition list and their newest activity would stop arriving. Leave the
stream selectable and let a workspace decide whether it wants the cost.
"""

from __future__ import annotations

from ._shared import BaseConnector, ConfigField, Incremental, Parent, Stream

#: Both hosts answer identically; the first is the default.
DOMAINS = ("basecrm.vn", "base.vn")

#: A closed range in the body, like Income and unlike the older applications.
#: Both bounds are required for the server to filter at all -- see the module
#: docstring.
_INC = Incremental(param="last_update_stime", end_param="last_update_etime",
                   inject_into="body_data")

#: Same server-side filtering, different parameter names and a different field.
#:
#: `pipeline/get.logs` takes `stime`/`etime` rather than the `last_update_*`
#: pair, and its records carry no `last_update` at all -- the timestamp is
#: `since`, epoch seconds -- so the cursor field differs from every other
#: stream here. It matters most of any stream: an audit log only ever grows,
#: and this one dropped from 1,389 rows to 0 on an unchanged second sync.
_INC_LOG = Incremental(field="since", param="stime", end_param="etime",
                       inject_into="body_data")

#: Track a cursor, filter on the way out, send nothing.
#:
#: `deal/get.activities` documents exactly two parameters, `id` and `user_id`.
#: It has no time filter: `last_update_stime`, `stime` and `since_stime` were
#: each probed alone and as a closed pair, and all six probes returned the full
#: 16 records. Sending an invented parameter name would be noise now and a trap
#: later, so nothing is sent.
#:
#: `client_side=True` is what makes the cursor mean something anyway. Without
#: it a declared cursor is a label with nothing behind it -- the stream saved
#: 5,583 partitions of state and re-emitted the identical 3,970 rows on the
#: next sync, because the CDK only filters records when this flag is set.
_INC_FEED = Incremental(param="last_update_stime", end_param="last_update_etime",
                        inject_into="body_data", send_request_options=False,
                        client_side=True)

CRM = BaseConnector(
    app="crm",
    # Named for the half of Base CRM it covers. Leads is a separate
    # application with its own token, its own password and its own path root,
    # and ships as `source-base-crm-leads`. The key stays `source-base-crm`:
    # renaming it would orphan every source already built on it.
    title="Base CRM - Deals",
    summary="Pipeline bán hàng, deal, khách hàng và liên hệ theo từng dịch "
            "vụ. Phần Lead nằm ở connector Base CRM - Leads.",
    url_base="https://apis.{domain}/sales/v1/",
    docs_url="https://basecrm-standard.apidocs.rework.site/",
    token_field="access_token",
    # The reviewed contract is filed as `base_crm_sale.yaml`.
    schema_app="crm_sale",
    domains=DOMAINS,
    config=(
        ConfigField(
            name="password",
            title="Mật khẩu tài khoản",
            description=(
                "Base CRM yêu cầu cả token lẫn mật khẩu tài khoản trong mỗi "
                "lần gọi. Đây là bí mật thứ hai, không phải bước đăng nhập."
            ),
            required=True, secret=True, send_in_body=True,
        ),
    ),
    streams=(
        # ── roots ────────────────────────────────────────────────────────
        Stream(
            # `pipeline/all` ignores `page`/`limit` and every time filter: it
            # returned all 270 records for this tenant whatever it was sent.
            # Declaring pagination here would describe a filter the server does
            # not apply, which reads as a product bug the first time somebody
            # counts rows.
            name="pipeline", path="pipeline/all", collection=("pipelines",),
            paginate=False,
            fields={"name": "string", "last_update": "string"},
            note="Mọi pipeline bán hàng, kèm sẵn stages và segments trong "
                 "cached_stages / cached_segments.",
        ),
        Stream(
            name="account_service", path="account/service/all",
            collection=("account_services",), paginate=False,
            fields={"name": "string"},
            note="Danh mục dịch vụ khách hàng. Là cha của account và "
                 "account_segment -- thay cho service_id hardcode.",
        ),
        Stream(
            name="contact_service", path="contact/service/all",
            collection=("contact_services",), paginate=False,
            fields={"name": "string"},
            note="Danh mục dịch vụ liên hệ. Là cha của contact và "
                 "contact_segment.",
        ),

        # ── under a pipeline ─────────────────────────────────────────────
        Stream(
            name="deal", path="pipeline/deals", collection=("deals",),
            parent=Parent(stream="pipeline", inject="id"),
            incremental=_INC, page_size=1000,
            first_page=1, page_on_first_request=True,
            fields={"name": "string", "account_id": "string",
                    "contact_id": "string", "value": "number",
                    "stage_id": "string", "status": "string",
                    "last_update": "string"},
            note="Deal theo từng pipeline.",
        ),
        Stream(
            name="pipeline_log", path="pipeline/get.logs", collection=("logs",),
            parent=Parent(stream="pipeline", inject="id"),
            incremental=_INC_LOG,
            page_size=1000, first_page=1, page_on_first_request=True,
            fields={"action": "string", "user_id": "string", "since": "integer"},
            note="Nhật ký thay đổi của pipeline: ai đổi gì, lúc nào. Lọc tăng "
                 "dần theo `since` ngay trên server -- stream duy nhất ở CRM "
                 "mà incremental tiết kiệm được lượt gọi API.",
        ),

        # ── under a deal ─────────────────────────────────────────────────
        Stream(
            # A grandchild: deal is itself a substream of pipeline. `user_id`
            # is documented but optional -- verified, same records without it
            # -- so it is not asked of the workspace.
            name="deal_activity", path="deal/get.activities",
            collection=("feeds",),
            parent=Parent(stream="deal", inject="id"),
            incremental=_INC_FEED,
            paginate=False,
            fields={"content": "string", "metatype": "string",
                    "last_update": "string"},
            note="Dòng hoạt động của từng deal. Tốn kém: một lượt gọi cho mỗi "
                 "deal, khoảng 30 phút mỗi lần sync ở quy mô 5.500 deal.",
        ),

        # ── under an account service ─────────────────────────────────────
        Stream(
            name="account", path="account/list", collection=("accounts",),
            parent=Parent(stream="account_service", inject="service_id"),
            incremental=_INC, page_size=1000,
            first_page=1, page_on_first_request=True,
            fields={"name": "string", "service_id": "string",
                    "status": "string", "last_update": "string"},
            note="Khách hàng theo từng dịch vụ. service_id là bắt buộc, dù "
                 "tài liệu ghi optional.",
        ),
        Stream(
            name="account_segment", path="account/service/get.segments",
            collection=("account_segments",),
            parent=Parent(stream="account_service", inject="service_id"),
            paginate=False,
            fields={"name": "string", "service_id": "string"},
            note="Phân khúc trong một dịch vụ khách hàng.",
        ),

        # ── under a contact service ──────────────────────────────────────
        Stream(
            name="contact", path="contact/list", collection=("contacts",),
            parent=Parent(stream="contact_service", inject="service_id"),
            incremental=_INC, page_size=1000,
            first_page=1, page_on_first_request=True,
            fields={"name": "string", "service_id": "string",
                    "last_update": "string"},
            note="Liên hệ theo từng dịch vụ.",
        ),
        Stream(
            name="contact_segment", path="contact/service/get.segments",
            collection=("contact_segments",),
            parent=Parent(stream="contact_service", inject="service_id"),
            paginate=False,
            fields={"name": "string", "service_id": "string"},
            note="Phân khúc trong một dịch vụ liên hệ.",
        ),
    ),
)
