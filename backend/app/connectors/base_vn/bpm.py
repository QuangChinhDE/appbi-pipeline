"""Base BPM — services, their fields and steps, and the jobs filed against them.

An extapis application; `_extapi.py` compiles it. This file only declares it.

Measured on 2026-10-19 against `extapis.base.vn/bpm` with a key scoped
`services:read` + `job:read` (workspace "Base Ha Noi", 11 services):

* `services` returns every active service in one response; no pagination
* `service/fields` and `service/stages` take a `service_id` and return the
  service's definition whole -- neither pages
* `job/list` without `status` returns every status (active and failed both
  seen); `per_page` caps at 100; `next_cursor` is `{"last_update", "id"}`,
  sent back verbatim as `cursor`, and is null on the last page
* a key without a scope is refused 403 `scope_required`, naming the scope
* `extapis.base.com.vn/bpm` refused this key with 401 -- the two installations
  hold separate keys, as for every other Base app, hence the domain field

Which streams are incremental, and on what
------------------------------------------

`job` and `job_detail`. `service`, `field` and `stage` carry no timestamp,
their endpoints take no filter, and their ids are not sequences
(`service.id` is a random string; `stage.id` is reassigned when a service is
redesigned), so they are full refresh.

A job carries three cursor candidates, and the workspace picks one in the
pipeline: `created_at` and `finished_at` (epoch seconds) and `id` (a
sequence). The record has no update time -- `last_update` exists only inside
`next_cursor`; probed with `fields`, `include` and `job/get`, none return it.

`job/list` filters on `updated_at_from`, and the stored cursor value is sent
as it. That is a superset for every candidate: a job created, finished or
newly numbered after the mark was necessarily updated after it too. For `id`
the value is a small integer read as an epoch, so the filter narrows nothing
but loses nothing.

`job/get` takes an id and no time filter, so `job_detail` filters on the
client only: every job is still requested, and only records past the chosen
cursor are emitted.

Custom field values (`data` on a job) appear only for fields a service
manager has allowlisted under Service settings -> Public API, and that list
starts empty. The schema stays open so they land when a service opts in.
"""

from __future__ import annotations

from ._extapi import ExtapiConnector, ExtapiStream, Incremental, Parent, nullable, obj

#: The largest page `job/list` accepts.
JOB_PAGE_SIZE = 100

#: The real columns of a job a cursor may be.
JOB_CURSORS = ("created_at", "finished_at", "id")

_OPTIONS = nullable(
    "array", en="Choices of a select field.",
    description_vi="Các lựa chọn của trường dạng chọn.",
    items=obj({
        "value": nullable("string", en="Stored value of the choice.", description_vi="Giá trị lưu của lựa chọn."),
        "label": nullable("string", en="Label shown for the choice.", description_vi="Nhãn hiển thị của lựa chọn."),
    }))

_ACTIONS = nullable(
    "array", en="Buttons a person can press on the step.",
    description_vi="Các nút người xử lý có thể bấm ở bước này.",
    items=obj({
        "id": nullable("string", en="Action id (submit, approve, reject…).",
                       description_vi="Id hành động (submit, approve, reject…)."),
        "name": nullable("string", en="Action label.", description_vi="Tên hành động."),
    }))

