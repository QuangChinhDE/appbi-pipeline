"""Schedule — shifts staff are assigned, and the positions they fill.

Endpoint surface established by probing a live `schedule.base.com.vn` tenant,
the same way `core.py` established Account, Workflow, Request and Timeoff:
`shift/list` and `position/list` answer JSON; `user/list`, `department/list`,
`team/list`, `office/list`, `shift/get.all` and a dozen other guesses all
answer the platform's generic 404 page.

Two things this measurement found that the spec would not have told us:

* **`shift/list` takes a mandatory date window, not an optional filter.**
  `start_time`/`end_time` (epoch seconds) are required on every call --
  `{"code": 0, "message": "Invalid time range"}` with neither supplied -- and
  `start_time=0` is refused the same way. `1` is accepted, so the window is
  sent as `[1, now]` on every sync: the widest range the server will take,
  which in practice is "everything". A ten-year and a fifty-five-year window
  from the same account both returned the identical 96 rows, so there is no
  narrower window worth sending -- the server is not slicing by it once past
  that lower bound.
* **`updated_from` changes nothing here.** Records carry `last_update`, but
  sending `updated_from` alongside a valid window returned the same 96 rows as
  omitting it -- this endpoint does not filter server-side on it, unlike most
  other Base applications. So the cursor stays client-side only: every sync
  reads the whole window and the CDK drops what has not changed since the
  high-water mark, rather than asking the server for a slice it will not give.
* **Pagination fields are `page` / `items_per_page`**, not the `page`/`limit`
  pair most other Base applications use.
* **`position/list` takes no arguments and paginates nothing** -- the response
  carries no `total_items`/`page` envelope at all, only a bare `positions`
  array, so it is read in one call like Account's `units` or Timeoff's
  `groups`.
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Stream

SCHEDULE = BaseConnector(
    app="schedule",
    title="Base Schedule",
    summary="Shift assignments, and the positions staff are scheduled into.",
    summary_vi="Ca làm việc được xếp cho nhân viên, và vị trí công việc đi kèm.",
    url_base="https://schedule.{domain}/extapi/v1/",
    streams=(
        Stream(
            name="shift", path="shift/list", collection=("shifts",),
            primary_key=("id",),
            page_field="page", page_size_field="items_per_page",
            body={
                # `0` is refused ("Invalid time range"); `1` is the widest
                # start this endpoint accepts. See the module docstring.
                "start_time": "1",
                "end_time": "{{ now_utc().strftime('%s') }}",
            },
            # The server does not filter on `updated_from` -- measured, not
            # assumed -- so no request option is sent for it; the cursor only
            # trims what reaches the destination.
            incremental=Incremental(
                field="last_update", param="updated_from",
                send_request_options=False, client_side=True,
            ),
            fields={
                "user_id": "string", "date": "string",
                "s_time": "string", "e_time": "string",
                "position_id": "string", "position_name": "string",
                "type_id": "string", "type_name": "string",
                "published": "string",
            },
            note="One row per assigned shift, across the account's full "
                 "history -- the endpoint has no way to ask for less.",
        ),
        Stream(
            name="position", path="position/list", collection=("positions",),
            primary_key=("id",), paginate=False,
            fields={"name": "string"},
            note="Positions shifts are assigned to. No pagination envelope "
                 "in the response, so read in one call.",
        ),
    ),
)
