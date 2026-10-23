"""Base Sales — pipelines, deals, accounts, contacts and the work stream around them.

An extapis application; `_extapi.py` compiles it. This file only declares it.
Not to be confused with `crm.py` ("Base CRM - Deals"), which reads the older
`apis.<domain>/sales` surface with a token and password in the form body; this
is the Sales Public API on the extapis gateway, with its own key.

Measured on 2026-10-20 against `extapis.base.vn/sales/v1` (workspace "Base Ha
Noi": 6 pipelines, 999 deals in the largest, 501 accounts, 502 contacts, 233
feed entries). `api-v3.base.vn`, which the documentation names, answers
`INVALID_SYS_PATH_4` for this installation.

* Every record carries `created_at` and `updated_at` as epoch-second integers
  (feed entries: `since` and `last_update`). Those are the cursor candidates.
  `closed_at`, `expected_close` and `last_activity_ts` are 0 on almost every
  record and are not offered.
* `pipeline/deals`, `account/list`, `contact/list` filter on
  `last_update_stime` (against `updated_at`) and `since_stime` (against
  `created_at`) -- but only with the closing `*_etime` beside them; a lone
  start bound is ignored and everything comes back.
* `feed/list` filters on `updated_from` (against `last_update`) with the start
  bound alone.
* Page-number paging loses records server-side. Walking 999 deals at 100 a
  page returned 996 distinct deals; at 37 a page, 987. Repeating a call
  returns the same order, and one page of 1,000 returns all 999. The same
  holds for accounts and contacts. So every list reads at the endpoint's
  maximum page size (1,000; feed 200), which keeps a list under that size in
  one page. A list longer than one page can still lose records; reported, not
  fixable from the client.
* `contact/list` returns active contacts only unless sent `status:
  "disabled"`, so it is read once for each. `pipeline/deals` and
  `account/list` return every status by default.
* Shapes below are as measured: `account.address` is an object and
  `account.segments` a map of segment id to value, not a string and a list.

Incremental filters are superset filters for every candidate: a record
created after the mark, or moved to a stage after it, was updated after it
too. So the stored value of whichever cursor the workspace picks is sent as
the update-time bound, and the chosen column is enforced on the records.
Streams whose endpoint has no filter (pipelines, stages, services, segments)
still offer incremental on their real columns, filtered on the client.
"""

from __future__ import annotations

from ._extapi import (
    Before, ExtapiConnector, ExtapiStream, Incremental, PageNumber, Parent, Split, nullable, obj,
)

#: The real timestamp columns of a Sales object.
TIMESTAMPS = ("created_at", "updated_at")

#: The largest page the record lists accept.
LIST_PAGE = PageNumber(size=1000)

# ── shared field definitions ─────────────────────────────────────────────────

_CREATED = nullable("integer", en="When the record was created, Unix seconds.",
                    description_vi="Thời điểm tạo bản ghi, Unix timestamp (giây).")
_UPDATED = nullable("integer", en="When the record was last changed, Unix seconds.",
                    description_vi="Thời điểm bản ghi được cập nhật gần nhất, Unix timestamp (giây).")
_SYSTEM = nullable("integer", en="Id of the Base workspace the record belongs to.",
                   description_vi="Id workspace Base chứa bản ghi.")
_USER_IDS = {"type": "integer"}
_OWNERS = nullable("array", items=_USER_IDS, en="User ids of the owners.",
                   description_vi="Danh sách user id của người sở hữu.")
_FOLLOWERS = nullable("array", items=_USER_IDS, en="User ids of the followers.",
                      description_vi="Danh sách user id của người theo dõi.")
_LABELS = nullable("array", items={"type": "string"}, en="Labels attached to the record.",
                   description_vi="Các nhãn gắn với bản ghi.")
_STATUS = nullable("integer", en="Record status code.", description_vi="Mã trạng thái của bản ghi.")

