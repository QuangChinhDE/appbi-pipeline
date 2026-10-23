"""Base Lead — lead services, their fields, stages and sources, leads and lead activity.

An extapis application; `_extapi.py` compiles it. This file only declares it.
Not `crm_leads.py` ("Base CRM - Leads"), which reads the legacy
`apis.<domain>/leads` surface with a token and password in the body; the Lead
Public API is a separate surface with its own key (documented: a token issued
for `leads` is not accepted here).

Measured on 2026-10-22 against `extapis.base.vn/lead/v1` (workspace "Base Ha
Noi", acting user `mytest01`: 3 services, 126 leads, 46 feed entries).

* `service/all` returns `leads`-type services only unless sent
  `metatype=events` (one more service here), so it is read once for each.
  Each service already embeds `fields`, `system_fields`, `stages` and
  `statuses`; `service/get.fields` and `service/get.stages` returned the same
  ids. They are separate streams anyway, one row per definition.
* `lead/list` pages by `page` / `limit` (at most 200) and **loses records
  across pages**, as Sales does: 111 leads walked 7 a page gave 103 distinct
  (99 when sorted by update), while one page of 200 held all 111. So it reads
  200 a page; a service with more than 200 leads can still lose some.
* `lead/list` filters on `updated_from`, `created_from`, `stage_updated_from`
  and `status_updated_from`, each bound alone, each against its own column.
  `updated_at` was never below `created_at`, `last_update_stage` or
  `last_update_status` on any of 126 leads, so `updated_from` is a superset
  filter for every cursor candidate and is the one sent.
* `lead/feed/list` is one call per lead, pages by `next_cursor` =
  `{before_ts, before_id}` (both sent back), at most 100 a page. An empty
  cursor is accepted on the first page.
* `lead/get` and `service/get` return exactly the list endpoints' fields.

Not streams: `service/get.labels`, `service/get.segments` and
`service/get.statuses` returned nothing on any service, so neither key nor
shape could be measured.
"""

from __future__ import annotations

from ._extapi import (
    BodyField, ExtapiConnector, ExtapiStream, Incremental, Keyset, PageNumber, Parent, Split,
    nullable, obj,
)

PAGE = PageNumber(size=200)
TIMESTAMPS = ("created_at", "updated_at")

_CREATED = nullable("integer", en="When it was created, Unix seconds.",
                    description_vi="Thời điểm tạo, Unix timestamp (giây).")
_UPDATED = nullable("integer", en="When it was last changed, Unix seconds.",
                    description_vi="Thời điểm cập nhật gần nhất, Unix timestamp (giây).")
_SYSTEM = nullable("integer", en="Base workspace id.", description_vi="Id workspace Base.")

_FIELD_DEF = {
    "service_id": nullable("string", en="Service the field belongs to.", description_vi="Service chứa trường."),
    "id": nullable("string", en="Field id.", description_vi="Id trường."),
    "name": nullable("string", en="Field label.", description_vi="Tên hiển thị của trường."),
    "type": nullable("string", en="Field type.", description_vi="Kiểu trường."),
    "vtype": nullable("string", en="Type of the stored value.", description_vi="Kiểu giá trị lưu."),
    "group_id": nullable("string", en="Group of fields it is shown in.",
                         description_vi="Nhóm trường mà trường được hiển thị."),
    "content": nullable("string", en="Description of the field.", description_vi="Mô tả trường."),
    "readonly": nullable("integer", "boolean", en="1 when the field cannot be edited.",
                         description_vi="1 nếu trường không sửa được."),
    "gs_key": nullable("string", en="Global search key of the field.",
                       description_vi="Khóa tìm kiếm toàn cục của trường."),
    "attrs": nullable("object", en="Display and validation flags of the field.",
                      description_vi="Các cờ hiển thị và kiểm tra của trường."),
}

_PER_SERVICE = Parent(stream="service", partition_field="service_id", stamp=True)