#: What `job/list` and `job/get` both return for a job.
JOB_PROPERTIES = {
    "id": nullable("integer", en="Job id, increasing with creation.", description_vi="Id job, tăng dần theo thời gian tạo."),
    "name": nullable("string", en="Job title.", description_vi="Tiêu đề job."),
    "counter": nullable("integer", en="Sequence number within the service; 0 when unnumbered.",
                        description_vi="Số thứ tự trong service; 0 nếu service không đánh số."),
    "counter_display": nullable("string", en="Formatted job number, e.g. REQ-2026-0002.",
                                description_vi="Mã job hiển thị, ví dụ REQ-2026-0002."),
    "status": nullable("integer", en="Status code: 0 active, 120 failed; see status_label.",
                       description_vi="Mã trạng thái: 0 đang chạy, 120 lỗi; xem status_label."),
    "status_label": nullable("string", en="Status: active, done, failed or cancelled.",
                             description_vi="Trạng thái: active, done, failed hoặc cancelled."),
    "service_id": nullable("string", en="Service the job was filed in.", description_vi="Service chứa job."),
    "created_by": nullable("integer", "string", en="User id of who created the job.",
                           description_vi="User id người tạo job."),
    "created_at": nullable("integer", en="When the job was created, Unix seconds.",
                           description_vi="Thời điểm tạo job, Unix timestamp (giây)."),
    "finished_at": nullable("integer", en="When the job finished, Unix seconds; 0 while running.",
                            description_vi="Thời điểm job kết thúc, Unix timestamp (giây); 0 khi chưa xong."),
    "url": nullable("string", en="Link to the job in BPM.", description_vi="Đường dẫn tới job trên BPM."),
    "data": nullable("object", en="Custom field values the service allows the API to return.",
                     description_vi="Giá trị trường tùy chỉnh mà service cho phép API trả về."),
}

#: Read once per service. Neither `service/fields` nor `service/stages`
#: repeats the service id on its rows, hence the stamp.
_PER_SERVICE = Parent(stream="service", partition_field="service_id", stamp=True)