#: A custom field value as it rides on a deal, account or contact.
#: An array -- except that a record with no custom field answers `{}`, an
#: empty object (499 of 501 accounts measured), so both types are declared.
_FORM = nullable(
    "array", "object",
    en="Custom field values of the record, one element per field.",
    description_vi="Giá trị các trường tùy chỉnh của bản ghi, mỗi phần tử là một trường.",
    items=obj({
        "id": nullable("string", en="Custom field id.", description_vi="Id trường tùy chỉnh."),
        "code": nullable("string", en="Custom field code, stable across renames.",
                         description_vi="Mã trường tùy chỉnh, không đổi khi đổi tên."),
        "name": nullable("string", en="Custom field label.", description_vi="Tên hiển thị của trường."),
        "type": nullable("string", en="Custom field type (text, select, date…).",
                         description_vi="Kiểu trường (text, select, date…)."),
        "value": nullable("string", "number", "integer", "boolean", "array", "object",
                          en="The stored value.", description_vi="Giá trị đã lưu."),
        "display": nullable("string", en="The value as shown in Base.",
                            description_vi="Giá trị như hiển thị trên Base."),
        "vtype": nullable("string", en="Type of the stored value.",
                          description_vi="Kiểu của giá trị đã lưu."),
        "gs_key": nullable("string", en="Global search key of the field.",
                           description_vi="Khóa tìm kiếm toàn cục của trường."),
    }),
)

_SEGMENTS_MAP = nullable(
    "object", "array",
    en="Segments the record belongs to, keyed by segment id.",
    description_vi="Các phân khúc (segment) của bản ghi, theo id segment.",
)

#: What every service-like object (pipeline, account service, contact
#: service) carries that is worth typing.
_OBJECT = {
    "id": nullable("string", en="Id.", description_vi="Id."),
    "name": nullable("string", en="Name.", description_vi="Tên."),
    "code": nullable("string", en="Short code.", description_vi="Mã ngắn."),
    "status": nullable("integer", en="1 active, 0 disabled.", description_vi="1 đang dùng, 0 đã tắt."),
    "system_id": _SYSTEM,
    "user_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
    "owners": _OWNERS,
    "followers": _FOLLOWERS,
    "created_at": _CREATED,
    "updated_at": _UPDATED,
}

_SEGMENT = {
    "id": nullable("string", en="Segment id.", description_vi="Id phân khúc."),
    "name": nullable("string", en="Segment name.", description_vi="Tên phân khúc."),
    "service_id": nullable("string", en="Id of the pipeline or service the segment belongs to.",
                           description_vi="Id pipeline hoặc service chứa phân khúc."),
    "status": nullable("integer", en="1 active, 0 disabled.", description_vi="1 đang dùng, 0 đã tắt."),
    "metatype": nullable("string", en="Kind of segment.", description_vi="Loại phân khúc."),
    "created_at": _CREATED,
    "updated_at": _UPDATED,
}

_CLIENT_SIDE = Incremental(candidates=TIMESTAMPS)

#: Update-time window, both bounds; see the module docstring.
_UPDATED_WINDOW = Incremental(
    candidates=TIMESTAMPS,
    request_param="last_update_stime",
    request_end_param="last_update_etime",
)


def _service_list(name: str, path: str, extract: str) -> ExtapiStream:
    return ExtapiStream(
        name=name, path=path, extract=(extract,), primary_key=("id",),
        incremental=_CLIENT_SIDE, properties=_OBJECT)


def _segments(name: str, path: str, extract: str) -> ExtapiStream:
    return ExtapiStream(
        name=name, path=path, extract=(extract,), primary_key=("id",),
        incremental=_CLIENT_SIDE, properties=_SEGMENT)


