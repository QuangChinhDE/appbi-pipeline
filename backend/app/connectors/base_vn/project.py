"""Base Project — project types, projects, their boards and groups, tasks and task history.

An extapis application; `_extapi.py` compiles it. This file only declares it.

Measured on 2026-10-22 against `extapis.base.vn/project/v1` (workspace "Base
Ha Noi": 9 project services, 12 projects, 32 tasks, 73 task events, no
comments). The documentation names `api-v3.{domain}`; as for Sales, the
installation answers on extapis.

* **Every read answers as a person.** Without `user_id` the API refuses
  (`invalid_data`, "this endpoint answers as a person"), and with it returns
  only what that user may see -- one test user saw 5 services and 6 projects
  where an administrator saw 9 and 12. `user_id` takes a numeric id or a
  username, as documented ("a username is accepted too"): `104171` and
  `mytest01` returned the same data. `@mytest01` and a name with no active
  user are refused with "No active user with this username in this
  workspace". So `user_id` is a required string setting, sent on every
  request.
* `service/all`, `project/list`, `task/list` page by `page` (from 0) and
  `limit` (at most 200). Walking 20 tasks three at a time returned all 20,
  no duplicate -- the page loss seen on Sales did not reproduce here.
* `project/list` filters on `updated_from` (against `updated_at`) and
  `created_from` (against `created_at`), each bound alone. Archived projects
  are in the default list already (`archived=1` returns a subset of it).
* `task/list` has **no update-time filter**: `updated_from`,
  `updated_at_from` and `last_update_from` were all ignored. `created_from`
  works, but it is not a superset filter for a cursor on `updated_at` or
  `finished_at`, so tasks filter on the client only. Archived tasks are
  excluded unless `include_archived` is sent; it is.
* `task/get.activity` pages by `next_cursor.before`, at most 100 events a
  page. An event has no id; `(task_id, at, action, user_id)` was unique across
  all 73 measured and is the key.
* `project/get`, `service/get`, `task/get` return exactly the fields of the
  list endpoints, so they are not streams.

Not a stream yet: `task/get.comments` -- no task in the test workspace has a
comment, so neither its key nor its shape could be measured.

Incremental, per the extapis rules: the cursor is the workspace's pick among
the record's real timestamp columns. `project` sends the stored value as
`updated_from`, a superset filter for every candidate; everything else filters
on the client.
"""

from __future__ import annotations

from ._extapi import (
    BodyField, ExtapiConnector, ExtapiStream, Incremental, Keyset, PageNumber, Parent,
    nullable, obj,
)

#: The largest page the list endpoints accept.
PAGE = PageNumber(size=200)

TIMESTAMPS = ("created_at", "updated_at")

_CREATED = nullable("integer", en="When it was created, Unix seconds.",
                    description_vi="Thời điểm tạo, Unix timestamp (giây).")
_UPDATED = nullable("integer", en="When it was last changed, Unix seconds.",
                    description_vi="Thời điểm cập nhật gần nhất, Unix timestamp (giây).")
_STATUS = nullable("integer", en="1 active, 0 disabled.", description_vi="1 đang dùng, 0 đã tắt.")
_USERS = {"type": "integer"}

#: A custom field value, as it rides on a project or task.
_CFIELDS = nullable(
    "array",
    en="Custom field values, one element per field, filtered by what the acting user may see.",
    description_vi="Giá trị các trường tùy chỉnh, mỗi phần tử một trường, theo quyền xem của user truy cập.",
    items=obj({
        "id": nullable("string", en="Custom field id.", description_vi="Id trường tùy chỉnh."),
        "code": nullable("string", en="Custom field code.", description_vi="Mã trường tùy chỉnh."),
        "name": nullable("string", en="Custom field label.", description_vi="Tên hiển thị của trường."),
        "type": nullable("string", en="Field type.", description_vi="Kiểu trường."),
        "vtype": nullable("string", en="Type of the stored value.", description_vi="Kiểu giá trị đã lưu."),
        "value": nullable("string", "number", "integer", "boolean", "array", "object",
                          en="The stored value.", description_vi="Giá trị đã lưu."),
        "display": nullable("string", en="The value as shown in Base.",
                            description_vi="Giá trị như hiển thị trên Base."),
        "gs_key": nullable("string", en="Global search key of the field.",
                           description_vi="Khóa tìm kiếm toàn cục của trường."),
    }),
)

