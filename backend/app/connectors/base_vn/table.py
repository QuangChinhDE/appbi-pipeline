"""Base Table — the rows of a spreadsheet the account already keeps.

The odd one out in this package, in a way that shapes the whole definition:
every other Base application answers "give me everything you have". Table does
not. `table/records` without a `table_id` answers `INVALID_DATA`, and there is
no endpoint that says which tables an account can see -- `table/tables`,
`table/list`, `tables`, `table/all`, `table/table.list`, `table/groups`,
`table/get`, `table/tables.list`, `table/info`, `table/views` and `table/base.list`
all answer `<h1>File Not Found</h1>`.

So the ids cannot be discovered, and a stream that is not told which table to
read is not a slow sync -- it is a sync that cannot run. That is why the scope
here is `required=True`: the configuration is refused up front rather than
accepted and failed once per schedule.

Where the reviewed YAML would have failed
-----------------------------------------

The Airbyte-builder manifest this was written from hardcodes
`https://table.base.vn/extapi/v1/table` with no domain option, and its own
`testedStreams` metadata records a successful run -- against `base.vn`. Probed
with a `base.com.vn` token on 2026-09-18:

    POST https://table.base.vn/extapi/v1/table/records      -> access_token_v2_invalid_3
    POST https://table.base.com.vn/extapi/v1/table/records  -> INVALID_DATA

The first reads like an expired token and is not; it is the other installation
refusing a token it never issued. `_shared.py` documents this for every Base
application, and Table is where hardcoding the host would have turned it into a
support ticket. Hence `{domain}`, like every other connector here.

What was measured
-----------------

Against `base.com.vn` on 2026-09-18, tables 307, 295 and 294 (24, 39 and 66
rows):

* `limit` is honoured; without it the server returns 20, which is not the end
  of the table and looks exactly like it
* `page_id` pages, zero-based, and an exhausted page is empty. `page`, which
  every other older Base application uses, is accepted and silently ignored --
  a paginator trusting it re-reads page zero forever
* `updated_from` filters, in the query string, epoch seconds: 24 rows -> 20
  past a mid-range cutoff -> 0 past a future one
* every row of all three tables carried the same eleven fields, so those are
  pinned; `vals` and `form` differ per table by design and stay open
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Scope, Stream

TABLE = BaseConnector(
    app="table",
    title="Base Table",
    summary="Rows from the Base Table sheets a workspace names.",
    summary_vi="Dữ liệu dòng từ các bảng Base Table mà workspace chỉ định.",
    url_base="https://table.{domain}/extapi/v1/table/",
    docs_url="https://table.base.vn/",
    streams=(
        Stream(
            name="record",
            path="records",
            # Table answers `{"code":…, "data": [...]}` -- the records are the
            # `data` array itself, not a named collection inside it.
            collection=("data",),
            # `table_id` first, `id` second, because every named table lands in
            # one destination table and the ids of the three measured tables
            # come from a single installation-wide sequence -- 129 rows, 129
            # distinct ids, no overlap. That is evidence, not a guarantee from
            # Base, and if it ever stops holding a bare `id` would deduplicate
            # one table's rows away using another's. The second component is
            # constant within a partition, so it costs nothing and removes the
            # question.
            primary_key=("table_id", "id"),
            # `last_update` in epoch seconds, filtered with `updated_from` in
            # the query string: the package defaults, which is what Table uses.
            incremental=Incremental(),
            # `page_id`, not `page`. Table shares Workflow's spelling, and the
            # generic `page` is accepted and ignored -- which paginates page
            # zero forever rather than failing.
            page_field="page_id",
            page_size_field="limit",
            scope=Scope(
                config_key="table_ids",
                field="table_id",
                required=True,
                title="Tables to read",
                title_vi="Các bảng cần đọc",
                description=(
                    "The id of each Base Table sheet to sync. Base offers no "
                    "endpoint that lists them, so copy the id from the table's "
                    "URL in Base Table. At least one is required."
                ),
                description_vi=(
                    "Id của từng bảng Base Table cần đồng bộ. Base không có "
                    "endpoint liệt kê bảng, nên hãy lấy id trên URL của bảng "
                    "trong Base Table. Cần ít nhất một bảng."
                ),
            ),
            fields={
                "name": "string",
                # Present on every row of all three tables measured. Epoch
                # seconds arrive as strings here, which is why `since` is not
                # typed as an integer.
                "table_id": "string",
                "user_id": "string",
                "system_id": "string",
                "keywords": "string",
                "since": "string",
                # The cells. Base Table is user-defined, so `form` carries the
                # column definitions and `vals` the values under generated
                # keys (`f1`, `f2`, …). Typed as containers and no further:
                # every sheet has a different set, and pinning the keys of one
                # customer's table would drop another's columns.
                "form": "array",
                "vals": "object",
            },
            note=(
                "One row per record, per named table. Reading is per table by "
                "necessity, not as an optimisation: Base exposes no way to "
                "list or to read them all. The response also carries a `table` "
                "object beside `data` holding the sheet's column definitions; "
                "it is not a second stream, because `form` already repeats "
                "those definitions on every row and a second stream would "
                "double the requests to read them again."
            ),
        ),
    ),
    # The response side has now been read from the live API rather than from
    # the builder's recording: pagination, the page field, the time filter and
    # the record shape were each measured against three real tables.
    certification="SUPPORTED",
)
