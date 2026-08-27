"""Base CRM - Leads, the other half of Base CRM and a separate application.

It looks like part of the CRM and behaves like a different product:

* **Its own credentials.** The Sales token is refused here with
  `INVALID TOKEN APPKEY`, on both hosts, with and without the password, while
  the very same token still answers `sales/v1/pipeline/all` with 271 pipelines.
  The published collection calls them `leads_access_token` / `leads_password`
  for that reason, and this connector asks for its own pair.
* **Its own path root.** `apis.basecrm.vn/leads/`, not `.../sales/v1/`.
* **No shared entities.** Lead services, leads and lead feeds do not reference
  a pipeline, a deal, an account or a contact.

So it ships as `source-base-crm-leads` rather than as three more streams on
`source-base-crm`. Bolting it on would force every workspace that only sells to
hold Leads credentials, or make the pair optional and leave three streams that
appear in the schema tab and fail at sync -- the exact silent shape this
codebase spent the day removing.

`time_filter_key` is the interesting one
----------------------------------------

`lead/list` does not have a single "changed since" field. It takes a
`start_time` / `end_time` range plus `time_filter_key` naming *which* timestamp
the range applies to: `since`, `last_update`, `last_update_stage` or
`last_update_status`. The documented default is `since` -- the creation time.

That default loses data, and the loss is measurable. Take a service with 205
leads and a threshold one second above every `since` value in it, so the only
rows that can qualify are ones edited after they were created:

    time_filter_key=since               ->  0 leads
    time_filter_key=last_update         ->  4 leads
    time_filter_key=last_update_stage   ->  4 leads
    time_filter_key=last_update_status  ->  1 lead
    (no key sent)                       ->  0 leads   -- the default is `since`

Every count matched the records counted by hand, so the server applies the key
properly. On the default those four edited leads never come back: an incremental
sync would collect new leads forever and never notice one being renamed,
re-staged or re-qualified. This connector pins `last_update`, sent as an ordinary
body field beside the range.

The other two keys are not offered, and the same measurement is why: each moves
on one kind of change only, while `last_update` covers all of them.

Three streams, and why not more
-------------------------------

    lead_service  ──  lead  ──  lead_feed

`lead/services` lists the services the token can see, exactly as
`account/service/all` does on the Sales side, so nothing here needs a hardcoded
`service_id` -- which is what went wrong in the YAML the Sales connector was
ported from.

`lead/feed/list` takes a lead id and returns that lead's activity. It is the
Leads twin of `deal/get.activities`, and it carries the same cost: one request
per lead, every sync, because a substream iterates every parent id regardless
of the parent's cursor. See `Parent` for why `incremental_dependency` is not
the answer.

`stage_id` on `lead/list` is a filter, not a dimension, and is not sent: the
stream exists to return every lead in a service, and the stage is already on
each record.

Measured against a live tenant
------------------------------

`qa/probe/base_crm_leads.py`, run against a real Leads token. What it changed:

* **`lead/services` returns `services`, not `lead_services`.** The convention
  the other eleven connectors follow -- a collection named after its entity --
  does not hold here, and a connector built on that guess would have read zero
  records while reporting success.
* **`service_id` is required.** Without it `lead/list` answers
  `Invalid service`, which is why the service list is a stream and every lead is
  read through it.
* **Pages are 100 records, fixed, and `limit` is ignored** -- 5, 500 and 1000 all
  returned 100. Paging the 205-lead service gave 100 / 100 / 5 / 0 with no
  repeats, so `page` works and starts at 1 (`page=0` repeats page 1).
  `page_size_field=None` is therefore right, and so is leaving `page_size`
  undeclared: hardcoding the server's 100 would save one request per stream and
  silently truncate every sync the day Base changes it.
* **`lead/feed/list` neither pages nor filters on time.** `page=2` returned the
  same 15 records as page 1, and `start_time`/`end_time`, `stime`/`etime` and
  the pair plus `time_filter_key` each returned all 15. Every feed carries
  `last_update`, so it gets a client-side cursor -- see `_INC_FEED`, and
  `crm.deal_activity` for the same shape and the same reasoning.
* A lead record has no `service_id` and no `stage_id`; the stage is `stage` and
  the status is `status`. The declared field types follow the records, not the
  request parameters.
"""

from __future__ import annotations

