"""Account, Workflow, Request, Timeoff — the small, sharply-scoped applications.

Endpoint existence in this file was established by probing: a path that does not
exist on a Base host answers with an HTML error page, while a real one answers
with JSON even when the token is refused. So "this endpoint exists" is a
measurement here, not an assumption from the old YAML.
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Parent, Stream

# ── Account ──────────────────────────────────────────────────────────────────
# Two endpoints, and the probe confirmed there are only two: `user/get`,
# `office/list`, `role/list` and the rest all 404.

ACCOUNT = BaseConnector(
    app="account",
    title="Base Account",
    summary="Người dùng và đơn vị tổ chức. Danh bạ mà mọi ứng dụng Base khác tham chiếu tới.",
    url_base="https://account.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/1623302/T1Dv6u6m",
    streams=(
        Stream(
            name="user", path="users", collection=("users",),
            primary_key=("id",), paginate=False,
            fields={"username": "string", "email": "string", "name": "string"},
            note="Every user in the account.",
        ),
        Stream(
            name="group", path="units", collection=("units",),
            primary_key=("id",), paginate=False,
            fields={"name": "string"},
            note="Organisational units. Base calls them units; the product "
                 "has always surfaced them as groups.",
        ),
    ),
)


# ── Workflow ─────────────────────────────────────────────────────────────────

WORKFLOW = BaseConnector(
    app="workflow",
    title="Base Workflow",
    summary="Quy trình, các giai đoạn, và mọi công việc đang chạy trong đó.",
    url_base="https://workflow.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/1345096/SWE84Hd1",
    streams=(
        Stream(
            name="workflow", path="workflows/get", collection=("workflows",),
            page_field="page_id",
            incremental=Incremental(),
            fields={"name": "string"},
            note="The workflow definitions.",
        ),
        Stream(
            name="job", path="jobs/get", collection=("jobs",),
            page_field="page_id",
            incremental=Incremental(),
            fields={"name": "string", "workflow_id": "string",
                    "stage_id": "string"},
            note="Jobs are the fact table here; everything else is a dimension.",
        ),
        Stream(
            name="stage", path="workflow/stages", collection=("stages",),
            parent=Parent(stream="workflow", inject="id"),
            paginate=False,
            fields={"name": "string"},
            note="Stages belong to a workflow; Base has no endpoint that lists "
                 "them all, so this is read once per workflow.",
        ),
    ),
)


# ── Request ──────────────────────────────────────────────────────────────────
# No YAML existed for this application. The endpoint surface below was found by
# probing: `request/list`, `request/get` and `group/list` exist, and the twenty
# other paths tried -- `forms/get`, `type/list`, `stage/list`, `requests/get`
# and so on -- all 404. So this is the whole API, not a subset of it.

REQUEST = BaseConnector(
    app="request",
    title="Base Request",
    summary="Đề xuất nhân viên gửi lên, và các nhóm tiếp nhận.",
    url_base="https://request.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/1345096/SzzheyWQ",
    streams=(
        Stream(
            name="request", path="request/list", collection=("requests",),
            incremental=Incremental(),
            fields={"name": "string", "status": "string", "group_id": "string"},
            note="The fact table. `request/get` also exists but returns a "
                 "single record, so it is not a stream -- it would be one "
                 "HTTP call per row for data this endpoint already returns.",
        ),
        Stream(
            name="group", path="group/list", collection=("groups",),
            paginate=False,
            fields={"name": "string"},
            note="Request groups.",
        ),
    ),
)


# ── Timeoff ──────────────────────────────────────────────────────────────────

TIMEOFF = BaseConnector(
    app="timeoff",
    title="Base Timeoff",
    summary="Đơn nghỉ phép và nhóm chính sách áp dụng.",
    url_base="https://timeoff.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/1345096/UyrHftXj",
    streams=(
        Stream(
            name="group", path="group/list", collection=("groups",),
            primary_key=("id",), paginate=False,
            fields={"name": "string"},
            note="Timeoff policy groups. Had no primary key before, so a "
                 "re-sync could not deduplicate them.",
        ),
        Stream(
            name="timeoff", path="timeoff/list", collection=("timeoffs",),
            # The old manifest tracked `last_update` as the cursor but asked
            # the server for `start_date_from` -- filtering on when the leave
            # *starts* while remembering when the record last *changed*. A
            # leave request booked last year and approved today has a
            # `last_update` inside the window and a start date outside it, so
            # incremental syncs skipped it silently. `updated_from` is Base's
            # convention across every other application; if this endpoint
            # ignores it the sync degrades to a full read, which is slower but
            # never wrong.
            incremental=Incremental(field="last_update", param="updated_from"),
            fields={"user_id": "string", "status": "string",
                    "start_date": "string", "end_date": "string"},
            note="Leave requests.",
        ),
    ),
)