#: Read once per project service; the rows do not repeat the service id.
_PER_SERVICE = Parent(stream="service", partition_field="service_id", stamp=True)

PROJECT = ExtapiConnector(
    app="project",
    title="Project",
    summary="Project services, projects with their boards and groups, tasks and task history from Base Project.",
    summary_vi="Loại dự án, dự án cùng board và nhóm, công việc và lịch sử công việc từ Base Project.",
    docs_url="https://rework-crm-public-apis-standard.dev.rework.site/doc/project-getting-started",
    key_help=(
        "The Project API key (X-API-TOKEN), issued by a workspace admin in "
        "Settings > API keys for the project application. A key for another "
        "application is refused."
    ),
    key_help_vi=(
        "API key của ứng dụng Project (X-API-TOKEN), do quản trị viên tạo trong "
        "Settings > API keys cho ứng dụng project. Key của ứng dụng khác sẽ bị "
        "từ chối."
    ),
    scopes="access to the project application",
    password=True,
    body_fields=(
        # The documented "acting user". `user_id` takes either form: a number
        # is read as a user id, anything else as a username -- measured, both
        # `104171` and `mytest01` return the same 12 projects. A leading `@`
        # is refused, and so is a name with no active user behind it.
        BodyField(
            name="user_id",
            kind="string",
            title="Read as user (id or username)",
            title_vi="Đọc dữ liệu với quyền của user (id hoặc username)",
            description=(
                "The Base user the connector reads as: their numeric user id "
                "or their username, without @. Project answers only with what "
                "this user may see, so pick someone who sees every project, "
                "such as an administrator. The user must be active in this "
                "workspace."
            ),
            description_vi=(
                "User Base mà connector dùng để đọc: user id dạng số hoặc "
                "username (không có @). Project chỉ trả dữ liệu user này được "
                "xem, nên hãy chọn người xem được mọi dự án, ví dụ quản trị "
                "viên. User phải đang hoạt động trong workspace."
            ),
        ),
    ),
    streams=(
        ExtapiStream(
            name="service",
            path="service/all",
            extract=("services",),
            primary_key=("id",),
            pages=PAGE,
            incremental=Incremental(candidates=TIMESTAMPS),
            properties={
                "id": nullable("string", en="Project service (project type) id.",
                               description_vi="Id loại dự án (service)."),
                "name": nullable("string", en="Service name.", description_vi="Tên loại dự án."),
                "code": nullable("string", en="Service code.", description_vi="Mã loại dự án."),
                "content": nullable("string", en="Description.", description_vi="Mô tả."),
                "model": nullable("string", en="Object model (project.project).",
                                  description_vi="Mô hình đối tượng (project.project)."),
                "status": _STATUS,
                "group_id": nullable("string", en="Group the service sits in.",
                                     description_vi="Nhóm chứa loại dự án."),
                "icon": nullable("string", en="Icon name.", description_vi="Tên biểu tượng."),
                "color": nullable("string", en="Display colour.", description_vi="Màu hiển thị."),
                "cost_enabled": nullable("boolean", en="Cost tracking is on.",
                                         description_vi="Bật theo dõi chi phí."),
                "income_enabled": nullable("boolean", en="Income tracking is on.",
                                           description_vi="Bật theo dõi doanh thu."),
                "workload_enabled": nullable("boolean", en="Workload tracking is on.",
                                             description_vi="Bật theo dõi khối lượng công việc."),
                "time_tracking_enabled": nullable("boolean", en="Time tracking is on.",
                                                  description_vi="Bật chấm giờ làm."),
                "backlog_enabled": nullable("boolean", en="Backlog is on.", description_vi="Bật backlog."),
                "issue_enabled": nullable("boolean", en="Issues are on.", description_vi="Bật quản lý vấn đề."),
                "doc_enabled": nullable("boolean", en="Documents are on.", description_vi="Bật tài liệu."),
                "deliverable_mode": nullable("string", "integer",
                                             en="How deliverables are handled.",
                                             description_vi="Cách xử lý sản phẩm bàn giao."),
                "deliverable_required": nullable("boolean", en="A deliverable is required to finish a task.",
                                                 description_vi="Bắt buộc có sản phẩm bàn giao để hoàn thành công việc."),
                "can_create_project": nullable("boolean", en="Whether the acting user may create projects here.",
                                               description_vi="User truy cập có được tạo dự án ở đây không."),
                "can_manage": nullable("boolean", en="Whether the acting user may manage the service.",
                                       description_vi="User truy cập có được quản lý loại dự án không."),
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
            properties={
                "service_id": nullable("string", en="Service the field belongs to.",
                                       description_vi="Loại dự án chứa trường."),
                "id": nullable("string", en="Custom field id.", description_vi="Id trường tùy chỉnh."),
                "code": nullable("string", en="Custom field code.", description_vi="Mã trường."),
                "label": nullable("string", en="Field label.", description_vi="Tên hiển thị của trường."),
                "type": nullable("string", en="Field type.", description_vi="Kiểu trường."),
                "vtype": nullable("string", en="Type of the stored value.", description_vi="Kiểu giá trị lưu."),
                "placeholder": nullable("string", en="Hint shown in the empty input.",
                                        description_vi="Gợi ý hiển thị khi ô nhập trống."),
                "required": nullable("boolean", en="Whether a value is required.",
                                     description_vi="Có bắt buộc nhập không."),
                "origin": nullable("string", en="Where the field is defined (service…).",
                                   description_vi="Nơi định nghĩa trường (service…)."),
                "protected": nullable("boolean", en="Protected by field-level security.",
                                      description_vi="Được bảo vệ bởi phân quyền theo trường."),
                "options": nullable("array", en="Choices of a select field.",
                                    description_vi="Các lựa chọn của trường dạng chọn."),
            },
        ),
        ExtapiStream(
            name="service_item_type",
            path="service/get.itemtypes",
            extract=("item_types",),
            primary_key=("service_id", "id"),
            body={"id": "{{ stream_partition.service_id }}"},
            parent=_PER_SERVICE,
            properties={
                "service_id": nullable("string", en="Service the work item type belongs to.",
                                       description_vi="Loại dự án chứa loại công việc."),
                "id": nullable("string", en="Work item type id.", description_vi="Id loại công việc."),
                "name": nullable("string", en="Work item type name (bug, feature…).",
                                 description_vi="Tên loại công việc (lỗi, tính năng…)."),
                "code": nullable("string", en="Short code.", description_vi="Mã ngắn."),
                "icon": nullable("string", en="Icon name.", description_vi="Tên biểu tượng."),
                "color": nullable("string", en="Display colour.", description_vi="Màu hiển thị."),
                "sort": nullable("integer", en="Display order.", description_vi="Thứ tự hiển thị."),
                "status": _STATUS,
            },
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
                                       description_vi="Loại dự án chứa giai đoạn."),
                "id": nullable("string", en="Stage id.", description_vi="Id giai đoạn."),
                "name": nullable("string", en="Stage name.", description_vi="Tên giai đoạn."),
                "code": nullable("string", en="Stage code.", description_vi="Mã giai đoạn."),
                "color": nullable("string", en="Display colour.", description_vi="Màu hiển thị."),
                "sort_order": nullable("integer", en="Position of the stage.", description_vi="Thứ tự giai đoạn."),
                "lc_status": nullable("integer", en="Lifecycle the stage maps to (-1 failed…).",
                                      description_vi="Trạng thái vòng đời tương ứng (-1 thất bại…)."),
                "status": _STATUS,
            },
        ),
        ExtapiStream(
            name="project",
            path="project/list",
            extract=("projects",),
            primary_key=("id",),
            pages=PAGE,
            incremental=Incremental(candidates=TIMESTAMPS, request_param="updated_from"),
            properties={
                "id": nullable("string", en="Project id.", description_vi="Id dự án."),
                "service_id": nullable("string", en="Project service (type) of the project.",
                                       description_vi="Loại dự án."),
                "name": nullable("string", en="Project name.", description_vi="Tên dự án."),
                "content": nullable("string", en="Description.", description_vi="Mô tả."),
                "prefix": nullable("string", en="Prefix used to number the project's tasks.",
                                   description_vi="Tiền tố đánh số công việc của dự án."),
                "status": nullable("integer", en="0 active, -1 archived.", description_vi="0 đang chạy, -1 đã lưu trữ."),
                "priority": nullable("integer", en="Priority level.", description_vi="Mức ưu tiên."),
                "creator_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
                "owners": nullable("array", items=_USERS, en="User ids of the project owners.",
                                   description_vi="User id của người phụ trách dự án."),
                "followers": nullable("array", items=_USERS, en="User ids of the followers.",
                                      description_vi="User id của người theo dõi."),
                "start_date": nullable("integer", en="Start date, Unix seconds; 0 if unset.",
                                       description_vi="Ngày bắt đầu, Unix timestamp (giây); 0 nếu chưa đặt."),
                "deadline": nullable("integer", en="Deadline, Unix seconds; 0 if unset.",
                                     description_vi="Hạn chót, Unix timestamp (giây); 0 nếu chưa đặt."),
                "cfields": _CFIELDS,
                "stage": nullable("object", "string", en="Current stage of the project.",
                                  description_vi="Giai đoạn hiện tại của dự án."),
                "billing_type": nullable("string", en="Billing model.", description_vi="Hình thức tính phí."),
                "workload_unit": nullable("string", en="Unit workload is counted in.",
                                          description_vi="Đơn vị tính khối lượng công việc."),
                "hours_per_day": nullable("number", en="Working hours per day.",
                                          description_vi="Số giờ làm việc mỗi ngày."),
                "currency": nullable("string", en="Currency of the financials.", description_vi="Đơn vị tiền tệ."),
                "financials": nullable(
                    "object", en="Money and effort totals of the project.",
                    description_vi="Tổng hợp tài chính và công sức của dự án.",
                    properties={
                        "income": nullable("number", en="Planned income.", description_vi="Doanh thu kế hoạch."),
                        "cost": nullable("number", en="Planned cost.", description_vi="Chi phí kế hoạch."),
                        "net": nullable("number", en="Planned income minus cost.",
                                        description_vi="Lợi nhuận kế hoạch (doanh thu trừ chi phí)."),
                        "effort": nullable("number", en="Planned effort.", description_vi="Công sức kế hoạch."),
                        "income_recorded": nullable("number", en="Recorded income.",
                                                    description_vi="Doanh thu đã ghi nhận."),
                        "cost_recorded": nullable("number", en="Recorded cost.",
                                                  description_vi="Chi phí đã ghi nhận."),
                        "net_recorded": nullable("number", en="Recorded income minus cost.",
                                                 description_vi="Lợi nhuận đã ghi nhận."),
                        "effort_recorded": nullable("number", en="Recorded effort.",
                                                    description_vi="Công sức đã ghi nhận."),
                    }),
                "url": nullable("string", en="Link to the project in Base.",
                                description_vi="Đường dẫn dự án trên Base."),
                "created_at": _CREATED,
                "updated_at": _UPDATED,
            },
        ),
        ExtapiStream(
            name="project_board",
            path="project/get.boards",
            extract=("boards",),
            primary_key=("id",),
            body={"project_id": "{{ stream_partition.project_id }}"},
            parent=Parent(stream="project", partition_field="project_id"),
            incremental=Incremental(candidates=TIMESTAMPS),
            properties={
                "id": nullable("string", en="Board id.", description_vi="Id board."),
                "project_id": nullable("string", en="Project the board belongs to.",
                                       description_vi="Dự án chứa board."),
                "name": nullable("string", en="Board name.", description_vi="Tên board."),
                "prefix": nullable("string", en="Prefix of the items on the board.",
                                   description_vi="Tiền tố của các mục trên board."),
                "metatype": nullable("string", en="Kind of board (tasks, issues…).",
                                     description_vi="Loại board (công việc, vấn đề…)."),
                "created_at": _CREATED,
                "updated_at": _UPDATED,
            },
        ),
        ExtapiStream(
            name="project_group",
            path="project/get.groups",
            extract=("groups",),
            primary_key=("id",),
            body={"project_id": "{{ stream_partition.project_id }}"},
            parent=Parent(stream="project", partition_field="project_id"),
            properties={
                "id": nullable("string", en="Task group id.", description_vi="Id nhóm công việc."),
                "project_id": nullable("string", en="Project the group belongs to.",
                                       description_vi="Dự án chứa nhóm."),
                "name": nullable("string", en="Group name.", description_vi="Tên nhóm."),
                "color": nullable("string", en="Display colour.", description_vi="Màu hiển thị."),
                "order": nullable("integer", en="Display order.", description_vi="Thứ tự hiển thị."),
                "status": nullable("integer", en="Group status code.", description_vi="Mã trạng thái của nhóm."),
            },
        ),
        ExtapiStream(
            name="task",
            path="task/list",
            extract=("tasks",),
            primary_key=("id",),
            body={"project_id": "{{ stream_partition.project_id }}", "include_archived": True},
            parent=Parent(stream="project", partition_field="project_id"),
            pages=PAGE,
            # No update-time filter exists; see the module docstring.
            incremental=Incremental(candidates=(*TIMESTAMPS, "finished_at")),
            properties={
                "id": nullable("string", en="Task id.", description_vi="Id công việc."),
                "project_id": nullable("string", en="Project the task belongs to.",
                                       description_vi="Dự án chứa công việc."),
                "parent_id": nullable("string", en="Parent task, for a subtask.",
                                      description_vi="Công việc cha, nếu là công việc con."),
                "name": nullable("string", en="Task name.", description_vi="Tên công việc."),
                "content": nullable("string", en="Description.", description_vi="Mô tả."),
                "counter": nullable("integer", "string", en="Task number within the project.",
                                    description_vi="Số thứ tự công việc trong dự án."),
                "status": nullable("integer", en="Task status code (0, 1, 10 seen).",
                                   description_vi="Mã trạng thái công việc (gặp 0, 1, 10)."),
                "stage_id": nullable("string", en="Current stage.", description_vi="Giai đoạn hiện tại."),
                "board_id": nullable("string", en="Board the task is on.", description_vi="Board chứa công việc."),
                "group_id": nullable("string", en="Task group.", description_vi="Nhóm công việc."),
                "item_type_id": nullable("string", en="Work item type.", description_vi="Loại công việc."),
                "roadmap_id": nullable("string", en="Roadmap item the task belongs to.",
                                       description_vi="Mục roadmap chứa công việc."),
                "priority": nullable("integer", en="Priority level.", description_vi="Mức ưu tiên."),
                "creator_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
                "owner_id": nullable("integer", en="User id of the owner.", description_vi="User id người phụ trách."),
                "assignee_id": nullable("integer", en="Id of the assignee.", description_vi="Id người được giao."),
                "assignee_type": nullable("string", en="Kind of assignee (user, team…).",
                                          description_vi="Loại người được giao (user, nhóm…)."),
                "executable": nullable("integer", en="1 when the task can be worked on now.",
                                       description_vi="1 nếu công việc có thể thực hiện ngay."),
                # Measured 2026-10-22: a boolean on 32 of 32 tasks. One type
                # only: for a union the destination picks the column type by
                # a fixed priority (array > object > string > number >
                # integer > boolean), not by order, so ["boolean", "integer"]
                # still made an INT64 column and BigQuery refused `false`
                # ("Bad int64 value: false") -- measured on two runs.
                "in_review": nullable("boolean", en="Whether the task is in review.",
                                      description_vi="Công việc có đang chờ duyệt không."),
                # An integer on 32 of 32 tasks, not the object it was declared.
                "review": nullable("integer", en="Review state code.",
                                   description_vi="Mã trạng thái duyệt."),
                "start_date": nullable("integer", en="Start date, Unix seconds; 0 if unset.",
                                       description_vi="Ngày bắt đầu, Unix timestamp (giây); 0 nếu chưa đặt."),
                "deadline": nullable("integer", en="Deadline, Unix seconds; 0 if unset.",
                                     description_vi="Hạn chót, Unix timestamp (giây); 0 nếu chưa đặt."),
                "finished_at": nullable("integer", en="When the task was finished, Unix seconds; 0 if open.",
                                        description_vi="Thời điểm hoàn thành, Unix timestamp (giây); 0 nếu chưa xong."),
                "archived_from": nullable("integer", en="When the task was archived, Unix seconds; 0 if not.",
                                          description_vi="Thời điểm lưu trữ, Unix timestamp (giây); 0 nếu chưa."),
                "cost": nullable("number", en="Cost recorded on the task.", description_vi="Chi phí ghi nhận."),
                # A number on 32 of 32 tasks; with "object" beside it the
                # column would have become JSON.
                "workload": nullable("number", en="Workload of the task.",
                                     description_vi="Khối lượng công việc."),
                "score": nullable("number", en="Score.", description_vi="Điểm."),
                "weight": nullable("number", en="Weight.", description_vi="Trọng số."),
                "sort_order": nullable("string", "number", en="Position within its list.",
                                       description_vi="Vị trí trong danh sách."),
                "files": nullable("array", en="Attached files.", description_vi="Tệp đính kèm."),
                "cfields": _CFIELDS,
                "url": nullable("string", en="Link to the task in Base.",
                                description_vi="Đường dẫn công việc trên Base."),
                "created_at": _CREATED,
                "updated_at": _UPDATED,
            },
        ),
        # One call per task. Events carry no id: (task_id, at, action,
        # user_id) is the key, unique across every event measured.
        ExtapiStream(
            name="task_activity",
            path="task/get.activity",
            extract=("events",),
            primary_key=("task_id", "at", "action", "user_id"),
            body={"task_id": "{{ stream_partition.task_id }}"},
            parent=Parent(stream="task", partition_field="task_id", stamp=True),
            keyset=Keyset(keys=("before",), size=100),
            # One mark for the stream: a partition is one task's history.
            incremental=Incremental(candidates=("at",), global_cursor=True),
            properties={
                "task_id": nullable("string", en="Task the event belongs to.",
                                    description_vi="Công việc mà sự kiện thuộc về."),
                "at": nullable("integer", en="When it happened, Unix seconds.",
                               description_vi="Thời điểm xảy ra, Unix timestamp (giây)."),
                "user_id": nullable("integer", en="User id of who did it.",
                                    description_vi="User id người thực hiện."),
                "action": nullable("string", en="What happened, e.g. task:created.",
                                   description_vi="Hành động, ví dụ task:created."),
                "message": nullable("string", en="Human-readable description (HTML).",
                                    description_vi="Mô tả dạng đọc được (HTML)."),
                "changed": nullable("array", items={"type": "string"},
                                    en="Names of the fields the event changed.",
                                    description_vi="Tên các trường bị thay đổi."),
            },
        ),
    ),
)