from ._shared import BaseConnector, ConfigField, Incremental, Parent, Stream

#: The same two hosts the Sales connector answers on.
DOMAINS = ("basecrm.vn", "base.vn")

#: A closed range plus the field it applies to.
#:
#: `time_filter_key` rides along as a constant body field on the stream rather
#: than living here, because it is not part of the range -- it selects which
#: column the range is compared against, and only `lead/list` has that idea.
_INC = Incremental(field="last_update", param="start_time", end_param="end_time",
                   inject_into="body_data")

#: Track a cursor, filter on the way out, send nothing.
#:
#: `lead/feed/list` documents `lead_id` and nothing else, and measurement agrees:
#: `start_time`/`end_time`, `stime`/`etime`, and the pair plus `time_filter_key`
#: each returned all 15 feeds of a lead. Feeds do carry `last_update` on every
#: record, so `client_side=True` makes the cursor real -- without it the stream
#: would advertise `incremental`, save state, and re-emit every feed forever.
#: That is exactly what `crm.deal_activity` did before it was fixed.
_INC_FEED = Incremental(param="start_time", end_param="end_time",
                        inject_into="body_data", send_request_options=False,
                        client_side=True)

CRM_LEADS = BaseConnector(
    app="crm-leads",
    title="Base CRM - Leads",
    summary="Lead theo từng dịch vụ, kèm dòng hoạt động của mỗi lead. "
            "Dùng token và mật khẩu riêng, không dùng chung với Base CRM - Deals.",
    url_base="https://apis.{domain}/leads/",
    docs_url="https://basecrm-standard.apidocs.rework.site/",
    token_field="access_token",
    domains=DOMAINS,
    # Measured, not read: `lead/feed/list` refused with HTTP 400 and
    # `Quota exceeded: 100 req/min` after roughly a hundred requests.
    rate_limit=(100, "PT1M"),
    config=(
        ConfigField(
            name="password",
            title="Mật khẩu tài khoản",
            description=(
                "Base CRM Leads yêu cầu cả token lẫn mật khẩu tài khoản trong "
                "mỗi lần gọi. Đây là bí mật thứ hai, không phải bước đăng nhập, "
                "và là cặp riêng của Leads — token của Base CRM - Deals bị từ "
                "chối ở đây."
            ),
            required=True, secret=True, send_in_body=True,
        ),
    ),
    streams=(
        Stream(
            # `services`, measured. Not `lead_services`: this endpoint breaks
            # the naming every other Base collection follows, and the guess
            # would have read nothing while reporting success.
            name="lead_service", path="lead/services",
            collection=("services",), paginate=False,
            fields={"name": "string", "last_update": "string"},
            note="Danh mục dịch vụ lead. Là cha của lead — thay cho việc phải "
                 "điền sẵn service_id.",
        ),
        Stream(
            name="lead", path="lead/list", collection=("leads",),
            parent=Parent(stream="lead_service", inject="service_id"),
            incremental=_INC,
            # `time_filter_key` is what turns the range from "created between"
            # into "changed between". Without it the server compares against
            # `since` and an edited lead never comes back.
            body={"time_filter_key": "last_update"},
            first_page=1, page_on_first_request=True,
            # The endpoint documents `page` and no size at all. Declaring one
            # would invent a parameter and, worse, tell the paginator a page
            # length the server never agreed to.
            page_size_field=None,
            # From the records, not from the request parameters: a lead has no
            # `service_id` and no `stage_id`.
            fields={"name": "string", "phone": "string", "email": "string",
                    "stage": "string", "status": "string",
                    "source_id": "string", "owner_id": "string",
                    "last_update": "string"},
            note="Lead theo từng dịch vụ, lọc tăng dần theo last_update ngay "
                 "trên server.",
        ),
        Stream(
            name="lead_feed", path="lead/feed/list", collection=("feeds",),
            parent=Parent(stream="lead", inject="lead_id"),
            incremental=_INC_FEED,
            paginate=False,
            fields={"content": "string", "metatype": "string",
                    "last_update": "string"},
            note="Dòng hoạt động của từng lead. Lọc tăng dần phía client vì "
                 "endpoint không nhận bộ lọc thời gian. Tốn kém: một lượt gọi "
                 "cho mỗi lead ở mỗi lần sync.",
        ),
    ),
)
