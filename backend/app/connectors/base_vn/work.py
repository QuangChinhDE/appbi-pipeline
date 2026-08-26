"""WeWork, Service, Payroll — the applications built on parent/child endpoints.

All three share a shape: a small list of containers, then per-container calls
for the rows inside them. Getting the parent right matters more here than
anywhere else, because the parent decides how much of the account is visible at
all.
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Parent, Stream

# ── WeWork ───────────────────────────────────────────────────────────────────
#
# The old manifest had a hardcoded project id in the request body:
#
#     "project": { "path": "project/get.full", "body": {"id": "131471"} }
#
# So the `project` stream returned exactly one project -- somebody's test
# project, baked into the connector -- and `task`, `topic`, `tasklist` and
# `milestone` all hung off it. Every WeWork sync, for every customer, read one
# project's worth of data and reported success. The spec even declared a
# `project_id` config field that the body ignored.
#
# `project/list` exists (confirmed by probe) and the old manifest never used it.
# It is the parent this connector should always have had: list the projects,
# then read each one.

WEWORK = BaseConnector(
    app="wework",
    title="Base WeWork",
    summary="Dự án, công việc, và các cấu trúc quanh chúng.",
    url_base="https://wework.{domain}/extapi/v3/",
    docs_url="https://documenter.getpostman.com/view/1345096/SztA68Az",
    streams=(
        Stream(
            name="project", path="project/list", collection=("projects",),
            page_size_field="items_per_page",
            fields={"name": "string"},
            note="Every project in the account. Replaces a stream that read "
                 "one hardcoded project id.",
        ),
        Stream(
            name="dept", path="dept/list", collection=("depts",),
            paginate=False,
            fields={"name": "string"},
            note="Departments.",
        ),
        Stream(
            name="task", path="task/project", collection=("tasks",),
            parent=Parent(stream="project", inject="id"),
            # Incremental on a substream: Airbyte keeps the cursor per
            # partition, so each project advances on its own high-water mark
            # rather than sharing one. Worth it here because tasks are the
            # largest table in the application by a wide margin.
            incremental=Incremental(field="last_update", param="updated_from"),
            fields={"name": "string", "project_id": "string",
                    "status": "string", "assignee_id": "string"},
            note="Tasks, read per project. The fact table.",
        ),
        Stream(
            name="topic", path="topic/list", collection=("topics",),
            parent=Parent(stream="project", inject="id"),
            page_size_field="per_page",
            fields={"name": "string", "project_id": "string"},
            note="Discussion topics attached to a project.",
        ),
        Stream(
            name="tasklist", path="project/get.full", collection=("tasklists",),
            parent=Parent(stream="project", inject="id"),
            paginate=False,
            fields={"name": "string", "project_id": "string"},
            note="`project/get.full` returns the project with its tasklists "
                 "and milestones nested; both are extracted from the same "
                 "call per project.",
        ),
        Stream(
            name="milestone", path="project/get.full", collection=("milestones",),
            parent=Parent(stream="project", inject="id"),
            primary_key=("id",), paginate=False,
            fields={"name": "string", "project_id": "string"},
            note="Milestones. Had no primary key before.",
        ),
    ),
)


# ── Service ──────────────────────────────────────────────────────────────────

SERVICE = BaseConnector(
    app="service",
    title="Base Service",
    summary="Các hàng dịch vụ, giai đoạn xử lý, và toàn bộ ticket gửi tới.",
    url_base="https://service.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/24787730/2sB2cYcfdo",
    streams=(
        Stream(
            name="service", path="service/get.all", collection=("services",),
            paginate=False, body={"limit": "500"},
            fields={"name": "string"},
            note="The service desks. Parent of stages and tickets.",
        ),
        Stream(
            name="group", path="group/get.all", collection=("groups",),
            paginate=False, body={"limit": "500"},
            fields={"name": "string"},
            note="Service groups.",
        ),
        Stream(
            name="compound", path="compound/get.all",
            collection=("compound_blocks",), paginate=False,
            fields={"name": "string"},
            note="Compound blocks.",
        ),
        Stream(
            name="stage", path="service/get.stages", collection=("stages",),
            parent=Parent(stream="service", inject="service_id"),
            paginate=False,
            fields={"name": "string", "service_id": "string"},
            note="Stages of each service desk.",
        ),
        # The old manifest had this stream twice: once as `ticket` and once as
        # `test`, identical in path, extractor, parent and cursor. Every sync
        # crawled every ticket in the account twice and wrote them to two
        # tables. `test` is gone.
        Stream(
            name="ticket", path="ticket/get.all", collection=("tickets",),
            parent=Parent(stream="service", inject="service_id"),
            primary_key=("id",),
            # Per-service cursor, as the old manifest had. `last_update_from`
            # rather than `updated_from`: Service is the one application that
            # spells the filter differently, and it was verified working.
            incremental=Incremental(field="last_update", param="last_update_from"),
            fields={"name": "string", "service_id": "string",
                    "stage_id": "string", "status": "string"},
            note="Tickets, per service desk. Had no primary key, so a "
                 "re-sync could not deduplicate the largest table here.",
        ),
    ),
)


# ── Payroll ──────────────────────────────────────────────────────────────────

PAYROLL = BaseConnector(
    app="payroll",
    title="Base Payroll",
    summary="Kỳ lương, các bảng lương trong kỳ, và bản ghi lương từng nhân viên.",
    url_base="https://payroll.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/12068719/2s8YRmGrgf",
    streams=(
        # The old manifest also had a `test` stream: `GET /test`, with an empty
        # extractor. It produced no records and existed only as somebody's
        # connectivity check. Gone -- `check` does that job properly now.
        Stream(
            name="cycle", path="cycle/list", collection=("cycles",),
            fields={"name": "string"},
            note="Payroll cycles. The root of the chain.",
        ),
        Stream(
            name="payroll", path="payroll/list", collection=("payrolls",),
            parent=Parent(stream="cycle", inject="cycle_id"),
            fields={"name": "string", "cycle_id": "string"},
            note="Payrolls within a cycle. The old manifest declared a "
                 "`last_update` cursor on this stream with no request "
                 "parameter to filter on, so it re-read everything every sync "
                 "and only pretended to be incremental. It is a substream: "
                 "Base offers no way to ask for changed payrolls across "
                 "cycles, so a full read of the cycles in scope is the "
                 "honest behaviour.",
        ),
        Stream(
            name="record", path="record/list", collection=("records",),
            parent=Parent(stream="payroll", inject="payroll_id"),
            fields={"payroll_id": "string", "employee_id": "string"},
            note="Per-employee payroll records. Grandchild of cycle.",
        ),
    ),
)