SALES = ExtapiConnector(
    app="sales",
    title="Sales",
    summary="Pipelines, stages, deals, accounts, contacts, segments and the work stream from Base Sales.",
    summary_vi="Pipeline, stage, deal, account, contact, segment và luồng công việc từ Base Sales.",
    docs_url="https://rework-crm-public-apis-standard.dev.rework.site/",
    key_help=(
        "The Sales API key (X-API-TOKEN), issued by a workspace admin in "
        "Settings > API keys for the sales application. A key for another "
        "application is refused."
    ),
    key_help_vi=(
        "API key của ứng dụng Sales (X-API-TOKEN), do quản trị viên tạo trong "
        "Settings > API keys cho ứng dụng sales. Key của ứng dụng khác sẽ bị "
        "từ chối."
    ),
    scopes="access to the sales application",
    password=True,
    streams=(
        _service_list("pipeline", "pipeline/all", "pipelines"),
        ExtapiStream(
            name="pipeline_stage",
            path="pipeline/get.stages",
            extract=("stages",),
            primary_key=("id",),
            body={"id": "{{ stream_partition.pipeline_id }}"},
            parent=Parent(stream="pipeline", partition_field="pipeline_id"),
            incremental=_CLIENT_SIDE,
            properties={
                "id": nullable("string", en="Stage id.", description_vi="Id giai đoạn."),
                "pipeline_id": nullable("string", en="Pipeline the stage belongs to.",
                                        description_vi="Pipeline chứa giai đoạn."),
                "name": nullable("string", en="Stage name.", description_vi="Tên giai đoạn."),
                "code": nullable("string", en="Stage code.", description_vi="Mã giai đoạn."),
                "prob": nullable("number", en="Win probability of a deal in this stage, percent.",
                                 description_vi="Xác suất thắng của deal ở giai đoạn này, %."),
                "real_order": nullable("integer", en="Position of the stage in the pipeline.",
                                       description_vi="Thứ tự giai đoạn trong pipeline."),
                "lc_status": nullable("integer", en="Lifecycle status the stage maps to.",
                                      description_vi="Trạng thái vòng đời tương ứng của giai đoạn."),
                "metatype": nullable("string", en="Kind of stage.", description_vi="Loại giai đoạn."),
                "created_at": _CREATED,
                "updated_at": _UPDATED,
            },
        ),
        _segments("pipeline_segment", "segment/pipeline.segments", "pipeline_segments"),
        # The audit trail of each pipeline: who created, moved, won, lost or
        # reassigned which deal, and when. Measured 2026-10-22: at most 200
        # logs a page whatever `limit` says (the documentation says 1,000);
        # older ones by `before`; `stime` filters on `created_at` and works
        # alone. Many logs share a second (141 of 200), hence `Before`'s
        # one-second overlap -- expect a few duplicate rows per page, removed
        # by the primary key under Append + Deduped.
        ExtapiStream(
            name="pipeline_log",
            path="pipeline/get.logs",
            extract=("logs",),
            primary_key=("id",),
            body={"id": "{{ stream_partition.pipeline_id }}"},
            parent=Parent(stream="pipeline", partition_field="pipeline_id"),
            before=Before(field="created_at", param="before", size=200),
            incremental=Incremental(candidates=("created_at",), request_param="stime"),
            properties={
                "id": nullable("string", en="Log entry id.", description_vi="Id dòng nhật ký."),
                "service_id": nullable("string", en="Pipeline the change happened in.",
                                       description_vi="Pipeline nơi thay đổi xảy ra."),
                "service_model": nullable("string", en="Kind of container (sales.pipeline).",
                                          description_vi="Loại đối tượng chứa (sales.pipeline)."),
                "user_id": nullable("integer", en="User id of who made the change.",
                                    description_vi="User id người thực hiện thay đổi."),
                "action": nullable("string", en="What happened, e.g. deal.created, deal.won, deal.owner.updated.",
                                   description_vi="Hành động, ví dụ deal.created, deal.won, deal.owner.updated."),
                "tag": nullable("string", en="Category of the action.", description_vi="Nhóm của hành động."),
                "name": nullable("string", en="Name of the object at the time.",
                                 description_vi="Tên đối tượng tại thời điểm đó."),
                "message": nullable("string", en="Human-readable description of the change (markdown).",
                                    description_vi="Mô tả thay đổi dạng đọc được (markdown)."),
                "obj_id": nullable("string", en="Id of the object changed, usually a deal.",
                                   description_vi="Id đối tượng bị thay đổi, thường là deal."),
                "obj_type": nullable("string", en="Type of that object (deal…).",
                                     description_vi="Loại đối tượng (deal…)."),
                "changes": nullable(
                    "array", en="Field-level changes, one element per field.",
                    description_vi="Thay đổi theo từng trường, mỗi phần tử là một trường.",
                    items=obj({
                        "k": nullable("string", en="Field key.", description_vi="Khóa trường."),
                        "name": nullable("string", en="Field label.", description_vi="Tên trường."),
                        "o": nullable("string", "number", "integer", "boolean", "array", "object",
                                      en="Value before the change.", description_vi="Giá trị trước khi đổi."),
                        "n": nullable("string", "number", "integer", "boolean", "array", "object",
                                      en="Value after the change.", description_vi="Giá trị sau khi đổi."),
                    })),
                "url": nullable("string", en="Path of the object in Base.",
                                description_vi="Đường dẫn đối tượng trên Base."),
                "appkey": nullable("string", en="Base application (sales).", description_vi="Ứng dụng Base (sales)."),
                # Epoch seconds, sent as a numeric string on some logs. Kept a
                # single integer type: a digit string casts to INT64, while a
                # union with string would make the whole column STRING.
                "created_at": nullable("integer", en="When the change happened, Unix seconds.",
                                       description_vi="Thời điểm thay đổi, Unix timestamp (giây)."),
                "system_id": _SYSTEM,
            },
        ),
        ExtapiStream(
            name="deal",
            path="pipeline/deals",
            extract=("deals",),
            primary_key=("id",),
            body={"id": "{{ stream_partition.pipeline_id }}"},
            parent=Parent(stream="pipeline", partition_field="pipeline_id"),
            pages=LIST_PAGE,
            # `stage_start` is real too, and a stage move updates the deal,
            # so the update-time window still covers it.
            incremental=Incremental(
                candidates=(*TIMESTAMPS, "stage_start"),
                request_param="last_update_stime",
                request_end_param="last_update_etime",
            ),
            properties={
                "id": nullable("string", en="Deal id.", description_vi="Id deal."),
                "name": nullable("string", en="Deal name.", description_vi="Tên deal."),
                "pipeline_id": nullable("string", en="Pipeline the deal is in.",
                                        description_vi="Pipeline chứa deal."),
                "stage_id": nullable("string", en="Current stage of the deal.",
                                     description_vi="Giai đoạn hiện tại của deal."),
                "status": nullable("integer", en="1 open, 10 won, -10 lost.",
                                   description_vi="1 đang mở, 10 thắng, -10 thua."),
                "value": nullable("number", en="Deal value in its own currency.",
                                  description_vi="Giá trị deal theo đơn vị tiền của deal."),
                "normalized_value": nullable("number",
                                             en="Deal value converted to the workspace currency.",
                                             description_vi="Giá trị deal quy đổi sang tiền tệ mặc định."),
                "currency_id": nullable("string", en="Currency of the value.",
                                        description_vi="Đơn vị tiền tệ của giá trị."),
                "account_id": nullable("string", en="Account the deal is for.",
                                       description_vi="Khách hàng doanh nghiệp (account) của deal."),
                "contact_id": nullable("string", en="Main contact of the deal.",
                                       description_vi="Người liên hệ chính của deal."),
                "owner_id": nullable("integer", en="User id of the deal owner.",
                                     description_vi="User id người phụ trách deal."),
                "creator_id": nullable("integer", en="User id of who created the deal.",
                                       description_vi="User id người tạo deal."),
                "owners": _OWNERS,
                "followers": _FOLLOWERS,
                "collaborators": nullable(
                    "array", en="People collaborating on the deal.",
                    description_vi="Những người cùng phối hợp trên deal.",
                    items=obj({"id": nullable("integer", en="User id.", description_vi="User id.")})),
                "labels": _LABELS,
                "segments": _SEGMENTS_MAP,
                "form": _FORM,
                "cached_moves": nullable(
                    "array", en="Stage moves of the deal, newest kept by Base.",
                    description_vi="Lịch sử chuyển giai đoạn của deal.",
                    items=obj({
                        "id": nullable("string", en="Move id.", description_vi="Id lần chuyển."),
                        "from_id": nullable("string", en="Stage moved from.",
                                            description_vi="Giai đoạn chuyển đi."),
                        "to_id": nullable("string", en="Stage moved to.",
                                          description_vi="Giai đoạn chuyển đến."),
                        "since": nullable("integer", en="When the move happened, Unix seconds.",
                                          description_vi="Thời điểm chuyển, Unix timestamp (giây)."),
                        "content": nullable("string", en="Note left with the move.",
                                            description_vi="Ghi chú kèm lần chuyển."),
                        "metatype": nullable("string", en="Kind of move.", description_vi="Loại chuyển."),
                    })),
                "created_at": _CREATED,
                "updated_at": _UPDATED,
                "stage_start": nullable("integer", en="When the deal entered its current stage, Unix seconds.",
                                        description_vi="Thời điểm deal vào giai đoạn hiện tại, Unix timestamp (giây)."),
                "closed_at": nullable("integer", en="When the deal was won or lost, Unix seconds; 0 while open.",
                                      description_vi="Thời điểm deal thắng/thua, Unix timestamp (giây); 0 khi còn mở."),
                "expected_close": nullable("integer", en="Expected closing date, Unix seconds; 0 if unset.",
                                           description_vi="Ngày dự kiến chốt, Unix timestamp (giây); 0 nếu chưa đặt."),
                "last_activity_ts": nullable("integer", en="Time of the last activity on the deal, Unix seconds.",
                                             description_vi="Thời điểm hoạt động gần nhất trên deal, Unix timestamp (giây)."),
                "system_id": _SYSTEM,
            },
        ),
        _service_list("account_service", "account/service/all", "account_services"),
        _segments("account_segment", "segment/account.segments", "account_segments"),
        ExtapiStream(
            name="account",
            path="account/list",
            extract=("accounts",),
            primary_key=("id",),
            body={"service_id": "{{ stream_partition.service_id }}"},
            parent=Parent(stream="account_service", partition_field="service_id"),
            pages=LIST_PAGE,
            incremental=_UPDATED_WINDOW,
            properties={
                "id": nullable("string", en="Account id.", description_vi="Id account."),
                "name": nullable("string", en="Account (company) name.", description_vi="Tên account (doanh nghiệp)."),
                "service_id": nullable("string", en="Account service the account belongs to.",
                                       description_vi="Account service chứa account."),
                "stage_id": nullable("string", en="Current stage of the account.",
                                     description_vi="Giai đoạn hiện tại của account."),
                "status": _STATUS,
                "domain": nullable("string", en="Website domain of the company.",
                                   description_vi="Tên miền website của doanh nghiệp."),
                "address": nullable(
                    "object", en="Postal address.",
                    description_vi="Địa chỉ.",
                    properties={
                        "display": nullable("string", en="Address as one line.", description_vi="Địa chỉ đầy đủ một dòng."),
                        "l1": nullable("string", en="Address line 1.", description_vi="Dòng địa chỉ 1."),
                        "l2": nullable("string", en="Address line 2.", description_vi="Dòng địa chỉ 2."),
                        "l3": nullable("string", en="Address line 3.", description_vi="Dòng địa chỉ 3."),
                        "state": nullable("string", en="State or province.", description_vi="Tỉnh/thành."),
                        "country": nullable("string", en="Country.", description_vi="Quốc gia."),
                        "postal_code": nullable("string", en="Postal code.", description_vi="Mã bưu chính."),
                        "lat": nullable("number", en="Latitude.", description_vi="Vĩ độ."),
                        "lng": nullable("number", en="Longitude.", description_vi="Kinh độ."),
                    }),
                "value": nullable("number", en="Account value.", description_vi="Giá trị account."),
                "currency_id": nullable("string", en="Currency of the value.", description_vi="Đơn vị tiền tệ."),
                "owner_id": nullable("integer", en="User id of the account owner.",
                                     description_vi="User id người phụ trách account."),
                "creator_id": nullable("integer", en="User id of who created the account.",
                                       description_vi="User id người tạo account."),
                "owners": _OWNERS,
                "followers": _FOLLOWERS,
                "contact_ids": nullable("array", items={"type": "string"},
                                        en="Ids of the contacts at this account.",
                                        description_vi="Id các contact thuộc account."),
                "labels": _LABELS,
                "segments": _SEGMENTS_MAP,
                "form": _FORM,
                "created_at": _CREATED,
                "updated_at": _UPDATED,
                "last_activity_ts": nullable("integer", en="Time of the last activity, Unix seconds.",
                                             description_vi="Thời điểm hoạt động gần nhất, Unix timestamp (giây)."),
                "system_id": _SYSTEM,
            },
        ),
        _service_list("contact_service", "contact/service/all", "contact_services"),
        _segments("contact_segment", "segment/contact.segments", "contact_segments"),
        ExtapiStream(
            name="contact",
            path="contact/list",
            extract=("contacts",),
            primary_key=("id",),
            body={"service_id": "{{ stream_partition.service_id }}"},
            parent=Parent(stream="contact_service", partition_field="service_id"),
            # Active contacts unless asked otherwise; read both.
            split=Split(field="status", values=("active", "disabled")),
            pages=LIST_PAGE,
            incremental=_UPDATED_WINDOW,
            properties={
                "id": nullable("string", en="Contact id.", description_vi="Id contact."),
                "name": nullable("string", en="Full name.", description_vi="Họ tên đầy đủ."),
                "first_name": nullable("string", en="First name.", description_vi="Tên."),
                "last_name": nullable("string", en="Last name.", description_vi="Họ."),
                "prefix": nullable("string", en="Salutation (Mr, Ms…).", description_vi="Danh xưng (Anh, Chị…)."),
                "title": nullable("string", en="Job title.", description_vi="Chức danh."),
                "email": nullable("string", en="Primary email.", description_vi="Email chính."),
                "emails": nullable(
                    "array", en="All email addresses.",
                    description_vi="Tất cả email.",
                    items=obj({
                        "email": nullable("string", en="Email address.", description_vi="Địa chỉ email."),
                        "type": nullable("string", en="Kind of address (work, home…).",
                                         description_vi="Loại email (công việc, cá nhân…)."),
                    })),
                "phone": nullable("string", en="Primary phone number.", description_vi="Số điện thoại chính."),
                "phones": nullable(
                    "array", en="All phone numbers.",
                    description_vi="Tất cả số điện thoại.",
                    items=obj({
                        "phone": nullable("string", en="Phone number.", description_vi="Số điện thoại."),
                        "type": nullable("string", en="Kind of number (mobile, work…).",
                                         description_vi="Loại số (di động, công việc…)."),
                    })),
                "service_id": nullable("string", en="Contact service the contact belongs to.",
                                       description_vi="Contact service chứa contact."),
                "stage_id": nullable("string", en="Current stage of the contact.",
                                     description_vi="Giai đoạn hiện tại của contact."),
                "account_id": nullable("string", en="Account the contact works at.",
                                       description_vi="Account mà contact thuộc về."),
                "status": nullable("integer", en="1 active, 0 or -1 disabled.",
                                   description_vi="1 đang hoạt động, 0 hoặc -1 đã chặn."),
                "owner_id": nullable("integer", en="User id of the contact owner.",
                                     description_vi="User id người phụ trách contact."),
                "creator_id": nullable("integer", en="User id of who created the contact.",
                                       description_vi="User id người tạo contact."),
                "owners": _OWNERS,
                "followers": _FOLLOWERS,
                "labels": _LABELS,
                "segments": _SEGMENTS_MAP,
                "form": _FORM,
                "created_at": _CREATED,
                "updated_at": _UPDATED,
                "last_activity_ts": nullable("integer", en="Time of the last activity, Unix seconds.",
                                             description_vi="Thời điểm hoạt động gần nhất, Unix timestamp (giây)."),
                "system_id": _SYSTEM,
            },
        ),
        ExtapiStream(
            name="feed",
            path="feed/list",
            extract=("feeds",),
            primary_key=("id",),
            pages=PageNumber(size=200),
            incremental=Incremental(
                candidates=("since", "last_update"),
                request_param="updated_from",
            ),
            properties={
                "id": nullable("string", en="Feed entry id.", description_vi="Id hoạt động."),
                "name": nullable("string", en="Title of the entry.", description_vi="Tiêu đề hoạt động."),
                "content": nullable("string", en="Body of the entry.", description_vi="Nội dung hoạt động."),
                "metatype": nullable("string", en="Kind of entry (note, call, meeting, task…).",
                                     description_vi="Loại hoạt động (ghi chú, cuộc gọi, cuộc họp, công việc…)."),
                "model": nullable("string", en="Object type the entry is about.",
                                  description_vi="Loại đối tượng mà hoạt động gắn với."),
                "service_id": nullable("string", en="Pipeline or service of that object.",
                                       description_vi="Pipeline hoặc service của đối tượng."),
                "source_id": nullable("string", en="Id of the deal, account or contact.",
                                      description_vi="Id deal, account hoặc contact liên quan."),
                "user_id": nullable("integer", en="User id of the author.", description_vi="User id người tạo."),
                "status": _STATUS,
                "followers": _FOLLOWERS,
                "files": nullable(
                    "array", en="Attached files.",
                    description_vi="Tệp đính kèm.",
                    items=obj({
                        "name": nullable("string", en="Stored file name.", description_vi="Tên tệp lưu trữ."),
                        "original_name": nullable("string", en="File name as uploaded.",
                                                  description_vi="Tên tệp gốc khi tải lên."),
                        "size": nullable("integer", en="Size in bytes.", description_vi="Dung lượng (byte)."),
                        "mime_type": nullable("string", en="MIME type.", description_vi="Kiểu MIME."),
                        "path": nullable("string", en="Storage path.", description_vi="Đường dẫn lưu trữ."),
                        "secured_preview_url": nullable("string", en="Signed preview link; expires.",
                                                        description_vi="Link xem trước có chữ ký, có hạn dùng."),
                        "secured_download_url": nullable("string", en="Signed download link; expires.",
                                                         description_vi="Link tải có chữ ký, có hạn dùng."),
                    })),
                "since": nullable("integer", en="When the work happened, Unix seconds.",
                                  description_vi="Thời điểm diễn ra hoạt động, Unix timestamp (giây)."),
                "last_update": nullable("integer", en="When the entry was last changed, Unix seconds.",
                                        description_vi="Thời điểm hoạt động được cập nhật gần nhất, Unix timestamp (giây)."),
            },
        ),
    ),
)
