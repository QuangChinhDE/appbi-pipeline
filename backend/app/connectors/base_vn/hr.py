"""HRM and Hiring — the two largest Base applications.

HRM is 25 streams and mostly flat: one endpoint per entity, all under the same
host. Hiring is smaller but almost entirely parent/child.
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Parent, Stream


def _hrm(name: str, path: str, collection: str, *, incremental: bool = True,
         paginate: bool = True, fields: dict[str, str] | None = None,
         note: str = "") -> Stream:
    """Every HRM stream has the same shape, so say it once."""
    return Stream(
        name=name, path=path, collection=(collection,),
        primary_key=("id",),
        incremental=Incremental() if incremental else None,
        paginate=paginate, fields=fields or {}, note=note,
    )


# ── HRM ──────────────────────────────────────────────────────────────────────
#
# Two things changed for every stream here.
#
# **The path is fixed; only the domain is configurable.** The old manifest built
# its URL from `https://hrm.{{ config['domain'] }}/extapi/{{ config['version'] }}`
# -- two required fields with no defaults, one of them an API version nobody
# should have to know. `version` is gone: `extapi/v1` is what these streams are
# written against, and letting a workspace change it only lets them point at an
# API the streams do not match.
#
# `domain` stayed, and it had to: `base.vn` and `base.com.vn` are separate
# installations with separate accounts, and only the customer knows which they
# are on. It defaults to `base.vn`, and every connector takes it the same way.
#
# **Pagination.** The old manifest paginated 8 of 25 streams. `employee/list`,
# `contract/list`, `insurance/list` and the rest were read as a single request
# and silently truncated at whatever Base returns by default — which for an
# account of any size means missing employees, with no error. Pagination is on
# by default here; the handful of genuinely small enumerations opt out.

HRM = BaseConnector(
    app="hrm",
    title="Base HRM",
    summary="Nhân sự và mọi thứ hệ thống HR ghi nhận: hợp đồng, bảo hiểm, vị trí, khen thưởng, quá trình công tác, lương.",
    url_base="https://hrm.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/30137628/2sB3HoneRk",
    streams=(
        # People and the structures they sit in.
        _hrm("employee", "employee/list", "employees",
             fields={"name": "string", "email": "string", "username": "string",
                     "office_id": "string", "team_id": "string"},
             note="The fact table of the whole application. Was unpaginated, "
                  "so any account past Base's default page size lost people."),
        _hrm("office", "office/list", "offices", fields={"name": "string"}),
        _hrm("team", "team/list", "teams", fields={"name": "string"}),
        _hrm("area", "area/list", "areas", fields={"name": "string"}),
        _hrm("position", "position/list", "positions", fields={"name": "string"}),
        _hrm("position_type", "position/types", "types", fields={"name": "string"}),

        # Employment terms.
        _hrm("contract", "contract/list", "contracts",
             fields={"employee_id": "string", "type_id": "string"}),
        _hrm("contract_type", "contract/types", "types",
             incremental=False, paginate=False, fields={"name": "string"},
             note="A short fixed enumeration."),
        _hrm("employee_type", "employee/types", "types", fields={"name": "string"}),
        _hrm("insurance", "insurance/list", "insurances",
             fields={"employee_id": "string"}),
        _hrm("tax", "tax/list", "taxes", fields={"employee_id": "string"}),

        # Per-employee detail.
        _hrm("employee_work", "employee/works", "works",
             fields={"employee_id": "string"}),
        _hrm("employee_legal", "employee/legals", "legals",
             fields={"employee_id": "string"},
             note="The old manifest paginated this one but gave it no cursor; "
                  "it now has both."),
        _hrm("employee_relation", "employee/relations", "relations",
             fields={"employee_id": "string"}),
        _hrm("employee_education", "employee/educations", "educations",
             fields={"employee_id": "string"}),
        _hrm("career_record", "career/records", "records",
             fields={"employee_id": "string"}),

        # Recognition.
        _hrm("merit_record", "merit/records", "records",
             fields={"employee_id": "string"}),
        _hrm("merit_award", "merit/awards", "awards", fields={"name": "string"}),
        _hrm("merit_cert", "merit/certs", "certs", fields={"name": "string"}),
        _hrm("merit_rule", "merit/rules", "rules", fields={"name": "string"}),
        _hrm("merit_type", "merit/types", "types",
             incremental=False, paginate=False, fields={"name": "string"}),
        _hrm("merit_template", "merit/templates", "templates",
             incremental=False, paginate=False, fields={"name": "string"}),

        # Time and pay, as HRM sees them.
        _hrm("timesheet", "timesheet/list", "timesheets",
             incremental=False,
             fields={"employee_id": "string"},
             note="No cursor: the old manifest had none either, and Base "
                  "documents no `updated_from` here. Paginated now, which it "
                  "was not -- a timesheet list is one of the largest tables "
                  "in the application."),
        _hrm("payroll_cycle", "payroll/cycles", "cycles", fields={"name": "string"}),
        _hrm("payroll_record", "payroll/records", "records",
             fields={"employee_id": "string", "cycle_id": "string"}),
    ),
)


# ── Hiring ───────────────────────────────────────────────────────────────────

HIRING = BaseConnector(
    app="hiring",
    title="Base E-Hiring",
    summary="Vị trí tuyển dụng, ứng viên, và quá trình phỏng vấn.",
    url_base="https://hiring.{domain}/publicapi/v2/",
    docs_url="https://documenter.getpostman.com/view/30137628/2sAYdipA2D",
    streams=(
        Stream(
            name="opening", path="opening/list", collection=("openings",),
            page_size_field="num_per_page",
            fields={"name": "string", "dept_id": "string"},
            note="Job openings. Parent of candidates and stages.",
        ),
        Stream(
            name="pool", path="pool/list", collection=("pools",),
            paginate=False, fields={"name": "string"},
            note="Talent pools. Parent of contacts.",
        ),
        Stream(
            name="dept", path="system/depts", collection=("depts",),
            paginate=False, fields={"name": "string"},
        ),
        Stream(
            name="office", path="system/offices", collection=("depts",),
            paginate=False, fields={"name": "string"},
            note="Offices come back under a `depts` key -- Base reuses the "
                 "envelope. Kept as the old manifest had it, because that is "
                 "what the endpoint actually returns.",
        ),
        Stream(
            name="candidate", path="candidate/list", collection=("candidates",),
            parent=Parent(stream="opening", inject="opening_id"),
            page_size_field="num_per_page",
            fields={"name": "string", "email": "string",
                    "opening_id": "string", "stage_id": "string"},
            note="The fact table, read per opening.",
        ),
        Stream(
            name="stage", path="stage/list", collection=("stages",),
            parent=Parent(stream="opening", inject="opening_id"),
            page_size_field="num_per_page",
            fields={"name": "string", "opening_id": "string"},
            note="Pipeline stages, per opening.",
        ),
        Stream(
            name="contact", path="contact/list", collection=("contacts",),
            parent=Parent(stream="pool", inject="pool_id"),
            page_size_field="num_per_page",
            fields={"name": "string", "email": "string", "pool_id": "string"},
            note="Contacts, per talent pool.",
        ),
        Stream(
            name="interview", path="interview/list", collection=("interviews",),
            page_size_field="num_per_page",
            fields={"candidate_id": "string", "opening_id": "string"},
            note="Interviews are listed account-wide rather than per opening, "
                 "so this is one crawl instead of one call per opening.",
        ),
    ),
)