LEAD = ExtapiConnector(
    app="lead",
    title="Lead",
    summary="Lead services with their fields, stages and sources, leads and lead activity from Base Lead.",
    summary_vi="Service lead cùng trường, giai đoạn, nguồn; lead và hoạt động của lead từ Base Lead.",
    docs_url="https://rework-crm-public-apis-standard.dev.rework.site/category/lead",
    key_help=(
        "The Lead API key (X-API-TOKEN), issued by a workspace admin in "
        "Settings > API keys for the lead application. A key for another "
        "application, including the legacy leads key, is refused."
    ),
    key_help_vi=(
        "API key của ứng dụng Lead (X-API-TOKEN), do quản trị viên tạo trong "
        "Settings > API keys cho ứng dụng lead. Key của ứng dụng khác, kể cả "
        "key leads cũ, sẽ bị từ chối."
    ),
    scopes="access to the lead application",
    password=True,
    body_fields=(
        # Documented as the acting user: required by service/get.fields and
        # service/get.stages, and the person whose view of mail entries
        # `lead/feed/list` returns. Either form works (measured: mytest01).
        BodyField(
            name="user_id",
            title="Read as user (id or username)",
            title_vi="Đọc dữ liệu với quyền của user (id hoặc username)",
            description=(
                "The Base user the connector reads as: numeric user id or "
                "username, without @. Field and stage definitions require it, "
                "and lead activity includes this user's mail entries. The user "
                "must be active in this workspace."
            ),
            description_vi=(
                "User Base mà connector dùng để đọc: user id dạng số hoặc "
                "username (không có @). Bắt buộc để đọc định nghĩa trường và "
                "giai đoạn; hoạt động của lead sẽ gồm cả email của user này. "
                "User phải đang hoạt động trong workspace."
            ),
        ),
    ),
    streams=(
        ExtapiStream(
            name="service",
            path="service/all",
            extract=("services",),
            primary_key=("id",),
            split=Split(field="metatype", values=("leads", "events")),
            pages=PAGE,
            incremental=Incremental(candidates=TIMESTAMPS),
            properties={
                "id": nullable("string", en="Lead service id.", description_vi="Id service lead."),
                "name": nullable("string", en="Service name.", description_vi="Tên service."),
                "code": nullable("string", en="Service code.", description_vi="Mã service."),
                "content": nullable("string", en="Description.", description_vi="Mô tả."),
                "metatype": nullable("string", en="Kind of service: coredata.leads or coredata.events.",
                                     description_vi="Loại service: coredata.leads hoặc coredata.events."),
                "model": nullable("string", en="Object model.", description_vi="Mô hình đối tượng."),
                "status": nullable("integer", en="1 active.", description_vi="1 đang hoạt động."),
                "fields": nullable("array", en="Custom field definitions (also stream service_field).",
                                   description_vi="Định nghĩa trường tùy chỉnh (cũng có ở stream service_field)."),
                "system_fields": nullable("array", en="Built-in field definitions (also stream service_system_field).",
                                          description_vi="Định nghĩa trường hệ thống (cũng có ở stream service_system_field)."),
                "internal_fields": nullable("array", en="Internal field definitions.",
                                            description_vi="Định nghĩa trường nội bộ."),
                "stages": nullable("array", en="Stages (also stream service_stage).",
                                   description_vi="Các giai đoạn (cũng có ở stream service_stage)."),
                "statuses": nullable("array", en="Lead statuses of the service.",
                                     description_vi="Các trạng thái lead của service."),
                "system_id": _SYSTEM,
                "created_at": _CREATED,
                "updated_at": _UPDATED,
            },
        ),
        ExtapiStream(
            name="service_field",
            path="service/get.fields",
            extract=("fields",),
            primary_key=("service_id", "id"),
            body={"id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties=_FIELD_DEF,
        ),
        ExtapiStream(
            name="service_system_field",
            path="service/get.fields",
            extract=("system_fields",),
            primary_key=("service_id", "id"),
            body={"id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties=_FIELD_DEF,
        ),
        ExtapiStream(
            name="service_stage",
            path="service/get.stages",
            extract=("stages",),
            primary_key=("service_id", "id"),
            body={"id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties={
                "service_id": nullable("string", en="Service the stage belongs to.",
                                       description_vi="Service chứa giai đoạn."),
                "id": nullable("string", en="Stage id.", description_vi="Id giai đoạn."),
                "name": nullable("string", en="Stage name.", description_vi="Tên giai đoạn."),
                "code": nullable("string", en="Stage code.", description_vi="Mã giai đoạn."),
                "icon": nullable("string", en="Icon name.", description_vi="Tên biểu tượng."),
                "color": nullable("string", en="Display colour.", description_vi="Màu hiển thị."),
                "sort_order": nullable("integer", en="Position of the stage.", description_vi="Thứ tự giai đoạn."),
                "lc_status": nullable("integer", en="Lifecycle the stage maps to.",
                                      description_vi="Trạng thái vòng đời tương ứng."),
            },
        ),
        ExtapiStream(
            name="service_source",
            path="service/get.sources",
            extract=("sources",),
            primary_key=("service_id", "id"),
            body={"id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            pages=PAGE,
            properties={
                "service_id": nullable("string", en="Service the source feeds.", description_vi="Service nhận lead từ nguồn."),
                "id": nullable("string", en="Source id.", description_vi="Id nguồn."),
                "name": nullable("string", en="Source name (form, ad account…).",
                                 description_vi="Tên nguồn (form, tài khoản quảng cáo…)."),
                "metatype": nullable("string", en="Kind of source (google, facebook, form…).",
                                     description_vi="Loại nguồn (google, facebook, form…)."),
                "status": nullable("integer", en="1 active.", description_vi="1 đang hoạt động."),
            },
        ),
        ExtapiStream(
            name="lead",
            path="lead/list",
            extract=("leads",),
            primary_key=("id",),
            body={"service_id": "{{ stream_partition.service_id }}"},
            parent=Parent(stream="service", partition_field="service_id"),
            pages=PAGE,
            incremental=Incremental(
                candidates=(*TIMESTAMPS, "last_update_stage", "last_update_status"),
                request_param="updated_from",
            ),
            properties={
                "id": nullable("string", en="Lead id.", description_vi="Id lead."),
                "gid": nullable("string", en="Global id (lead:<id>).", description_vi="Id toàn cục (lead:<id>)."),
                "service_id": nullable("string", en="Service the lead belongs to.", description_vi="Service chứa lead."),
                "source_id": nullable("string", en="Source the lead came from.", description_vi="Nguồn tạo ra lead."),
                "name": nullable("string", en="Lead name.", description_vi="Tên lead."),
                "prefix": nullable("string", en="Salutation.", description_vi="Danh xưng."),
                "title": nullable("string", en="Job title.", description_vi="Chức danh."),
                "company": nullable("string", en="Company name.", description_vi="Tên công ty."),
                "email": nullable("string", en="Email address.", description_vi="Email."),
                "phone": nullable("string", en="Phone number.", description_vi="Số điện thoại."),
                "stage_id": nullable("string", en="Current stage.", description_vi="Giai đoạn hiện tại."),
                "stage": nullable("string", en="Current stage name.", description_vi="Tên giai đoạn hiện tại."),
                # A stage id, or 0 when there was none (125 of 126 measured).
                "old_stage": nullable("string", "integer", en="Previous stage id; 0 if none.",
                                      description_vi="Id giai đoạn trước đó; 0 nếu không có."),
                "status": nullable("string", "integer", en="Lead status.", description_vi="Trạng thái lead."),
                "old_status": nullable("string", "integer", en="Previous status.", description_vi="Trạng thái trước đó."),
                "owner_id": nullable("integer", en="User id of the owner.", description_vi="User id người phụ trách."),
                "salesman_id": nullable("integer", en="User id of the salesperson.", description_vi="User id nhân viên bán hàng."),
                "user_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
                "followers": nullable("array", items={"type": "integer"}, en="User ids of the followers.",
                                      description_vi="User id của người theo dõi."),
                "collaborators": nullable("array", en="People collaborating on the lead.",
                                          description_vi="Những người cùng phối hợp trên lead."),
                "labels": nullable("array", en="Labels.", description_vi="Nhãn."),
                # `{}` on every lead measured, not a list.
                "segments": nullable("object", "array", en="Segments, keyed by segment id.",
                                     description_vi="Phân khúc, theo id phân khúc."),
                "campaign_ids": nullable("array", en="Campaigns the lead came from.", description_vi="Chiến dịch tạo ra lead."),
                "is_blacklisted": nullable("integer", "boolean", en="1 when the lead is on the blocklist.",
                                           description_vi="1 nếu lead nằm trong danh sách chặn."),
                "is_mql": nullable("integer", "boolean", en="1 when marketing-qualified.",
                                   description_vi="1 nếu lead đạt chuẩn marketing (MQL)."),
                "score": nullable("number", en="Lead score.", description_vi="Điểm lead."),
                "event_type": nullable("string", en="How the lead was captured.", description_vi="Cách lead được ghi nhận."),
                "ext_id": nullable("string", en="Id in the originating system.", description_vi="Id ở hệ thống nguồn."),
                "profile_id": nullable("string", en="Customer profile id.", description_vi="Id hồ sơ khách hàng."),
                "metatype": nullable("string", en="Kind of record.", description_vi="Loại bản ghi."),
                "model": nullable("string", en="Object model.", description_vi="Mô hình đối tượng."),
                "linked_gids": nullable("array", items={"type": "string"},
                                        en="Global ids of linked records (account, contact, deal…).",
                                        description_vi="Id toàn cục của bản ghi liên kết (account, contact, deal…)."),
                "form": nullable(
                    "array", en="Custom field values, one element per field.",
                    description_vi="Giá trị trường tùy chỉnh, mỗi phần tử một trường.",
                    items=obj({
                        "id": nullable("string", en="Field id.", description_vi="Id trường."),
                        "code": nullable("string", en="Field code.", description_vi="Mã trường."),
                        "name": nullable("string", en="Field label.", description_vi="Tên trường."),
                        "type": nullable("string", en="Field type.", description_vi="Kiểu trường."),
                        "vtype": nullable("string", en="Type of the stored value.", description_vi="Kiểu giá trị lưu."),
                        "value": nullable("string", "number", "integer", "boolean", "array", "object",
                                          en="Stored value.", description_vi="Giá trị đã lưu."),
                        "display": nullable("string", en="Value as shown in Base.",
                                            description_vi="Giá trị như hiển thị trên Base."),
                        "gs_key": nullable("string", en="Global search key.", description_vi="Khóa tìm kiếm toàn cục."),
                    })),
                "moves": nullable(
                    "array", en="Stage moves of the lead.",
                    description_vi="Lịch sử chuyển giai đoạn của lead.",
                    items=obj({
                        "from_id": nullable("string", en="Stage moved from.", description_vi="Giai đoạn chuyển đi."),
                        "to_id": nullable("string", en="Stage moved to.", description_vi="Giai đoạn chuyển đến."),
                        "user_id": nullable("integer", en="Who moved it.", description_vi="Người chuyển."),
                        "metatype": nullable("string", en="Kind of move.", description_vi="Loại chuyển."),
                        "change_at": nullable("integer", en="When, Unix seconds.",
                                              description_vi="Thời điểm chuyển, Unix timestamp (giây)."),
                        "duration_in_previous": nullable("integer", en="Seconds spent in the previous stage.",
                                                         description_vi="Số giây ở giai đoạn trước."),
                    })),
                "service_export": nullable("object", en="Snapshot of the service (id, name, icon…).",
                                           description_vi="Thông tin rút gọn của service (id, tên, biểu tượng…)."),
                "source_export": nullable("object", en="Snapshot of the source (id, name, type…).",
                                          description_vi="Thông tin rút gọn của nguồn (id, tên, loại…)."),
                "ad_attributes": nullable("object", en="Advertising attribution (Facebook ad, click ids…).",
                                          description_vi="Thông tin quảng cáo (quảng cáo Facebook, click id…)."),
                "enrichment": nullable("object", en="Data enrichment result for the lead.",
                                       description_vi="Kết quả làm giàu dữ liệu cho lead."),
                "computed_stats": nullable("object", en="Computed totals (won deals, value…).",
                                           description_vi="Số liệu tính sẵn (deal thắng, giá trị…)."),
                "scoring": nullable("object", en="Scoring state (is_mql, mql_at).",
                                    description_vi="Trạng thái chấm điểm (is_mql, mql_at)."),
                "data": nullable("object", en="Raw data captured with the lead (ext_data…).",
                                 description_vi="Dữ liệu gốc ghi nhận cùng lead (ext_data…)."),
                "system_id": _SYSTEM,
                "created_at": _CREATED,
                "updated_at": _UPDATED,
                "last_update_stage": nullable("integer", en="When the stage last changed, Unix seconds.",
                                              description_vi="Thời điểm đổi giai đoạn gần nhất, Unix timestamp (giây)."),
                "last_update_status": nullable("integer", en="When the status last changed, Unix seconds.",
                                               description_vi="Thời điểm đổi trạng thái gần nhất, Unix timestamp (giây)."),
                "mql_at": nullable("integer", en="When the lead became an MQL, Unix seconds; 0 if not.",
                                   description_vi="Thời điểm lead thành MQL, Unix timestamp (giây); 0 nếu chưa."),
                "last_activity_time": nullable("integer", en="Last activity, Unix seconds; 0 if none.",
                                               description_vi="Hoạt động gần nhất, Unix timestamp (giây); 0 nếu chưa có."),
                "next_activity_time": nullable("integer", en="Next scheduled activity, Unix seconds; 0 if none.",
                                               description_vi="Hoạt động kế tiếp đã lên lịch, Unix timestamp (giây); 0 nếu chưa có."),
                "total_activity": nullable("integer", en="Number of activities.", description_vi="Số hoạt động."),
            },
        ),
        # One call per lead; entries are the work done on it (notes, calls,
        # meetings, changelog).
        ExtapiStream(
            name="lead_feed",
            path="lead/feed/list",
            extract=("feeds",),
            primary_key=("id",),
            body={"lead_id": "{{ stream_partition.lead_id }}"},
            parent=Parent(stream="lead", partition_field="lead_id", stamp=True),
            keyset=Keyset(keys=("before_ts", "before_id"), size=100),
            # One mark for the stream: a partition is one lead's history.
            incremental=Incremental(candidates=("since", "last_update"), global_cursor=True),
            properties={
                "lead_id": nullable("string", en="Lead the entry belongs to.", description_vi="Lead mà hoạt động thuộc về."),
                "id": nullable("string", en="Entry id.", description_vi="Id hoạt động."),
                "name": nullable("string", en="Title.", description_vi="Tiêu đề."),
                "content": nullable("string", en="Body of the entry.", description_vi="Nội dung."),
                "metatype": nullable("string", en="Kind of entry (internal, note, call…).",
                                     description_vi="Loại hoạt động (nội bộ, ghi chú, cuộc gọi…)."),
                "model": nullable("string", en="Entry model (changelog, note…).",
                                  description_vi="Mô hình hoạt động (changelog, note…)."),
                "status": nullable("integer", en="Entry status code.", description_vi="Mã trạng thái."),
                "user_id": nullable("integer", en="User id of the author; 0 for the system.",
                                    description_vi="User id người tạo; 0 là hệ thống."),
                "source_id": nullable("string", en="Id of the lead it is about.", description_vi="Id lead liên quan."),
                "service_id": nullable("string", en="Service of that lead.", description_vi="Service của lead."),
                "followers": nullable("array", en="Followers of the entry.", description_vi="Người theo dõi."),
                "files": nullable("array", en="Attached files.", description_vi="Tệp đính kèm."),
                "since": nullable("integer", en="When the work happened, Unix seconds.",
                                  description_vi="Thời điểm diễn ra hoạt động, Unix timestamp (giây)."),
                "last_update": nullable("integer", en="When the entry was last changed, Unix seconds.",
                                        description_vi="Thời điểm cập nhật gần nhất, Unix timestamp (giây)."),
            },
        ),
    ),
)