BPM = ExtapiConnector(
    app="bpm",
    title="BPM",
    summary="Services, their fields and steps, and jobs with their current steps from Base BPM.",
    summary_vi="Service, field, step, job và step hiện tại của job từ Base BPM.",
    docs_url="https://bpm.base.vn/",
    key_help=(
        "The BPM API key (X-API-TOKEN), issued in the platform Settings app. "
        "It needs the services:read and job:read scopes."
    ),
    key_help_vi=(
        "API key của BPM (X-API-TOKEN), tạo trong ứng dụng Settings của hệ "
        "thống. Key cần có scope services:read và job:read."
    ),
    scopes="the services:read and job:read scopes",
    password=True,
    streams=(
        ExtapiStream(
            name="service",
            path="services",
            extract=("services",),
            primary_key=("id",),
            properties={
                "id": nullable("string", en="Service id.", description_vi="Id service (quy trình)."),
                "name": nullable("string", en="Service name.", description_vi="Tên service."),
                "status": nullable("integer", en="1 active.", description_vi="1 đang hoạt động."),
            },
        ),
        # The Start step's custom fields. `builtin` beside them is the fixed
        # create-payload vocabulary, identical for every service, so not data.
        ExtapiStream(
            name="field",
            path="service/fields",
            extract=("fields",),
            primary_key=("service_id", "key"),
            body={"service_id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties={
                "service_id": nullable("string", en="Service the field belongs to.",
                                       description_vi="Service chứa trường."),
                "key": nullable("string", en="Field key, as used in a job's data.",
                                description_vi="Khóa trường, dùng trong data của job."),
                "label": nullable("string", en="Field label.", description_vi="Tên hiển thị của trường."),
                "type": nullable("string", en="Field type (text, int, select, table, user…).",
                                 description_vi="Kiểu trường (text, int, select, table, user…)."),
                "required": nullable("boolean", en="Whether the Start step requires it.",
                                     description_vi="Bước Start có bắt buộc nhập không."),
                # Select options and table columns, kept nested: they belong
                # to the field and differ in shape per field type.
                "options": _OPTIONS,
                "columns": nullable(
                    "array", en="Columns of a table field.",
                    description_vi="Các cột của trường dạng bảng.",
                    items=obj({
                        "code": nullable("string", en="Column code.", description_vi="Mã cột."),
                        "name": nullable("string", en="Column label.", description_vi="Tên cột."),
                        "type": nullable("string", en="Column type.", description_vi="Kiểu cột."),
                        "options": _OPTIONS,
                    })),
            },
        ),
        # Keyed by `block_key`, not `id`: the numeric id changes when a
        # service is redesigned, the block key does not.
        ExtapiStream(
            name="stage",
            path="service/stages",
            extract=("stages",),
            primary_key=("service_id", "block_key"),
            body={"service_id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties={
                "service_id": nullable("string", en="Service the step belongs to.",
                                       description_vi="Service chứa bước."),
                "block_key": nullable("string", en="Stable key of the step; survives a redesign.",
                                      description_vi="Khóa ổn định của bước, không đổi khi thiết kế lại."),
                "id": nullable("string", en="Numeric step id; changes when the service is redesigned.",
                               description_vi="Id số của bước; đổi khi thiết kế lại service."),
                "name": nullable("string", en="Step name.", description_vi="Tên bước."),
                "type": nullable("integer", en="Step type: 1 Start, 2 End, 3 Approval, 11 Wait, 14 AI Agent, 16 Custom, 17 Sub jobs.",
                                 description_vi="Loại bước: 1 Bắt đầu, 2 Kết thúc, 3 Phê duyệt, 11 Chờ, 14 AI Agent, 16 Tùy chỉnh, 17 Job con."),
                "position": nullable("integer", en="Position hint for drawing the flow.",
                                     description_vi="Vị trí gợi ý để vẽ luồng."),
                "next_block_keys": nullable("array", items={"type": "string"},
                                            en="Block keys of the steps that can follow.",
                                            description_vi="block_key của các bước có thể đi tiếp."),
                "actions": _ACTIONS,
            },
        ),
        ExtapiStream(
            name="job",
            path="job/list",
            extract=("jobs",),
            primary_key=("id",),
            body={
                "service_id": "{{ stream_partition.service_id }}",
                "per_page": JOB_PAGE_SIZE,
                # Not cosmetic. The first page defaults to `newest` (creation
                # order) while every cursor page walks last-update order, so a
                # job created early but edited recently fell between the two
                # and was never read: measured, 29 of 30 jobs at per_page 3.
                # `updated` makes page one agree with the cursor.
                "sort_by": "updated",
            },
            parent=Parent(stream="service", partition_field="service_id"),
            paginate=True,
            incremental=Incremental(candidates=JOB_CURSORS,
                                    request_param="updated_at_from"),
            properties=JOB_PROPERTIES,
        ),
        # One `job/get` per job the `job` stream read. It adds where each job
        # stands -- `current_steps` and `active_steps`, whose `block_key`
        # joins to `stage` -- which `job/list` does not return.
        ExtapiStream(
            name="job_detail",
            path="job/get",
            extract=("job",),
            primary_key=("id",),
            body={"job_id": "{{ stream_partition.job_id }}"},
            parent=Parent(stream="job", partition_field="job_id"),
            # One mark for the whole stream: every partition is a single job,
            # and a per-partition cursor re-emitted all 30 jobs on the second
            # incremental sync (measured).
            incremental=Incremental(candidates=JOB_CURSORS, global_cursor=True),
            properties={
                **JOB_PROPERTIES,
                "current_steps": nullable("array", items={"type": "string"},
                                          en="Names of the steps the job is standing on.",
                                          description_vi="Tên các bước job đang đứng."),
                "active_steps": nullable(
                    "array", en="Steps waiting on someone, with what they accept.",
                    description_vi="Các bước đang chờ xử lý và các thao tác được phép.",
                    items=obj({
                        "job_block_id": nullable("integer", en="Id of this step within the job.",
                                                 description_vi="Id của bước trong job này."),
                        "block_key": nullable("string", en="Step key; joins to stage.block_key.",
                                              description_vi="Khóa bước; nối với stage.block_key."),
                        "name": nullable("string", en="Step name.", description_vi="Tên bước."),
                        "type": nullable("integer", en="Step type, as on stage.type.",
                                         description_vi="Loại bước, như stage.type."),
                        "status": nullable("integer", en="Step status code (20 waiting).",
                                           description_vi="Mã trạng thái của bước (20 đang chờ)."),
                        "can_submit": nullable("boolean", en="Whether an assignee can submit it.",
                                               description_vi="Người được giao có thể submit bước này không."),
                        "actions": _ACTIONS,
                    })),
            },
        ),
    ),
)
